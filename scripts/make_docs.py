#!/usr/bin/env python3
"""Generate docs/data_dictionary.md and docs/data_findings.md from the clean outputs (data/curated, data/_meta).

    .venv/bin/python scripts/make_docs.py

Reads only the sanitized local mirror written by scripts/etl.py. Fails if a published column has no description,
so the dictionary can never silently fall behind the tables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CURATED, META = ROOT / "data/curated", ROOT / "data/_meta"

C, T, A, F, G, L, X = ("Customer_Clip", "matched_upstream.txt", "arcos_prod_damage_09_28_Clip",
                       "Hospitals / Fire_Stations / Ambulance layers", "Primary/Secondary lines + device layers",
                       "derived", "Install_Labor_HackOHIO.xlsx")

# table -> column -> (source, meaning)
COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    "premise_dim": {
        "premise_token": (f"{C}.premise_nu / {T}", "Opaque premise id: 'p_' + 16 hex of HMAC-SHA256(salt, premise number)"),
        "transformer_key": (T, "Salted key of the premise's service transformer (first device in its upline chain)"),
        "circuit": (T, "Numeric circuit id from the topology breaker ('0000060' -> 60). Topology wins over the customer file"),
        "service_type": (f"{C}.kind_of_se", "Overhead, Underground or Unknown"),
        "protective_count": (T, "Fuses, reclosers and sectionalizers between the premise and the breaker"),
        "hops": (T, "Devices in the upline chain minus one"),
        "hex_cell": (f"{C} geometry", "H3 cell holding >= 10 premises (res 8, else 7, else 6). Null if no cell qualifies"),
        "hex_res": (L, "H3 resolution of hex_cell"),
        "in_topology": (L, "Premise has an upline chain in the topology file"),
        "in_customer_file": (L, "Premise is in the customer file (false = topology only)"),
        "alternate_path": (L, "Premise's own chain disagrees with the majority-parent tree (alternate feed)"),
    },
    "device_dim": {
        "device_key": (T, "Opaque device id: 'd_' + 12 hex of HMAC-SHA256(salt, class*id)"),
        "device_class": (T, "transformer, isolating_device or breaker"),
        "device_type": (T, "Device subtype from the topology (e.g. Fuse, Recloser (Electronic), Distribution)"),
        "parent_key": (L, "device_key of the majority upstream parent; null at the breaker"),
        "circuit": (T, "Numeric circuit id of the breaker the device feeds from"),
        "depth": (L, "Distance from the breaker in the resolved tree (0 = breaker)"),
        "is_protective": (T, "Fuse, recloser or sectionalizer"),
        "n_premises_served": (L, "Premise chains that pass through this device"),
        "small_group": (L, "n_premises_served < 10 (use aggregated, never shown per device to customers)"),
    },
    "damage_fact": {
        "damage_id": (L, "Row number in the ARCOS extract (not the ARCOS outage/incident id)"),
        "storm_id": (f"{A}.Report_Tim", "S1 (Sep 27-Oct 2 2024), S2 (Feb 12-18 2025) from report-date clusters; null = background"),
        "report_date": (f"{A}.Report_Tim", "Date the damage was reported"),
        "repaired": (f"{A}.Repaired", "Repair recorded as done"),
        "repair_days": (f"{A}.Repaired_D - Report_Tim", "Whole days to repair; null when unrepaired (placeholder dates)"),
        "is_backlog": (L, "repair_days >= 7: backlog tail, not outage length"),
        "asset_type": (f"{A}.Asset_Type", "Normalized asset: primary_line, pole, fuse_bank, transformer, ..."),
        "damage_type": (f"{A}.Inform_Wor", "wire, switch, pole, equipment or light"),
        "access_issue": (f"{A}.Access_Iss", "Crew reported an access problem"),
        "special_equipment": (f"{A}.Special_Eq", "Special equipment needed, slugged"),
        "media_count": (f"{A}.Media", "Number of attached photos/media"),
        "circuit": (f"{A}.Circuit via crosswalk", "Numeric circuit id. ARCOS 'CIRn' labels are mapped through the device-layer crosswalk, never by their digits"),
        "circuit_status": (L, "mapped, label_unknown (facility-style names), not_in_topology or blank_label"),
        "hex_cell": (f"{A} geometry", "H3 res-8 cell of the damage point (no exact coordinates)"),
    },
    "circuit_dim": {
        "circuit": (T, "Numeric circuit id. Circuits with < 10 premises are suppressed from the clean bucket"),
        "n_premises": (T, "Premises fed by the circuit"),
        "n_overhead": (C, "Overhead-service premises"),
        "n_underground": (C, "Underground-service premises"),
        "n_unknown_service": (C, "Premises with unknown service type"),
        "n_transformers": (T, "Distribution transformers in the circuit's tree"),
        "n_protective_devices": (T, "Protective devices in the circuit's tree"),
        "max_hops": (T, "Longest premise chain on the circuit"),
        "primary_overhead_miles": (G, "Overhead primary line miles (report_len feet / 5280)"),
        "primary_underground_miles": (G, "Underground primary line miles"),
        "secondary_miles": (G, "Secondary line miles"),
        "gis_fuse_banks": (G, "Fuse banks in the GIS layer"),
        "gis_reclosers": (G, "Recloser banks in the GIS layer"),
        "gis_switches": (G, "Switches in the GIS layer"),
        "gis_transformers": (G, "Transformers in the GIS layer"),
        "damage_s1": (A, "Mapped damage rows in storm S1"),
        "damage_s2": (A, "Mapped damage rows in storm S2"),
        "damage_background": (A, "Mapped damage rows outside both storms"),
    },
    "hex_context": {
        "hex_cell": (L, "H3 cell (only cells with >= 10 premises are published)"),
        "hex_res": (L, "H3 resolution"),
        "center_lat": (L, "Cell centre latitude, 4 dp (centre of a ~0.7 km2+ cell, not a premise location)"),
        "center_lon": (L, "Cell centre longitude, 4 dp"),
        "n_premises": (C, "Premises assigned to the cell (>= 10)"),
        "overhead_share": (C, "Share of the cell's premises with overhead service"),
        "damage_s1": (A, "Storm S1 damage rows inside the cell"),
        "damage_s2": (A, "Storm S2 damage rows inside the cell"),
        "hospital_nearby": (F, "A hospital is in or next to the cell (EPSG:3857 -> WGS84; names/addresses never read)"),
        "fire_nearby": (F, "A fire station is in or next to the cell"),
        "ambulance_nearby": (F, "An ambulance service is in or next to the cell"),
    },
}

JSONL = {  # serving/risk/premise_risk.jsonl keys (also the DynamoDB item attributes; the API never returns cn/n0/nl/nf)
    "cn": ("int", "Homes on the premise's line. Server-side exposure weight for restoration; never returned"),
    "n0": ("int", "Homes served by the premise's transformer. Server-side only; never returned"),
    "nl": ("int", "Homes served by its nearest protective device (branch). Server-side only; never returned"),
    "nf": ("int", "Homes served by its farthest protective device before the breaker (main line). Server-side only; never returned"),
    "t": ("string", "premise_token"), "st": ("string", "ok or fb (fallback)"),
    "why": ("string", "Fallback reason code (no_circuit, circuit_too_small, no_history_small_circuit)"),
    "R": ("float", "Circuit damage reports per 1,000 premises per storm"), "Re": ("float", "R / k_bar (shrunk rate)"),
    "svc": ("string", "Service type"), "P": ("int", "Protective device count"), "H": ("int", "Hops"),
    "n": ("int", "Damage records behind the circuit rate"), "c": ("string", "Confidence: High, Medium, Low"),
}

# Raw source columns that are dropped (never read, or tokenised then dropped) before the clean bucket.
SENSITIVE_RAW = [
    ("critical_c", C, "Life-support / critical-customer flag"),
    ("address_nu, address", C, "House number and address"),
    ("road_name, road_id, city, street_cod, anno_strin", C, "Road, city, street annotation"),
    ("service_po, service__1, point geometry", C, "Service-point coordinates (replaced by hex cells)"),
    ("premise_nu", C, "Premise number (replaced by premise_token)"),
    ("macss_mete", C, "Meter id"),
    ("customer_c, aep_xfmr_l, aep_locati, special_op", C, "Customer / location internal codes"),
    ("NAME, ADDRESS, ADDRESSBUI, CITY, ZIPCODE, STATE, GNIS_ID, ...", F, "Facility names and addresses (geometry only is read)"),
    ("Outage_Num, Incident_W, Substation, Crew_Area, District, TASK_*, Reporting_*", A, "ARCOS internal ops fields"),
    ("Latitude, Longitude, NEAR_FID, NEAR_DIST", A, "Exact damage coordinates (replaced by hex cell)"),
    ("scada_cont, aep_statio, aep_stat_1, owner_name, global_id", "device layers", "SCADA, station, owner, GIS ids"),
    ("aep_circui", "device layers", "Circuit label: used only to build the internal crosswalk (raw bucket staging)"),
]


def dictionary() -> str:
    out = ["# Data dictionary", "",
           "Generated by `make docs` from the published (clean bucket) tables. Every published column is listed; "
           "`make docs` fails if one is missing.", "",
           "| Table | Column | Type | Source | Meaning | Sensitive |", "| --- | --- | --- | --- | --- | --- |"]
    missing = []
    for table, cols in COLUMNS.items():
        schema = pq.read_schema(CURATED / "tables" / f"{table}.parquet")
        for f in schema:
            if f.name not in cols:
                missing.append(f"{table}.{f.name}")
                continue
            src, meaning = cols[f.name]
            out.append(f"| {table} | `{f.name}` | {f.type} | {src} | {meaning} | no |")
    for k, (typ, meaning) in JSONL.items():
        out.append(f"| premise_risk.jsonl | `{k}` | {typ} | derived | {meaning} | no |")
    if missing:
        raise SystemExit(f"undocumented published columns: {missing}")
    out += ["", "## Raw columns removed before the clean bucket", "",
            "These exist only in the raw-sensitive bucket. Validation fails if any of them appears in the clean bucket.",
            "", "| Column(s) | Source | Meaning | Sensitive |", "| --- | --- | --- | --- |"]
    out += [f"| `{c}` | {s} | {m} | yes |" for c, s, m in SENSITIVE_RAW]
    return "\n".join(out) + "\n"


def findings() -> str:
    etl = json.loads((META / "etl_run_latest.json").read_text())
    vals = sorted(META.glob("validation_*.json"))
    val = json.loads(vals[-1].read_text()) if vals else None
    f, rows = etl["findings"], etl["rows"]
    pct = lambda a, b: f"{100 * a / b:.2f}%" if b else "n/a"  # noqa: E731
    t = ["# Data findings", "",
         f"Generated by `make docs` from ETL run `{etl['run_id']}` (built {etl['built_at']}).", "",
         "## Inputs (raw bucket)", "", "| Dataset | Ingest date | Files | Bytes |", "| --- | --- | --- | --- |"]
    t += [f"| {k} | {v['date']} | {v['files']} | {v['bytes']:,} |" for k, v in etl["inputs"].items()]
    t += ["", "## Row counts", "", "| Table | Staging | Clean | Note |", "| --- | --- | --- | --- |"]
    sup = etl["sanitize"]["suppressed_rows"]
    for name in ["premise_dim", "device_dim", "damage_fact", "circuit_dim", "hex_context", "premise_risk"]:
        note = (f"{sup[name]['rows']} circuits ({sup[name]['premises']} premises) suppressed: {sup[name]['rule']}"
                if name in sup else "")
        t.append(f"| {name} | {rows['staging'][name]:,} | {rows['clean'][name]:,} | {note} |")
    cs = f["damage_circuit_status"]
    t += ["", "## Match rates", "",
          f"- Customer rows: {f['customer_rows']:,}. Topology premises: {f['topology_premises']:,}. "
          f"Union published in premise_dim: {f['premises']:,}.",
          f"- Customers with no topology chain (`in_topology = false`, \"can't rate\" cases): "
          f"{f['customers_without_topology']}.",
          f"- Customer file and topology disagree on circuit (topology wins): "
          f"{f['circuit_mismatch_customer_vs_topology']}.",
          f"- Premises on an alternate feed path: {f['premises_on_alternate_path']}. Devices with more than one "
          f"parent: {f['devices_with_multiple_parents']} of {f['devices']:,}.",
          f"- ARCOS damage rows mapped to a topology circuit via the crosswalk: {cs.get('mapped', 0):,} of "
          f"{f['damage_rows']:,} ({pct(cs.get('mapped', 0), f['damage_rows'])}). Unmapped: "
          + ", ".join(f"{k} {v}" for k, v in cs.items() if k != "mapped") + ".",
          f"- Crosswalk labels: {f['crosswalk_labels']} (device layers agree 1:1). Numeric id check: "
          f"'0000060' -> {f['circuit_key_checks']['0000060']}.",
          f"- Hex cells: " + ", ".join(f"res {k}: {v:,}" for k, v in sorted(f["hex_resolution_counts"].items())) +
          " customer points (none = no cell with >= 10 premises).",
          f"- Facility cells (after EPSG:3857 -> WGS84): " +
          ", ".join(f"{k} {v}" for k, v in f["facility_cells"].items()) + "."]
    t += ["", "## Storm clusters (from Report_Tim)", "", "| Storm | Label | Start | End | Damage rows |",
          "| --- | --- | --- | --- | --- |"]
    t += [f"| {s['id']} | {s['label']} | {s['start']} | {s['end']} | {s['records']:,} |" for s in f["storms"]]
    t.append(f"| background | outside both windows | | | {f['damage_storm_rows'].get('background', 0):,} |")
    rc = f["risk_counts"]
    t += ["", "## Issues found", "",
          "- ARCOS `CIRn` labels are not circuit numbers (CIR2 is circuit 16). Mapping by digits would mis-assign "
          "damage; the crosswalk from the device layers is used instead.",
          f"- {cs.get('label_unknown', 0)} ARCOS rows carry facility-style labels (e.g. TINKER, PENN FOREST, "
          "SERVICE CENTER) that are not circuits.",
          f"- {f['damage_unrepaired_censored']} rows are unrepaired but carry a placeholder repair date; their "
          "repair_days is null, not zero.",
          f"- {f['damage_backlog_rows']} rows take 7+ days and are flagged is_backlog (repair backlog, not outage length).",
          "- `Event` is 'Default' on every ARCOS row, so storms are found from report-date clusters.",
          "- Damage rows are repairs, not outages. Only two storms exist; Sep 2024 is ~6.5x smaller than Feb 2025.",
          f"- Risk engine: {rc['scored']:,} premises scored, fallbacks " +
          ", ".join(f"{k} {v:,}" for k, v in rc["fallbacks"].items()) + f". Backtest gate: {f['risk_gate']}.",
          f"- {sup.get('circuit_dim', {}).get('rows', 0)} circuits have fewer than 10 premises and are suppressed "
          "from circuit_dim in the clean bucket.",
          "- Raw files (including the customer and hospital layers) were committed to Git before `data/` was "
          "ignored; see docs/pipeline.md."]
    if val:
        t += ["", "## Latest validation", "",
              f"- {'PASS' if val['passed'] else 'FAIL'}: {sum(c['ok'] for c in val['checks'])}/{len(val['checks'])} "
              f"checks, run `{val['run_id']}`. Full report: docs/validation_{val['date']}.md"]
    return "\n".join(t) + "\n"


def main() -> int:
    if not (META / "etl_run_latest.json").exists():
        raise SystemExit("no ETL output in data/; run `make etl` first")
    (ROOT / "docs/data_dictionary.md").write_text(dictionary())
    (ROOT / "docs/data_findings.md").write_text(findings())
    print("wrote docs/data_dictionary.md, docs/data_findings.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
