"""Rules table loading and the alert evaluator (tier, eligibility, timing, channels, escalation)."""
import copy

import pytest
from concierge.alerts.evaluate import decide, duration_bucket, evaluate, replay
from concierge.alerts.table import BANDS, DURATIONS, TableError, load_table, load_table_from
from hypothesis import given, settings
from hypothesis import strategies as st


def est(p50, p90=None):
    return {"p50_hours": p50, "p90_hours": p90 if p90 is not None else p50 * 2}


def run(table, band="Medium", p50=30, p90=55, needs=None, channels=None, **kw):
    estimate = None if p50 is None else est(p50, p90)
    return evaluate(table, band=band, insufficient_data=band is None, estimate=estimate, needs=needs or {},
                    channels=channels, **kw)


# ---- table (1.2) -------------------------------------------------------------------------------------------------
def test_every_cell_has_a_tier_and_reason(table):
    for band in BANDS:
        for dur in DURATIONS:
            cell = table.data["base_tier"][band][dur]
            assert cell["tier"] in table.tiers and cell["reason"]
    assert table.data["base_tier"]["Unrated"]["unknown"]["tier"] in table.tiers


def test_hash_is_stable_and_changes_with_content(table):
    assert table.hash == load_table().hash and table.hash.startswith("sha256:")
    changed = copy.deepcopy(table.data)
    changed["base_tier"]["Low"]["short"]["tier"] = "priority"
    assert load_table_from(changed).hash != table.hash


def test_missing_cell_is_rejected_with_its_name(table):
    broken = copy.deepcopy(table.data)
    del broken["base_tier"]["Medium"]["long"]
    with pytest.raises(TableError, match="Medium/long"):
        load_table_from(broken)


def test_bad_tier_and_missing_reason_are_rejected(table):
    bad = copy.deepcopy(table.data)
    bad["base_tier"]["High"]["short"]["tier"] = "gold"
    with pytest.raises(TableError, match="unknown tier"):
        load_table_from(bad)
    nore = copy.deepcopy(table.data)
    nore["escalation"]["E1"]["reason"] = ""
    with pytest.raises(TableError, match="reason"):
        load_table_from(nore)


def test_response_names_the_table(table):
    r = run(table).response
    assert r["rules_table_version"] == table.version and r["rules_table_hash"] == table.hash


# ---- tier ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("band", BANDS)
@pytest.mark.parametrize("dur,p50", [("short", 3), ("medium", 10), ("long", 30), ("extended", 80)])
def test_each_grid_cell_gives_its_table_tier(table, band, dur, p50):
    d = run(table, band=band, p50=p50, p90=p50 + 1)
    assert d.response["tier"] == table.data["base_tier"][band][dur]["tier"]
    assert f"T-{band}-{dur}" in d.rules_fired and f"B-{d.response['tier']}" in d.rules_fired


def test_unknown_duration_uses_the_unknown_column(table):
    d = run(table, band="Low", p50=None)
    assert d.response["tier"] == table.data["base_tier"]["Low"]["unknown"]["tier"]


def test_unrated_is_standard_and_general(table):
    d = run(table, band=None, p50=None)
    r = d.response
    assert r["tier"] == "standard" and "T-Unrated-unknown" in d.rules_fired
    assert r["eligibility"]["pre_storm"]["eligible"] and r["eligibility"]["pre_storm"]["rule"] == "K-pre-unrated"
    assert "can't rate" in r["eligibility"]["pre_storm"]["reason"]


def test_declared_need_raises_the_tier(table):
    base = run(table, band="Medium", p50=30)
    need = run(table, band="Medium", p50=30, needs={"mobility_limited": True})
    assert base.response["tier"] == "elevated" and need.response["tier"] == "priority"
    assert "N1" in need.rules_fired and "N1" not in base.rules_fired


def test_medical_need_floor_is_elevated(table):
    d = run(table, band="Low", p50=2, p90=3, needs={"medical_equipment": True})
    assert d.response["tier"] == "elevated" and "N2" in d.rules_fired and "N3" not in d.rules_fired


def test_medical_with_several_hours_is_priority(table):
    d = run(table, band="Low", p50=5, p90=8, needs={"medical_equipment": True})
    assert d.response["tier"] == "priority" and "N3" in d.rules_fired


def test_no_need_rule_fires_without_a_declared_need(table):
    for band in (*BANDS, None):
        for p50 in (None, 2, 10, 30, 90):
            d = run(table, band=band, p50=p50 if band else None)
            assert not {"N1", "N2", "N3", "E2", "E5"} & set(d.rules_fired)


