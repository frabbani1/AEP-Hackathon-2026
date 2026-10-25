"""Alert decision: bucket -> tier -> eligibility -> timing -> channels -> escalation.

Every threshold, tier and reason comes from the rules table (alerts/rules_table.json). This module has no
band or duration branches of its own. The function is pure: the same inputs and table always give the same decision,
which is what lets an audit entry be replayed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ..guidance.prepare import EMERGENCY_NOTICE
from .table import BANDS, CHANNELS, RulesTable

ENGINE_VERSION = "alerts-v2"
MESSAGE_TYPES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
TIER_LABEL = "prototype alert level"
MAX_HOURS_UNTIL_STORM = 168
MAX_HOURS_SINCE_UPDATE = 168


class Decision(NamedTuple):
    response: dict          # the public result (no decision_id; the caller adds it)
    inputs: dict            # everything needed to replay the decision (audit entry inputs)
    rules_fired: list[str]


def duration_bucket(table: RulesTable, p50_hours: int | float | None) -> str:
    if p50_hours is None:
        return "unknown"
    b = table.data["duration_buckets"]
    if p50_hours < b["short_below_hours"]:
        return "short"
    if p50_hours < b["medium_below_hours"]:
        return "medium"
    if p50_hours < b["long_below_hours"]:
        return "long"
    return "extended"


def _hours(estimate: Mapping | None) -> tuple[int | None, int | None]:
    """p50 and p90 from a restoration estimate. Falls back to the v1 field names until restoration-v2 ships."""
    if not estimate:
        return None, None
    p50 = estimate.get("p50_hours", estimate.get("typical_hours"))
    p90 = estimate.get("p90_hours", estimate.get("high_hours"))
    return p50, p90


def _lead_timing(leads: Sequence[int], h: int | None) -> list[dict]:
    if h is None:
        return [{"hours_before_storm": lead} for lead in leads]
    reached = [lead for lead in leads if lead >= h]
    catch_up = min(reached) if (reached and h > 0) else None
    out = []
    for lead in leads:
        if lead < h:
            out.append({"hours_before_storm": lead, "status": "scheduled"})
        elif lead == catch_up:
            out.append({"hours_before_storm": lead, "status": "send_now",
                        "note": "That time has passed, so we send one catch-up alert now."})
        else:
            out.append({"hours_before_storm": lead, "status": "skipped", "note": "That time has already passed."})
    return out


def _check_int(name: str, v: Any, lo: int, hi: int) -> None:
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi)):
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}")


def decide(table: RulesTable, *, band: str | None, unrated: bool, p50_hours: int | None, p90_hours: int | None,
           medical: bool, any_need: bool, channels: Sequence[str] | None,
           prior_typical_hours: int | None = None, hours_until_storm: int | None = None,
           hours_since_last_update: int | None = None, opt_in: bool = False) -> Decision:
    _check_int("hours_until_storm", hours_until_storm, 0, MAX_HOURS_UNTIL_STORM)
    _check_int("hours_since_last_update", hours_since_last_update, 0, MAX_HOURS_SINCE_UPDATE)
    if channels is not None:
        bad = [c for c in channels if c not in CHANNELS]
        if bad:
            raise ValueError(f"unknown channels: {bad}")
    if medical:
        any_need = True

    data = table.data
    if unrated or band not in BANDS:
        band_key, bucket, p50_hours, p90_hours = "Unrated", "unknown", None, None
    else:
        band_key, bucket = band, duration_bucket(table, p50_hours)

    # ---- tier -----------------------------------------------------------------------------------------------
    cell = data["base_tier"][band_key][bucket]
    tier, fired, reasons = cell["tier"], [f"T-{band_key}-{bucket}"], [cell["reason"]]
    rank = table.tier_rank
    top = len(table.tiers) - 1
    rules = data["needs_rules"]
    if any_need:
        tier = table.tiers[min(rank(tier) + rules["N1"].get("step", 1), top)]
        fired.append("N1")
        reasons.append(rules["N1"]["reason"])
    if medical:
        floor = rules["N2"]["min_tier"]
        if rank(tier) < rank(floor):
            tier = floor
        fired.append("N2")
        reasons.append(rules["N2"]["reason"])
        n3 = rules["N3"]
        if band_key == "Unrated" or (p50_hours is not None and p50_hours >= n3["min_p50_hours"]):
            if rank(tier) < rank(n3["min_tier"]):
                tier = n3["min_tier"]
            fired.append("N3")
            reasons.append(n3["reason"])
    fired.append(f"B-{tier}")
    beh = data["tier_behavior"][tier]

    # ---- eligibility ------------------------------------------------------------------------------------------
    elig_rules = data["eligibility"]
    if rank(tier) >= rank("standard"):
        rid = "K-pre-unrated" if band_key == "Unrated" else "K-pre"
        pre = {"eligible": True, "rule": rid, "reason": elig_rules[rid]["reason"]}
    else:
        pre = {"eligible": bool(opt_in), "rule": "K-pre-optin",
               "reason": elig_rules["K-pre-optin"]["reason_opted_in" if opt_in else "reason_not_opted_in"]}
    fired.append(pre["rule"])

    upd_rule = elig_rules["K-upd"]
    min_gap = beh["estimate_update_min_gap_hours"]
    if prior_typical_hours is not None and p50_hours is not None:
        needed = max(upd_rule["min_change_hours"], upd_rule["min_change_fraction"] * prior_typical_hours)
        if abs(p50_hours - prior_typical_hours) < needed:
            upd = {"eligible": False, "reason": upd_rule["reason_small"]}
        elif hours_since_last_update is not None and hours_since_last_update < min_gap:
            upd = {"eligible": False, "reason": upd_rule["reason_recent"]}
        else:
            upd = {"eligible": True, "reason": upd_rule["reason_changed"]}
        fired.append("K-upd")
    elif hours_since_last_update is not None and hours_since_last_update < min_gap:
        upd = {"eligible": False, "reason": upd_rule["reason_recent"]}
        fired.append("K-upd")
    else:
        upd = {"eligible": True, "reason": upd_rule["reason_no_prior"]}
    upd["rule"] = "K-upd"

    eligibility = {
        "pre_storm": pre,
        "outage_confirmed": {"eligible": True, "rule": "K-out", "reason": elig_rules["K-out"]["reason"]},
        "estimate_updated": upd,
        "restoration_complete": {"eligible": True, "rule": "K-restored", "reason": elig_rules["K-restored"]["reason"]},
    }

    # ---- timing -----------------------------------------------------------------------------------------------
    timing = {
        "pre_storm": _lead_timing(beh["pre_storm_lead_hours"], hours_until_storm),
        "outage_confirmed": {"within_minutes": beh["outage_confirmed_within_minutes"]},
        "estimate_updated": {"min_gap_hours": min_gap},
        "restoration_complete": {"when": "as soon as restoration is confirmed"},
    }

    # ---- channels ---------------------------------------------------------------------------------------------
    order = list(beh["channel_order"])
    if channels is None:
        recommended = order[:2]
        channel_note = "These are the channels we would suggest for your level. Sign up to choose your own."
    else:
        recommended = [c for c in order if c in channels] + [c for c in channels if c not in order]
        if not recommended:
            channel_note = "No channel is enrolled, so messages would only appear in the app."
        else:
            channel_note = "Channels are in the order we would try them."
        if medical and "voice" not in channels:
            channel_note += " Adding a voice channel is recommended because you declared a medical-equipment need."
    recommended = list(dict.fromkeys(recommended))

    # ---- escalation (recommendations for a person, never automatic) -------------------------------------------
    esc, er = [], data["escalation"]

    def add(rid: str) -> None:
        esc.append({"rule": rid, "action": er[rid]["action"], "reason": er[rid]["reason"]})
        fired.append(rid)

    if p50_hours is not None and p50_hours >= er["E1"]["min_p50_hours"]:
        add("E1")
    if medical and (band_key == "Unrated" or (p50_hours is not None and p50_hours >= er["E2"]["min_p50_hours"])):
        add("E2")
    if p90_hours is not None and p90_hours >= er["E3"]["min_p90_hours"]:
        add("E3")
    # E4 needs a wide range in relative terms (ratio) AND in absolute terms (gap in hours). The gap keeps a 2-to-9 hour
    # range, wide only because of rounding, from being sent to a person (design.md, "E4 check against restoration-v2").
    if (p50_hours and p90_hours and p90_hours / p50_hours > er["E4"]["min_p90_over_p50_ratio"]
            and p90_hours - p50_hours >= er["E4"]["min_p90_minus_p50_hours"]):
        add("E4")
    if any_need and p50_hours is not None and p50_hours >= er["E5"]["min_p50_hours"]:
        add("E5")

    response = {
        "engine_version": ENGINE_VERSION,
        "rules_table_version": table.version,
        "rules_table_hash": table.hash,
        "qualifies": bool(pre["eligible"]),
        "qualifies_note": ("Outage and restoration alerts are available to every enrolled customer. "
                           "This flag shows whether you also qualify for storm alerts before an event."),
        "tier": tier,
        "tier_label": TIER_LABEL,
        "tier_reason": " ".join(reasons),
        "eligibility": eligibility,
        "timing": timing,
        "recommended_channels": recommended,
        "channel_note": channel_note,
        "escalation": esc,
        "rules_fired": list(fired),
        "emergency_notice": EMERGENCY_NOTICE,
        "assumptions": list(data["assumptions"]),
    }
    inputs = {
        "band": band_key, "duration_bucket": bucket, "p50_hours": p50_hours, "p90_hours": p90_hours,
        "any_need": bool(any_need), "medical": bool(medical),
        "channels_enrolled": None if channels is None else list(channels),
        "prior_typical_hours": prior_typical_hours, "hours_until_storm": hours_until_storm,
        "hours_since_last_update": hours_since_last_update, "opt_in": bool(opt_in),
    }
    return Decision(response, inputs, list(fired))


def evaluate(table: RulesTable, *, band: str | None, insufficient_data: bool, estimate: Mapping | None,
             needs: Mapping[str, bool], channels: Sequence[str] | None, prior_typical_hours: int | None = None,
             hours_until_storm: int | None = None, hours_since_last_update: int | None = None,
             opt_in: bool = False) -> Decision:
    p50, p90 = _hours(estimate)
    return decide(table, band=band, unrated=insufficient_data or band is None, p50_hours=p50, p90_hours=p90,
                  medical=bool(needs.get("medical_equipment")), any_need=any(needs.values()), channels=channels,
                  prior_typical_hours=prior_typical_hours, hours_until_storm=hours_until_storm,
                  hours_since_last_update=hours_since_last_update, opt_in=opt_in)


def replay(table: RulesTable, inputs: Mapping[str, Any]) -> Decision:
    """Re-run a decision from the inputs stored in an audit entry."""
    band = inputs["band"]
    return decide(table, band=None if band == "Unrated" else band, unrated=band == "Unrated",
                  p50_hours=inputs["p50_hours"], p90_hours=inputs["p90_hours"], medical=inputs["medical"],
                  any_need=inputs["any_need"], channels=inputs["channels_enrolled"],
                  prior_typical_hours=inputs["prior_typical_hours"], hours_until_storm=inputs["hours_until_storm"],
                  hours_since_last_update=inputs["hours_since_last_update"], opt_in=inputs["opt_in"])
