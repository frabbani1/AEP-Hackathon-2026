"""Customer messages. The model only phrases a fixed fact card; code decides every fact and checks every number.

Flow: fact card (whitelist) -> Bedrock phrasing -> guard -> (pass) use it, (fail or error) use the template.
The safety line is appended by code after the guard, so the model cannot drop or change it.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping

from .templates import (BAND_WORDS, CHANNEL_LIMITS, MESSAGE_TYPES, SAFETY_LINE, SAFETY_TYPES,  # noqa: F401
                        SCENARIO_WORDS, render)

log = logging.getLogger("concierge.messages")
ENGINE_VERSION = "messages-v1"

ESTIMATE_TYPES = {"pre_storm", "outage_confirmed", "estimate_updated"}

# Fact-card keys the model may see. Nothing free-form from the customer is ever included.
FACT_KEYS = ("message_type", "channel", "risk_level", "storm_scenario", "estimate_display", "prior_estimate_display",
             "confidence", "has_declared_need", "can_rate")

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
          "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12, "half": 0.5, "twice": 2,
          "triple": 3, "double": 2}
_BAD_PATTERNS = [
    (re.compile(r"https?://|www\.|\.com\b|\.org\b|\.net\b", re.I), "link"),
    (re.compile(r"\S+@\S+\.\S+"), "email address"),
    (re.compile(r"CIR\d+|STATION|SUBSTATION|\bcircuit\b|\btransformer\b", re.I), "internal term"),
    (re.compile(r"\bguarantee[sd]?\b|\bpromise[sd]?\b", re.I), "promise"),
    # We have no weather data, so the model must not describe weather.
    (re.compile(r"\b(winds?|gusts?|rain|snow|ice|icy|sleet|hail|flood\w*|tornado\w*|thunder\w*|lightning|"
                r"hurricane|temperatures?|freezing|heat ?wave)\b", re.I), "weather claim"),
    # Claims about what already happened or what is certain are not in the fact card.
    (re.compile(r"\b(was|were) (off|out|without)\b|\btook\b|\b(completed|finished) in\b|\bthan we (thought|expected)\b|"
                r"\b(is|are) (expected|coming|approaching|here)\b|\bwill (hit|arrive|reach|strike)\b|"
                r"\bcrews? (are|is|will|have|has)\b", re.I), "claim not in the facts"),
    (re.compile(r"\b(moderate|higher|lower|high|low)[- ](risk )?storm\b", re.I), "risk level described as storm strength"),
]



def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text):
        out.add(m.replace(",", "").rstrip("."))
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in _WORDS:
            out.add(f"word:{w}")
    return out


def allowed_numbers(card: Mapping) -> set[str]:
    allowed = set()
    for k in ("estimate_display", "prior_estimate_display", "storm_scenario"):
        for m in _NUM.findall(str(card.get(k) or "")):
            allowed.add(m.replace(",", ""))
    return allowed


def guard(text: str, card: Mapping, channel: str) -> list[str]:
    """Return the reasons the text is not allowed (empty list = pass)."""
    reasons = []
    allowed = allowed_numbers(card)
    for n in numbers_in(text):
        if n.startswith("word:"):
            # spelled-out numbers are only fine if the same value is in the fact card
            val = _WORDS[n[5:]]
            if not any(a.replace(".", "", 1).isdigit() and float(a) == val for a in allowed):
                reasons.append(f"spelled-out number '{n[5:]}' not in the facts")
        elif n not in allowed:
            reasons.append(f"number {n} not in the facts")
    for rx, label in _BAD_PATTERNS:
        if rx.search(text):
            reasons.append(f"contains a {label}")
    reserve = len(SAFETY_LINE) + 1 if card["message_type"] in SAFETY_TYPES else 0
    if len(text) > CHANNEL_LIMITS[channel] - reserve:
        reasons.append("too long for the channel")
    if not text.strip():
        reasons.append("empty")
    est = card.get("estimate_display")
    if est and card["message_type"] in ESTIMATE_TYPES:
        if not allowed_numbers({"estimate_display": est}) <= {n for n in numbers_in(text)}:
            reasons.append("estimate numbers missing")
        if "estimate" not in text.lower():
            reasons.append("does not say the time is an estimate")
        if card["message_type"] == "pre_storm" and not re.search(r"\bif\b", text, re.I):
            reasons.append("pre-storm time must be conditional on losing power")
    if card["message_type"] == "restoration_complete" and any(not n.startswith("word:") for n in numbers_in(text)):
        reasons.append("restoration-complete message must not contain numbers")
    return reasons


def fact_card(*, message_type: str, channel: str, band: str | None, scenario: str, estimate: Mapping | None,
              prior_display: str | None, confidence: str | None, has_need: bool, can_rate: bool) -> dict:
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"message_type must be one of {list(MESSAGE_TYPES)}")
    if channel not in CHANNEL_LIMITS:
        raise ValueError(f"channel must be one of {list(CHANNEL_LIMITS)}")
    # Each message type only gets the facts it is allowed to state. A restoration-complete message must not carry a
    # model estimate (it would read as a claim about how long the outage lasted), and only a pre-storm message
    # talks about the planning scenario or the risk level.
    has_est = message_type in ESTIMATE_TYPES and estimate is not None
    return {
        "message_type": message_type, "channel": channel,
        "risk_level": BAND_WORDS.get(band) if (can_rate and message_type == "pre_storm") else None,
        "storm_scenario": SCENARIO_WORDS.get(scenario, SCENARIO_WORDS["custom"]) if message_type == "pre_storm" else None,
        "estimate_display": estimate["display"] if has_est else None,
        "prior_estimate_display": prior_display if (has_est and message_type == "estimate_updated") else None,
        "confidence": confidence if has_est else None,
        "has_declared_need": bool(has_need), "can_rate": bool(can_rate),
    }


def template(card: Mapping) -> str:
    """Fixed fallback text. The canonical wording lives in messages/templates.py."""
    return render(card)


SYSTEM_PROMPT = (
    "You write short text messages for an electric utility's customers during storms. "
    "Use plain, calm language at a 6th-grade reading level. "
    "Use ONLY the facts in the JSON. Never write a number, time, or duration that is not copied exactly from "
    "estimate_display or prior_estimate_display. Write times exactly as given, for example 'about 6 to 12 hours'. "
    "Call every restoration time an estimate and never promise it. "
    "risk_level is the chance of losing power in the customer's area. It is NOT how strong the storm is. "
    "storm_scenario is a what-if planning scenario, not a forecast: say 'if a storm like that hits', never that a "
    "storm is expected or coming. "
    "You have no weather data, so never mention wind, rain, snow, ice, flooding or temperature. "
    "Do not say what crews are doing, what caused an outage, how long power was out, or that anything is worse or "
    "better than expected. "
    "Do not mention circuits, equipment, other customers, links, phone numbers or names. "
    "Do not add a greeting, sign-off or safety instructions. "
    "Do not add facts that are not in the JSON. If a field is null, do not mention it. "
    "Output only the message text, with no quotes or labels."
)

TYPE_GUIDE = {
    "pre_storm": "Say the outage risk level for a storm like the scenario. If estimate_display is present, say that IF "
                 "they lose power, restoration is estimated to take that long.",
    "outage_confirmed": "Say an outage is confirmed at their location and give the estimated restoration time.",
    "estimate_updated": "Say the estimate has changed. Give the new estimate and, if present, the earlier one.",
    "restoration_complete": "Say power has been restored and to report it if their power is still out. "
                            "Use no numbers.",
}


def make_bedrock_phraser(client, model_id: str) -> Callable[[Mapping], str]:
    def phrase(card: Mapping) -> str:
        budget = CHANNEL_LIMITS[card["channel"]] - (len(SAFETY_LINE) + 1 if card["message_type"] in SAFETY_TYPES else 0)
        prompt = (f"Write one {card['message_type'].replace('_', ' ')} message for the {card['channel']} channel. "
                  f"{TYPE_GUIDE[card['message_type']]} Keep it under {budget} characters.\n\nFacts:\n{json.dumps({k: card[k] for k in FACT_KEYS}, indent=1)}")
        resp = client.converse(
            modelId=model_id, system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.2, "topP": 0.9})
        return resp["output"]["message"]["content"][0]["text"].strip().strip('"')
    return phrase


def compose(card: Mapping, phraser: Callable[[Mapping], str] | None, *, use_llm: bool = True,
            model_id: str | None = None) -> dict:
    channel = card["channel"]
    body, source, reasons = None, "template", []
    if use_llm and phraser is not None:
        try:
            draft = phraser(card)
            reasons = guard(draft, card, channel)
            if not reasons:
                body, source = draft, "model"
        except Exception as e:  # noqa: BLE001 - any Bedrock failure falls back to the template
            reasons = [f"model unavailable: {type(e).__name__}"]
            log.warning("bedrock phrasing failed: %s", type(e).__name__)
    if body is None:
        body = template(card)
        bad = guard(body, card, channel)
        if bad:   # a template that fails its own guard is a bug, fail loudly rather than send it
            raise RuntimeError(f"template for {card['message_type']}/{channel} failed the guard: {bad}")
    text = f"{body} {SAFETY_LINE}" if card["message_type"] in SAFETY_TYPES else body
    return {
        "engine_version": ENGINE_VERSION, "message_type": card["message_type"], "channel": channel, "text": text,
        "source": source, "model_id": model_id if source == "model" else None,
        "guard": {"checked": bool(use_llm and phraser is not None), "passed_model_draft": source == "model",
                  "reasons": reasons},
        "facts_used": {k: card[k] for k in FACT_KEYS if card[k] is not None},
        "note": "Times in this message are model estimates, not confirmed by the utility.",
    }
