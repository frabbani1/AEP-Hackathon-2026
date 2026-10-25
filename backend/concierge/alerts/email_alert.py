"""The text of the "email me this alert" message. Pure functions, no network.

Nothing here computes a number. An area email reuses the answers the outage map already shows (`zone_assess`): the
engines' risk band, restoration range, preparation steps and pre-storm message. A forecast-only email reuses the
outlook. The wording around them is fixed. No customer text goes into an email, and the language model is not involved.

Plain text only: no links, no images, nothing to render or track.
"""
from __future__ import annotations

from collections.abc import Mapping

PREP_SHOWN = 5
SENT_BECAUSE = ("This is a one-time email from the Outage Concierge prototype (HackOHI/O 2026), sent because you asked "
                "for it on the web page. Your email address was used only to send it and was not saved.")
ESTIMATE_NOTE = "These are model estimates, not confirmed utility information or a promised restoration time."
EMERGENCY = "In an emergency, such as a downed power line, call 911."


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _safety(prep: Mapping) -> list[str]:
    return [n["text"] for n in prep.get("safety", []) if n.get("id") != "emergency"]


def for_area(assessment: Mapping) -> tuple[str, str]:
    """(subject, body) for an area that has outage data."""
    name = assessment["zone_name"]
    risk, rest, alerts = assessment.get("risk"), assessment.get("restoration"), assessment.get("alerts") or {}
    parts = ["Hello,", ""]

    pre = next((m for m in assessment.get("messages") or [] if m.get("stage") == "pre-storm"), None)
    if pre:
        parts += [pre["body"], ""]

    parts.append(f"Your area: {name}")
    if risk:
        parts.append(f"Outage risk: {risk['band']} (confidence {risk['confidence']['level']})")
    else:
        fb = (assessment.get("fallback") or {}).get("message")
        parts.append(f"Outage risk: not rated. {fb or 'There is not enough storm history here to rate this area.'}")
    if rest:
        parts.append(f"If power goes out, restoration could take {rest['range_text']}.")
    if alerts.get("summary"):
        parts.append(alerts["summary"])
    parts.append("")

    prep = assessment.get("preparation") or {}
    before = next((g for g in prep.get("groups", []) if g.get("id") == "before"), None)
    if before:
        parts += ["Get ready:", _bullets([a["text"] for a in before["actions"][:PREP_SHOWN]]), ""]
    safety = _safety(prep)
    if safety:
        parts += ["Safety first:", _bullets(safety), ""]

    parts += [ESTIMATE_NOTE, EMERGENCY, "", "--", SENT_BECAUSE]
    band = f"{risk['band']} outage risk" if risk else "storm alert"
    return f"Outage Concierge: {band} for the {name} area", "\n".join(parts)


def for_outlook(outlook: Mapping) -> tuple[str, str]:
    """(subject, body) for a place with no outage data. Says it is a prediction, and what it is not."""
    place, o = outlook["place"]["name"], outlook["outlook"]
    parts = ["Hello,", "", f"PREDICTION ONLY. {outlook['notice']}", "", f"Weather level for {place}: {o['label']}", o["summary"], ""]
    hazards = [h["text"] for h in outlook.get("hazards", [])]
    if hazards:
        parts += ["Also forecast:", _bullets(hazards), ""]
    prep = outlook.get("preparation") or {}
    actions = [a["text"] for a in prep.get("actions", [])[:PREP_SHOWN]]
    if actions:
        parts += ["Get ready:", _bullets(actions), ""]
    safety = _safety(prep)
    if safety:
        parts += ["Safety first:", _bullets(safety), ""]
    parts += [f"Not available for {place}: " + ", ".join(i.lower() for i in outlook.get("not_available", [])) + ".",
              "This is general advice from a weather forecast, not an outage rating.", EMERGENCY, "", "--", SENT_BECAUSE]
    attribution = (outlook.get("weather") or {}).get("attribution")
    if attribution:
        parts.append(attribution)
    return f"Outage Concierge: weather outlook for {place} ({o['label']}), prediction only", "\n".join(parts)
