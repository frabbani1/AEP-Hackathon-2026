"""The fact card (`factcard-v1`): the only content a model ever sees (design D1).

`build()` makes the full card from the engine outputs. Code keeps the full card (range text with digits, confidence reason).
`to_prompt()` makes the projection that is sent: no digit, no token, no count, needs reduced to one boolean.
`sanitize_card()` refuses any string that could carry an instruction or a stray character.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "factcard-v1"
KINDS = ("explanation", "prep_before", "actions_during", "actions_after", "msg_pre_storm", "msg_outage_confirmed",
         "msg_estimate_updated", "msg_restoration_complete")
STAGES = ("before", "during", "after")
CHANNELS = ("sms", "push", "email", "voice")
STAGE_OF_KIND = {"prep_before": "before", "actions_during": "during", "actions_after": "after",
                 "msg_pre_storm": "before", "msg_outage_confirmed": "during", "msg_estimate_updated": "during",
                 "msg_restoration_complete": "after"}
# Kinds whose card may carry a restoration time. Restoration-complete never does (spec-05: no numbers, no outage length);
# after-outage actions have nothing to estimate.
RANGE_KINDS = {"explanation", "prep_before", "actions_during", "msg_pre_storm", "msg_outage_confirmed",
               "msg_estimate_updated"}
PRIOR_KINDS = {"msg_estimate_updated"}

RANGE_PLACEHOLDER, PRIOR_PLACEHOLDER = "{{range}}", "{{prior_range}}"
RANGE_LABEL = "estimate, not confirmed"
BAND_WORDS = {"High": "higher", "Medium": "moderate", "Low": "lower"}
SCENARIO_WORDS = {"sep-2024-like": "a milder storm like September 2024", "typical": "a typical storm",
                  "feb-2025-like": "a stronger storm like February 2025",
                  "beyond-observed": "a storm stronger than any we have seen",
                  "weather": "a storm of the strength the weather in your area points to",
                  "custom": "the storm strength you chose"}

# The model's version of the scenario words: SCENARIO_WORDS names the year of two past storms, and the model sees no digit.
SCENARIO_PROMPT_WORDS = {"sep-2024-like": "a milder past storm", "typical": "a typical storm",
                         "feb-2025-like": "a stronger past storm",
                         "beyond-observed": "a storm stronger than any we have seen",
                         "weather": "a storm of the strength the weather in your area points to",
                         "custom": "the storm strength you chose"}

# Digit-free factor sentences keyed by (factor id, direction). The risk engine's own sentences state numbers.
FACTOR_TEXT = {
    ("hist", "raises"): "Your line had more storm damage than most lines in past storms.",
    ("hist", "lowers"): "Your line had less storm damage than most lines in past storms.",
    ("type", "raises"): "Your service is overhead, which is more exposed to trees and falling branches.",
    ("type", "lowers"): "Your service is underground, which is better protected from trees and falling branches.",
    ("dev", "raises"): "Power reaches you through more protective devices than most homes, and each one is another "
                       "place a fault can cut service.",
    ("dev", "lowers"): "Power reaches you through fewer protective devices than most homes.",
    ("hops", "raises"): "You are farther down the line from the power source than most homes.",
    ("hops", "lowers"): "You are closer to the power source than most homes.",
    ("sev", "raises"): "The storm strength in this scenario is stronger than a typical past storm.",
    ("sev", "lowers"): "The storm strength in this scenario is milder than a typical past storm.",
}
# Digit-free stand-ins for the confidence reason (the engine's reason states record counts).
CONFIDENCE_SAFE = {
    "Low": "There is little storm history on your line behind this rating, so treat it with caution.",
    "Medium": "There is some storm history on your line behind this rating.",
    "High": "There is a good amount of storm history on your line behind this rating.",
}
# Digit-bearing assumption sentences from the engines, matched by their fixed wording.
ASSUMPTION_SAFE = [
    (re.compile(r"^Risk comes from damage reports on your line in \d+ past storms\.$"),
     "Risk comes from damage reports on your line in past storms."),
]
# The engine's risk label names "circuit", an internal term the guard rejects, so the model gets this sentence instead.
LABEL_SAFE = "based on storm history for your line, not a forecast for your home"
GENERIC_ASSUMPTION = "Some figures in this answer rest on stated model assumptions."
# The fixed digit-free sentences the risk engine puts in assumptions, missing_data and could_change (risk/engine.py). Only
# these reach the model; anything else in those lists is left out of the projection. A test checks the engine against this set.
RISK_SENTENCES = frozenset({
    "Storm strength is a scenario you choose, not a weather forecast.",
    "How much your service type and place on the line matter is an assumption, not something we measured.",
    "This scenario is stronger than any storm in our history, so the rating is an extrapolation.",
    "No weather data is used.",
    "Damage reports show where repairs were needed, not which homes lost power.",
    "Your service type (overhead or underground) is not on file, so we assumed overhead.",
    "A storm that hits your area harder or softer than past storms.",
    "Tree trimming or line upgrades done since the last storm.",
    "New damage reports on your line as a storm unfolds.",
})
# Restoration assumptions are fixed, digit-free sentences. The engine's own assumption list states crew hours and spreads.
RESTORATION_ASSUMPTIONS = [
    "Restoration times are simulated from assumed crew work hours and repair effort, not from live crew information.",
    "Repairs that restore the most customers per hour are done first.",
]

MAX_STRING = 300
MAX_FACTORS = 3
MAX_LIST = 5
_SAFE = re.compile(r"^[A-Za-z0-9 ,.'%()/-]*$")
# Closed-vocabulary fields hold identifiers such as `insufficient_data`, never free text.
_ENUM_KEYS = {"schema_version", "kind", "stage", "channel", "status", "band", "id", "direction", "level", "bucket",
              "preset"}
_ENUM = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_RANGE_DISPLAY = re.compile(r"^about \d{1,3}( to \d{1,3})? (hours?|days?)$")
# Instruction-like phrases that never belong in a fact. Checked case-insensitively on every card string.
_DENY = re.compile(
    r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier)|disregard|system prompt|you are now|\bact as\b|pretend|"
    r"jailbreak|developer (?:message|mode)|override|new instructions?|forget (?:everything|all)|"
    r"</?(?:card|items|system)|assistant:|user:|```|\bprompt\b|reveal|print your", re.I)
_DIGIT = re.compile(r"\d")


class CardRejected(ValueError):
    """A card string failed sanitization. No model call is made."""


def _safe_list(items: list[str] | None) -> list[str]:
    """Keep only sentences code knows: the engine's fixed digit-free sentences, the code-owned restoration sentences, and the
    digit-bearing assumptions swapped for their digit-free versions. Anything else is left out of what the model sees."""
    out: list[str] = []
    for s in items or []:
        if s in RISK_SENTENCES or s in RESTORATION_ASSUMPTIONS:
            safe = s
        else:
            safe = next((rep for rx, rep in ASSUMPTION_SAFE if rx.match(s)), None)
        if safe is not None and safe not in out:
            out.append(safe)
    return out[:MAX_LIST]


def build(*, kind: str, risk: Mapping, restoration: Mapping, channel: str | None, has_declared_need: bool,
          prior_display: str | None = None, stage: str | None = None) -> dict[str, Any]:
    """Full card from the risk and restoration outputs. Never contains the token, premise numbers or counts."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    stage = STAGE_OF_KIND.get(kind) or stage or "before"
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {list(STAGES)}")
    rated = risk.get("status") == "ok" and risk.get("band") in BAND_WORDS
    est = restoration.get("estimate") if restoration.get("status") == "ok" else None
    sev = risk.get("severity") or {}
    preset = sev.get("preset") if isinstance(sev.get("preset"), str) else "custom"
    conf = risk.get("confidence") or {}

    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "kind": kind, "stage": stage, "channel": channel,
        "status": "ok" if rated else "insufficient_data",
        "band": risk["band"] if rated else None,
        "band_word": BAND_WORDS[risk["band"]] if rated else None,
        "confidence": ({"level": conf["level"], "reason": conf.get("reason") or CONFIDENCE_SAFE[conf["level"]]}
                       if rated and conf.get("level") in CONFIDENCE_SAFE else None),
        "scenario": {"preset": preset, "words": SCENARIO_WORDS.get(preset, SCENARIO_WORDS["custom"]),
                     "extrapolated": bool(sev.get("extrapolated"))},
        "range": None, "prior_range": None,
        "top_factors": [{"id": f["id"], "direction": f["direction"], "text": FACTOR_TEXT[(f["id"], f["direction"])]}
                        for f in (risk.get("top_factors") or [])[:MAX_FACTORS]
                        if (f.get("id"), f.get("direction")) in FACTOR_TEXT] if rated else [],
        "assumptions": list(risk.get("assumptions") or []) if rated else [],
        "missing_data": list(risk.get("missing_data") or []) if rated else [],
        "could_change": list(risk.get("could_change") or []) if rated else [],
        "label": risk.get("label") if rated else None,
        "has_declared_need": bool(has_declared_need),
        "fallback_message": ((risk.get("fallback") or {}).get("message") if not rated else None),
    }
    if est and kind in RANGE_KINDS:
        card["range"] = {"display": est["display"], "bucket": range_bucket(est.get("p50_hours")),
                         "label": RANGE_LABEL}
        card["assumptions"] = card["assumptions"] + RESTORATION_ASSUMPTIONS
        if prior_display and kind in PRIOR_KINDS:
            card["prior_range"] = {"display": prior_display}
    return card


