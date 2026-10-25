#!/usr/bin/env python3
"""Validate what the ETL published to the clean-public-safe bucket. Exits 1 if any check fails.

    export RAW_BUCKET=... CLEAN_BUCKET=... AWS_REGION=us-east-2
    .venv/bin/python scripts/validate.py                  # validates the run in .etl_work/latest_run.json

Checks
  readback     every Parquet file reads back in full; every JSON/JSONL parses; PNGs have a PNG header;
               every file matches curated/manifest.json (bytes + sha256)
  privacy      no banned column (Parquet schema, JSON key) anywhere in the clean bucket;
               no raw premise number appears as a value in any clean table or JSONL row;
               no internal/ prefix in the clean bucket
  row counts   raw -> staging: damage rows == ARCOS records, premise rows == customers UNION topology premises;
               staging -> clean: equal, except rows the ETL reported as suppressed;
               clean vs last passing validation: no table shrinks by more than 1%
  groups       every published location / aggregate group has >= 10 premises:
               hex_context rows, premise_dim (hex_cell, hex_res) groups, circuit_dim rows
  buckets      both buckets block all public access, are not public by policy, have default encryption + versioning

Report: docs/validation_<date>.md and s3://$CLEAN_BUCKET/_meta/validation_<date>.md (+ .json).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from concierge.risk.load import load_arcos, load_customers, load_topology  # noqa: E402
from etl import MIN_GROUP, WORK, is_banned  # noqa: E402

MAX_SHRINK = 0.01
TABLES = ["premise_dim", "device_dim", "damage_fact", "circuit_dim", "hex_context"]


class Report:
    def __init__(self) -> None:
        self.checks: list[tuple[str, str, bool, str]] = []   # (section, name, ok, detail)
        self.info: list[str] = []

    def check(self, section: str, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((section, name, bool(ok), detail))
        print(f"[validate] {'PASS' if ok else 'FAIL'}  {section:10s} {name}  {detail}", flush=True)
        return ok

    @property
    def passed(self) -> bool:
        return all(ok for _, _, ok, _ in self.checks)


def aws(*args: str) -> str:
    return subprocess.run(["aws", *args], check=True, text=True, capture_output=True).stdout


def aws_json(*args: str):
    out = aws(*args, "--output", "json")
    return json.loads(out) if out.strip() else None


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def json_keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from json_keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from json_keys(v)


def check_buckets(r: Report, raw_bucket: str, clean_bucket: str) -> None:
    for b in (raw_bucket, clean_bucket):
        pab = aws_json("s3api", "get-public-access-block", "--bucket", b)["PublicAccessBlockConfiguration"]
        r.check("buckets", f"{b} public access block", all(pab.values()), json.dumps(pab))
        status = aws_json("s3api", "get-bucket-policy-status", "--bucket", b)["PolicyStatus"]["IsPublic"]
        r.check("buckets", f"{b} not public by policy", status is False, f"IsPublic={status}")
        enc = aws_json("s3api", "get-bucket-encryption", "--bucket", b)
        algo = enc["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        r.check("buckets", f"{b} default encryption", algo in ("AES256", "aws:kms"), algo)
        ver = (aws_json("s3api", "get-bucket-versioning", "--bucket", b) or {}).get("Status")
        r.check("buckets", f"{b} versioning", ver == "Enabled", str(ver))


def readback(r: Report, clean: Path) -> dict[str, int]:
    """Read every file back. Returns row counts for tables and JSONL."""
    rows: dict[str, int] = {}
    for p in sorted(clean.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(clean))
        try:
            if p.suffix == ".parquet":
                rows[p.stem] = pq.read_table(p).num_rows
                detail = f"{rows[p.stem]} rows"
            elif p.suffix == ".jsonl":
                rows[p.stem] = sum(1 for line in open(p, encoding="utf-8") if json.loads(line) is not None)
                detail = f"{rows[p.stem]} lines"
            elif p.suffix == ".json":
                json.loads(p.read_text())
                detail = "parses"
            elif p.suffix == ".png":
                assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
                detail = "png header"
            else:
                detail = "unchecked type"
            r.check("readback", rel, True, detail)
        except Exception as e:  # noqa: BLE001 - any failure to read back is a validation failure
            r.check("readback", rel, False, f"{type(e).__name__}: {e}")
    return rows


def check_manifest(r: Report, clean: Path) -> None:
    m = json.loads((clean / "curated/manifest.json").read_text())
    bad = [f["path"] for f in m["files"]
           if not (clean / f["path"]).exists() or (clean / f["path"]).stat().st_size != f["bytes"]
           or sha256(clean / f["path"]) != f["sha256"]]
    r.check("readback", "manifest bytes + sha256", not bad, f"{len(m['files'])} files" + (f", bad={bad}" if bad else ""))


def readable_parquet(clean: Path) -> list[Path]:
    """Parquet files that open. Unreadable ones are already reported as FAIL by readback()."""
    out = []
    for p in sorted(clean.rglob("*.parquet")):
        try:
            pq.read_schema(p)
            out.append(p)
        except Exception:  # noqa: BLE001
            pass
    return out


# Keys that look like a banned column but are not one in this file. zones.json labels each map area "Area 1", "Area 2";
# the banned `name` column is for hospital and facility names.
ALLOWED_JSON_KEYS = {"zones.json": frozenset({"name"})}


def check_privacy(r: Report, clean: Path, raw_ids: set[str] | None) -> None:
    hits = []
    for p in readable_parquet(clean):
        hits += [f"{p.name}:{c}" for c in pq.read_schema(p).names if is_banned(c)]
    for p in clean.rglob("*.json"):
        allowed = ALLOWED_JSON_KEYS.get(p.name, frozenset())
        hits += [f"{p.name}:{k}" for k in set(json_keys(json.loads(p.read_text()))) if is_banned(k) and k not in allowed]
    for p in clean.rglob("*.jsonl"):
        keys = set()
        for line in open(p, encoding="utf-8"):
            keys |= set(json.loads(line))
        hits += [f"{p.name}:{k}" for k in keys if is_banned(k)]
    r.check("privacy", "no banned columns or keys", not hits, ", ".join(sorted(hits)) or "0 found")
    r.check("privacy", "no internal/ files", not list(clean.rglob("internal")), "")

    if raw_ids is None:
        r.check("privacy", "no raw premise numbers in values", False, "raw ids unavailable (run make etl first)")
        return
    leaks = Counter()
    for p in readable_parquet(clean):
        t = pq.read_table(p)
        for name in t.column_names:
            for v in set(t.column(name).to_pylist()):
                if v is not None and str(v) in raw_ids:
                    leaks[f"{p.name}:{name}"] += 1
    for p in clean.rglob("*.jsonl"):
        for line in open(p, encoding="utf-8"):
            for k, v in json.loads(line).items():
                if v is not None and str(v) in raw_ids:
                    leaks[f"{p.name}:{k}"] += 1
    r.check("privacy", "no raw premise numbers in values", not leaks,
            f"checked against {len(raw_ids):,} raw ids" + (f"; leaks={dict(leaks)}" if leaks else ""))


def check_groups(r: Report, clean: Path) -> None:
    t = clean / "curated/tables"
    hexes = pq.read_table(t / "hex_context.parquet").column("n_premises").to_pylist()
    small = [n for n in hexes if n < MIN_GROUP]
    r.check("groups", f"hex_context rows >= {MIN_GROUP} premises", not small,
            f"{len(hexes)} cells, min {min(hexes)}" + (f", {len(small)} small" if small else ""))

    pd = pq.read_table(t / "premise_dim.parquet", columns=["hex_cell", "hex_res", "circuit"]).to_pylist()
    groups = Counter((p["hex_cell"], p["hex_res"]) for p in pd if p["hex_cell"] is not None)
    small = {k: v for k, v in groups.items() if v < MIN_GROUP}
    r.check("groups", f"premise_dim hex groups >= {MIN_GROUP}", not small,
            f"{len(groups)} groups, min {min(groups.values())}, {sum(1 for p in pd if p['hex_cell'] is None)} "
            f"premises without a cell" + (f", {len(small)} small" if small else ""))

    circ = pq.read_table(t / "circuit_dim.parquet", columns=["circuit", "n_premises"]).to_pylist()
    small = [c["circuit"] for c in circ if c["n_premises"] < MIN_GROUP]
    r.check("groups", f"circuit_dim rows >= {MIN_GROUP} premises", not small,
            f"{len(circ)} circuits, min {min(c['n_premises'] for c in circ)}" + (f", small={small}" if small else ""))

    dev = pq.read_table(t / "device_dim.parquet", columns=["small_group"]).column("small_group").to_pylist()
    r.info.append(f"device_dim: {sum(dev):,} of {len(dev):,} devices serve fewer than {MIN_GROUP} premises "
                  f"(flagged small_group; device keys are salted and carry no location, so not counted as a "
                  f"published group)")


def check_zones(r: Report, clean: Path) -> None:
    """serving/risk/zones.json: every map area is a published group, ids are unique, each area is rated or has a reason."""
    doc = json.loads((clean / "serving/risk/zones.json").read_text())
    zones = doc["zones"]
    small = [z["id"] for z in zones if z["n"] < MIN_GROUP]
    r.check("groups", f"zones.json areas >= {MIN_GROUP} premises", not small and doc["min_homes"] == MIN_GROUP,
            f"{len(zones)} areas, min {min(z['n'] for z in zones)}" + (f", {len(small)} small" if small else ""))
    r.check("groups", "zones.json ids unique", len({z["id"] for z in zones}) == len(zones), f"{len(zones)} ids")
    both = [z["id"] for z in zones if (z["rep"] is None) == (z["fb"] is None)]
    r.check("groups", "zones.json each area has a typical home or a reason", not both, f"{len(both)} bad")
    reps = [z["rep"] for z in zones if z["rep"]]
    r.check("groups", "zones.json no home represents two areas", len(reps) == len(set(reps)), f"{len(reps)} typical homes")
    hexed = sum(1 for c in pq.read_table(clean / "curated/tables/premise_dim.parquet", columns=["hex_cell"])
                .column("hex_cell").to_pylist() if c is not None)
    covered = sum(z["n"] for z in zones)
    r.check("groups", "zones.json homes do not exceed homes with a cell", covered <= hexed,
            f"{covered:,} in areas, {hexed:,} with a cell")


def check_rows(r: Report, clean_rows: dict, staging_rows: dict, etl: dict, raw_counts: dict | None,
               previous: dict | None) -> None:
    if raw_counts:
        r.check("rows", "raw ARCOS records -> staging damage_fact", raw_counts["arcos"] == staging_rows["damage_fact"],
                f"{raw_counts['arcos']} -> {staging_rows['damage_fact']}")
        r.check("rows", "raw customers UNION topology -> staging premise_dim",
                raw_counts["premise_union"] == staging_rows["premise_dim"],
                f"{raw_counts['premise_union']} -> {staging_rows['premise_dim']} (customers {raw_counts['customers']},"
                f" topology {raw_counts['topology']})")
    else:
        r.check("rows", "raw -> staging", False, "raw files unavailable locally (run make etl first)")
    r.check("rows", "staging premise_dim == premise_risk", staging_rows["premise_dim"] == clean_rows["premise_risk"],
            f"{staging_rows['premise_dim']} vs {clean_rows['premise_risk']}")

    suppressed = {k: v["rows"] for k, v in etl["sanitize"]["suppressed_rows"].items()}
    for t in TABLES:
        exp = staging_rows[t] - suppressed.get(t, 0)
        got = clean_rows.get(t)
        r.check("rows", f"staging -> clean {t}", got == exp,
                f"{staging_rows[t]} -> {got}" + (f" ({suppressed[t]} suppressed by rule)" if t in suppressed else ""))

    if previous:
        for t, prev_n in previous.get("clean_rows", {}).items():
            got = clean_rows.get(t, 0)
            r.check("rows", f"vs previous run {t}", got >= prev_n * (1 - MAX_SHRINK), f"{prev_n} -> {got}")
    else:
        r.info.append("no previous passing validation in _meta/; skipped run-over-run comparison")


def raw_counts(run_dir: Path) -> tuple[dict | None, set[str] | None]:
    raw = run_dir / "raw"
    cust_path, topo_path = raw / "Data1/Customer_Clip.dbf", raw / "matched_upstream/matched_upstream.txt"
    if not cust_path.exists() or not topo_path.exists():
        return None, None
    customers = load_customers(cust_path)
    topology, _ = load_topology(topo_path)
    ids = set(customers) | set(topology)
    counts = {"arcos": len(load_arcos(raw / "Data1/arcos_prod_damage_09_28_Clip.dbf")),
              "customers": len(customers), "topology": len(topology), "premise_union": len(ids)}
    return counts, ids


def write_report(r: Report, meta: dict) -> str:
    lines = [f"# Validation report {meta['date']}", "",
             f"- Result: **{'PASS' if r.passed else 'FAIL'}** "
             f"({sum(ok for _, _, ok, _ in r.checks)}/{len(r.checks)} checks passed)",
             f"- ETL run: `{meta['run_id']}`", f"- Validated at: {meta['validated_at']}",
             f"- Clean bucket: `s3://{meta['clean_bucket']}/`", "",
             "| Section | Check | Result | Detail |", "| --- | --- | --- | --- |"]
    for s, n, ok, d in r.checks:
        lines.append(f"| {s} | {n} | {'PASS' if ok else '**FAIL**'} | {d.replace('|', '/')} |")
    if r.info:
        lines += ["", "## Notes", ""] + [f"- {i}" for i in r.info]
    return "\n".join(lines) + "\n"


def main() -> int:
    raw_bucket, clean_bucket = os.environ.get("RAW_BUCKET"), os.environ.get("CLEAN_BUCKET")
    if not raw_bucket or not clean_bucket:
        raise SystemExit("set RAW_BUCKET and CLEAN_BUCKET")
    state_path = WORK / "latest_run.json"
    if not state_path.exists():
        raise SystemExit("no ETL run found; run `make etl` first")
    state = json.loads(state_path.read_text())
    run_id, run_dir = state["run_id"], Path(state["run_dir"])
    if not state.get("uploaded"):
        raise SystemExit(f"run {run_id} was built with --no-upload; nothing in the clean bucket to validate")

    now = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    vdir = run_dir / "validate"
    shutil.rmtree(vdir, ignore_errors=True)
    clean, staging = vdir / "clean", vdir / "staging"
    print(f"[validate] run {run_id}: downloading s3://{clean_bucket}/ and staging tables", flush=True)
    for sub in ("curated", "serving"):
        aws("s3", "sync", f"s3://{clean_bucket}/{sub}/", str(clean / sub), "--only-show-errors")
    aws("s3", "sync", f"s3://{raw_bucket}/staging/{run_id}/curated/tables/", str(staging), "--only-show-errors")
    etl = json.loads(aws("s3", "cp", f"s3://{clean_bucket}/_meta/etl_run_{run_id}.json", "-"))
    try:
        previous = json.loads(aws("s3", "cp", f"s3://{clean_bucket}/_meta/validation_latest.json", "-"))
    except subprocess.CalledProcessError:
        previous = None
    if previous and previous.get("run_id") == run_id:
        previous = None   # re-validating the same run: nothing to compare against

    r = Report()
    check_buckets(r, raw_bucket, clean_bucket)
    clean_rows = readback(r, clean)
    check_manifest(r, clean)
    staging_rows = {}
    for t in TABLES:
        try:
            staging_rows[t] = pq.read_metadata(staging / f"{t}.parquet").num_rows
        except Exception as e:  # noqa: BLE001
            r.check("readback", f"staging {t}", False, str(e))
            staging_rows[t] = -1
    counts, raw_ids = raw_counts(run_dir)
    # A check that crashes (missing table, wrong schema) is recorded as a FAIL so the report is still written.
    for name, fn in [("privacy", lambda: check_privacy(r, clean, raw_ids)),
                     ("rows", lambda: check_rows(r, clean_rows, staging_rows, etl, counts, previous)),
                     ("groups", lambda: check_groups(r, clean)),
                     ("groups", lambda: check_zones(r, clean))]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            r.check(name, "check ran", False, f"{type(e).__name__}: {e}")
    del raw_ids

    meta = {"date": date, "run_id": run_id, "validated_at": now.isoformat(timespec="seconds"),
            "clean_bucket": clean_bucket}
    md = write_report(r, meta)
    result = {**meta, "passed": r.passed, "clean_rows": {t: clean_rows.get(t) for t in [*TABLES, "premise_risk"]},
              "staging_rows": staging_rows, "raw_counts": counts,
              "checks": [{"section": s, "check": n, "ok": ok, "detail": d} for s, n, ok, d in r.checks],
              "notes": r.info}

    out_md = ROOT / "docs" / f"validation_{date}.md"
    out_md.write_text(md)
    (vdir / f"validation_{date}.md").write_text(md)
    (vdir / f"validation_{run_id}.json").write_text(json.dumps(result, indent=2) + "\n")
    aws("s3", "cp", str(vdir / f"validation_{date}.md"), f"s3://{clean_bucket}/_meta/validation_{date}.md",
        "--content-type", "text/markdown", "--only-show-errors")
    aws("s3", "cp", str(vdir / f"validation_{run_id}.json"), f"s3://{clean_bucket}/_meta/validation_{run_id}.json",
        "--only-show-errors")
    if r.passed:   # only a passing run becomes the baseline for the next run-over-run comparison
        aws("s3", "cp", str(vdir / f"validation_{run_id}.json"), f"s3://{clean_bucket}/_meta/validation_latest.json",
            "--only-show-errors")
    (ROOT / "data/_meta").mkdir(parents=True, exist_ok=True)
    shutil.copy2(vdir / f"validation_{run_id}.json", ROOT / "data/_meta" / f"validation_{run_id}.json")
    shutil.rmtree(staging, ignore_errors=True)

    n_ok = sum(ok for _, _, ok, _ in r.checks)
    print(f"[validate] {'PASS' if r.passed else 'FAIL'}: {n_ok}/{len(r.checks)} checks. "
          f"Report: {out_md.relative_to(ROOT)} and s3://{clean_bucket}/_meta/validation_{date}.md")
    return 0 if r.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
