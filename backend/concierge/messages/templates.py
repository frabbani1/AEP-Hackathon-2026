"""Canonical customer message text: four message types x four channels. Fixed wording, no model.

These are the fallback for the language model path, and the text the alert simulator sends. Every number comes from
the fact card (`estimate_display`, `prior_estimate_display`); code fills it in, nothing here does arithmetic.

Rules every template follows (the guard in compose.py checks them):
- Any restoration time is called an estimate. A pre-storm time is conditional ("If you lose power ...").
- A restoration-complete message contains no digits.
- No links, names, phone numbers, weather claims, or promises.
The downed-line safety sentence (SAFETY_LINE) is appended by code to pre-storm and outage-confirmed messages only.
The restoration-complete template carries its own stay-away sentence so it can stay free of digits.
"""
from __future__ import annotations

from collections.abc import Mapping

MESSAGE_TYPES = ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete")
CHANNELS = ("sms", "push", "email", "voice")
CHANNEL_LIMITS = {"sms": 480, "push": 200, "email": 1000, "voice": 500}
SAFETY_LINE = "If you see a downed power line, stay far away and call 911."
SAFETY_TYPES = {"pre_storm", "outage_confirmed"}
BAND_WORDS = {"High": "higher", "Medium": "moderate", "Low": "lower"}
SCENARIO_WORDS = {"sep-2024-like": "a milder storm like September 2024", "typical": "a typical storm",
                  "feb-2025-like": "a stronger storm like February 2025",
                  "beyond-observed": "a storm stronger than any we have seen",
                  "custom": "the storm strength you chose"}

_DEMO_PREPARE = {
    "sms": "Charge devices and gather supplies now.",
    "email": "Please charge your phones and battery packs, gather flashlights and drinking water, and save your "
             "utility's outage-reporting method in your phone.",
    "voice": "Please charge your devices and gather supplies now.",
}


def _pre_storm(card: Mapping) -> str:
    ch, est, risk, scen = card["channel"], card["estimate_display"], card["risk_level"], card["storm_scenario"]
    if not card["can_rate"]:
        return {
            "push": "Storm alert: prepare for a possible outage. Charge devices now.",
            "sms": "Storm alert: we can't rate your outage risk, so please prepare as if an outage is possible. "
                   + _DEMO_PREPARE["sms"],
            "email": "Storm alert: we can't rate your outage risk for this storm, so please prepare as if an outage is "
                     "possible. " + _DEMO_PREPARE["email"],
            "voice": "This is a storm alert. We cannot rate your outage risk, so please prepare as if an outage is "
                     "possible. " + _DEMO_PREPARE["voice"],
        }[ch]
    if ch == "push":
        tail = f" If you lose power, estimate: {est}." if est else ""
        return f"Storm alert: {risk} outage risk in {scen}.{tail}"
    head = {"sms": "Storm alert: your area has a {r} chance of power outages in {s}.",
            "email": "Storm alert: your area has a {r} chance of power outages in {s}.",
            "voice": "This is a storm alert. Your area has a {r} chance of power outages in {s}."}[ch]
    body = head.format(r=risk, s=scen)
    if est:
        if ch == "email":
            body += (f" If you lose power, our model estimates restoration could take {est}. "
                     "This is an estimate, not a confirmed time.")
        else:
            body += f" If you lose power, our model estimates restoration could take {est}."
    return f"{body} {_DEMO_PREPARE[ch]}"


def _outage_confirmed(card: Mapping) -> str:
    ch, est = card["channel"], card["estimate_display"]
    if est:
        return {
            "push": f"Outage confirmed. Estimated restoration: {est}. This is an estimate.",
            "sms": f"We confirmed an outage at your location. Our estimate for restoring power is {est}. "
                   "This is a model estimate, not a confirmed time. We will update you.",
            "email": f"We confirmed an outage at your location. Our estimate for restoring power is {est}. "
                     "This is a model estimate, not a confirmed time, and it can change as crews learn more. "
                     "We will send an update when the estimate changes.",
            "voice": f"This is an outage alert. We confirmed an outage at your location. Our estimate for restoring "
                     f"power is {est}. This is an estimate, not a confirmed time. We will update you.",
        }[ch]
    return {
        "push": "Outage confirmed. We don't have a reliable estimate yet.",
        "sms": "We confirmed an outage at your location. We don't have a reliable estimate yet and will update you.",
        "email": "We confirmed an outage at your location. We don't have a reliable estimate yet. "
                 "We will send an update as soon as we have one.",
        "voice": "This is an outage alert. We confirmed an outage at your location. We do not have a reliable "
                 "estimate yet and will update you.",
    }[ch]


def _estimate_updated(card: Mapping) -> str:
    ch, est, prior = card["channel"], card["estimate_display"], card["prior_estimate_display"]
    if est and prior:
        return {
            "push": f"Estimate updated: {est} (was {prior}). Still an estimate.",
            "sms": f"Update: our estimate for restoring your power is now {est}, changed from {prior}. "
                   "This is still an estimate, not a confirmed time.",
            "email": f"Update: our estimate for restoring your power is now {est}. It was {prior} before. "
                     "This is still a model estimate, not a confirmed time, and it may change again.",
            "voice": f"This is an update. Our estimate for restoring your power is now {est}, changed from {prior}. "
                     "This is still an estimate, not a confirmed time.",
        }[ch]
    if est:
        return {
            "push": f"Estimate updated: {est}. Still an estimate.",
            "sms": f"Update: our estimate for restoring your power is now {est}. This is an estimate, not a confirmed time.",
            "email": f"Update: our estimate for restoring your power is now {est}. This is a model estimate, "
                     "not a confirmed time, and it may change.",
            "voice": f"This is an update. Our estimate for restoring your power is now {est}. "
                     "This is an estimate, not a confirmed time.",
        }[ch]
    return {
        "push": "Update: we are still working on an estimate for your outage.",
        "sms": "Update: we are still working on an estimate for your outage and will share it when we have one.",
        "email": "Update: we are still working on an estimate for your outage. We will share it as soon as we have one.",
        "voice": "This is an update. We are still working on an estimate for your outage and will share it when we "
                 "have one.",
    }[ch]


def _restoration_complete(card: Mapping) -> str:
    return {
        "push": "Power is restored. If it is still out, please report it.",
        "sms": "Power has been restored at your location. If your power is still out, please report it. "
               "Stay away from any downed lines.",
        "email": "Power has been restored at your location. If your power is still out, please report it so crews "
                 "know. Please stay away from any downed lines.",
        "voice": "This is a restoration notice. Power has been restored at your location. If your power is still "
                 "out, please report it. Stay away from any downed lines.",
    }[card["channel"]]


_RENDER = {"pre_storm": _pre_storm, "outage_confirmed": _outage_confirmed, "estimate_updated": _estimate_updated,
           "restoration_complete": _restoration_complete}


def render(card: Mapping) -> str:
    """Fixed text for one fact card (message type and channel come from the card). Safety line not included."""
    if card["channel"] not in CHANNEL_LIMITS:
        raise ValueError(f"channel must be one of {list(CHANNEL_LIMITS)}")
    try:
        return _RENDER[card["message_type"]](card)
    except KeyError:
        raise ValueError(f"message_type must be one of {list(MESSAGE_TYPES)}") from None