def range_bucket(p50_hours: int | float | None) -> str:
    """Same cutoffs as the guidance module: short < 8, medium 8 to 23, long 24 to 71, extended 72 or more."""
    if p50_hours is None:
        return "unknown"
    if p50_hours < 8:
        return "short"
    if p50_hours < 24:
        return "medium"
    if p50_hours < 72:
        return "long"
    return "extended"


def _enum(value: Any, allowed, name: str, *, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    if value not in allowed:
        raise CardRejected(f"{name} is not one of the allowed values")
    return value


def to_prompt(card: Mapping) -> dict[str, Any]:
    """The projection sent to the model. No digit anywhere, no token, needs as one boolean, times as placeholders.

    Every field is an enum checked against a closed set, a fixed sentence looked up in code, or (for assumptions, missing data
    and could-change items) one of the engine's own fixed sentences. No string from the card is copied through unchecked.
    """
    conf = card.get("confidence")
    level = _enum(conf["level"], CONFIDENCE_SAFE, "confidence level") if conf else None
    band = _enum(card.get("band"), BAND_WORDS, "band", optional=True)
    factors = []
    for f in card["top_factors"]:
        key = (f.get("id"), f.get("direction"))
        if key in FACTOR_TEXT:
            factors.append({"id": key[0], "direction": key[1], "text": FACTOR_TEXT[key]})
    preset = card["scenario"].get("preset")
    out: dict[str, Any] = {
        "kind": _enum(card["kind"], KINDS, "kind"), "stage": _enum(card["stage"], STAGES, "stage"),
        "channel": _enum(card.get("channel"), (*CHANNELS, None), "channel", optional=True),
        "status": _enum(card["status"], ("ok", "insufficient_data"), "status"),
        "band": band, "band_word": BAND_WORDS[band] if band else None,
        "confidence": {"level": level, "reason": CONFIDENCE_SAFE[level]} if level else None,
        "scenario": {"words": SCENARIO_PROMPT_WORDS.get(preset, SCENARIO_PROMPT_WORDS["custom"]),
                     "extrapolated": bool(card["scenario"].get("extrapolated"))},
        "range": ({"display": RANGE_PLACEHOLDER, "label": RANGE_LABEL} if card.get("range") else None),
        "prior_range": ({"display": PRIOR_PLACEHOLDER} if card.get("prior_range") else None),
        "top_factors": factors,
        "assumptions": _safe_list(card.get("assumptions")),
        "missing_data": _safe_list(card.get("missing_data")),
        "could_change": _safe_list(card.get("could_change")),
        "label": LABEL_SAFE if card.get("label") else None,
        "has_declared_need": bool(card["has_declared_need"]),
    }
    # A digit anywhere in the projection is a bug in this module: refuse rather than send it.
    _assert_no_digit(out)
    return out


def _walk(value: Any, path: tuple = ()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _walk(v, path + (k,))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk(v, path + (i,))


def _assert_no_digit(projection: Mapping) -> None:
    for path, s in _walk(projection):
        if _DIGIT.search(s):
            raise CardRejected(f"projection has a digit at {'.'.join(map(str, path))}")


def sanitize_card(card: Mapping, *, projection: bool = False) -> None:
    """Raise CardRejected if any string is unsafe. Run on the full card and on the projection before any model call.

    Safe characters only, a length cap, no braces or newlines (except the two fixed placeholders in the projection),
    and no instruction-like phrase.
    """
    for path, s in _walk(card):
        where = ".".join(map(str, path))
        if len(s) > MAX_STRING:
            raise CardRejected(f"string too long at {where}")
        if projection and path in (("range", "display"), ("prior_range", "display")):
            if s != (RANGE_PLACEHOLDER if path[0] == "range" else PRIOR_PLACEHOLDER):
                raise CardRejected(f"unexpected value at {where}")
            continue
        if path == ("range", "display") or path == ("prior_range", "display"):
            if not _RANGE_DISPLAY.match(s):
                raise CardRejected(f"unexpected range text at {where}")
            continue
        pattern = _ENUM if path and path[-1] in _ENUM_KEYS else _SAFE
        if not pattern.match(s):
            raise CardRejected(f"unsafe character at {where}")
        if _DENY.search(s):
            raise CardRejected(f"instruction-like text at {where}")
