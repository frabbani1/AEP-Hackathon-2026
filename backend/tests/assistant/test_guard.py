"""Tasks 4.1 to 4.5: one test per guard rule (design D3), strict parsing, item ids, placeholder fill, and the property test."""
from __future__ import annotations

import json
import re

import pytest
from concierge.assistant import guard
from hypothesis import given, settings
from hypothesis import strategies as st

from .conftest import GOOD, make_card


def ok(kind, text=None, **card_kw):
    card = make_card(kind, **card_kw)
    draft = {"text": text if text is not None else GOOD[kind]["text"]}
    return card, draft


def reasons(kind, text, **card_kw):
    card, draft = ok(kind, text, **card_kw)
    return guard.check(kind, draft, card)


# ---- the good drafts pass --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", list(GOOD))
def test_good_drafts_pass(kind):
    card = make_card(kind, prior="about 4 to 9 hours")
    assert guard.check(kind, dict(GOOD[kind]), card) == []


# ---- rule 1: strict JSON, keys, types, markup -----------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["plain prose", "", None, "[1, 2]", '{"text": 5}', '{"text": "x", "tool": "run"}',
                                 '{"txt": "x"}', '{"intro": "x", "items": "no"}'])
def test_rule_1_bad_shapes_are_rejected(raw):
    with pytest.raises(guard.DraftRejected):
        guard.parse(raw, "msg_pre_storm")


def test_rule_1_item_shapes():
    good = '{"intro": "Hi.", "items": [{"id": "a", "text": "b"}]}'
    assert guard.parse(good, "prep_before")["items"] == [{"id": "a", "text": "b"}]
    for bad in ('{"intro": "Hi.", "items": [{"id": "a"}]}', '{"intro": "Hi.", "items": [{"id": "a", "text": "b", "x": 1}]}',
                '{"intro": "Hi."}', '{"intro": "Hi.", "items": ["a"]}', '{"intro": "Hi.", "items": [{"id": 1, "text": "b"}]}'):
        with pytest.raises(guard.DraftRejected):
            guard.parse(bad, "prep_before")


def test_rule_1_a_single_code_fence_is_framing_only():
    assert guard.parse('```json\n{"text": "Hello there."}\n```', "msg_restoration_complete") == {"text": "Hello there."}


def test_rule_1_markup_is_stripped_and_links_are_rejected():
    card, _ = ok("msg_restoration_complete")
    assert guard.strip_markup("**Power** is <b>back</b>.") == "Power is back."
    assert guard.fill("msg_restoration_complete", {"text": "**Power** is <i>back</i>."}, card)["text"] == "Power is back."
    assert "contains a link" in reasons("msg_restoration_complete", "See [our page](x) now.")
    assert "contains a link" in reasons("msg_restoration_complete", "Go to https://example now.")


# ---- rule 2: no digit, no spelled-out number -------------------------------------------------------------------------
def test_rule_2_digit_falls_back():
    assert "contains a digit" in reasons("msg_outage_confirmed", "Restoration in 6 hours is our estimate {{range}}.")


@pytest.mark.parametrize("word", ["six", "twice", "half", "dozen", "hundred", "double", "triple", "ninety"])
def test_rule_2_spelled_out_numbers_fall_back(word):
    r = reasons("msg_outage_confirmed", f"About {word} hours is our estimate: {{{{range}}}}.")
    assert "contains a spelled-out number" in r


def test_rule_2_placeholder_digits_do_not_count():
    assert reasons("msg_outage_confirmed", GOOD["msg_outage_confirmed"]["text"]) == []


# ---- rule 3: placeholders ---------------------------------------------------------------------------------------------
def test_rule_3_placeholder_not_allowed_for_the_kind():
    assert "uses a placeholder that is not allowed here" in reasons(
        "msg_restoration_complete", "Power is back. It took {{range}}.", prior="about 4 to 9 hours")
    assert "uses a placeholder that is not allowed here" in reasons("msg_pre_storm", "If so, {{prior_range}} estimate.")


