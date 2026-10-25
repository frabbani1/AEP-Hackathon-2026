"""Map zones: the areas a customer picks on the outage map, each tied to one representative premise.

The map shows areas, the engines take premise tokens. This module is the link between the two, and it runs at build time
only (needs pyarrow and h3). The API reads the result (`zones.json`) and never imports this module.

A zone is an H3 cell at resolution 6 (about 36 km2). Every published premise cell (resolution 8, else 7, else 6, from
`premise_dim.hex_cell`) has exactly one resolution-6 ancestor, so every zone holds at least as many premises as its
smallest child cell, which is 10 or more. Zones are coarser than the premise cells on purpose: the map stays readable
and no zone describes a handful of households.

Each zone is rated through one representative premise: the one whose typical-storm risk score is the median among the
zone's rated premises (ties broken by token). The zone answers are that premise's answers, so they describe a typical
home in the area, not the area as a whole. A zone with no rated premise is "unrated" and carries the most common
fallback reason among its premises.

`zones.json` is server-side only. It holds premise tokens, so it is never published to a browser. The zone id is an
HMAC of the cell under the token salt (letters only), so it cannot be turned back into a cell. Responses carry the id, a
display name, the outline, the band and the confidence, nothing else. The name comes from public place names near the
cell's outline (`concierge.zones.places`), such as "Salem" or "East Roanoke". It uses no customer field.

The display text is stored under `label`, not `name`: `scripts/validate.py` bans the key `name` from every published file
(hospital and facility names).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from concierge.curate.network import MIN_GROUP
from concierge.risk.config import RiskConfig
from concierge.risk.features import raw_score
from concierge.risk.schema import _IDENTIFIER
from concierge.risk.tokens import get_salt
from concierge.zones.places import name_zones

ZONES_FILE = "zones.json"
ZONES_VERSION = "zones-v1"
ZONE_RESOLUTION = 6
RING_DECIMALS = 4                 # about 11 m, far finer than the 36 km2 cell it outlines
BOUNDS_PAD_DEGREES = 0.01


def zone_id(salt: bytes, cell: str) -> str:
    """'z_' plus 12 lowercase letters. Letters only, because the response scan rejects any run of 6 or more digits."""
    for attempt in range(100):
        digest = hmac.new(salt, b"zone:" + cell.encode() + (b":%d" % attempt if attempt else b""),
                          hashlib.sha256).digest()
        candidate = "z_" + "".join(chr(ord("a") + b % 26) for b in digest[:12])
        if not _IDENTIFIER.search(candidate):
            return candidate
    raise RuntimeError("could not make a zone id that passes the response scan")


def zone_cell(cell: str, resolution: int = ZONE_RESOLUTION) -> str:
    """The zone (resolution-6 ancestor) of a premise cell. Cells already at the zone resolution map to themselves."""
    import h3
    return cell if h3.get_resolution(cell) <= resolution else h3.cell_to_parent(cell, resolution)


def ring_lng_lat(cell: str) -> list[list[float]]:
    """Outline of a cell as [longitude, latitude] pairs (GeoJSON order), without repeating the first point."""
    import h3
    return [[round(lng, RING_DECIMALS), round(lat, RING_DECIMALS)] for lat, lng in h3.cell_to_boundary(cell)]


def pick_representative(scored: Iterable[tuple[float, str]]) -> str | None:
    """Token of the median-scored premise, or None when there are none. Ties are broken by token."""
    ordered = sorted(scored)
    return ordered[len(ordered) // 2][1] if ordered else None


def build_zone_doc(premises: Iterable[Mapping], rows: Mapping[str, Mapping], cell_facility: Mapping[str, bool],
                   cfg: RiskConfig, salt: bytes, resolution: int = ZONE_RESOLUTION) -> tuple[dict, dict]:
    """Return (zones document, stats).

    premises: {premise_token, hex_cell}; rows: premise_risk rows by token; cell_facility: any critical facility
    nearby, by premise cell.
    """
    import h3
    members: dict[str, list[str]] = defaultdict(list)
    for p in premises:
        if p["hex_cell"]:
            members[zone_cell(p["hex_cell"], resolution)].append(p["premise_token"])
    facility: dict[str, bool] = defaultdict(bool)
    for cell, flag in cell_facility.items():
        facility[zone_cell(cell, resolution)] |= bool(flag)

    zones, skipped = [], 0
    for cell, tokens in members.items():
        if len(tokens) < MIN_GROUP:
            skipped += 1
            continue
        scored, reasons = [], Counter()
        for t in tokens:
            row = rows.get(t)
            if row is None:
                continue
            if row["st"] == "ok":
                scored.append((raw_score(row["Re"], row["svc"], row["P"], row["H"], 1.0, cfg), t))
            elif row.get("why"):
                reasons[row["why"]] += 1
        rep = pick_representative(scored)
        fallback = None
        if rep is None:
            if not reasons:
                skipped += 1               # nothing to say about this zone at all
                continue
            fallback = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        lat, lng = h3.cell_to_latlng(cell)
        zones.append({"id": zone_id(salt, cell), "ring": ring_lng_lat(cell), "rep": rep, "fb": fallback,
                      "facility": facility[cell], "n": len(tokens), "_lat": lat, "_lng": lng})

    # Stable order (north to south, then west to east) and readable names from public place names ("Salem", "East Roanoke").
    zones.sort(key=lambda z: (-round(z["_lat"], 3), z["_lng"], z["id"]))
    labels = name_zones(zones)
    for z in zones:
        z["label"] = labels[z["id"]]
        del z["_lat"], z["_lng"]
    lngs = [pt[0] for z in zones for pt in z["ring"]]
    lats = [pt[1] for z in zones for pt in z["ring"]]
    pad = BOUNDS_PAD_DEGREES
    bounds = [[round(min(lats) - pad, 3), round(min(lngs) - pad, 3)],
              [round(max(lats) + pad, 3), round(max(lngs) + pad, 3)]] if zones else None
    doc = {"version": ZONES_VERSION, "resolution": resolution, "min_homes": MIN_GROUP, "bounds": bounds,
           "zones": [{k: z[k] for k in ("id", "label", "ring", "rep", "fb", "facility", "n")} for z in zones]}
    stats = {"zones": len(zones), "zones_unrated": sum(1 for z in zones if z["rep"] is None),
             "zones_skipped": skipped, "premises_in_zones": sum(z["n"] for z in zones)}
    return doc, stats


def build_zones(tables_dir: str | Path, risk_dir: str | Path, salt: str | bytes | None = None) -> dict:
    """Write `zones.json` next to premise_risk.jsonl and return aggregate stats (no tokens)."""
    import pyarrow.parquet as pq

    tables, risk = Path(tables_dir), Path(risk_dir)
    premises = pq.read_table(tables / "premise_dim.parquet", columns=["premise_token", "hex_cell"]).to_pylist()
    hexes = pq.read_table(tables / "hex_context.parquet",
                          columns=["hex_cell", "hospital_nearby", "fire_nearby", "ambulance_nearby"]).to_pylist()
    cell_facility = {h["hex_cell"]: h["hospital_nearby"] or h["fire_nearby"] or h["ambulance_nearby"] for h in hexes}
    rows = {}
    with open(risk / "premise_risk.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rows[r.pop("t")] = r
    cfg = RiskConfig.from_dict(json.loads((risk / "calibration.json").read_text())["config"])
    doc, stats = build_zone_doc(premises, rows, cell_facility, cfg, get_salt(salt))
    check_zone_doc(doc)
    (risk / ZONES_FILE).write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return stats


def check_zone_doc(doc: Mapping) -> None:
    """Fail the build rather than publish a zone that could describe a handful of households."""
    from concierge.zones.index import ZoneIndex
    ZoneIndex.from_dict(doc)
