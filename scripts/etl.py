#!/usr/bin/env python3
"""ETL: raw-sensitive bucket -> staging -> sanitized clean-public-safe bucket.

    export RAW_BUCKET=aep-hack-raw-<acct> CLEAN_BUCKET=aep-hack-clean-<acct> AWS_REGION=us-east-2
    .venv/bin/python scripts/etl.py                       # latest ingest date of every dataset
    .venv/bin/python scripts/etl.py --ingest-date 2026-10-24
    .venv/bin/python scripts/etl.py --no-upload           # build + sanitize locally, write nothing to S3

Stages (all local work happens in .etl_work/<run_id>/, which is gitignored):
  1. extract   s3://$RAW_BUCKET/raw/<dataset>/<date>/  -> .etl_work/<run>/raw/   (sizes verified)
  2. transform the cleaning rules in docs/data-cleaning.md (concierge.curate + concierge.risk):
               circuit crosswalk (ARCOS CIRn labels are NOT circuit numbers; numeric ids '0000060' -> 60),
               facilities EPSG:3857 -> WGS84 -> hex flags, storms from Report_Tim clusters,
               customers joined to topology with unmatched premises flagged, salted HMAC tokens
  3. staging   full build incl. internal/ (crosswalk, quality report) -> s3://$RAW_BUCKET/staging/<run>/
  4. sanitize  drop banned columns, suppress circuits with < 10 premises -> .etl_work/<run>/clean/
  5. load      clean/ -> s3://$CLEAN_BUCKET/{curated,serving}/ ; run summary -> _meta/ ; mirror -> data/curated/

The salt comes from $CONCIERGE_TOKEN_SALT or SSM /aep-hack/token-salt. It is never written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.curate.network import MIN_GROUP  # noqa: E402
from concierge.curate.pipeline import curate, sha256  # noqa: E402
from concierge.curate.restoration_tree import TREE_FILE  # noqa: E402
from concierge.curate.restoration_tree import enrich_risk_rows as enrich  # noqa: E402
from concierge.risk.build import build_artifacts  # noqa: E402
from concierge.risk.load import normalize_circuit  # noqa: E402
from concierge.risk.tokens import SALT_ENV  # noqa: E402

WORK = ROOT / ".etl_work"
SSM_SALT = "/aep-hack/token-salt"

# dataset -> where it lands inside the local work dir (mirrors the repo's data/ + docs/ layout)
DATASETS = {
    "Data1": "raw/Data1",
    "Data2": "raw/Data2",
    "matched_upstream": "raw/matched_upstream",
    "labor": "raw",                # Install_Labor_HackOHIO.xlsx
    "drawings": "docs",            # structure_drawings.pdf
}

# Columns that must never reach the clean bucket (compared lower-case). The curate loaders never read these;
# this is a second, independent net. validate.py uses the same list.
BANNED_COLUMNS = frozenset({
    # life-support / critical-customer flag
    "critical_c", "life_support", "special_op",
    # house numbers, road, city, addresses
    "address", "address_nu", "house_number", "house_num", "road_name", "road_id", "city", "street_cod",
    "anno_strin", "zipcode", "state",
    # service / exact coordinates
    "service_po", "service__1", "latitude", "longitude", "lat", "lon", "x", "y", "point_x", "point_y",
    # raw premise / meter / customer ids (tokens replace them)
    "premise_nu", "premise_id", "premise_number", "macss_mete", "meter_id", "meter", "customer_c",
    "aep_xfmr_l", "aep_locati",
    # hospital / facility names and addresses
    "name", "addressbui", "gnis_id", "foot_id", "complex_id",
    # scada / station / owner / internal ops
    "scada_cont", "aep_statio", "aep_stat_1", "owner_name", "global_id", "substation", "station",
    "outage_num", "incident_w", "near_fid", "near_dist", "task_crew", "task_job_i", "crew_area", "district",
    "reporting_", "reporting1",
})
BANNED_SUBSTRINGS = ("premise_nu", "meter", "critical", "life_support", "scada", "station", "owner", "address",
                     "latitude", "longitude", "road_", "house_")


def is_banned(col: str) -> bool:
    c = col.lower()
    return c in BANNED_COLUMNS or any(s in c for s in BANNED_SUBSTRINGS)


def log(msg: str) -> None:
    print(f"[etl] {msg}", flush=True)


def aws(*args: str, capture: bool = True) -> str:
    out = subprocess.run(["aws", *args], check=True, text=True, capture_output=capture)
    return out.stdout if capture else ""


def get_salt() -> str:
    if os.environ.get(SALT_ENV):
        log(f"salt: from ${SALT_ENV}")
        return os.environ[SALT_ENV]
    log(f"salt: from SSM {SSM_SALT}")
    return aws("ssm", "get-parameter", "--name", SSM_SALT, "--with-decryption",
               "--query", "Parameter.Value", "--output", "text").strip()


def list_dates(bucket: str, dataset: str) -> list[str]:
    out = aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", f"raw/{dataset}/", "--delimiter", "/",
              "--query", "CommonPrefixes[].Prefix", "--output", "json")
    prefixes = json.loads(out or "null") or []
    return sorted(p.rstrip("/").split("/")[-1] for p in prefixes)


def prefix_stats(bucket: str, prefix: str) -> tuple[int, int]:
    out = aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
              "--query", "[length(Contents), sum(Contents[].Size)]", "--output", "json")
    n, b = json.loads(out)
    return int(n or 0), int(b or 0)


def extract(raw_bucket: str, run_dir: Path, ingest_date: str | None) -> dict:
    """Download each dataset's chosen date folder and verify file count and bytes against S3."""
    inputs = {}
    for ds, rel in DATASETS.items():
        dates = list_dates(raw_bucket, ds)
        if not dates:
            raise SystemExit(f"no raw/{ds}/ in s3://{raw_bucket}; run `make ingest` first")
        date = ingest_date or dates[-1]
        if date not in dates:
            raise SystemExit(f"raw/{ds}/{date}/ not found (have {dates})")
        prefix = f"raw/{ds}/{date}/"
        dest = run_dir / rel
        dest.mkdir(parents=True, exist_ok=True)
        aws("s3", "sync", f"s3://{raw_bucket}/{prefix}", str(dest), "--only-show-errors", capture=False)
        n, b = prefix_stats(raw_bucket, prefix)
        keys = json.loads(aws("s3api", "list-objects-v2", "--bucket", raw_bucket, "--prefix", prefix,
                              "--query", "Contents[].Key", "--output", "json"))
        local_b = sum((dest / k[len(prefix):]).stat().st_size for k in keys)
        if local_b != b:
            raise SystemExit(f"{prefix}: downloaded {local_b} bytes, S3 has {b}")
        inputs[ds] = {"date": date, "files": n, "bytes": b}
        log(f"extract {ds:17s} {date}  {n:3d} files {b:>11,d} bytes  OK")
    return inputs


