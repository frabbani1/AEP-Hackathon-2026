#!/usr/bin/env python3
"""Load artifacts/risk/premise_risk.jsonl into a DynamoDB table (key: token).

Dry run by default: reads the file, converts every row, prints what WOULD be written, and never imports boto3 or
contacts AWS. Pass --apply to write.

    .venv/bin/python scripts/load_dynamodb.py --table aep-premise-risk
    AWS_PROFILE=aep-hack .venv/bin/python scripts/load_dynamodb.py --table aep-premise-risk --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def to_item(row: dict) -> dict:
    """token becomes the partition key; None values are dropped; floats become Decimal (DynamoDB has no float)."""
    item = {"token": row["t"]}
    for k, v in row.items():
        if k == "t" or v is None:
            continue
        item[k] = Decimal(repr(v)) if isinstance(v, float) else v
    return item


def read_items(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield to_item(json.loads(line))


def write_items(table, items: Iterable[dict]) -> int:
    """batch_writer groups writes into batches of 25 and retries unprocessed items."""
    n = 0
    with table.batch_writer(overwrite_by_pkeys=["token"]) as batch:
        for item in items:
            batch.put_item(Item=item)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", required=True)
    ap.add_argument("--file", type=Path, default=ROOT / "artifacts" / "risk" / "premise_risk.jsonl")
    ap.add_argument("--region", default=None)
    ap.add_argument("--apply", action="store_true", help="actually write to DynamoDB")
    args = ap.parse_args()

    if not args.apply:
        n, sample = 0, None
        for item in read_items(args.file):
            n, sample = n + 1, sample or item
        print(f"DRY RUN: would write {n:,} items to table '{args.table}' (no AWS calls made)")
        print("sample item:", json.dumps(sample, default=str))
        print("re-run with --apply to write")
        return 0

    import boto3  # imported lazily so a dry run needs no AWS libraries or credentials

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    n = write_items(table, read_items(args.file))
    print(f"wrote {n:,} items to '{args.table}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
