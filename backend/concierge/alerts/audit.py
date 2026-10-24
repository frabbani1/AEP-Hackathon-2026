"""Audit log: one entry per alert decision, so a judge can see the inputs, the rules that fired, and the outputs.

Entries never hold contact values, message text, raw premise numbers, addresses, or circuit or device identifiers.
Declared needs appear only as two booleans (any need, medical). They are self-declared medical information, so entries
expire after 7 days and are returned only by `GET /alerts/audit`.
"""
from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping, Sequence

from .stores import AlertStore, Clock, iso, utc_now

log = logging.getLogger("concierge.alerts.audit")
_LETTERS = "abcdefghjkmnpqrstuvwxyz"   # letters only, so an id can never look like a long digit run to the identifier scan


def new_id(prefix: str) -> str:
    return f"{prefix}_" + "".join(secrets.choice(_LETTERS) for _ in range(12))


def decision_outputs(response: Mapping) -> dict:
    return {
        "tier": response["tier"],
        "channels": list(response["recommended_channels"]),
        "eligible": {k: bool(v["eligible"]) for k, v in response["eligibility"].items()},
        "escalation": [e["rule"] for e in response["escalation"]],
    }


def build_entry(*, decision_id: str, route: str, token: str, engine_version: str, table_hash: str,
                inputs: Mapping, rules_fired: Sequence[str], outputs: Mapping, clock: Clock = utc_now) -> dict:
    return {
        "decision_id": decision_id, "ts": iso(clock()), "route": route, "token": token,
        "engine_version": engine_version, "rules_table_hash": table_hash,
        "inputs": dict(inputs), "rules_fired": list(rules_fired), "outputs": dict(outputs),
    }


def record(store: AlertStore, entry: dict) -> dict:
    store.add_audit(entry["token"], entry)
    log.info("alert_decision %s", json.dumps(entry, separators=(",", ":")))
    return entry


def read(store: AlertStore, token: str, limit: int = 50) -> list[dict]:
    return store.list_audit(token, min(limit, 50))