def stage(raw_bucket: str, run_dir: Path, run_id: str) -> None:
    for sub in ("curated", "risk"):
        aws("s3", "sync", str(run_dir / sub), f"s3://{raw_bucket}/staging/{run_id}/{sub}/", "--only-show-errors",
            capture=False)
    log(f"staging -> s3://{raw_bucket}/staging/{run_id}/ (expires after 7 days)")


def sanitize(run_dir: Path) -> dict:
    """Copy publishable outputs into clean/, dropping banned columns and suppressing small circuit groups."""
    src, clean = run_dir / "curated", run_dir / "clean"
    shutil.rmtree(clean, ignore_errors=True)
    (clean / "curated/tables").mkdir(parents=True)
    report = {"dropped_columns": {}, "suppressed_rows": {}, "rows": {}}

    for path in sorted((src / "tables").glob("*.parquet")):
        name = path.stem
        table = pq.read_table(path)
        drop = [c for c in table.column_names if is_banned(c)]
        if drop:
            table = table.drop_columns(drop)
        report["dropped_columns"][name] = drop
        if name == "circuit_dim":
            # A circuit row with fewer than MIN_GROUP premises would describe a handful of households.
            keep = [n >= MIN_GROUP for n in table.column("n_premises").to_pylist()]
            report["suppressed_rows"][name] = {
                "rule": f"n_premises < {MIN_GROUP}", "rows": keep.count(False),
                "premises": sum(n for n, k in zip(table.column("n_premises").to_pylist(), keep) if not k)}
            table = table.filter(pa.array(keep))
        pq.write_table(table, clean / "curated/tables" / path.name, compression="zstd")
        report["rows"][name] = table.num_rows

    shutil.copy2(src / "labor_standards.json", clean / "curated/labor_standards.json")
    if (src / "drawings").exists():
        shutil.copytree(src / "drawings", clean / "curated/drawings")
    (clean / "serving/risk").mkdir(parents=True)
    for f in ("premise_risk.jsonl", "calibration.json", TREE_FILE):
        shutil.copy2(run_dir / "risk" / f, clean / "serving/risk" / f)
    report["rows"]["premise_risk"] = sum(1 for _ in open(clean / "serving/risk/premise_risk.jsonl"))
    # internal/ (crosswalk, quality report) is deliberately NOT copied: it stays in the raw bucket's staging/.
    return report


