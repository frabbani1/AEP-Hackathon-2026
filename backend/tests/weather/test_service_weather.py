"""Severity 'weather', as_of, and the zones / zones/assess / weather routes through the service layer."""
import json
import urllib.error

import pytest

from concierge.api.service import BadRequest, Unavailable
from concierge.risk.schema import _scan_strings
from concierge.weather import severity as sev

from .conftest import ANCHORS, TOKENS, ZONE_A, ZONE_B, ZONE_C
from .fakes import FakeOpenMeteo

STORM_NORTH = FakeOpenMeteo(gust=lambda la, lo, i: 62.0 if la > 37.2 else 12.0)      # only Area 1 is under the storm


def expected_multiplier(svc, gust):
    return sev.severity_from_gust(gust, ANCHORS, svc.engine.presets, m_min=svc.engine.cfg.severity_min,
                                  m_max=svc.engine.cfg.severity_max)["multiplier"]


def layer_by_name(out):
    return {z["name"]: z for z in out["zones"]}


# ---- token routes keep their scenario-only behaviour -----------------------------------------------------------------
@pytest.mark.parametrize("route,extra", [("risk", {}), ("restoration", {}), ("guidance", {}), ("alerts", {}),
                                         ("summary", {}), ("messages", {"message_type": "pre_storm"}),
                                         ("assistant", {"kind": "explanation"})])
def test_token_routes_say_weather_needs_a_map_area(svc, route, extra):
    with pytest.raises(BadRequest) as e:
        svc.handle(route, {"token": TOKENS[0], "severity": "weather", **extra})
    assert "needs a map area" in str(e.value)


def test_token_routes_do_not_accept_as_of(svc):
    with pytest.raises(BadRequest):
        svc.handle("risk", {"token": TOKENS[0], "as_of": "2025-02-16"})


# ---- /zones ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("severity", ["sep-2024-like", "typical", "feb-2025-like", "beyond-observed", 0.5, 2.2])
def test_scenario_layer_is_unchanged_and_matches_the_engine(svc, severity):
    out = svc.handle("zones", {"severity": severity})
    assert "weather" not in out and out["severity"]["preset"] != "weather"
    by_id = {z["id"]: z for z in out["zones"]}
    for rep, zid in ((TOKENS[0], ZONE_A), (TOKENS[2], ZONE_B)):
        r = svc.engine.score(rep, severity)
        assert (by_id[zid]["band"], by_id[zid]["confidence"]) == (r.band, r.confidence["level"])
    assert by_id[ZONE_C]["band"] is None


def test_weather_layer_uses_each_areas_own_gusts(make_svc):
    svc = make_svc(fake_=STORM_NORTH)
    out = svc.handle("zones", {"severity": "weather"})
    z = layer_by_name(out)
    feb = svc.engine.presets["feb-2025-like"]
    assert z["Area 1"]["multiplier"] > feb and z["Area 2"]["multiplier"] == 0.25
    assert z["Area 1"]["multiplier"] == pytest.approx(expected_multiplier(svc, 62.0), abs=1e-3)
    assert z["Area 1"]["peak_gust_mph"] == 62.0 and z["Area 2"]["peak_gust_mph"] == 12.0
    w = out["weather"]
    assert w["status"] == "ok" and w["peak_gust_mph_max"] == 62.0 and w["peak_gust_mph_min"] == 12.0
    assert w["areas_total"] == 3 and w["areas_without_weather"] == 0 and w["kind"] == "live"
    assert out["severity"]["preset"] == "weather" and out["source"] == "live"
    assert STORM_NORTH.calls == 1                                                    # one batched request for all areas


def test_weather_layer_bands_match_the_engine_at_each_areas_multiplier(make_svc):
    svc = make_svc(fake_=STORM_NORTH)
    z = layer_by_name(svc.handle("zones", {"severity": "weather"}))
    assert z["Area 1"]["band"] == svc.engine.score(TOKENS[0], z["Area 1"]["multiplier"]).band
    assert z["Area 2"]["band"] == svc.engine.score(TOKENS[2], z["Area 2"]["multiplier"]).band
    assert z["Area 3"]["band"] is None and z["Area 3"]["confidence"] is None


def test_stronger_weather_never_lowers_an_areas_multiplier(make_svc):
    ms = []
    for gust in (10, 40, 50, 55, 62, 90):
        out = make_svc(fake_=FakeOpenMeteo(gust=float(gust))).handle("zones", {"severity": "weather"})
        ms.append(layer_by_name(out)["Area 1"]["multiplier"])
    assert ms == sorted(ms) and ms[0] < ms[-1]


def test_weather_layer_flags_extrapolation(make_svc):
    out = make_svc(fake_=FakeOpenMeteo(gust=90.0)).handle("zones", {"severity": "weather"})
    assert out["severity"]["extrapolated"] is True
    calm = make_svc(fake_=FakeOpenMeteo(gust=10.0)).handle("zones", {"severity": "weather"})
    assert calm["severity"]["extrapolated"] is False and calm["severity"]["multiplier"] == 0.25


def test_weather_layer_falls_back_when_the_weather_fails_and_leaves_the_cache_alone(make_svc):
    svc = make_svc(fake_=FakeOpenMeteo(fail=urllib.error.URLError("x")))
    out = svc.handle("zones", {"severity": "weather"})
    assert out["weather"]["status"] == "unavailable" and out["severity"]["preset"] == "typical" and len(out["zones"]) == 3
    assert "weather" not in svc.handle("zones", {"severity": "typical"})           # the cached layer was not changed


def test_weather_layer_replays_a_past_date(svc):
    out = svc.handle("zones", {"severity": "weather", "as_of": "2025-02-16"})
    assert out["weather"]["kind"] == "historical" and out["weather"]["as_of"] == "2025-02-16"


