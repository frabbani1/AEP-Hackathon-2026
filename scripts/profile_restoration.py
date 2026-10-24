#!/usr/bin/env python3
"""Time restoration-v2 on the real artifacts (task 5.5 / 7.2 of spec-03).

    <py> scripts/profile_restoration.py [--calls 300] [--severity beyond-observed]

Needs artifacts/risk (premise_risk.jsonl, calibration.json, restoration_tree.json) and artifacts/curated/labor_standards.json
(run scripts/curate.py and scripts/build_risk_artifacts.py first). Prints the p50/p95/max latency of one estimate() call,
including its sensitivity runs, for the heaviest premises and for a random sample, plus the trial counts used.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.restoration import model  # noqa: E402
from concierge.restoration.tree import RestorationTree  # noqa: E402
from concierge.risk.engine import RiskEngine  # noqa: E402


def timed(rows, tree, labor, engine, token, severity, **scenario):
    sev = engine.resolve_severity(severity)
    sev_out = {"preset": sev["preset"], "multiplier": round(sev["multiplier"], 2), "extrapolated": sev["extrapolated"]}
    row = rows[token]
    t0 = time.perf_counter()
    out = model.estimate(token=token, row=row, severity=sev_out, fallback=None, base_confidence=row["c"], labor=labor,
                         tree=tree, engine="v2", **scenario)
    return (time.perf_counter() - t0) * 1000.0, out


def pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def pick_band_premises(engine, rows, tree) -> dict[str, str]:
    """One scored premise per risk band: the one with the median expected damage among Medium-confidence rows
    (or all scored rows if the band has none), so the table shows a typical line rather than an extreme one."""
    by_band: dict[str, list[tuple[float, str]]] = {"Low": [], "Medium": [], "High": []}
    for tok in sorted(rows):
        r = rows[tok]
        if r["st"] != "ok" or tree.locate(r) is None:
            continue
        by_band[engine.score(tok, "typical").band].append((r["R"] * r["cn"] / 1000.0, tok))
    out = {}
    for band, items in by_band.items():
        med = [it for it in items if rows[it[1]]["c"] == "Medium"] or items
        med.sort()
        out[band] = med[len(med) // 2][1]
    return out


def print_cases(engine, rows, args) -> int:
    tree = RestorationTree.from_dict(json.loads((args.risk_dir / "restoration_tree.json").read_text()))
    labor = json.loads(args.labor.read_text())
    picks = pick_band_premises(engine, rows, tree)
    scenarios = [("base: 3 crews, normal access, simulated", {}),
                 ("6 crews", {"crews": 6}), ("restricted access", {"access": "restricted"}),
                 ("remote access", {"access": "remote"}),
                 ("damage_count 5", {"damage_count": 5}), ("damage_count 40", {"damage_count": 40})]
    print("| band | confidence | expected jobs | scenario | P50 | P90 | trials | what-ifs (P50) |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for band, tok in picks.items():
        row = rows[tok]
        for label, kw in scenarios:
            ms, out = timed(rows, tree, labor, engine, tok, args.severity if args.severity != "beyond-observed" else "typical", **kw)
            e = out["estimate"]
            what = "; ".join(f"{x['change']} {x['p50_hours']}" for x in out["sensitivity"])
            print(f"| {band} | {row['c']} | {out['scenario']['damage_count']} | {label} | {e['p50_hours']} | "
                  f"{e['p90_hours']} | {out['simulation']['trials']} | {what} |")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--risk-dir", type=Path, default=ROOT / "artifacts" / "risk")
    ap.add_argument("--labor", type=Path, default=ROOT / "artifacts" / "curated" / "labor_standards.json")
    ap.add_argument("--severity", default="beyond-observed")
    ap.add_argument("--calls", type=int, default=300)
    ap.add_argument("--cases", action="store_true",
                    help="print the task 7.1 table instead: one premise per risk band, scenario changes, P50 to P90")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    engine = RiskEngine.from_artifacts(args.risk_dir)
    rows = engine._rows
    if args.cases:
        return print_cases(engine, rows, args)
    tree = RestorationTree.from_dict(json.loads((args.risk_dir / "restoration_tree.json").read_text()))
    labor = json.loads(args.labor.read_text())
    mult = engine.resolve_severity(args.severity)["multiplier"]
    ok = [(t, r) for t, r in rows.items() if r["st"] == "ok" and "pn" in r]
    expected = {t: r["R"] * r["cn"] / 1000.0 * mult for t, r in ok}
    heavy = sorted(expected, key=expected.get, reverse=True)[:20]
    biggest = max((t for t, _ in ok), key=lambda t: rows[t]["cn"])
    print(f"{len(ok)} scored premises with a tree position; severity {args.severity} (x{mult:g})")
    print(f"largest circuit: {rows[biggest]['cn']} homes; heaviest expected damage: {max(expected.values()):.0f} jobs; "
          f"median {statistics.median(expected.values()):.1f}")

    def report(title, tokens, **scenario):
        times, trials, jobs = [], [], []
        for tok in tokens:
            ms, out = timed(rows, tree, labor, engine, tok, args.severity, **scenario)
            times.append(ms)
            trials.append(out["simulation"]["trials"])
            jobs.append(out["scenario"]["damage_count"])
        print(f"{title:44s} n={len(times):4d}  p50 {pct(times, .5):6.1f} ms  p95 {pct(times, .95):6.1f} ms  "
              f"max {max(times):6.1f} ms  trials {min(trials)}-{max(trials)}  jobs up to {max(jobs)}")
        return times

    rnd = random.Random(args.seed)
    sample = [t for t, _ in rnd.sample(ok, min(args.calls, len(ok)))]
    report("heaviest 20 premises, simulated", heavy)
    report("heaviest 20, 1 crew", heavy, crews=1)
    report("heaviest 20, 100 crews", heavy, crews=100)
    report("largest circuit premise, simulated", [biggest] * 20)
    report("damage_count=400 on the largest circuit", [biggest] * 20, damage_count=400)
    report("damage_count=200 on the largest circuit", [biggest] * 20, damage_count=200)
    report("damage list of 10 items x 20", [biggest] * 20,
           damage=[("primary_line", 20), ("pole", 20), ("fuse_bank", 20), ("transformer", 20)])
    allt = report("random premises, simulated", sample)
    report("random premises, damage_count=40", sample[:100], damage_count=40)
    print(f"overall p95 (random premises): {pct(allt, .95):.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
