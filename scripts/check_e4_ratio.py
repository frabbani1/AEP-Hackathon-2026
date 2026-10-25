#!/usr/bin/env python3
"""Check the alert escalation rule E4 (P90 / P50 above a ratio) against real restoration-v2 ranges
(openspec/changes/spec-05-alerts-and-notifications, task 6.3).

    <py> scripts/check_e4_ratio.py --risk-dir <dir with premise_risk.jsonl, calibration.json, restoration_tree.json> \
         --labor data/curated/labor_standards.json [--sample 3000] [--seed 2026]

The risk dir must hold rows that carry `pc`/`pn` (see scripts/build_risk_artifacts.py or
`concierge.curate.restoration_tree.enrich_risk_rows`). Reads only; prints aggregate counts, never a token.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.alerts.table import load_table  # noqa: E402
from concierge.api.service import Concierge  # noqa: E402
from concierge.restoration.tree import RestorationTree  # noqa: E402
from concierge.risk.engine import RiskEngine  # noqa: E402

SEVERITIES = ("sep-2024-like", "typical", "feb-2025-like", "beyond-observed")


def pct(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--risk-dir", type=Path, required=True)
    ap.add_argument("--labor", type=Path, default=ROOT / "data" / "curated" / "labor_standards.json")
    ap.add_argument("--sample", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    engine = RiskEngine.from_artifacts(args.risk_dir)
    tree = RestorationTree.from_dict(json.loads((args.risk_dir / "restoration_tree.json").read_text()))
    calibration = json.loads((args.risk_dir / "calibration.json").read_text())
    svc = Concierge(engine._rows, calibration, json.loads(args.labor.read_text()), tree=tree, restoration_engine="v2")
    e4 = load_table().data["escalation"]["E4"]
    threshold, floor = e4["min_p90_over_p50_ratio"], e4["min_p90_minus_p50_hours"]

    scored = sorted(t for t, r in engine._rows.items() if r["st"] == "ok" and "pn" in r)
    picks = random.Random(args.seed).sample(scored, min(args.sample, len(scored)))
    print(f"E4 rule in the rules table: P90/P50 > {threshold:g} and P90 - P50 >= {floor:g} h; "
          f"{len(picks)} random scored premises, 3 crews\n")

    ratios: dict[str, list[float]] = defaultdict(list)
    gaps: dict[str, list[float]] = defaultdict(list)
    fires: dict[str, int] = defaultdict(int)
    by_band: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    small_p50 = defaultdict(lambda: [0, 0])
    for sev in SEVERITIES:
        for tok in picks:
            est = svc.handle("restoration", {"token": tok, "severity": sev})["estimate"]
            p50, p90 = est["p50_hours"], est["p90_hours"]
            r = p90 / p50
            ratios[sev].append(r)
            gaps[sev].append(p90 - p50)
            band = engine.score(tok, sev).band
            by_band[(sev, band)][1] += 1
            bucket = "p50 < 4 h" if p50 < 4 else "p50 4 to 23 h" if p50 < 24 else "p50 24 h or more"
            small_p50[(sev, bucket)][1] += 1
            if r > threshold and p90 - p50 >= floor:
                fires[sev] += 1
                by_band[(sev, band)][0] += 1
                small_p50[(sev, bucket)][0] += 1

    print(f"{'severity':<18}{'n':>6}{'median':>8}{'p75':>7}{'p90':>7}{'p95':>7}{'max':>7}{'E4 fires':>10}")
    for sev in SEVERITIES:
        s = sorted(ratios[sev])
        print(f"{sev:<18}{len(s):>6}{pct(s, .5):>8.2f}{pct(s, .75):>7.2f}{pct(s, .9):>7.2f}{pct(s, .95):>7.2f}{s[-1]:>7.2f}"
              f"{fires[sev] / len(s):>10.1%}")
    print("\nE4 fires by risk band and by size of the P50 (share of premises in the group)")
    for key, (hit, n) in sorted(by_band.items()):
        print(f"  {key[0]:<16}{key[1]:<8}{hit / n:>7.1%}  of {n}")
    for key, (hit, n) in sorted(small_p50.items()):
        print(f"  {key[0]:<16}{key[1]:<18}{hit / n:>7.1%}  of {n}")
    typ = list(zip(ratios["typical"], gaps["typical"]))
    print("\nRatio alone (typical storm): " + ", ".join(
        f"{t:g} -> {sum(1 for r, _ in typ if r > t) / len(typ):.0%}" for t in (2, 2.5, 3, 4, 5, 6)))
    print("Ratio 4 plus a gap floor (typical storm): " + ", ".join(
        f"{g:g} h -> {sum(1 for r, d in typ if r > 4 and d >= g) / len(typ):.0%}" for g in (0, 12, 24, 36, 48)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
