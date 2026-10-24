"""Orchestrates curation: raw files -> artifacts/curated/{tables,internal,drawings,...}."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from concierge.risk.load import load_crosswalk, normalize_circuit, normalize_service
from concierge.risk.storms import count_days, detect_storms
from concierge.risk.tokens import get_salt

from . import damage, facilities, geo
from .circuits import build_circuit_dim, gis_device_counts, gis_line_miles
from .labor import parse_labor
from .network import MIN_GROUP, build_network
from .schemas import BANNED_OUTPUT_COLUMNS, SCHEMAS
from .shapes import iter_points, iter_records

DEVICE_LAYERS = ["Data1/Fuse_Bank_Clip.dbf", "Data2/Transformer_Clip.dbf",
                 "Data2/Switch_Clip.dbf", "Data2/Recloser_Bank_Clip.dbf"]
PUBLIC_TABLES = ["premise_dim", "device_dim", "damage_fact", "circuit_dim", "hex_context"]


class PrivacyError(RuntimeError):
    """A curated table contains a banned column."""


def write_table(name: str, rows: list[dict], directory: Path) -> Path:
    schema = SCHEMAS[name]
    banned = {f.name for f in schema} & BANNED_OUTPUT_COLUMNS
    if banned:
        raise PrivacyError(f"{name}: banned columns {sorted(banned)}")
    table = pa.Table.from_pylist(rows, schema=schema)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_drawings(pdf: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    """Render each PDF page to PNG with poppler's pdftoppm. Skipped (returns []) if poppler is not installed."""
    if shutil.which("pdftoppm") is None or not pdf.exists():
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(out_dir / "page")], check=True)
    return sorted(out_dir.glob("page-*.png"))


