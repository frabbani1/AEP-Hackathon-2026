import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from concierge.risk.schema import _scan_strings
from concierge.weather import openmeteo as om
from concierge.weather import severity as sev
from concierge.weather import summary as sm

from .fakes import FakeOpenMeteo, hourly, NOW

PRESETS = {"sep-2024-like": 0.267, "typical": 1.0, "feb-2025-like": 1.733, "beyond-observed": 2.5}
ANCHORS = {"storms": [{"label": "Sep 2024", "preset": "sep-2024-like", "peak_gust_mph": 47.9},
                      {"label": "Feb 2025", "preset": "feb-2025-like", "peak_gust_mph": 55.3}]}
POINTS = sev.points_from(ANCHORS, PRESETS)
M = dict(m_min=0.25, m_max=3.0)


def live_series(gust=15.0, **kw):
    fake = FakeOpenMeteo(gust=gust, **kw)
    return om.OpenMeteo(fetch=fake, clock=lambda: NOW.timestamp()).live([(37.3, -79.95)])[(37.3, -79.95)]


# ---- summary ---------------------------------------------------------------------------------------------------------
def test_live_split_puts_the_forecast_after_the_current_hour():
    # 20 mph in the past, 60 mph in the forecast: only the forecast may set the storm strength
    s = live_series(gust=lambda la, lo, i: 20.0 if i < 24 else 60.0)
    out = sm.summarize(s, "Area 1")
    assert out["recent_24h"]["peak_gust_mph"] == 20.0 and out["ahead_48h"]["peak_gust_mph"] == 60.0
    assert out["severity_window"]["peak_gust_mph"] == 60.0
    assert [h["forecast"] for h in out["hourly"]].count(True) == 48
    assert out["current"]["conditions"] == "Clear" and out["location"] == {"area": "Area 1", "grid_lat": 37.3, "grid_lon": -79.95}
    assert out["attribution"].startswith("Weather data by Open-Meteo.com") and out["kind"] == "live"


def test_a_past_gust_does_not_count_as_a_forecast():
    out = sm.summarize(live_series(gust=lambda la, lo, i: 70.0 if i < 24 else 10.0))
    assert out["severity_window"]["peak_gust_mph"] == 10.0 and out["recent_24h"]["peak_gust_mph"] == 70.0


def test_historical_window_is_the_whole_48_hours():
    fake = FakeOpenMeteo(gust=lambda la, lo, i: 55.5 if i == 30 else 12.0)
    s = om.OpenMeteo(fetch=fake, clock=lambda: NOW.timestamp()).historical([(37.3, -79.95)], __import__("datetime").date(2025, 2, 16))
    out = sm.summarize(s[(37.3, -79.95)])
    assert out["kind"] == "historical" and out["current"] is None and out["ahead_48h"] is None
    assert out["window_48h"]["peak_gust_mph"] == 55.5 and out["severity_window"]["peak_gust_mph"] == 55.5
    assert out["as_of"] == "2025-02-16T00:00"


@pytest.mark.parametrize("gust,ids", [(30, []), (45.9, []), (46, ["strong_wind"]), (57.9, ["strong_wind"]),
                                      (58, ["damaging_wind"])])
def test_wind_thresholds(gust, ids):
    hz = sm.summarize(live_series(gust=gust))["hazards"]
    assert [h["id"] for h in hz] == ids
    assert all(h["modelled"] for h in hz)


def test_other_hazards_are_flagged_but_not_modelled():
    hz = {h["id"]: h for h in sm.summarize(live_series(code=66))["hazards"]}
    assert hz["ice"]["modelled"] is False and "not part of the risk rating" in hz["ice"]["text"]
    assert "thunderstorm" in {h["id"] for h in sm.summarize(live_series(code=95))["hazards"]}
    assert "heavy_snow" in {h["id"] for h in sm.summarize(live_series(snow=0.2))["hazards"]}      # 48 x 0.2 in
    assert "heavy_rain" in {h["id"] for h in sm.summarize(live_series(precip=0.2))["hazards"]}     # 24 x 0.2 in