@settings(max_examples=80, deadline=None)
@given(p50=st.integers(1, 200), need=st.booleans())
def test_property_tier_never_drops_as_band_rises(table, p50, need):
    ranks = [table.tier_rank(run(table, band=b, p50=p50, p90=p50 + 1, needs={"mobility_limited": need}).response["tier"])
             for b in BANDS]
    assert ranks == sorted(ranks)


@settings(max_examples=80, deadline=None)
@given(band=st.sampled_from(BANDS), need=st.booleans())
def test_property_tier_never_drops_as_duration_rises(table, band, need):
    ranks = [table.tier_rank(run(table, band=band, p50=p, p90=p + 1, needs={"mobility_limited": need}).response["tier"])
             for p in (3, 10, 30, 90)]
    assert ranks == sorted(ranks)


@settings(max_examples=80, deadline=None)
@given(band=st.sampled_from(BANDS), p50=st.integers(1, 200))
def test_property_adding_a_need_never_lowers_the_tier(table, band, p50):
    without = run(table, band=band, p50=p50, p90=p50 + 1).response["tier"]
    for need in ("medical_equipment", "mobility_limited", "electric_heat_or_cooling"):
        with_need = run(table, band=band, p50=p50, p90=p50 + 1, needs={need: True}).response["tier"]
        assert table.tier_rank(with_need) >= table.tier_rank(without)


# ---- eligibility ---------------------------------------------------------------------------------------------------
def test_low_risk_without_needs_gets_no_storm_alert_but_gets_outage_alert(table):
    r = run(table, band="Low", p50=3, p90=5).response
    assert not r["eligibility"]["pre_storm"]["eligible"] and r["qualifies"] is False
    assert "opt in" in r["eligibility"]["pre_storm"]["reason"]
    assert r["eligibility"]["outage_confirmed"]["eligible"] and r["eligibility"]["restoration_complete"]["eligible"]


def test_opt_in_makes_info_tier_eligible_for_storm_alerts(table):
    r = run(table, band="Low", p50=3, p90=5, opt_in=True).response
    assert r["eligibility"]["pre_storm"]["eligible"] and r["eligibility"]["pre_storm"]["rule"] == "K-pre-optin"
    assert r["qualifies"] is True


def test_small_estimate_change_is_not_worth_a_message(table):
    r = run(table, band="Medium", p50=11, p90=20, prior_typical_hours=10).response
    assert not r["eligibility"]["estimate_updated"]["eligible"]
    assert "not changed enough" in r["eligibility"]["estimate_updated"]["reason"]


def test_big_estimate_change_is_eligible_and_respects_the_gap(table):
    ok = run(table, band="Medium", p50=20, p90=30, prior_typical_hours=10, hours_since_last_update=10).response
    assert ok["eligibility"]["estimate_updated"]["eligible"]
    recent = run(table, band="Medium", p50=20, p90=30, prior_typical_hours=10, hours_since_last_update=1).response
    assert not recent["eligibility"]["estimate_updated"]["eligible"]
    assert "recently" in recent["eligibility"]["estimate_updated"]["reason"]


def test_no_prior_estimate_means_eligible_to_receive_updates(table):
    r = run(table).response
    assert r["eligibility"]["estimate_updated"]["eligible"]
    assert "meaningful" in r["eligibility"]["estimate_updated"]["reason"]


# ---- timing ----------------------------------------------------------------------------------------------------------
def test_lead_hours_come_from_the_tier(table):
    pri = run(table, band="High", p50=80, p90=100, needs={"medical_equipment": True}).response
    assert [x["hours_before_storm"] for x in pri["timing"]["pre_storm"]] == [72, 48, 24, 6]
    std = run(table, band="Medium", p50=3, p90=5).response
    assert [x["hours_before_storm"] for x in std["timing"]["pre_storm"]] == [24, 6]
    assert run(table, band="Low", p50=3, p90=5).response["timing"]["pre_storm"] == []


@pytest.mark.parametrize("h,expected", [
    (20, {48: "skipped", 24: "send_now", 6: "scheduled"}),
    (60, {48: "scheduled", 24: "scheduled", 6: "scheduled"}),
    (10, {48: "skipped", 24: "send_now", 6: "scheduled"}),
    (0, {48: "skipped", 24: "skipped", 6: "skipped"}),
])
def test_timing_statuses_follow_hours_until_storm(table, h, expected):
    r = run(table, band="Medium", p50=30, p90=40, hours_until_storm=h).response
    got = {x["hours_before_storm"]: x["status"] for x in r["timing"]["pre_storm"]}
    assert got == expected


