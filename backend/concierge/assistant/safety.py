"""Hard-coded safety text (design D6). The model never sees it and never writes it; code adds it after the guard.

`assert_present` is the last step before a response leaves: a missing required string is a bug, so it raises.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable

DOWNED_LINES = "Stay far away from downed power lines. Assume they are live, keep others away, and call 911."
# Messages use a shorter sentence because push is limited to 200 characters (same text as messages/templates.SAFETY_LINE).
DOWNED_LINES_SHORT = "If you see a downed power line, stay far away and call 911."
GENERATOR_CO = "Never run a generator, grill or camp stove inside your home or garage. Carbon monoxide can kill."
EMERGENCY = "In an emergency, call 911. Utility alerts and this tool do not replace emergency services."
REPORT_OUTAGE_GENERIC = "Report your outage through your utility's official outage line or app."

# id -> text. `report_outage` is built by `report_outage_text()` because a deployment may set a verified contact.
SAFETY_TEXT = {
    "downed_lines": DOWNED_LINES,
    "downed_lines_short": DOWNED_LINES_SHORT,
    "generator_co": GENERATOR_CO,
    "emergency": EMERGENCY,
    "report_outage": REPORT_OUTAGE_GENERIC,
}

# Which safety ids each kind must carry, in order (design D6 placement table). An empty tuple means none appended.
PLACEMENT: dict[str, tuple[str, ...]] = {
    "explanation": ("emergency",),
    "prep_before": ("downed_lines", "generator_co", "emergency"),
    "actions_during": ("downed_lines", "generator_co", "emergency", "report_outage"),
    "actions_after": ("downed_lines", "emergency"),
    "msg_pre_storm": ("downed_lines_short",),
    "msg_outage_confirmed": ("downed_lines_short",),
    "msg_estimate_updated": (),
    "msg_restoration_complete": (),   # the fixed template carries its own stay-away sentence with no digit
}

# Text that makes a draft "safety advice": the model may not write about these topics (guard rule 6).
SAFETY_TOPIC = re.compile(
    r"\b(downed|live\s+(?:power\s+)?(?:lines?|wires?)|power\s+lines?|generators?|carbon\s+monoxide|911|emergenc\w+|"
    r"grill|camp\s+stove)\b", re.I)

_CONTACT = re.compile(r"^[A-Za-z0-9 .,'()/-]{1,80}$")


class SafetyTextMissing(RuntimeError):
    """A required safety string is not in the assembled response. This is a bug, not a customer error."""


def outage_contact() -> str | None:
    """`UTILITY_OUTAGE_CONTACT`, only if it is a short plain value. Anything else is ignored (generic wording)."""
    value = (os.environ.get("UTILITY_OUTAGE_CONTACT") or "").strip()
    return value if value and _CONTACT.match(value) else None


def report_outage_text() -> str:
    contact = outage_contact()
    if contact is None:
        return REPORT_OUTAGE_GENERIC
    return f"Report your outage through your utility's official outage line or app: {contact}."


def text_for(safety_id: str) -> str:
    return report_outage_text() if safety_id == "report_outage" else SAFETY_TEXT[safety_id]


def required_for(kind: str) -> list[dict]:
    return [{"id": i, "text": text_for(i)} for i in PLACEMENT[kind]]


def assert_present(kind: str, final_texts: Iterable[str]) -> None:
    """Raise unless every required safety string appears verbatim in the assembled customer text."""
    haystack = "\n".join(final_texts)
    for item in required_for(kind):
        if item["text"] not in haystack:
            raise SafetyTextMissing(f"{kind}: required safety text '{item['id']}' is missing from the response")
