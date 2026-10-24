"""Alert eligibility, timing, channel and escalation. Documented rules (challenge assumptions), no model involved.

Every decision returns the rule id and a plain-language reason so a customer or judge can see why.
These rules are prototype assumptions, not utility policy.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

ENGINE_VERSION = "alerts-v1"
MESSAGE_TYPES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
CHANNELS = ("sms", "push", "email", "voice")

# Thresholds (assumptions)
UPDATE_MIN_CHANGE_HOURS = 2           # R5: send an updated estimate only if it moved by at least this much...
UPDATE_MIN_CHANGE_FRACTION = 0.25     # ...and by at least this share of the earlier estimate
LONG_OUTAGE_HOURS = 24                # R7
MEDICAL_ESCALATION_HOURS = 4          # R8
EXTENDED_OUTAGE_HOURS = 72            # R9
WIDE_RANGE_RATIO = 3.0                # R10: high end more than 3x the low end

PRE_STORM_LEAD_HOURS = {"High": [48, 24, 6], "Medium": [24, 6], "Low": [], None: [24]}


def pick_channels(available: Sequence[str] | None, medical: bool) -> list[str]:
    """Preferred order. A declared medical need adds voice first when the customer has it. Only channels the
    customer says they have are used."""
    order = ["voice", "sms", "push", "email"] if medical else ["sms", "push", "email", "voice"]
    if available is None:
        return order[:2]
    bad = [c for c in available if c not in CHANNELS]
    if bad:
        raise ValueError(f"unknown channels: {bad}")
    chosen = [c for c in order if c in available]
    return chosen


def evaluate(*, band: str | None, insufficient_data: bool, estimate: Mapping | None, needs: Mapping[str, bool],
             channels: Sequence[str] | None, prior_typical_hours: int | None = None) -> dict:
    medical = bool(needs.get("medical_equipment"))
    any_need = any(needs.values())
    typical = estimate["typical_hours"] if estimate else None
    low, high = (estimate["low_hours"], estimate["high_hours"]) if estimate else (None, None)
    chosen = pick_channels(channels, medical)

    # ---- eligibility ----------------------------------------------------------------------------------------
    elig: dict[str, dict] = {}
    if insufficient_data:
        elig["pre_storm"] = {"eligible": True, "rule": "R2",
                             "reason": "We can't rate your risk, so we send a general storm alert to be safe."}
    elif band in ("High", "Medium"):
        elig["pre_storm"] = {"eligible": True, "rule": "R1",
                             "reason": f"Your area is rated {band} for this scenario, which qualifies for storm alerts."}
    elif any_need:
        elig["pre_storm"] = {"eligible": True, "rule": "R3",
                             "reason": "You declared a need, so you qualify for storm alerts even at lower risk."}
    else:
        elig["pre_storm"] = {"eligible": False, "rule": "R4",
                             "reason": "Your area is rated Low for this scenario. You can still opt in to outage alerts."}
    elig["outage_confirmed"] = {"eligible": True, "rule": "R4b",
                                "reason": "Every enrolled customer is alerted when an outage is confirmed."}
    if prior_typical_hours is not None and typical is not None:
        change = abs(typical - prior_typical_hours)
        needed = max(UPDATE_MIN_CHANGE_HOURS, UPDATE_MIN_CHANGE_FRACTION * prior_typical_hours)
        ok = change >= needed
        elig["estimate_updated"] = {
            "eligible": ok, "rule": "R5",
            "reason": ("The estimate moved enough to be worth telling you about." if ok
                       else "The estimate has not changed enough to send another message.")}
    else:
        elig["estimate_updated"] = {"eligible": True, "rule": "R5b",
                                    "reason": "An updated estimate is sent whenever it changes by a meaningful amount."}
    elig["restoration_complete"] = {"eligible": True, "rule": "R6",
                                    "reason": "Every enrolled customer is told when power is restored."}

    # ---- timing ---------------------------------------------------------------------------------------------
    lead = PRE_STORM_LEAD_HOURS[None if insufficient_data else band]
    timing = {
        "pre_storm": ({"hours_before_storm": lead,
                       "note": "Send at each lead time. Skip any that have already passed."} if lead else None),
        "outage_confirmed": {"when": "within minutes of outage confirmation"},
        "estimate_updated": {"when": "when the estimate changes enough (R5), at most once an hour"},
        "restoration_complete": {"when": "as soon as restoration is confirmed"},
    }

    # ---- escalation (recommendations to a human team, never automatic actions) ---------------------------
    esc = []
    if typical is not None and typical >= LONG_OUTAGE_HOURS:
        esc.append({"rule": "R7", "action": "customer_care_follow_up",
                    "reason": "The typical estimate is a day or longer, so a person should follow up."})
    if medical and typical is not None and typical >= MEDICAL_ESCALATION_HOURS:
        esc.append({"rule": "R8", "action": "priority_outreach",
                    "reason": "A declared medical-equipment need plus an outage of several hours calls for priority outreach."})
    if medical and insufficient_data:
        esc.append({"rule": "R8b", "action": "priority_outreach",
                    "reason": "A declared medical-equipment need and no reliable estimate calls for priority outreach."})
    if high is not None and high >= EXTENDED_OUTAGE_HOURS:
        esc.append({"rule": "R9", "action": "community_resources",
                    "reason": "The high end of the range reaches several days, so share shelter and resource information."})
    if low and high and high / low > WIDE_RANGE_RATIO:
        esc.append({"rule": "R10", "action": "human_review",
                    "reason": "The estimate range is very wide, so a person should review it before it is shared."})

    return {
        "engine_version": ENGINE_VERSION,
        "eligibility": elig,
        "recommended_channels": chosen,
        "channel_note": ("Voice is listed first because you declared a medical-equipment need." if medical and "voice" in chosen
                         else "No channel on file; the message would only appear in the app." if not chosen
                         else "Channels are in the order we would try them."),
        "timing": timing,
        "escalation": esc,
        "assumptions": [
            "These alert rules are prototype assumptions, not utility policy.",
            "Needs are only the ones you declared. We do not guess them.",
            "Escalations are recommendations for a person to act on, not automatic actions.",
        ],
    }
