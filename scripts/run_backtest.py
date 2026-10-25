#!/usr/bin/env python3
"""Run the Sep 2024 -> Feb 2025 backtest (openspec/changes/spec-04-backtest).

    <py> scripts/run_backtest.py [--seed 2026] [--cutoff-days 3] [--sample]

Reads artifacts/curated/tables/*.parquet and artifacts/risk/calibration.json (build them first).
Writes artifacts/backtest/backtest.json, three SVG plots, and docs/backtest.md.
`--sample` uses fewer shuffles and resamples and writes under artifacts/backtest/sample/ only (never docs/).
Nothing is uploaded: review the report with the team first, then copy backtest.json and the plots to the clean
bucket's serving/backtest/ prefix (see docs/backtest.md and openspec/changes/spec-04-backtest/tasks.md 6.3).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.backtest import adapter, report, split  # noqa: E402
from concierge.backtest.config import DEFAULT_CONFIG  # noqa: E402


def _git_hash() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_CONFIG.seed)
    ap.add_argument("--cutoff-days", type=int, default=DEFAULT_CONFIG.backlog_primary,
                    help="repairs taking more than this many days are backlog (primary run; 7 is always the sensitivity run)")
    ap.add_argument("--sample", action="store_true", help="quick run with fewer shuffles; writes only under <out>/sample")
    ap.add_argument("--tables", type=Path, default=ROOT / "artifacts" / "curated" / "tables")
    ap.add_argument("--calibration", type=Path, default=ROOT / "artifacts" / "risk" / "calibration.json")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "backtest")
    ap.add_argument("--docs", type=Path, default=ROOT / "docs" / "backtest.md")
    args = ap.parse_args()

    cfg = replace(DEFAULT_CONFIG, seed=args.seed, backlog_primary=args.cutoff_days)
    out, docs = args.out, args.docs
    if args.sample:
        cfg = cfg.scaled()
        out, docs = args.out / "sample", args.out / "sample" / "backtest.md"

    calibration = json.loads(args.calibration.read_text())
    data = split.load_split(args.tables)
    engine = adapter.load_engine(args.tables)
    if engine is None:
        print("note: concierge.restoration.circuit not found (spec-03 has not shipped); restoration model runs are skipped",
              file=sys.stderr)

    inputs = {p.name: report.file_sha256(p) for p in (
        args.tables / "damage_fact.parquet", args.tables / "circuit_dim.parquet", args.tables / "device_dim.parquet",
        args.calibration)}
    rep = report.build_report(
        data, cfg, engine=engine, stored_backtest=calibration["backtest"],
        spec02_config_hash=calibration.get("config_hash"), input_hashes=inputs, git_hash=_git_hash())
    paths = report.write_outputs(rep, out, docs)

    print("spec-02 tables reproduced:", rep["spec02_check"])
    for v in rep["verdicts"]:
        print(f"  [{v['level']}] {v['code']}: {v['sentence']}")
    for name, p in paths.items():
        print("wrote", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
