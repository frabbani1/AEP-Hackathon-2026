"""Guards that keep the demo from sending anything real except the one opt-in email (`alerts/mailer.py`), plus parity with
the old (v1) alert rules."""
import re
from pathlib import Path

import pytest
from concierge.alerts.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend" / "concierge"

# Actions and services that would let the Lambda send a real message.
FORBIDDEN_IAM = re.compile(r"sns:|ses:|sesv2:|sms-voice|mobiletargeting|pinpoint|connect:|socialmessaging", re.I)
FORBIDDEN_CLIENTS = re.compile(
    r"""boto3\.(?:client|resource)\(\s*["'](?:sns|ses|sesv2|pinpoint[\w-]*|sms-voice[\w-]*|connect|socialmessaging|"""
    r"""pinpoint-sms-voice-v2)["']""")
FORBIDDEN_IMPORTS = re.compile(r"^\s*(?:import|from)\s+(?:smtplib|twilio|vonage|sendgrid|mailchimp|slack_sdk)\b", re.M)


@pytest.mark.parametrize("rel", ["scripts/setup_iam.sh", "infra/lib/stack.ts", "infra/bin/app.ts"])
def test_no_permission_to_send_real_messages_is_granted(rel):
    text = (ROOT / rel).read_text()
    assert not FORBIDDEN_IAM.search(text), f"{rel} mentions a messaging permission"


# The one module allowed to import smtplib: "email me this alert" sends a single email the person asked for, to the address
# they typed, after they ticked a consent box (see docs/api.md, "Email me this alert"). Every other module, and every other
# messaging library and client, stays forbidden, so nothing else can send anything real.
SMTP_ALLOWED = BACKEND / "alerts" / "mailer.py"
SMTP_IMPORT = re.compile(r"^\s*(?:import|from)\s+smtplib\b", re.M)


def test_no_messaging_client_is_created_or_imported():
    for path in BACKEND.rglob("*.py"):
        text = path.read_text()
        assert not FORBIDDEN_CLIENTS.search(text), f"{path} creates a messaging client"
        if path == SMTP_ALLOWED:
            text = SMTP_IMPORT.sub("", text)               # smtplib is allowed here, and only here
        assert not FORBIDDEN_IMPORTS.search(text), f"{path} imports a messaging library"


def test_only_the_mailer_imports_smtplib_and_only_the_email_route_calls_it():
    importers = [p for p in BACKEND.rglob("*.py") if SMTP_IMPORT.search(p.read_text())]
    assert importers == [SMTP_ALLOWED]
    callers = [p for p in BACKEND.rglob("*.py") if p != SMTP_ALLOWED and re.search(r"\bmailer\.send\(", p.read_text())]
    assert callers == [BACKEND / "api" / "service.py"]


def test_delivery_module_only_touches_the_store():
    text = (BACKEND / "alerts" / "delivery.py").read_text()
    assert "boto3" not in text and "socket" not in text and "urllib" not in text and "http" not in text.replace("https", "")


# ---- parity with alerts-v1 (1.3) ------------------------------------------------------------------------------------------
# Recorded from backend/concierge/alerts/rules.py (v1) before it was replaced:
#   case: (v1 pre-storm eligible, v1 rule, v1 escalation ids, v1 channels)
V1 = {
    "low_short_none": (False, "R4", [], ["sms", "push"]),
    "medium_medium_none": (True, "R1", [], ["sms", "push"]),
    "high_long_none": (True, "R1", ["R7"], ["sms", "push"]),
    "high_extended_medical": (True, "R1", ["R7", "R8", "R9"], ["voice", "sms"]),
    "unrated_none": (True, "R2", [], ["sms", "push"]),
    "low_short_mobility": (True, "R3", [], ["sms", "push"]),
}
CASES = {
    "low_short_none": dict(band="Low", insufficient_data=False, estimate={"typical_hours": 3, "high_hours": 5}, needs={}),
    "medium_medium_none": dict(band="Medium", insufficient_data=False, estimate={"typical_hours": 10, "high_hours": 16}, needs={}),
    "high_long_none": dict(band="High", insufficient_data=False, estimate={"typical_hours": 30, "high_hours": 48}, needs={}),
    "high_extended_medical": dict(band="High", insufficient_data=False, estimate={"typical_hours": 80, "high_hours": 130},
                                  needs={"medical_equipment": True}),
    "unrated_none": dict(band=None, insufficient_data=True, estimate=None, needs={}),
    "low_short_mobility": dict(band="Low", insufficient_data=False, estimate={"typical_hours": 3, "high_hours": 5},
                               needs={"mobility_limited": True}),
}
# v1 rule id -> v2 rule id (same meaning, new names)
V1_TO_V2_ESC = {"R7": "E1", "R8": "E2", "R9": "E3"}


@pytest.mark.parametrize("name", list(V1))
def test_storm_alert_eligibility_matches_v1(table, name):
    eligible, _, esc, _ = V1[name]
    r = evaluate(table, channels=None, **CASES[name]).response
    assert r["eligibility"]["pre_storm"]["eligible"] is eligible
    got = [e["rule"] for e in r["escalation"]]
    for old in esc:
        assert V1_TO_V2_ESC[old] in got, f"{name}: v1 escalation {old} is missing in v2"


@pytest.mark.parametrize("name", list(V1))
def test_channel_order_matches_v1_where_it_should(table, name):
    _, _, _, channels = V1[name]
    r = evaluate(table, channels=None, **CASES[name]).response
    if name == "low_short_none":
        assert r["recommended_channels"] == ["push", "email"]      # intended change: basic level prefers low-cost channels
    else:
        assert r["recommended_channels"] == channels


def test_documented_differences_from_v1(table):
    # v2 adds a tier, a fourth lead time for priority customers, and escalation E5 for declared needs.
    r = evaluate(table, channels=None, **CASES["high_extended_medical"]).response
    assert r["tier"] == "priority"
    assert [t["hours_before_storm"] for t in r["timing"]["pre_storm"]] == [72, 48, 24, 6]
    assert "E5" in [e["rule"] for e in r["escalation"]]
    # a low-risk customer with a declared need is raised from "no storm alert" to the standard level
    low = evaluate(table, channels=None, **CASES["low_short_mobility"]).response
    assert low["tier"] == "standard"