def test_layer_has_no_tokens_or_member_lists(svc):
    for body in ({}, {"severity": "weather"}):
        blob = json.dumps(svc.handle("zones", body))
        assert not any(t in blob for t in TOKENS) and '"rep"' not in blob


@pytest.mark.parametrize("value", ["2025-2-16", "16/02/2025", "2025-02-30", "2026-10-25", "2027-01-01", "1900-01-01",
                                   20250216, True, ["2025-02-16"]])
def test_bad_as_of_is_rejected(svc, value):
    with pytest.raises(BadRequest):
        svc.handle("zones", {"severity": "weather", "as_of": value})


def test_as_of_without_weather_severity_is_rejected(svc):
    with pytest.raises(BadRequest) as e:
        svc.handle("zones", {"as_of": "2025-02-16"})
    assert "only works with severity 'weather'" in str(e.value)


def test_no_zones_file_is_unavailable(make_svc):
    for route, body in (("zones", {"severity": "weather"}), ("zones/assess", {"zone_id": ZONE_A}),
                        ("weather", {"zone_id": ZONE_A})):
        with pytest.raises(Unavailable):
            make_svc(zones=False).handle(route, body)


# ---- /zones/assess ---------------------------------------------------------------------------------------------------
def test_assess_with_weather_uses_the_forecast_gust(make_svc):
    svc = make_svc(fake_=STORM_NORTH)
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"})
    m = expected_multiplier(svc, 62.0)
    assert out["severity"]["preset"] == "weather" and out["severity"]["multiplier"] == round(m, 2)
    w = out["weather"]
    assert w["status"] == "ok" and w["severity"]["multiplier"] == m and w["severity"]["peak_gust_mph"] == 62.0
    assert len(w["hourly"]) == 72 and w["location"]["area"] == "Area 1"
    assert out["status"] == "ok" and out["risk"]["band"] == svc.engine.score(TOKENS[0], m).band


def test_assess_wording_does_not_say_the_scenario_was_chosen(svc):
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"})
    text = " ".join(out["transparency"]["assumptions"] + out["transparency"]["missing_data"]
                    + [f["text"] for f in out["risk"]["top_factors"]])
    assert "scenario you choose" not in text and "No weather data is used" not in text
    assert "weather data for your area" in text
    assert out["restoration"] and out["alerts"] and out["preparation"] and len(out["messages"]) == 4


def test_assess_never_returns_a_token(svc):
    blob = json.dumps(svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather",
                                                  "needs": {"medical_equipment": True}}))
    assert not any(t in blob for t in TOKENS)


def test_assess_text_is_code_built_and_has_no_weather_words(svc):
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"})
    texts = [m["body"] for m in out["messages"]] + [out["risk"]["explanation"]["text"]]
    assert all(m["origin"] == "template" for m in out["messages"])
    assert not any(w in t.lower() for t in texts for w in ("mph", "gust", "wind"))


def test_assess_without_weather_is_unchanged(svc):
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "feb-2025-like"})
    assert "weather" not in out and out["severity"]["preset"] == "feb-2025-like"


def test_assess_falls_back_when_the_weather_fails(make_svc):
    svc = make_svc(fake_=FakeOpenMeteo(fail=urllib.error.URLError("down")))
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"})
    assert out["weather"]["status"] == "unavailable" and "default typical-storm" in out["weather"]["notice"]
    assert out["severity"]["preset"] == "typical" and out["status"] == "ok"


def test_assess_with_weather_turned_off_falls_back(make_svc):
    out = make_svc(weather="off").handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"})
    assert out["weather"]["status"] == "unavailable" and out["severity"]["preset"] == "typical"


def test_assess_historical_replay(svc):
    out = svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather", "as_of": "2025-02-16"})
    assert out["weather"]["kind"] == "historical" and out["weather"]["as_of"] == "2025-02-16T00:00"


def test_assess_unrated_zone_still_shows_its_weather(svc):
    out = svc.handle("zones/assess", {"zone_id": ZONE_C, "severity": "weather"})
    assert out["status"] == "insufficient_data" and out["risk"] is None and out["weather"]["status"] == "ok"


def test_assess_output_passes_the_identifier_scan(svc):
    _scan_strings(svc.handle("zones/assess", {"zone_id": ZONE_A, "severity": "weather"}), "response")


# ---- /weather --------------------------------------------------------------------------------------------------------
def test_weather_route(svc):
    out = svc.handle("weather", {"zone_id": ZONE_A})
    assert out["status"] == "ok" and out["location"]["area"] == "Area 1" and len(out["hourly"]) == 72
    assert out["current"]["conditions"] == "Clear" and out["severity"]["source"] == "weather"
    assert out["attribution"].startswith("Weather data by Open-Meteo.com")


def test_weather_route_historical(svc):
    out = svc.handle("weather", {"zone_id": ZONE_B, "as_of": "2024-09-27"})
    assert out["kind"] == "historical" and out["window_48h"]["from"] == "2024-09-27T00:00"


def test_weather_route_is_503_when_the_source_is_down(make_svc):
    with pytest.raises(Unavailable):
        make_svc(fake_=FakeOpenMeteo(fail=urllib.error.URLError("x"))).handle("weather", {"zone_id": ZONE_A})


@pytest.mark.parametrize("body", [{}, {"zone_id": "nope"}, {"zone_id": "z_zzzzzzzzzzzz"}, {"token": TOKENS[0]},
                                  {"zone_id": ZONE_A, "severity": "weather"}])
def test_weather_route_rejects_bad_input(svc, body):
    with pytest.raises(BadRequest):
        svc.handle("weather", body)
