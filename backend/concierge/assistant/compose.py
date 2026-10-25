"""Orchestration (design D10): card -> sanitize -> cache -> model -> guard -> fill -> safety -> response.

The model is optional at every step. Whenever it is off, unavailable, throttled, or its draft fails the guard, the response
uses the code-built fallback, and says so in `source` and `guard.reasons`.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..guidance import after as lib_after
from ..guidance import during as lib_during
from ..messages import compose as legacy
from . import ENGINE_VERSION, fallbacks, factcard, guard, prompts, safety
from .cache import DraftCache, cache_key
from .factcard import CardRejected
from .phrase import Breaker, ModelUnavailable

log = logging.getLogger("concierge.assistant")

CONFIRMED_NOTE = "No confirmed utility information is connected to this prototype."
TIME_NOTE = "Times are model estimates, not confirmed by the utility."
Phraser = Callable[[str, str, str], str]      # (kind, system text, user text) -> raw model text


def select_items(kind: str, *, guidance: Mapping | None, typical_hours: int | None,
                 needs: Mapping | None) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], str | None]:
    """Code picks the items. Returns (core items the model may rephrase, need items appended verbatim, library version)."""
    if kind == "prep_before":
        items = (guidance or {}).get("items", [])
        core = [(i["id"], i["category"], i["text"]) for i in items if i["priority"] == 3]
        need = [(i["id"], i["category"], i["text"]) for i in items if i["priority"] == 2]
        return core, need, (guidance or {}).get("engine_version")
    if kind == "actions_during":
        return lib_during.select(typical_hours=typical_hours), lib_during.need_items(needs), lib_during.LIBRARY_VERSION
    if kind == "actions_after":
        return lib_after.select(), lib_after.need_items(needs), lib_after.LIBRARY_VERSION
    return [], [], None


class Assistant:
    def __init__(self, phraser: Phraser | None = None, model_id: str | None = None, *, cache: DraftCache | None = None,
                 breaker: Breaker | None = None, clock: Callable[[], float] = time.monotonic):
        self.phraser, self.model_id = phraser, model_id
        self.cache = cache if cache is not None else DraftCache(clock=clock)
        self.breaker = breaker if breaker is not None else getattr(phraser, "breaker", None)
        self.clock = clock

    def breaker_state(self) -> str | None:
        return self.breaker.state() if self.breaker is not None else None

    # ---- the one entry point ----------------------------------------------------------------------------------
    def respond(self, kind: str, *, card: Mapping, guidance: Mapping | None = None, typical_hours: int | None = None,
                needs: Mapping | None = None, use_llm: bool = True, known: bool = True) -> dict[str, Any]:
        started = self.clock()
        core, need_items, library_version = select_items(kind, guidance=guidance, typical_hours=typical_hours,
                                                         needs=needs)
        reasons: list[str] = []
        source, draft, cache_hit, key = "template", None, False, None
        projection: dict | None = None
        try:
            factcard.sanitize_card(card)
            projection = factcard.to_prompt(card)
            factcard.sanitize_card(projection, projection=True)
        except CardRejected:
            reasons.append("card failed sanitization")
            projection = None
        model_ok = bool(use_llm and self.phraser is not None and known and projection is not None)
        checked = False
        if model_ok:
            key = cache_key(kind=kind, card=card, selected_ids=[i for i, _, _ in core],
                            prompt_version=prompts.prompt_version(kind), model_id=self.model_id,
                            library_version=library_version)
            ids = [i for i, _, _ in core]
            cached = self.cache.get(key)
            if cached is not None:
                cache_hit, checked = True, True
                bad = guard.check(kind, cached, card, ids)
                if bad:      # cannot happen for a draft stored after it passed; never serve it if it does
                    reasons += bad
                    self.cache.discard(key)
                else:
                    draft, source = cached, "cache"
            else:
                try:
                    items_arg = [(i, t) for i, _, t in core] if kind in guard.ITEM_KINDS else None
                    raw = self.phraser(kind, prompts.system_text(kind), prompts.user_text(projection, items_arg))
                    parsed = guard.parse(raw, kind)
                    checked = True
                    bad = guard.check(kind, parsed, card, ids)
                    if bad:
                        reasons += bad
                    else:
                        self.cache.put(key, parsed)
                        draft, source = parsed, "model"
                except ModelUnavailable:
                    reasons.append("model unavailable")
                except guard.DraftRejected as e:
                    checked = True
                    reasons.append(str(e))
        elif use_llm and self.phraser is not None and not known:
            reasons.append("location not recognized")

        headline = (guidance or {}).get("headline")
        if draft is not None:
            body = guard.fill(kind, draft, card)
        else:
            body = fallbacks.build(kind, card, core, headline)
            self._check_fallback(kind, body, card)

        response = self._assemble(kind, card, body, need_items, source=source, reasons=reasons, checked=checked,
                                  cache_hit=cache_hit, key=key, guidance=guidance)
        log.info(json.dumps({"event": "assistant", "kind": kind, "source": source, "reasons": reasons,
                             "cache_hit": cache_hit, "latency_ms": round((self.clock() - started) * 1000)}))
        return response

    # ---- pieces -----------------------------------------------------------------------------------------------
    @staticmethod
    def _check_fallback(kind: str, body: Mapping, card: Mapping) -> None:
        parts = [body["text"]] if "text" in body else [body["intro"]] + [i["text"] for i in body["items"]]
        bad = guard.check_fallback(kind, parts, card)
        if kind.startswith("msg_"):
            old = fallbacks.legacy_card(kind, card)
            bad += legacy.guard(body["text"], old, card["channel"])
        if bad:       # a fallback that fails its own guard is a bug; fail loudly instead of sending it
            raise RuntimeError(f"fallback for {kind} failed the guard: {bad}")

    def _assemble(self, kind: str, card: Mapping, body: Mapping, need_items: Sequence[tuple[str, str, str]], *,
                  source: str, reasons: list[str], checked: bool, cache_hit: bool, key: str | None,
                  guidance: Mapping | None) -> dict[str, Any]:
        required = safety.required_for(kind)
        actions: list[dict] = []
        if kind in guard.ITEM_KINDS:
            actions = ([{"id": s["id"], "text": s["text"], "safety": True} for s in required]
                       + [{"id": i["id"], "text": i["text"], "safety": False} for i in body["items"]]
                       + [{"id": i, "text": t, "safety": False} for i, _, t in need_items])
            text = body["intro"]
            texts = [text] + [a["text"] for a in actions]
        else:
            text = body["text"]
            if required:
                text = " ".join([text] + [s["text"] for s in required])
            texts = [text]
        safety.assert_present(kind, texts)

        estimates = []
        if card.get("range"):
            estimates.append({"id": "range", "text": f"Restoration, if you lose power: {card['range']['display']}.",
                              "label": card["range"]["label"]})
        if card.get("prior_range"):
            estimates.append({"id": "prior_range", "text": f"Earlier estimate: {card['prior_range']['display']}.",
                              "label": card["range"]["label"] if card.get("range") else factcard.RANGE_LABEL})
        used_model = source in ("model", "cache")
        facts = {"band": card["band"], "confidence": (card.get("confidence") or {}).get("level"),
                 "scenario": factcard.SCENARIO_WORDS.get(card["scenario"]["preset"], factcard.SCENARIO_WORDS["custom"]),
                 "range": (card.get("range") or {}).get("display"),
                 "prior_range": (card.get("prior_range") or {}).get("display")}
        return {
            "engine_version": ENGINE_VERSION, "kind": kind, "stage": card["stage"],
            "sections": {"confirmed_facts": [], "confirmed_note": CONFIRMED_NOTE, "model_estimates": estimates,
                         "customer_actions": actions},
            "text": text,
            "safety": required,
            "source": source,
            "model_id": self.model_id if used_model else None,
            "prompt_version": prompts.prompt_version(kind), "prompt_hash": prompts.prompt_hash(kind),
            "guard": {"checked": checked, "passed": used_model, "reasons": list(reasons)},
            "cache": {"hit": cache_hit, "key": key},
            "facts_used": {k: v for k, v in facts.items() if v is not None},
            "note": TIME_NOTE if estimates else None,
        }