def curate(*, data_dir: Path, docs_dir: Path | None, out_dir: Path, salt: str | bytes | None = None,
           drawings: bool = True) -> dict:
    salt_b = get_salt(salt)
    out_dir = Path(out_dir)
    tables_dir, internal_dir = out_dir / "tables", out_dir / "internal"
    for d in (tables_dir, internal_dir):
        shutil.rmtree(d, ignore_errors=True)
    q: dict = {"dropped_columns": {}, "notes": []}

    # 1. circuit crosswalk (ARCOS labels are not circuit numbers)
    crosswalk = load_crosswalk([data_dir / p for p in DEVICE_LAYERS])
    write_table("circuit_crosswalk", [{"circuit_label": k, "circuit": v} for k, v in sorted(crosswalk.items())],
                internal_dir)

    # 2. customers: tokenise immediately, bucket coordinates into suppressed hex cells
    cust_raw, points = [], []
    for rec, pt in iter_points(data_dir / "Data1/Customer_Clip.shp", ["premise_nu", "circuit_id", "kind_of_se"],
                               allow=["premise_nu"]):
        cust_raw.append(rec)
        points.append(pt)
    cells = geo.assign_cells(points, min_group=MIN_GROUP)
    customers = {}
    for rec, (cell, res) in zip(cust_raw, cells):
        pid = rec["premise_nu"]
        if pid:
            customers[pid] = {"circuit": normalize_circuit(rec["circuit_id"]),
                              "service": normalize_service(rec["kind_of_se"]), "cell": cell, "res": res}
    del points, cust_raw
    q["hex_resolution_counts"] = {str(k): v for k, v in Counter("none" if r is None else r for _, r in cells).items()}

    # 3. network
    premises, devices, nq = build_network(data_dir / "matched_upstream/matched_upstream.txt", customers, salt_b)
    q["network"] = nq
    topology_circuits = {p["circuit"] for p in premises if p["circuit"] is not None}

    # 4. ARCOS damage
    arcos = [(rec, pt) for rec, pt in iter_points(data_dir / "Data1/arcos_prod_damage_09_28_Clip.shp",
                                                  damage.ARCOS_COLUMNS)]
    storms = detect_storms(count_days(r["Report_Tim"] if hasattr(r["Report_Tim"], "year") else None
                                      for r, _ in arcos))
    dmg = damage.clean_all(arcos, crosswalk=crosswalk, topology_circuits=topology_circuits, storms=storms)
    q["storms"] = [{"id": f"S{i + 1}", "label": s.label, "start": s.start.isoformat(), "end": s.end.isoformat(),
                    "records": s.records} for i, s in enumerate(storms)]
    q["damage"] = {
        "rows": len(dmg),
        "circuit_status": dict(Counter(d["circuit_status"] for d in dmg)),
        "unrepaired_censored": sum(1 for d in dmg if not d["repaired"]),
        "backlog_rows": sum(1 for d in dmg if d["is_backlog"]),
        "storm_rows": dict(Counter(d["storm_id"] or "background" for d in dmg)),
        "unknown_asset_type": sum(1 for d in dmg if d["asset_type"] is None),
        "unknown_damage_type": sum(1 for d in dmg if d["damage_type"] is None),
    }
    q["dropped_columns"]["arcos"] = ["Outage_Num", "Incident_W", "Event", "Region", "District", "Crew_Area",
                                     "Substation", "Reporting_", "Reporting1", "TASK_Crew", "TASK_Job_I", "Status",
                                     "Repaired_B", "Longitude", "Latitude", "NEAR_FID", "NEAR_DIST"]

    # 5. hex context (premise counts, damage counts, priority-facility flags)
    fac = facilities.facility_cells(data_dir / "Data1")
    by_cell: dict[tuple[str, int], dict] = {}
    for p in premises:
        if p["hex_cell"] is None:
            continue
        c = by_cell.setdefault((p["hex_cell"], p["hex_res"]), {"n": 0, "oh": 0})
        c["n"] += 1
        c["oh"] += p["service_type"] == "Overhead"
    dmg_by = defaultdict(lambda: Counter())
    res_used = sorted({r for _, r in by_cell})
    for d in dmg:
        if d["hex_cell"] and d["storm_id"]:
            for r in res_used:
                dmg_by[(geo.parent_at(d["hex_cell"], r), r)][d["storm_id"]] += 1
    hex_rows = []
    for (cell, res), c in sorted(by_cell.items()):
        lat, lon = geo.cell_center(cell)
        flags = facilities.nearby_flags(cell, fac)
        hex_rows.append({
            "hex_cell": cell, "hex_res": res, "center_lat": lat, "center_lon": lon, "n_premises": c["n"],
            "overhead_share": round(c["oh"] / c["n"], 3),
            "damage_s1": dmg_by[(cell, res)]["S1"], "damage_s2": dmg_by[(cell, res)]["S2"],
            "hospital_nearby": flags["hospital"], "fire_nearby": flags["fire"], "ambulance_nearby": flags["ambulance"],
        })
    q["facilities"] = {k: len(v) for k, v in fac.items()}
    q["dropped_columns"]["facilities"] = "all attributes (names, addresses, ids); only geometry bucketed to hex flags"

    # 6. circuits
    circuits = build_circuit_dim(premises, devices, dmg, gis_line_miles(data_dir), gis_device_counts(data_dir))

    # 7. labor + drawings
    labor = parse_labor(data_dir / "Install_Labor_HackOHIO.xlsx")
    labor_path = out_dir / "labor_standards.json"
    labor_path.write_text(json.dumps(labor, indent=2) + "\n")
    pages = render_drawings(docs_dir / "structure_drawings.pdf", out_dir / "drawings") if (drawings and docs_dir) else []

    # 8. write tables
    written = {n: write_table(n, rows, tables_dir) for n, rows in
               [("premise_dim", premises), ("device_dim", devices), ("damage_fact", dmg),
                ("circuit_dim", circuits), ("hex_context", hex_rows)]}
    q["rows"] = {n: pq.read_metadata(p).num_rows for n, p in written.items()}
    q["rows"]["circuit_crosswalk"] = len(crosswalk)
    q["rows"]["labor_units"] = len(labor["units"])
    q["rows"]["drawing_pages"] = len(pages)

    # 9. privacy scan of every published table
    for name, path in written.items():
        cols = {c.lower() for c in pq.read_schema(path).names}
        if cols & BANNED_OUTPUT_COLUMNS:
            raise PrivacyError(f"{name}: banned columns {sorted(cols & BANNED_OUTPUT_COLUMNS)}")

    (internal_dir / "quality_report.json").write_text(json.dumps(q, indent=2, sort_keys=True, default=str) + "\n")

    # 10. manifest of publishable files
    files = [*written.values(), labor_path, *pages]
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [{"path": str(p.relative_to(out_dir)), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"quality": q, "manifest": manifest}
