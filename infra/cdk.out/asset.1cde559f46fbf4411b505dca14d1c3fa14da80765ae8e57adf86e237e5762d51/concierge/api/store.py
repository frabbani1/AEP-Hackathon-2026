"""Where the risk engine gets its data at runtime.

Cloud: premise rows come from DynamoDB (one GetItem per token, cached per warm container) and the small
calibration.json comes from S3. This avoids loading the 18 MB premise file on every cold start (docs/aws-setup.md step 9).
Local: set RISK_ARTIFACTS_DIR to a directory built by scripts/build_risk_artifacts.py.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from decimal import Decimal

from concierge.risk import RiskEngine
from concierge.risk.tokens import TOKEN_RE

CALIBRATION_KEY = "serving/risk/calibration.json"

# DynamoDB returns every number as Decimal. The engine needs the types build.py wrote.
_INT_FIELDS = ("P", "H", "n")
_FLOAT_FIELDS = ("R", "Re")


def row_from_item(item: Mapping) -> dict:
    """Convert a deserialized DynamoDB item back into the row dict RiskEngine expects (the key is dropped)."""
    row = {k: v for k, v in item.items() if k != "token"}
    for k in _INT_FIELDS:
        if isinstance(row.get(k), Decimal):
            row[k] = int(row[k])
    for k in _FLOAT_FIELDS:
        if isinstance(row.get(k), Decimal):
            row[k] = float(row[k])
    row.setdefault("why", None)  # load_dynamodb.py drops None values
    return row


class DynamoRowStore(Mapping):
    """Read-only, lazy view of the premise table. The engine only ever calls .get(token) with a validated token."""

    def __init__(self, client, table_name: str, cache_size: int = 2048):
        from boto3.dynamodb.types import TypeDeserializer  # lazy: tests and local runs need no boto3

        self._client = client
        self._table = table_name
        self._deserialize = TypeDeserializer().deserialize
        self._cache: OrderedDict[str, dict | None] = OrderedDict()
        self._cache_size = cache_size

    def __getitem__(self, token: str) -> dict:
        row = self._lookup(token)
        if row is None:
            raise KeyError(token)
        return row

    def _lookup(self, token: str) -> dict | None:
        # fullmatch, not match: TOKEN_RE ends in "$", which also accepts a trailing newline.
        if not TOKEN_RE.fullmatch(token):
            return None
        if token in self._cache:
            self._cache.move_to_end(token)
            return self._cache[token]
        resp = self._client.get_item(TableName=self._table, Key={"token": {"S": token}})
        item = resp.get("Item")
        row = row_from_item({k: self._deserialize(v) for k, v in item.items()}) if item else None
        self._cache[token] = row  # unknown tokens are cached too, so repeated bad input costs one read
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return row

    def __iter__(self) -> Iterator[str]:
        raise NotImplementedError("scanning the premise table is not supported")

    def __len__(self) -> int:
        raise NotImplementedError("scanning the premise table is not supported")


def engine_from_env() -> RiskEngine:
    """Build the engine from environment variables. Raises KeyError naming the missing variable."""
    local = os.environ.get("RISK_ARTIFACTS_DIR")
    if local:
        return RiskEngine.from_artifacts(local)

    import boto3

    table = os.environ["TABLE_NAME"]
    bucket = os.environ["DATA_BUCKET"]
    key = os.environ.get("CALIBRATION_KEY", CALIBRATION_KEY)
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return RiskEngine(DynamoRowStore(boto3.client("dynamodb"), table), json.loads(body))
