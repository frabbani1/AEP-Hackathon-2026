"""Output guard (design D3). Every rule must pass or the response uses the code-built fallback.

The model writes no number. It writes `{{range}}` and `{{prior_range}}`; `fill()` replaces them from the full card after the
guard passes, which is why one draft is valid for every customer in a cache bucket.

Reasons returned here are fixed phrases. They never quote the draft, so they are safe to log and to return.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..messages.compose import _BAD_PATTERNS, _WORDS   # same bad-pattern and number-word lists as the message guard
from ..messages.templates import CHANNEL_LIMITS
from . import safety

PLACEHOLDER = re.compile(r"\{\{(range|prior_range)\}\}")
_DIGIT = re.compile(r"\d")
_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)|https?:|www\.", re.I)
_META = re.compile(r"\bignore\b|system prompt|as an ai\b|language model|\bi cannot\b|\bi can't\b|\bi am unable\b|"
                   r"\binstructions?\b|\bprompt\b|\bjson\b|\bplaceholder\b", re.I)
_PROMISE = re.compile(r"\bwill be (?:restored|back|on)\b|\bby (?:tonight|tomorrow|morning|noon)\b", re.I)
_ESTIMATE = re.compile(r"\bestimat(?:e|ed|es)\b", re.I)
_IF = re.compile(r"\bif\b", re.I)
_MARKUP = re.compile(r"<[^>]*>|[*`#]|^\s*>\s?", re.M)
_NUMBER_WORDS = set(_WORDS)

MAX_AVG_SENTENCE_WORDS = 20
CAPS = {"text": 700, "intro": 300, "item": 240}
TEXT_KINDS = ("explanation", "msg_pre_storm", "msg_outage_confirmed", "msg_estimate_updated", "msg_restoration_complete")
ITEM_KINDS = ("prep_before", "actions_during", "actions_after")
# Placeholders each kind may use. A kind that is not listed may use none.
ALLOWED = {"explanation": {"range"}, "prep_before": {"range"}, "actions_during": {"range"},
           "msg_pre_storm": {"range"}, "msg_outage_confirmed": {"range"},
           "msg_estimate_updated": {"range", "prior_range"}}
# Kinds where a card with a range must be used in the text.
REQUIRED = {"msg_pre_storm": {"range"}, "msg_outage_confirmed": {"range"}, "msg_estimate_updated": {"range"}}


class DraftRejected(ValueError):
    """The model output is not usable. The message is a fixed phrase."""


def strip_markup(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _MARKUP.sub("", text)).strip()


def parse(raw: Any, kind: str) -> dict[str, Any]:
    """Strict parse: JSON object, exactly the expected keys and types, markup stripped. Raises DraftRejected."""
    if not isinstance(raw, str) or not raw.strip():
        raise DraftRejected("empty output")
    body = raw.strip()
    if body.startswith("```"):      # a single code fence around the JSON is only framing
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body).strip()
    try:
        obj = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise DraftRejected("output is not JSON") from None
    if not isinstance(obj, dict):
        raise DraftRejected("output is not a JSON object")
    if kind in TEXT_KINDS:
        if set(obj) != {"text"} or not isinstance(obj["text"], str):
            raise DraftRejected("output keys are not the expected ones")
        return {"text": obj["text"]}
    if set(obj) != {"intro", "items"} or not isinstance(obj["intro"], str) or not isinstance(obj["items"], list):
        raise DraftRejected("output keys are not the expected ones")
    items = []
    for it in obj["items"]:
        if (not isinstance(it, dict) or set(it) != {"id", "text"} or not isinstance(it["id"], str)
                or not isinstance(it["text"], str)):
            raise DraftRejected("an item is not {id, text}")
        items.append({"id": it["id"], "text": it["text"]})
    return {"intro": obj["intro"], "items": items}


def _texts(kind: str, draft: Mapping) -> list[tuple[str, str]]:
    if kind in TEXT_KINDS:
        return [("text", draft["text"])]
    return [("intro", draft["intro"])] + [("item", it["text"]) for it in draft["items"]]


def fill_text(text: str, card: Mapping) -> str:
    def repl(m: re.Match) -> str:
        entry = card.get(m.group(1))      # "range" or "prior_range"; a placeholder with no value is left as it is
        return entry["display"] if entry else m.group(0)
    return PLACEHOLDER.sub(repl, text)


def fill(kind: str, draft: Mapping, card: Mapping) -> dict[str, Any]:
    """Replace placeholders from the full card. Call only on a draft that passed `check`."""
    if kind in TEXT_KINDS:
        return {"text": strip_markup(fill_text(draft["text"], card))}
    return {"intro": strip_markup(fill_text(draft["intro"], card)),
            "items": [{"id": it["id"], "text": strip_markup(fill_text(it["text"], card))} for it in draft["items"]]}


def _sentence_avg(text: str) -> float:
    parts = [p for p in re.split(r"[.!?]+", text) if p.strip()]
    if not parts:
        return 0.0
    return sum(len(p.split()) for p in parts) / len(parts)


def _length_limit(kind: str, card: Mapping, field: str) -> int:
    if kind.startswith("msg_"):
        reserve = len(safety.DOWNED_LINES_SHORT) + 1 if safety.PLACEMENT[kind] else 0
        return CHANNEL_LIMITS[card["channel"]] - reserve
    return CAPS[field]


def check(kind: str, draft: Mapping, card: Mapping, selected_ids: Sequence[str] | None = None) -> list[str]:
    """Return the reasons the draft is not allowed (empty list = pass). `draft` is already parsed (see `parse`)."""
    reasons: list[str] = []

    def add(r: str) -> None:
        if r not in reasons:
            reasons.append(r)

    texts = _texts(kind, draft)
    allowed = ALLOWED.get(kind, set())
    have = {"range": bool(card.get("range")), "prior_range": bool(card.get("prior_range"))}
    used: set[str] = set()

    for field, raw in texts:
        text = raw
        if _LINK.search(text):
            add("contains a link")
        text = strip_markup(text)
        found = {m.group(1) for m in PLACEHOLDER.finditer(text)}
        used |= found
        for name in found:
            if name not in allowed or not have[name]:
                add("uses a placeholder that is not allowed here")
        bare = PLACEHOLDER.sub(" ", text)
        if "{" in bare or "}" in bare:
            add("contains braces")
        if _DIGIT.search(bare):
            add("contains a digit")
        for w in re.findall(r"[a-z]+", bare.lower()):
            if w in _NUMBER_WORDS:
                add("contains a spelled-out number")
                break
        for rx, label in _BAD_PATTERNS:
            if rx.search(bare):
                add(f"contains a {label}")
        if _PROMISE.search(bare):
            add("contains a promise")
        if safety.SAFETY_TOPIC.search(bare):
            add("writes about a safety topic that only code may write")
        if _META.search(bare):
            add("contains a meta phrase")
        if not text.strip():
            add("empty text")
        if _sentence_avg(bare) > MAX_AVG_SENTENCE_WORDS:
            add("sentences are too long")
        if len(fill_text(text, card)) > _length_limit(kind, card, field):
            add("too long")

    required = {n for n in REQUIRED.get(kind, set()) if have[n]}
    if kind == "msg_estimate_updated" and have["prior_range"]:
        required.add("prior_range")
    if required - used:
        add("a required time placeholder is missing")

    if used:      # any stated time must be called an estimate; a pre-storm time must be conditional
        whole = " ".join(t for _, t in texts)
        if not _ESTIMATE.search(whole):
            add("does not say the time is an estimate")
        if kind == "msg_pre_storm" and not _IF.search(whole):
            add("pre-storm time must be conditional on losing power")

    if kind in ITEM_KINDS:
        got = [it["id"] for it in draft["items"]]
        want = list(selected_ids or [])
        if got != want:
            add("items are not exactly the selected items in order")
        if any(not it["text"].strip() for it in draft["items"]):
            add("empty text")
    return reasons


def check_fallback(kind: str, text_parts: Sequence[str], card: Mapping) -> list[str]:
    """Checks for code-built fallback text. Digits are allowed only if the full card has them (range, confidence reason)."""
    reasons: list[str] = []
    card_numbers = set(re.findall(r"\d+", json.dumps(card)))
    for part in text_parts:
        for n in re.findall(r"\d+", part):
            if n not in card_numbers and n != "911":
                reasons.append("fallback has a number that is not in the facts")
        for rx, label in _BAD_PATTERNS:
            if rx.search(part):
                reasons.append(f"fallback contains a {label}")
        if _LINK.search(part):
            reasons.append("fallback contains a link")
    return list(dict.fromkeys(reasons))