def test_wording_matches_the_tense():
    forecast = sm.summarize(live_series(gust=60))["hazards"][0]["text"]
    assert "forecast" in forecast and "recorded" not in forecast


def test_every_string_passes_the_identifier_scan():
    for kw in ({}, {"gust": 60, "code": 66, "snow": 0.2, "precip": 0.2}, {"code": 95}):
        _scan_strings(sm.summarize(live_series(**kw), "Area 12"), "weather")
    _scan_strings(sm.unavailable("The weather service did not answer.", "Area 1"), "weather")


def test_summary_is_json_serialisable():
    json.dumps(sm.summarize(live_series(gust=50), "Area 2"))


def test_describe_code():
    assert sm.describe_code(0) == "Clear" and sm.describe_code(95) == "Thunderstorm"
    assert sm.describe_code(None) is None and sm.describe_code(1234) == "Unsettled"


# ---- gust -> storm strength ------------------------------------------------------------------------------------------
def test_the_past_storms_map_to_their_own_multipliers():
    assert sev.multiplier_for_gust(47.9, POINTS, **M) == pytest.approx(0.267)
    assert sev.multiplier_for_gust(55.3, POINTS, **M) == pytest.approx(1.733)


def test_calm_days_get_the_mildest_scenario():
    assert sev.multiplier_for_gust(0, POINTS, **M) == 0.25
    assert sev.multiplier_for_gust(sev.CALM_GUST_MPH, POINTS, **M) == 0.25
    assert sev.multiplier_for_gust(30, POINTS, **M) == pytest.approx(0.25, abs=0.02)


def test_midway_between_the_storms_is_midway_in_strength():
    mid = (47.9 + 55.3) / 2
    assert sev.multiplier_for_gust(mid, POINTS, **M) == pytest.approx((0.267 + 1.733) / 2)


def test_stronger_than_any_past_storm_continues_the_line_and_is_capped():
    above = sev.multiplier_for_gust(58, POINTS, **M)
    assert above > 1.733
    assert sev.multiplier_for_gust(200, POINTS, **M) == 3.0


@settings(max_examples=200, deadline=None)
@given(st.floats(min_value=0, max_value=150, allow_nan=False), st.floats(min_value=0, max_value=150, allow_nan=False))
def test_stronger_gusts_never_give_a_weaker_scenario(a, b):
    lo, hi = sorted((a, b))
    ma, mb = (sev.multiplier_for_gust(g, POINTS, **M) for g in (lo, hi))
    assert 0.25 <= ma <= mb <= 3.0


def test_anchors_that_do_not_increase_are_refused():
    bad = {"storms": [{"label": "a", "preset": "sep-2024-like", "peak_gust_mph": 60.0},
                      {"label": "b", "preset": "feb-2025-like", "peak_gust_mph": 50.0}]}
    with pytest.raises(sev.AnchorError):
        sev.points_from(bad, PRESETS)
    with pytest.raises(sev.AnchorError):
        sev.points_from({"storms": []}, PRESETS)


def test_severity_block_explains_itself():
    out = sev.severity_from_gust(52.0, ANCHORS, PRESETS, **M)
    assert out["source"] == "weather" and out["peak_gust_mph"] == 52.0 and 0.267 < out["multiplier"] < 1.733
    assert [a["storm"] for a in out["anchors"]] == ["Sep 2024", "Feb 2025"]
    assert "assumption" in out["method"]
    _scan_strings(out, "severity")


def test_the_shipped_anchors_file_is_usable():
    a = sev.load_anchors()
    pts = sev.points_from(a, PRESETS)
    assert len(pts) == 2 and pts[0][0] < pts[1][0]
    assert {s["preset"] for s in a["storms"]} == {"sep-2024-like", "feb-2025-like"}
