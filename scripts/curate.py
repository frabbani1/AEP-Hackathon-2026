#!/usr/bin/env python3
"""Curate the raw data into clean, privacy-safe tables.

    CONCIERGE_TOKEN_SALT=<secret> .venv/bin/python scripts/curate.py

Output (artifacts/curated/):
    tables/*.parquet        publishable tables
    labor_standards.json    labor hours, adders, indirect multiplier
    drawings/page-N.png     structure drawings, one PNG per page
    manifest.json           file list with sha256 (publishable)
    internal/               crosswalk + quality report (NOT published)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from concierge.curate.pipeline import curate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "curated")
    ap.add_argument("--no-drawings", action="store_true", help="skip rendering the PDF pages")
    args = ap.parse_args()
    res = curate(data_dir=args.data_dir, docs_dir=args.docs_dir, out_dir=args.out, drawings=not args.no_drawings)
    print(json.dumps(res["quality"], indent=2, sort_keys=True, default=str))
    print(f"\n{len(res['manifest']['files'])} files written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
