"""Demo-only sign-up. Only fictional contact details are accepted, nothing is ever sent, and records expire.

Phones must be in the 555-0100 to 555-0199 range (reserved for fiction). Emails must be at example.com, example.org,
example.net, or a .test / .invalid domain (reserved by RFC 2606 and RFC 6761). A real number or mailbox cannot get in.
Contact values are never logged and never returned in full.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..guidance.prepare import clean_needs
from .stores import ENROLLMENT_TTL, AlertStore, Clock, iso, utc_now

CHANNEL_TYPES = ("sms", "voice", "push", "email")
MESSAGE_TYPES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
MAX_VALUE_LENGTH = 64
DEMO_ONLY_MESSAGE = "Demo only: use a fictional number like 555-0123 or an email at example.com."
DEMO_NOTICE = "This is a demo. Nothing is sent, and only fictional contact details are accepted."

_PHONE_CHARS = re.compile(r"^\+?[\d\s().-]{7,20}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,40}@(?:[A-Za-z0-9-]{1,30}\.)+[A-Za-z]{2,12}$")
_RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_EMAIL_SUFFIXES = (".test", ".invalid")


class EnrollError(ValueError):
    """The sign-up request is not acceptable. The message is safe to return."""


def _phone(value: str) -> str:
    if not _PHONE_CHARS.match(value):
        raise EnrollError(DEMO_ONLY_MESSAGE)
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        if digits[0] in "01":
            raise EnrollError(DEMO_ONLY_MESSAGE)
        rest = digits[3:]
    elif len(digits) == 7:
        rest = digits
    else:
        raise EnrollError(DEMO_ONLY_MESSAGE)
    exchange, line = rest[:3], rest[3:]
    if exchange != "555" or not ("0100" <= line <= "0199"):
        raise EnrollError(DEMO_ONLY_MESSAGE)
    return digits


def _email(value: str) -> str:
    if not _EMAIL.match(value):
        raise EnrollError(DEMO_ONLY_MESSAGE)
    domain = value.rsplit("@", 1)[1].lower()
    ok = (any(domain == d or domain.endswith("." + d) for d in _RESERVED_EMAIL_DOMAINS)
          or domain.endswith(_RESERVED_EMAIL_SUFFIXES))
    if not ok:
        raise EnrollError(DEMO_ONLY_MESSAGE)
    return value


def validate_channels(items: Any) -> list[dict]:
    """Return normalized [{type, value}]. Raises EnrollError with a safe message."""
    if not isinstance(items, list) or not (1 <= len(items) <= len(CHANNEL_TYPES)):
        raise EnrollError(f"channels must be a list of 1 to {len(CHANNEL_TYPES)} items")
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"type", "value"}:
            raise EnrollError("each channel needs exactly type and value")
        ctype, value = item["type"], item["value"]
        if ctype not in CHANNEL_TYPES:
            raise EnrollError(f"channel type must be one of {list(CHANNEL_TYPES)}")
        if ctype in seen:
            raise EnrollError("use each channel type only once")
        seen.add(ctype)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE_LENGTH:
            raise EnrollError(DEMO_ONLY_MESSAGE)
        value = value.strip()
        if ctype in ("sms", "voice"):
            value = _phone(value)
        elif ctype == "email":
            value = _email(value)
        elif value != "app":
            raise EnrollError("push takes the value app (no device details are collected)")
        out.append({"type": ctype, "value": value})
    return out


def mask(ctype: str, value: str) -> str:
    if ctype in ("sms", "voice"):
        return "***-" + value[-4:]
    if ctype == "email":
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return "app"


def validate_message_types(items: Any) -> list[str]:
    if items is None:
        return list(MESSAGE_TYPES)
    if not isinstance(items, list) or not items or not all(isinstance(m, str) for m in items):
        raise EnrollError("message_types must be a list of message type names")
    bad = [m for m in items if m not in MESSAGE_TYPES]
    if bad:
        raise EnrollError(f"message_types must be from {list(MESSAGE_TYPES)}")
    return [m for m in MESSAGE_TYPES if m in items]


def enroll(store: AlertStore, token: str, channels: Any, needs: Mapping | None, message_types: Any,
           clock: Clock = utc_now) -> dict:
    """Validate and upsert one enrollment. Returns the stored record (full values; use `public_view` to return it)."""
    record = {
        "token": token,
        "channels": validate_channels(channels),
        "needs": None if needs is None else clean_needs(needs),
        "message_types": validate_message_types(message_types),
    }
    now = clock()
    record["created_at"] = iso(now)
    record["expires_at"] = iso(now + ENROLLMENT_TTL)
    store.put_enrollment(record)
    return record


def public_view(record: Mapping) -> dict:
    """What may be returned to the caller: contact values masked, no token echo."""
    return {
        "channels": [{"type": c["type"], "contact": mask(c["type"], c["value"])} for c in record["channels"]],
        "message_types": list(record["message_types"]),
        "declared_needs": None if record.get("needs") is None else [k for k, v in record["needs"].items() if v],
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "demo_notice": DEMO_NOTICE,
    }


def channel_types(record: Mapping | None) -> list[str] | None:
    return None if record is None else [c["type"] for c in record["channels"]]


def opted_in_pre_storm(record: Mapping | None) -> bool:
    return bool(record) and "pre_storm" in record["message_types"]


def unenroll(store: AlertStore, token: str) -> dict:
    existed = store.delete_enrollment(token)
    cleared = store.clear_outbox(token)
    return {"unenrolled": existed, "outbox_cleared": cleared}

