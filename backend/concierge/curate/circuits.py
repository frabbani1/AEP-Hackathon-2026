"""circuit_dim: one row per circuit from topology, customers, GIS layers and ARCOS."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from concierge.risk.load import normalize_circuit

from .shapes import iter_records

FEET_PER_MILE = 5280.0

GIS_LINES = {"primary": "Data1/Primary_ExportFeatures.dbf", "secondary": "Data2/Secondary_ExportFeatures.dbf"}
GIS_DEVICES = {"gis_fuse_banks": "Data1/Fuse_Bank_Clip.dbf", "gis_transformers": "Data2/Transformer_Clip.dbf",
               "gis_switches": "Data2/Switch_Clip.dbf", "gis_reclosers": "Data2/Recloser_Bank_Clip.dbf"}


def gis_line_miles(data_dir: Path) -> dict[int, dict[str, float]]:
    """Feet of line per circuit split by overhead/underground (report_len is in feet; checked against geodesic)."""
    miles: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for kind, rel in GIS_LINES.items():
        for r in iter_records(data_dir / rel, ["circuit_id", "mounting", "report_len"]):
            c = normalize_circuit(r["circuit_id"])
            if c is None or not isinstance(r["report_len"], (int, float)):
                continue
            mount = "overhead" if str(r["mounting"]).lower() == "overhead" else "underground"
            miles[c][f"{kind}_{mount}"] += r["report_len"] / FEET_PER_MILE
    return miles


def gis_device_counts(data_dir: Path) -> dict[int, Counter]:
    counts: dict[int, Counter] = defaultdict(Counter)
    for col, rel in GIS_DEVICES.items():
        for r in iter_records(data_dir / rel, ["circuit_id"]):
            c = normalize_circuit(r["circuit_id"])
            if c is not None:
                counts[c][col] += 1
    return counts


def build_circuit_dim(
    premises: Iterable[Mapping], devices: Iterable[Mapping], damage: Iterable[Mapping],
    miles: Mapping[int, Mapping[str, float]], gis_counts: Mapping[int, Counter],
) -> list[dict]:
    rows: dict[int, dict] = {}

    def row(c: int) -> dict:
        return rows.setdefault(c, {
            "circuit": c, "n_premises": 0, "n_overhead": 0, "n_underground": 0, "n_unknown_service": 0,
            "n_transformers": 0, "n_protective_devices": 0, "max_hops": 0,
            "damage_s1": 0, "damage_s2": 0, "damage_background": 0})

    for p in premises:
        if p["circuit"] is None:
            continue
        r = row(p["circuit"])
        r["n_premises"] += 1
        r[{"Overhead": "n_overhead", "Underground": "n_underground"}.get(p["service_type"], "n_unknown_service")] += 1
        r["max_hops"] = max(r["max_hops"], p["hops"] or 0)
    for d in devices:
        r = row(d["circuit"])
        if d["device_class"] == "transformer" and d["device_type"] == "Distribution":
            r["n_transformers"] += 1
        if d["is_protective"]:
            r["n_protective_devices"] += 1
    for d in damage:
        if d["circuit_status"] != "mapped":
            continue
        r = row(d["circuit"])
        r[{"S1": "damage_s1", "S2": "damage_s2"}.get(d["storm_id"], "damage_background")] += 1

    out = []
    for c, r in sorted(rows.items()):
        m, g = miles.get(c, {}), gis_counts.get(c, Counter())
        r.update({
            "primary_overhead_miles": round(m.get("primary_overhead", 0.0), 3),
            "primary_underground_miles": round(m.get("primary_underground", 0.0), 3),
            "secondary_miles": round(m.get("secondary_overhead", 0.0) + m.get("secondary_underground", 0.0), 3),
            "gis_fuse_banks": g["gis_fuse_banks"], "gis_reclosers": g["gis_reclosers"],
            "gis_switches": g["gis_switches"], "gis_transformers": g["gis_transformers"],
        })
        out.append(r)
    return out
