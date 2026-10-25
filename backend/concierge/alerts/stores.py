"""Where demo enrollments, mock outbox messages and audit entries live.

MemoryStore is for local runs and tests. DynamoStore uses one table (key: token + sk) with a TTL attribute, so demo
data expires on its own. Documents are stored as JSON text in a single attribute, which avoids DynamoDB number and
float rules. Reads also check the expiry time, because DynamoDB TTL deletion can lag by many hours.

Rows (sk): ENROLL | OUT#<time ns>#<id> | AUD#<time ns>#<id>
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

ENROLLMENT_TTL = timedelta(hours=24)
OUTBOX_TTL = timedelta(hours=24)
AUDIT_TTL = timedelta(days=7)
AUDIT_READ_LIMIT = 50
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class AlertStore(Protocol):
    def put_enrollment(self, record: dict) -> None: ...
    def get_enrollment(self, token: str) -> dict | None: ...
    def delete_enrollment(self, token: str) -> bool: ...
    def add_outbox(self, token: str, item: dict) -> None: ...
    def list_outbox(self, token: str, limit: int = 50) -> list[dict]: ...
    def clear_outbox(self, token: str) -> int: ...
    def add_audit(self, token: str, entry: dict) -> None: ...
    def list_audit(self, token: str, limit: int = AUDIT_READ_LIMIT) -> list[dict]: ...


class MemoryStore:
    def __init__(self, clock: Clock = utc_now):
        self.clock = clock
        self._enroll: dict[str, dict] = {}
        self._outbox: dict[str, list[dict]] = defaultdict(list)
        self._audit: dict[str, list[dict]] = defaultdict(list)

    def _live(self, ts: str, ttl: timedelta) -> bool:
        return parse_iso(ts) + ttl > self.clock()

    def put_enrollment(self, record: dict) -> None:
        self._enroll[record["token"]] = json.loads(json.dumps(record))

    def get_enrollment(self, token: str) -> dict | None:
        rec = self._enroll.get(token)
        if rec is None:
            return None
        if parse_iso(rec["expires_at"]) <= self.clock():
            del self._enroll[token]
            return None
        return json.loads(json.dumps(rec))

    def delete_enrollment(self, token: str) -> bool:
        return self._enroll.pop(token, None) is not None

    def add_outbox(self, token: str, item: dict) -> None:
        self._outbox[token].append(json.loads(json.dumps(item)))

    def list_outbox(self, token: str, limit: int = 50) -> list[dict]:
        live = [i for i in self._outbox.get(token, []) if self._live(i["ts"], OUTBOX_TTL)]
        return list(reversed(live))[:limit]

    def clear_outbox(self, token: str) -> int:
        return len(self._outbox.pop(token, []))

    def add_audit(self, token: str, entry: dict) -> None:
        self._audit[token].append(json.loads(json.dumps(entry)))

    def list_audit(self, token: str, limit: int = AUDIT_READ_LIMIT) -> list[dict]:
        live = [e for e in self._audit.get(token, []) if self._live(e["ts"], AUDIT_TTL)]
        return list(reversed(live))[:limit]      # newest first


class DynamoStore:
    """Single-table store. `table` is a boto3 Table resource (or any object with the same four methods)."""

    def __init__(self, table: Any, clock: Clock = utc_now):
        self.table, self.clock = table, clock

    def _stamp(self) -> str:
        """Sort-key time part: nanoseconds, zero padded, so rows sort in the order they were written."""
        return f"{int(self.clock().timestamp() * 1e9):020d}"

    def _put(self, token: str, sk: str, doc: dict, expires: datetime) -> None:
        self.table.put_item(Item={"token": token, "sk": sk, "doc": json.dumps(doc, separators=(",", ":")),
                                  "ttl": int(expires.timestamp())})

    def _query(self, token: str, prefix: str, limit: int, newest_first: bool) -> list[dict]:
        resp = self.table.query(
            KeyConditionExpression="#t = :t AND begins_with(#s, :p)",
            ExpressionAttributeNames={"#t": "token", "#s": "sk"},
            ExpressionAttributeValues={":t": token, ":p": prefix},
            ScanIndexForward=not newest_first, Limit=limit)
        return resp.get("Items", [])

    def put_enrollment(self, record: dict) -> None:
        self._put(record["token"], "ENROLL", record, parse_iso(record["expires_at"]))

    def get_enrollment(self, token: str) -> dict | None:
        item = self.table.get_item(Key={"token": token, "sk": "ENROLL"}).get("Item")
        if not item:
            return None
        rec = json.loads(item["doc"])
        if parse_iso(rec["expires_at"]) <= self.clock():
            return None
        return rec

    def delete_enrollment(self, token: str) -> bool:
        existed = self.get_enrollment(token) is not None
        self.table.delete_item(Key={"token": token, "sk": "ENROLL"})
        return existed

    def add_outbox(self, token: str, item: dict) -> None:
        self._put(token, f"OUT#{self._stamp()}#{item['id']}", item, parse_iso(item["ts"]) + OUTBOX_TTL)

    def list_outbox(self, token: str, limit: int = 50) -> list[dict]:
        return [json.loads(i["doc"]) for i in self._query(token, "OUT#", limit, True)]

    def clear_outbox(self, token: str) -> int:
        items = self._query(token, "OUT#", 100, False)
        for i in items:
            self.table.delete_item(Key={"token": token, "sk": i["sk"]})
        return len(items)

    def add_audit(self, token: str, entry: dict) -> None:
        self._put(token, f"AUD#{self._stamp()}#{entry['decision_id']}", entry, parse_iso(entry["ts"]) + AUDIT_TTL)

    def list_audit(self, token: str, limit: int = AUDIT_READ_LIMIT) -> list[dict]:
        return [json.loads(i["doc"]) for i in self._query(token, "AUD#", limit, True)]
