"""Turns engine output for a zone's representative premise into the shapes the outage map shows.

Pure functions: they take the dicts the engines already return and rearrange them. They compute no risk or restoration
number. The only figures formatted here are ones the engines supplied (hours, minutes, gaps), and text is either the
engines' own or fixed wording below.

Keys are snake_case like every other route; the frontend client converts them to camelCase.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..risk.confidence import FALLBACK_MESSAGES

SOURCE = "live"
UNRATED_TOKEN = "p_0000000000000000"          # well-formed, never in the premise table: engines answer "insufficient data"

SCOPE_NOTE = ("This rating shows how storm-prone the power line serving a typical home in this area has been. "
              "It is not a forecast for one home.")
AREA_ASSUMPTION = ("An area is rated through one typical home in it: the home in the middle of the area's homes when "
                   "they are ordered by risk score. Homes on other lines in the same area can differ.")
UNRATED_ASSUMPTION = "This area has no home with enough storm history to rate, so no risk or restoration time is shown."
UNRATED_MISSING = "Not enough storm history or too few homes on the lines here to rate them reliably."
UNRATED_COULD_CHANGE = "More storm history for the lines here would let us rate this area."
DAMAGE_UNKNOWN = "Damage found only after crews arrive may differ from what we assumed."
EXTRAPOLATED_MOVER = "This storm is stronger than any in our records, so damage could be worse than assumed."

NEED_TAILORING = {"med_": "medical-power", "mob_": "accessibility", "heat_": "heat-cooling"}
SAFETY_NOTICES = {                                 # id -> (level, title)
    "downed_lines": ("emergency", "Downed power line"),
    "generator_co": ("caution", "Generator and carbon monoxide"),
    "emergency": ("emergency", "In an emergency"),
}
GROUPS = (("before", "prep_before", "Before the storm hits"),
          ("during", "actions_during", "If the power goes out"),
          ("after", "actions_after", "After the power is back"))
MESSAGE_KINDS = (                                   # kind -> (stage, channel)
    ("msg_pre_storm", "pre-storm", "email"),
    ("msg_outage_confirmed", "outage-confirmed", "sms"),
    ("msg_estimate_updated", "estimate-updated", "sms"),
    ("msg_restoration_complete", "restored", "sms"),
)
ALWAYS_ALERTS = "Outage and restoration alerts are available to every enrolled customer."
PRE_STORM_SUBJECT = "A storm is expected in your area"
CHANNEL_LABEL = {"sms": "Text message", "email": "Email", "voice": "Phone call", "push": "App notification",
                 "app": "App notification"}
ELIGIBILITY_LABEL = {"pre_storm": "Before a storm", "outage_confirmed": "Outage confirmed",
                     "estimate_updated": "Estimate changes", "restoration_complete": "Power restored"}


def unrated_fallback(reason_code: str) -> dict:
    return {"reason_code": reason_code, "message": FALLBACK_MESSAGES[reason_code]}


def layer(zones: Sequence[Mapping], ratings: Mapping[str, tuple[str, str] | None], severity: Mapping,
          bounds: Sequence, min_homes: int) -> dict:
    """The map layer. `zones` are dicts with id, name, ring; `ratings` maps zone id to (band, confidence) or None."""
    out = []
    for z in zones:
        rating = ratings.get(z["id"])
        out.append({"id": z["id"], "name": z["name"], "ring": [list(p) for p in z["ring"]],
                    "band": rating[0] if rating else None, "confidence": rating[1] if rating else None})
    return {"source": SOURCE, "severity": dict(severity), "bounds": [list(bounds[0]), list(bounds[1])],
            "min_homes_per_area": min_homes, "zones": out}


def _origin(response: Mapping) -> str:
    return "model" if response.get("source") in ("model", "cache") else "template"


def risk_view(risk: Mapping, explanation: Mapping) -> dict | None:
    if risk.get("status") != "ok":
        return None
    evidence = risk.get("band_evidence")
    conf = risk["confidence"]
    return {
        "band": risk["band"], "scope_note": SCOPE_NOTE,
        "confidence": {"level": conf["level"], "records": conf["records"], "reason": conf["reason"]},
        "band_evidence": evidence["sentence"] if evidence else None,
        "top_factors": [{"id": f["id"], "direction": f["direction"], "weight": f["weight"], "text": f["text"]}
                        for f in risk["top_factors"]],
        "explanation": {"text": explanation["text"], "origin": _origin(explanation)},
    }


def _movers(est: Mapping, sensitivity: Sequence[Mapping], extrapolated: bool) -> list[dict]:
    base = est["p50_hours"]
    movers = []
    for i, s in enumerate(sensitivity):
        hours = s["p50_hours"]
        if hours == base:
            continue                                  # nothing to say about a change that does not move the likely time
        direction = "longer" if hours > base else "shorter"
        movers.append({"id": f"sensitivity-{i}", "direction": direction,
                       "text": f"{s['change']}: the likely time becomes about {hours} hours instead of about {base}."})
    movers.append({"id": "damage", "direction": "either", "text": DAMAGE_UNKNOWN})
    if extrapolated:
        movers.append({"id": "extrapolated", "direction": "longer", "text": EXTRAPOLATED_MOVER})
    return movers


def restoration_view(restoration: Mapping, severity: Mapping) -> dict | None:
    if restoration.get("status") != "ok" or not restoration.get("estimate"):
        return None
    est, scenario = restoration["estimate"], restoration["scenario"]
    return {
        "range_text": est["display"], "p50_hours": est["p50_hours"], "p90_hours": est["p90_hours"],
        "meaning": est["meaning"], "label": restoration["label"],
        "damage_count": scenario.get("damage_count"), "damage_source": scenario["damage_source"],
        "confidence": restoration["confidence"]["level"],
        "movers": _movers(est, restoration.get("sensitivity") or [], bool(severity.get("extrapolated"))),
        "steps": [{"stage": s["stage"], "label": s["label"], "done_by_hours": s["done_by_hours"]}
                  for s in restoration.get("steps") or []],
    }


def _tailored(item_id: str) -> str | None:
    return next((need for prefix, need in NEED_TAILORING.items() if item_id.startswith(prefix)), None)


def preparation_view(responses: Mapping[str, Mapping]) -> dict:
    """Safety notices from the 'before' list (the same two or three appear in every list), then three action groups."""
    safety = []
    for item in responses["prep_before"]["sections"]["customer_actions"]:
        if item["safety"] and item["id"] in SAFETY_NOTICES:
            level, title = SAFETY_NOTICES[item["id"]]
            safety.append({"id": item["id"], "level": level, "title": title, "text": item["text"]})
    notice_ids = {n["id"] for n in safety}
    groups = []
    for group_id, kind, title in GROUPS:
        actions = []
        for item in responses[kind]["sections"]["customer_actions"]:
            if item["id"] in notice_ids:
                continue                              # already shown as a notice
            action: dict[str, Any] = {"id": f"{group_id}-{item['id']}", "text": item["text"]}
            need = _tailored(item["id"])
            if need:
                action["tailored_for"] = need
            actions.append(action)
        if actions:
            groups.append({"id": group_id, "title": title, "actions": actions})
    return {"safety": safety, "groups": groups}


def _hours_list(hours: Sequence[int]) -> str:
    parts = [str(h) for h in hours]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _timing_text(alerts: Mapping) -> str:
    timing, elig = alerts["timing"], alerts["eligibility"]
    sentences = []
    pre = [t["hours_before_storm"] for t in timing.get("pre_storm") or [] if t.get("status") != "skipped"]
    if elig["pre_storm"]["eligible"] and pre:
        sentences.append(f"Storm alerts {_hours_list(pre)} hours before the storm.")
    if elig["outage_confirmed"]["eligible"] and "within_minutes" in timing["outage_confirmed"]:
        sentences.append(f"An outage alert within {timing['outage_confirmed']['within_minutes']} minutes of confirmation.")
    if elig["estimate_updated"]["eligible"] and "min_gap_hours" in timing["estimate_updated"]:
        gap = timing["estimate_updated"]["min_gap_hours"]
        sentences.append(f"Estimate updates at most once every {'hour' if gap == 1 else f'{gap} hours'}.")
    if elig["restoration_complete"]["eligible"]:
        sentences.append("A restoration message as soon as restoration is confirmed.")
    return " ".join(sentences)


def alerts_view(alerts: Mapping) -> dict:
    tier = alerts["tier"]
    qualifies = bool(alerts["qualifies"])
    lead = (f"Your prototype alert level is {tier}, so you qualify for storm alerts before an event."
            if qualifies else f"Your prototype alert level is {tier}, so storm alerts before an event are not triggered.")
    rules = [{"id": e["rule"], "text": f"{ELIGIBILITY_LABEL[name]}: {e['reason']}", "met": bool(e["eligible"])}
             for name, e in alerts["eligibility"].items() if name in ELIGIBILITY_LABEL]
    return {
        "eligible": qualifies, "level": tier, "level_reason": alerts["tier_reason"],
        "summary": f"{lead} {ALWAYS_ALERTS}",
        "rules": rules, "timing": _timing_text(alerts),
        "channels": [CHANNEL_LABEL.get(c, c) for c in alerts["recommended_channels"]],
        "escalation": [e["reason"] for e in alerts["escalation"]],
        "note": alerts["assumptions"][0] if alerts["assumptions"] else None,
    }


def messages_view(responses: Mapping[str, Mapping]) -> list[dict]:
    out = []
    for kind, stage, channel in MESSAGE_KINDS:
        r = responses[kind]
        message: dict[str, Any] = {"stage": stage, "channel": CHANNEL_LABEL[channel], "body": r["text"],
                                   "origin": _origin(r)}
        if stage == "pre-storm" and channel == "email":
            message["subject"] = PRE_STORM_SUBJECT
        out.append(message)
    return out


def _dedupe(*lists: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for items in lists:
        for item in items:
            seen.setdefault(item, None)
    return list(seen)


def transparency_view(risk: Mapping, restoration: Mapping, rated: bool) -> dict:
    if not rated:
        return {"assumptions": [UNRATED_ASSUMPTION], "missing_data": _dedupe(risk.get("missing_data") or [], [UNRATED_MISSING]),
                "could_change": [UNRATED_COULD_CHANGE]}
    return {
        "assumptions": _dedupe([AREA_ASSUMPTION], risk.get("assumptions") or [], restoration.get("assumptions") or []),
        "missing_data": _dedupe(risk.get("missing_data") or [], restoration.get("missing_data") or []),
        "could_change": _dedupe(risk.get("could_change") or [], restoration.get("could_change") or []),
    }