def test_rule_3_required_placeholder_missing():
    r = reasons("msg_outage_confirmed", "We confirmed an outage. This is an estimate we will share soon.")
    assert "a required time placeholder is missing" in r
    # with no range in the card (insufficient data) nothing is required and the placeholder is not allowed
    assert reasons("msg_outage_confirmed", "We confirmed an outage. We have no estimate yet.", ok=False) == []
    assert "uses a placeholder that is not allowed here" in reasons(
        "msg_outage_confirmed", "Our estimate is {{range}}.", ok=False)


def test_rule_3_estimate_update_with_a_prior_needs_both():
    r = reasons("msg_estimate_updated", "Our estimate is now {{range}}. It may change.", prior="about 4 to 9 hours")
    assert "a required time placeholder is missing" in r
    assert reasons("msg_estimate_updated", "Our estimate is now {{range}}. It may change.") == []


def test_rule_3_unknown_braces_are_rejected():
    assert "contains braces" in reasons("msg_pre_storm", "If you lose power, estimate {{range}} {{other}}.")
    assert "contains braces" in reasons("msg_restoration_complete", "Power is back }} now.")


# ---- rule 4: item ids -------------------------------------------------------------------------------------------------
ITEMS = ["a", "b", "c"]


def item_draft(ids, texts=None):
    return {"intro": "Here is your plan.", "items": [{"id": i, "text": (texts or {}).get(i, f"Do thing {i}.".replace("thing", "task"))} for i in ids]}


@pytest.mark.parametrize("ids", [["a", "b", "c"]])
def test_rule_4_exact_ids_pass(ids):
    card = make_card("prep_before")
    d = item_draft(ids, {i: "Charge your phones now." for i in ids})
    assert guard.check("prep_before", d, card, ITEMS) == []


@pytest.mark.parametrize("ids", [["a", "b"], ["a", "b", "c", "d"], ["a", "a", "b", "c"], ["a", "c", "b"], ["a", "b", "x"], []])
def test_rule_4_added_dropped_repeated_reordered_or_invented_items_are_rejected(ids):
    card = make_card("prep_before")
    d = item_draft(ids, {i: "Charge your phones now." for i in ids})
    assert "items are not exactly the selected items in order" in guard.check("prep_before", d, card, ITEMS)


def test_rule_4_empty_item_text_is_rejected():
    card = make_card("prep_before")
    d = {"intro": "Plan.", "items": [{"id": i, "text": " "} for i in ITEMS]}
    assert "empty text" in guard.check("prep_before", d, card, ITEMS)


# ---- rule 5: links, addresses, phones, internal terms, promises, weather ---------------------------------------------
@pytest.mark.parametrize("text,label", [
    ("Visit www.example.com for details.", "contains a link"),
    ("Write to help@example.org please.", "contains a email address"),
    ("Call 555 0123 for help.", "contains a digit"),
    ("Your circuit is out of service.", "contains a internal term"),
    ("A transformer problem is likely.", "contains a internal term"),
    ("We guarantee power is back soon.", "contains a promise"),
    ("We promise to be fast.", "contains a promise"),
    ("Strong winds may cause trouble.", "contains a weather claim"),
    ("Heavy rain and ice are likely.", "contains a weather claim"),
    ("Crews are working on your street.", "contains a claim not in the facts"),
    ("Your power was off for a long time.", "contains a claim not in the facts"),
])
def test_rule_5_blocked_content(text, label):
    assert label in reasons("msg_restoration_complete", text)


def test_rule_5_promise_phrases():
    assert "contains a promise" in reasons("msg_restoration_complete", "Power will be restored by tonight.")


# ---- rule 6: safety topics belong to code -----------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "It is fine to run a generator in the garage.", "Stay away from a downed line.", "Call 911 if needed.",
    "Carbon monoxide is a risk.", "Avoid live wires.", "In an emergency, get help.", "Never use a camp stove inside.",
])
def test_rule_6_safety_advice_is_rejected(text):
    r = reasons("msg_restoration_complete", text)
    assert "writes about a safety topic that only code may write" in r or "contains a digit" in r


