"""The sixteen canonical templates (4 types x 4 channels) and their agreement with the message guard."""
import itertools
import re

import pytest
from concierge.messages import compose as msg
from concierge.messages.templates import CHANNEL_LIMITS, MESSAGE_TYPES, SAFETY_LINE, SAFETY_TYPES, render

EST = {"display": "about 6 to 14 hours"}


def card(mtype, channel, *, estimate=EST, prior="about 4 to 9 hours", can_rate=True, band="Medium", scenario="typical"):
    return msg.fact_card(message_type=mtype, channel=channel, band=band if can_rate else None, scenario=scenario,
                         estimate=estimate, prior_display=prior, confidence="Medium", has_need=False, can_rate=can_rate)


CASES = [dict(), dict(estimate=None), dict(can_rate=False, estimate=None), dict(prior=None), dict(band="High", scenario="beyond-observed")]


@pytest.mark.parametrize("mtype,channel", list(itertools.product(MESSAGE_TYPES, CHANNEL_LIMITS)))
def test_every_template_passes_the_guard_and_fits_the_channel(mtype, channel):
    for kw in CASES:
        c = card(mtype, channel, **kw)
        text = render(c)
        assert text.strip()
        assert msg.guard(text, c, channel) == [], (mtype, channel, kw, text)
        reserve = len(SAFETY_LINE) + 1 if mtype in SAFETY_TYPES else 0
        assert len(text) + reserve <= CHANNEL_LIMITS[channel]


@pytest.mark.parametrize("channel", CHANNEL_LIMITS)
def test_restoration_complete_has_no_digits_and_a_stay_away_line(channel):
    text = render(card("restoration_complete", channel))
    assert not re.search(r"\d", text)
    assert "downed" in text.lower() or channel == "push"


@pytest.mark.parametrize("channel", CHANNEL_LIMITS)
def test_times_are_called_estimates_and_never_promised(channel):
    for mtype in ("pre_storm", "outage_confirmed", "estimate_updated"):
        text = render(card(mtype, channel))
        assert "estimate" in text.lower()
        assert not re.search(r"guarantee|promise", text, re.I)


@pytest.mark.parametrize("channel", CHANNEL_LIMITS)
def test_pre_storm_time_is_conditional_and_unrated_says_prepare(channel):
    assert re.search(r"\bif\b", render(card("pre_storm", channel)), re.I)
    unrated = render(card("pre_storm", channel, can_rate=False, estimate=None))
    assert "outage is possible" in unrated or "possible outage" in unrated


@pytest.mark.parametrize("mtype,channel", list(itertools.product(MESSAGE_TYPES, CHANNEL_LIMITS)))
def test_no_links_names_phone_numbers_or_internal_terms(mtype, channel):
    text = render(card(mtype, channel))
    assert not re.search(r"https?://|www\.|@|\bcircuit\b|\btransformer\b|\d{3}[-. ]\d{4}", text, re.I)


def test_compose_without_a_model_uses_templates_and_appends_the_safety_line():
    c = card("pre_storm", "sms")
    out = msg.compose(c, None, use_llm=False)
    assert out["source"] == "template" and out["text"].endswith(SAFETY_LINE)
    done = msg.compose(card("restoration_complete", "sms"), None, use_llm=False)
    assert SAFETY_LINE not in done["text"]


def test_bad_channel_and_type_are_rejected():
    with pytest.raises(ValueError):
        render({"message_type": "pre_storm", "channel": "pigeon"})
    with pytest.raises(ValueError):
        render({"message_type": "gossip", "channel": "sms"})


def test_compose_template_function_still_works():
    assert msg.template(card("outage_confirmed", "email")) == render(card("outage_confirmed", "email"))
