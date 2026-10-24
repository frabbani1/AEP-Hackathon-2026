#!/usr/bin/env python3
"""Build risk artifacts from the source data.

    CONCIERGE_TOKEN_SALT=<secret> .venv/bin/python scripts/build_risk_artifacts.py [--sample]

Writes artifacts/risk/premise_risk.jsonl and artifacts/risk/calibration.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.risk.build import build_artifacts  # noqa: E402
from concierge.risk.engine import RiskEngine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "risk")
    ap.add_argument("--allow-non-monotone", action="store_true",
                    help="record the override and continue if band hit rates are not ordered")
    ap.add_argument("--sample", action="store_true",
                    help="print one token per band and a severity switch, for the demo check")
    args = ap.parse_args()

    cal = build_artifacts(
        customer_dbf=args.data_dir / "Data1" / "Customer_Clip.dbf",
        arcos_dbf=args.data_dir / "Data1" / "arcos_prod_damage_09_28_Clip.dbf",
        topology_txt=args.data_dir / "matched_upstream" / "matched_upstream.txt",
        out_dir=args.out,
        allow_non_monotone=args.allow_non_monotone,
    )
    print(json.dumps({k: cal[k] for k in ("storms", "presets", "thresholds", "counts")}, indent=2))
    print("band evidence (Sep -> Feb backtest):")
    for band, ev in cal["band_evidence"].items():
        print(f"  {band}: {ev['sentence'] if ev else 'no circuits'}")
    print("gate:", cal["backtest"]["gate"], " config_hash:", cal["config_hash"][:19])

    if args.sample:
        engine = RiskEngine.from_artifacts(args.out)
        seen: dict[str, str] = {}
        for tok, row in engine._rows.items():  # demo helper only
            if row["st"] != "ok":
                continue
            band = engine.score(tok, "typical").band
            if band not in seen and row["c"] != "Low":
                seen[band] = tok
            if len(seen) == 3:
                break
        for band, tok in seen.items():
            a, b = engine.score(tok, "typical"), engine.score(tok, "feb-2025-like")
            print(f"{tok}: typical={a.band}/{a.score}  feb-2025-like={b.band}/{b.score}  "
                  f"factors={[f['id'] for f in b.top_factors]}")
        print("unknown token ->", engine.score("p_0000000000000000").fallback["reason_code"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
