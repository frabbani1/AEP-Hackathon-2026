"""Code-built fallbacks for all eight kinds (design D2). Used when the model is off, fails, or its draft fails the guard.

Message fallbacks are the spec-05 templates (messages/templates.py), so `/messages` and `/assistant` say the same thing.
Every fallback is checked by `compose.py` before it can reach a customer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..messages import compose as legacy
from ..messages import templates

DEFAULT_INTRO = {
    "actions_during": "Here is what to do while your power is out.",
    "actions_after": "Power is back. Here is what to do next.",
}
INSUFFICIENT = "We can't rate your outage risk, so prepare as if an outage is possible."
RANGE_SENTENCE = "If you lose power, our model estimates restoration could take {range}. This is an estimate, not a confirmed time."


def legacy_card(kind: str, card: Mapping) -> dict:
    """The older four-message fact card (messages/compose.py) for the same situation."""
    rng = card.get("range")
    prior = card.get("prior_range")
    return legacy.fact_card(
        message_type=kind[len("msg_"):], channel=card["channel"], band=card["band"], scenario=card["scenario"]["preset"],
        estimate={"display": rng["display"]} if rng else None, prior_display=prior["display"] if prior else None,
        confidence=(card.get("confidence") or {}).get("level"), has_need=card["has_declared_need"],
        can_rate=card["status"] == "ok")


def explanation(card: Mapping) -> str:
    if card["status"] != "ok":
        return card.get("fallback_message") or INSUFFICIENT
    parts = [f"Your area has a {card['band_word']} risk of losing power in this scenario."]
    parts += [f["text"] for f in card["top_factors"]]
    conf = card.get("confidence")
    if conf:
        parts.append(f"Confidence in this rating is {conf['level'].lower()}. {conf['reason']}")
    if card.get("range"):
        parts.append(RANGE_SENTENCE.format(range=card["range"]["display"]))
    if card.get("could_change"):
        parts.append("Things that could change this answer:")
        parts.extend(card["could_change"])
    return " ".join(parts)


def intro(kind: str, card: Mapping, headline: str | None) -> str:
    if kind == "prep_before":
        return headline or (INSUFFICIENT if card["status"] != "ok" else "Here is how to get ready.")
    text = DEFAULT_INTRO[kind]
    if kind == "actions_during" and card.get("range"):
        text += " " + RANGE_SENTENCE.format(range=card["range"]["display"])
    return text


def message(kind: str, card: Mapping) -> str:
    return templates.render(legacy_card(kind, card))


def build(kind: str, card: Mapping, core_items: Sequence[tuple[str, str, str]], headline: str | None = None) -> dict:
    """{"text": ...} for text kinds, {"intro": ..., "items": [{id, text}]} for item kinds."""
    if kind == "explanation":
        return {"text": explanation(card)}
    if kind.startswith("msg_"):
        return {"text": message(kind, card)}
    return {"intro": intro(kind, card, headline), "items": [{"id": i, "text": t} for i, _, t in core_items]}