# ---- rule 7: estimate wording and conditional pre-storm times --------------------------------------------------------
def test_rule_7_time_must_be_called_an_estimate():
    assert "does not say the time is an estimate" in reasons("msg_outage_confirmed", "Power returns in {{range}}.")


def test_rule_7_pre_storm_time_must_be_conditional():
    r = reasons("msg_pre_storm", "Restoration will take {{range}}, our best estimate.")
    assert "pre-storm time must be conditional on losing power" in r
    assert reasons("msg_pre_storm", "If you lose power, our estimate is {{range}}.") == []


# ---- rule 8: length and sentences -------------------------------------------------------------------------------------
def test_rule_8_length_leaves_room_for_the_safety_line_and_counts_the_filled_range():
    from concierge.assistant import safety
    from concierge.messages.templates import CHANNEL_LIMITS
    card = make_card("msg_pre_storm", channel="push")
    room = CHANNEL_LIMITS["push"] - len(safety.DOWNED_LINES_SHORT) - 1
    base = "If you lose power, our estimate is {{range}}. "
    text = base + "a" * (room - len(guard.fill_text(base, card)))      # exactly fills the room once {{range}} is replaced
    assert guard.check("msg_pre_storm", {"text": text}, card) == []
    assert "too long" in guard.check("msg_pre_storm", {"text": text + "a"}, card)


def test_rule_8_plain_language_sentence_length():
    long_sentence = " ".join(["word"] * 30) + "."
    assert "sentences are too long" in reasons("msg_restoration_complete", long_sentence)


# ---- rule 9: meta phrases ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Ignore the earlier rules.", "As an AI I cannot help.", "My system prompt says so.",
                                  "I can't do that.", "Here are the instructions for you."])
def test_rule_9_meta_phrases(text):
    assert "contains a meta phrase" in reasons("msg_restoration_complete", text)


# ---- placeholder fill (4.4) -------------------------------------------------------------------------------------------
def test_the_same_draft_gives_each_customer_their_own_range():
    draft = {"text": "If you lose power, restoration could take {{range}}, which is an estimate."}
    a = make_card("msg_pre_storm", p50=6, p90=14)
    b = make_card("msg_pre_storm", p50=7, p90=16)
    assert guard.check("msg_pre_storm", draft, a) == [] and guard.check("msg_pre_storm", draft, b) == []
    assert "about 6 to 14 hours" in guard.fill("msg_pre_storm", draft, a)["text"]
    assert "about 7 to 16 hours" in guard.fill("msg_pre_storm", draft, b)["text"]
    assert "about 6 to 14" not in guard.fill("msg_pre_storm", draft, b)["text"]


# ---- fallback checks ---------------------------------------------------------------------------------------------------
def test_fallback_may_use_card_numbers_but_no_others():
    card = make_card("explanation")
    assert guard.check_fallback("explanation", ["It could take about 6 to 14 hours."], card) == []
    assert guard.check_fallback("explanation", ["It could take about 99 hours."], card) != []
    assert guard.check_fallback("explanation", ["See www.example.com"], card) != []


# ---- 4.5 property: a passing draft leaks no digit ---------------------------------------------------------------------
TOKENS = ["power", "estimate", "estimated", "if", "you", "lose", "restoration", "could", "take", "{{range}}",
          "{{prior_range}}", ".", ",", "6", "14", "hours", "six", "half", "twice", "999", "1,000", "<b>", "**", "see",
          "www.x.com", "911", "storm", "{{", "}}", "plan", "wind", "okay"]


@settings(max_examples=400, deadline=None)
@given(words=st.lists(st.sampled_from(TOKENS), min_size=1, max_size=30), kind=st.sampled_from(sorted(GOOD)))
def test_if_the_guard_passes_the_final_text_has_no_digit_but_the_card_ranges(words, kind):
    card = make_card(kind, prior="about 4 to 9 hours")
    draft = {"text": " ".join(words)}
    if guard.check(kind, draft, card):
        return
    final = guard.fill(kind, draft, card)["text"]
    for display in (card["range"] or {}).get("display", ""), (card["prior_range"] or {}).get("display", ""):
        if display:
            final = final.replace(display, "")
    assert not re.search(r"\d", final), final