def test_timing_without_hours_has_no_status(table):
    r = run(table, band="Medium", p50=30, p90=40).response
    assert all("status" not in x for x in r["timing"]["pre_storm"])


def test_moving_hours_until_storm_changes_the_output(table):
    a = run(table, band="Medium", p50=30, p90=40, hours_until_storm=60).response["timing"]
    b = run(table, band="Medium", p50=30, p90=40, hours_until_storm=10).response["timing"]
    assert a != b


def test_timing_values_are_validated(table):
    with pytest.raises(ValueError):
        run(table, hours_until_storm=500)
    with pytest.raises(ValueError):
        run(table, hours_until_storm=True)


# ---- channels ----------------------------------------------------------------------------------------------------------
def test_voice_first_at_priority(table):
    r = run(table, band="High", p50=80, p90=100, channels=["email", "sms", "voice"]).response
    assert r["tier"] == "priority" and r["recommended_channels"] == ["voice", "sms", "email"]


def test_only_enrolled_channels_are_used(table):
    r = run(table, band="Medium", p50=30, channels=["email"]).response
    assert r["recommended_channels"] == ["email"]


def test_no_enrolled_channel_means_app_only(table):
    r = run(table, band="Medium", p50=30, channels=[]).response
    assert r["recommended_channels"] == [] and "only appear in the app" in r["channel_note"]


def test_medical_without_voice_gets_a_recommendation_not_a_channel(table):
    r = run(table, band="Medium", p50=30, needs={"medical_equipment": True}, channels=["sms"]).response
    assert r["recommended_channels"] == ["sms"] and "voice channel is recommended" in r["channel_note"]


def test_suggested_channels_when_none_supplied(table):
    r = run(table, band="Medium", p50=30).response
    assert len(r["recommended_channels"]) == 2 and "suggest" in r["channel_note"]


def test_unknown_channel_is_rejected(table):
    with pytest.raises(ValueError, match="unknown channels"):
        run(table, channels=["pigeon"])


# ---- escalation ----------------------------------------------------------------------------------------------------------
def ids(d):
    return [e["rule"] for e in d.response["escalation"]]


def test_long_outage_with_medical_need(table):
    d = run(table, band="Medium", p50=30, p90=40, needs={"medical_equipment": True})
    assert {"E1", "E2", "E5"} <= set(ids(d))
    for e in d.response["escalation"]:
        assert e["action"] and e["reason"]


def test_each_escalation_trigger(table):
    assert ids(run(table, p50=24, p90=25)) == ["E1"]
    assert "E3" in ids(run(table, p50=40, p90=72))
    assert "E4" in ids(run(table, band="Medium", p50=4, p90=14))
    assert "E4" not in ids(run(table, band="Medium", p50=4, p90=12))
    assert "E2" in ids(run(table, band=None, p50=None, needs={"medical_equipment": True}))
    assert ids(run(table, p50=3, p90=4)) == []


def test_escalation_is_advisory(table):
    assert any("recommendations for a person" in a for a in run(table).response["assumptions"])


def test_assumptions_and_labels_are_always_present(table):
    r = run(table).response
    text = " ".join(r["assumptions"])
    assert "prototype assumptions, not utility policy" in text
    assert "Needs are only the ones you declared" in text
    assert r["tier_label"] == "prototype alert level" and "911" in r["emergency_notice"]


# ---- v1 field names still work until restoration-v2 lands -----------------------------------------------------------------
def test_v1_estimate_field_names_are_read(table):
    d = evaluate(table, band="Medium", insufficient_data=False,
                 estimate={"low_hours": 20, "typical_hours": 30, "high_hours": 90}, needs={}, channels=None)
    assert d.inputs["p50_hours"] == 30 and d.inputs["p90_hours"] == 90 and "E3" in ids(d)


# ---- replay (5.4) ------------------------------------------------------------------------------------------------------------
def test_inputs_reproduce_the_decision(table):
    d = run(table, band="Medium", p50=30, p90=70, needs={"medical_equipment": True}, channels=["sms", "voice"],
            prior_typical_hours=10, hours_until_storm=20, hours_since_last_update=9, opt_in=True)
    again = replay(table, d.inputs)
    assert again.response == d.response and again.rules_fired == d.rules_fired


def test_decide_is_deterministic(table):
    kw = dict(band="High", unrated=False, p50_hours=12, p90_hours=20, medical=False, any_need=True, channels=["push"])
    assert decide(table, **kw) == decide(table, **kw)


def test_buckets(table):
    assert [duration_bucket(table, x) for x in (None, 7, 8, 23, 24, 71, 72)] == \
        ["unknown", "short", "medium", "medium", "long", "long", "extended"]
