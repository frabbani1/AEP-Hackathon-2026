"""Where the API gets its data.

Cloud: premise rows from DynamoDB (one GetItem per request), calibration.json and labor_standards.json from the clean
bucket's serving/ and curated/ prefixes. Local: set PREMISE_FILE, CALIBRATION_FILE and LABOR_FILE to read the files in
data/ instead, so the whole API can be exercised without AWS. The restoration tree (serving/risk/restoration_tree.json,
TREE_FILE locally) is only read when the v2 restoration engine is on. The map zones (serving/risk/zones.json,
ZONES_FILE locally) are always read; the file holds premise tokens and is never served to a browser.
"""
from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

CACHE_SIZE = 4096


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class DynamoRows:
    """Read-only mapping of token -> premise row, backed by DynamoDB with a small in-memory cache."""

    def __init__(self, table):
        self._table = table
        self._cache: OrderedDict[str, dict | None] = OrderedDict()

    def get(self, token, default=None):
        if not isinstance(token, str):
            return default
        if token in self._cache:
            self._cache.move_to_end(token)
            row = self._cache[token]
            return default if row is None else row
        item = self._table.get_item(Key={"token": token}).get("Item")
        row = None if item is None else {k: _plain(v) for k, v in item.items() if k != "token"}
        self._cache[token] = row
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return default if row is None else row


class FileRows(dict):
    """Premise rows loaded from premise_risk.jsonl (local testing)."""

    @classmethod
    def load(cls, path: str | Path) -> "FileRows":
        rows = cls()
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                rows[r.pop("t")] = r
        return rows


def load_rows():
    if os.environ.get("PREMISE_FILE"):
        return FileRows.load(os.environ["PREMISE_FILE"])
    import boto3
    return DynamoRows(boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]))


def _s3_json(key: str) -> dict:
    import boto3
    body = boto3.client("s3").get_object(Bucket=os.environ["CLEAN_BUCKET"], Key=key)["Body"].read()
    return json.loads(body)


def load_calibration() -> dict:
    if os.environ.get("CALIBRATION_FILE"):
        return json.loads(Path(os.environ["CALIBRATION_FILE"]).read_text())
    return _s3_json("serving/risk/calibration.json")


def load_evidence() -> dict | None:
    """The published backtest report (serving/backtest/backtest.json), or None while none has been uploaded.

    Local testing: set EVIDENCE_FILE. A missing file or object is not an error, because the report is published
    only after the team has reviewed it.
    """
    if os.environ.get("EVIDENCE_FILE"):
        p = Path(os.environ["EVIDENCE_FILE"])
        return json.loads(p.read_text()) if p.exists() else None
    from botocore.exceptions import ClientError
    try:
        return _s3_json("serving/backtest/backtest.json")
    except ClientError as e:
        # S3 answers AccessDenied instead of NoSuchKey when the caller cannot list the prefix.
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "AccessDenied"):
            return None
        raise


def load_zones():
    """The outage map's zones, or None when zones.json has not been published (the map routes then answer 503).
    A ZONES_FILE that does not exist is an error, not a silent fallback."""
    from ..zones.index import ZoneIndex
    if os.environ.get("ZONES_FILE"):
        return ZoneIndex.from_dict(json.loads(Path(os.environ["ZONES_FILE"]).read_text()))
    from botocore.exceptions import ClientError
    try:
        doc = _s3_json("serving/risk/zones.json")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "AccessDenied"):
            raise
        logging.getLogger("concierge.api").warning("zones.json not found; the map routes are unavailable")
        return None
    return ZoneIndex.from_dict(doc)


def load_labor() -> dict:
    if os.environ.get("LABOR_FILE"):
        return json.loads(Path(os.environ["LABOR_FILE"]).read_text())
    return _s3_json("curated/labor_standards.json")


def load_tree():
    """The restoration tree, or None when the artifact has not been published (premises then use the three-level
    fallback path and the response says so). A TREE_FILE that does not exist is an error, not a silent fallback."""
    from ..restoration.tree import RestorationTree
    if os.environ.get("TREE_FILE"):
        return RestorationTree.from_dict(json.loads(Path(os.environ["TREE_FILE"]).read_text()))
    from botocore.exceptions import ClientError
    try:
        doc = _s3_json("serving/risk/restoration_tree.json")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchKey":
            raise
        logging.getLogger("concierge.api").warning("restoration_tree.json not found; using the v1 level path")
        return None
    return RestorationTree.from_dict(doc)