def enrich_risk_rows(run_dir: Path) -> dict:
    """Add line context and tree position to every scored premise_risk row, and write restoration_tree.json.

      cn  homes on the premise's circuit            n0  homes served by its transformer
      nl  homes served by its nearest protective    nf  homes served by its farthest protective device
          device (branch)                               before the breaker (main line)
      pc, pn  opaque circuit index and node index in restoration_tree.json    ap  1 if its chain uses an alternate feed

    Derived only from the clean device_dim / premise_dim tables (concierge.curate.restoration_tree). The API uses these
    fields as weights and positions and never returns them. Premises without a chain (or on fallback) get none.
    """
    return enrich(run_dir / "curated/tables", run_dir / "risk")


def write_manifest(clean: Path) -> dict:
    files = sorted(p for p in clean.rglob("*") if p.is_file() and not p.relative_to(clean).parts[0] == "_meta")
    manifest = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": [{"path": str(p.relative_to(clean)), "bytes": p.stat().st_size, "sha256": sha256(p)}
                          for p in files]}
    (clean / "curated/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def summarize(quality: dict, calibration: dict) -> dict:
    """Aggregate-only numbers for _meta (no ids, no labels, no crosswalk)."""
    net = quality.get("network", {})
    premises = net.get("premises", 0)
    without = net.get("customers_without_topology", 0)
    dmg = quality["damage"]
    cs = dmg["circuit_status"]
    return {
        "premises": premises,
        "premises_in_topology": premises - without,
        "customers_without_topology": without,
        "circuit_mismatch_customer_vs_topology": net.get("circuit_mismatch_customer_vs_topology", 0),
        "premises_on_alternate_path": net.get("premises_on_alternate_path", 0),
        "devices": net.get("devices", 0),
        "devices_with_multiple_parents": net.get("devices_with_multiple_parents", 0),
        "customer_rows": calibration["counts"]["customer_rows"],
        "topology_premises": premises - without,
        "damage_rows": dmg["rows"],
        "damage_circuit_status": cs,
        "damage_circuit_match_rate": round(cs.get("mapped", 0) / dmg["rows"], 4) if dmg["rows"] else None,
        "damage_storm_rows": dmg["storm_rows"],
        "damage_unrepaired_censored": dmg["unrepaired_censored"],
        "damage_backlog_rows": dmg["backlog_rows"],
        "storms": quality["storms"],
        "hex_resolution_counts": quality["hex_resolution_counts"],
        "facility_cells": quality["facilities"],
        "crosswalk_labels": quality["rows"]["circuit_crosswalk"],
        "labor_units": quality["rows"]["labor_units"],
        "drawing_pages": quality["rows"]["drawing_pages"],
        "risk_counts": calibration["counts"],
        "risk_gate": calibration["backtest"]["gate"],
        "circuit_key_checks": {"0000060": normalize_circuit("0000060"), "60": normalize_circuit("60")},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest-date", help="raw/<dataset>/<date>/ to read (default: latest per dataset)")
    ap.add_argument("--no-upload", action="store_true", help="do not write staging or the clean bucket")
    ap.add_argument("--no-drawings", action="store_true")
    args = ap.parse_args()

    raw_bucket, clean_bucket = os.environ.get("RAW_BUCKET"), os.environ.get("CLEAN_BUCKET")
    if not raw_bucket or not clean_bucket:
        raise SystemExit("set RAW_BUCKET and CLEAN_BUCKET")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = WORK / run_id
    # Old work dirs hold a full local copy of the raw (sensitive) files; keep only the current run.
    for old in WORK.glob("2*T*Z"):
        if old.is_dir():
            shutil.rmtree(old)
    run_dir.mkdir(parents=True)
    log(f"run {run_id}  raw={raw_bucket}  clean={clean_bucket}")

    salt = get_salt()
    inputs = extract(raw_bucket, run_dir, args.ingest_date)

    res = curate(data_dir=run_dir / "raw", docs_dir=run_dir / "docs", out_dir=run_dir / "curated", salt=salt,
                 drawings=not args.no_drawings)
    log(f"curate: {res['quality']['rows']}")
    raw = run_dir / "raw"
    calibration = build_artifacts(
        customer_dbf=raw / "Data1/Customer_Clip.dbf", arcos_dbf=raw / "Data1/arcos_prod_damage_09_28_Clip.dbf",
        topology_txt=raw / "matched_upstream/matched_upstream.txt",
        device_dbfs=[raw / "Data1/Fuse_Bank_Clip.dbf", raw / "Data2/Transformer_Clip.dbf",
                     raw / "Data2/Switch_Clip.dbf", raw / "Data2/Recloser_Bank_Clip.dbf"],
        out_dir=run_dir / "risk", salt=salt)
    log(f"risk: {calibration['counts']['premises']} premises, gate={calibration['backtest']['gate']}")
    log(f"enrich: {enrich_risk_rows(run_dir)}")

    if not args.no_upload:
        stage(raw_bucket, run_dir, run_id)

    san = sanitize(run_dir)
    clean = run_dir / "clean"
    manifest = write_manifest(clean)
    staging_rows = dict(res["quality"]["rows"])
    staging_rows["premise_risk"] = calibration["counts"]["premises"]
    summary = {
        "run_id": run_id, "built_at": manifest["built_at"], "inputs": inputs,
        "rows": {"staging": staging_rows, "clean": san["rows"]},
        "sanitize": {"dropped_columns": san["dropped_columns"], "suppressed_rows": san["suppressed_rows"]},
        "findings": summarize(res["quality"], calibration),
    }
    (clean / "_meta").mkdir()
    (clean / "_meta" / f"etl_run_{run_id}.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (clean / "_meta" / "etl_run_latest.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")

    if not args.no_upload:
        for sub in ("curated", "serving"):
            aws("s3", "sync", str(clean / sub), f"s3://{clean_bucket}/{sub}/", "--delete", "--only-show-errors",
                capture=False)
        aws("s3", "sync", str(clean / "_meta"), f"s3://{clean_bucket}/_meta/", "--only-show-errors", capture=False)
        log(f"clean -> s3://{clean_bucket}/{{curated,serving,_meta}}/")

    # local mirror of the clean bucket's curated/ (and serving/, _meta/ alongside it)
    mirror = ROOT / "data"
    for sub in ("curated", "serving"):
        shutil.rmtree(mirror / sub, ignore_errors=True)
        shutil.copytree(clean / sub, mirror / sub)
    (mirror / "_meta").mkdir(exist_ok=True)
    for f in (clean / "_meta").iterdir():
        shutil.copy2(f, mirror / "_meta" / f.name)
    (WORK / "latest_run.json").write_text(json.dumps({"run_id": run_id, "run_dir": str(run_dir),
                                                      "uploaded": not args.no_upload}, indent=2) + "\n")
    log(f"mirror -> {mirror / 'curated'}  ({len(manifest['files'])} files)")
    log("done. next: make validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
