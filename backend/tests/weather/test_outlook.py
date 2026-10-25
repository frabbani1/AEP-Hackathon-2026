"""The forecast-only outlook: places outside the outage data get a clearly labelled prediction and nothing more."""
import json
import urllib.error

import pytest

from concierge.api import handler
from concierge.api.service import BadRequest, Concierge, Unavailable
from concierge.weather import geocode, outlook
from concierge.weather.geocode import Geocoder
from concierge.weather.openmeteo import OpenMeteo
from concierge.zones import places

from .fakes import NOW, FakeOpenMeteo

COLUMBUS = {"name": "Columbus", "latitude": 39.96118, "longitude": -82.99879, "admin1": "Ohio",
            "postcodes": ["43085", "43201", "43215"]}
COLUMBUS_GA = {"name": "Columbus", "latitude": 32.46098, "longitude": -84.98771, "admin1": "Georgia",
               "postcodes": ["31901"]}


class FakeGeocoder:
    """Used as `Geocoder(fetch=...)`: returns the canned results, and records the URLs it was asked for."""

    def __init__(self, results=(COLUMBUS, COLUMBUS_GA), fail=None):
        self.results, self.fail, self.urls = list(results), fail, []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail
        return {"results": self.results} if self.results else {"generationtime_ms": 0.1}


@pytest.fixture()
def build(make_svc, rows, calibration):
    def make(fake_wx=None, geo=None, geocoder=True):
        svc = make_svc(fake_=fake_wx)
        svc.geocoder = Geocoder(fetch=geo or FakeGeocoder()) if geocoder else None
        return svc
    return make


# ---- parsing ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Columbus", ("city", "Columbus", None)),
    ("columbus, oh", ("city", "columbus", "Ohio")),
    ("Columbus OH", ("city", "Columbus", "Ohio")),
    ("Columbus, Ohio", ("city", "Columbus", "Ohio")),
    ("Salt Lake City, UT", ("city", "Salt Lake City", "Utah")),
    ("Charleston West Virginia", ("city", "Charleston", "West Virginia")),
    ("St. Louis", ("city", "St. Louis", None)),
    ("43215", ("zip", "43215", None)),
    ("43215-1234", ("zip", "43215", None)),
])
def test_parse_accepts_a_city_or_zip(text, expected):
    assert geocode.parse(text) == expected


@pytest.mark.parametrize("text", ["", "12 Main Street", "Columbus, Narnia", "4321", "x" * 61, "Col<umbus>", None, 5])
def test_parse_refuses_anything_else(text):
    assert geocode.parse(text) is None


def test_a_state_in_the_query_picks_that_state_not_the_biggest_match():
    geo = Geocoder(fetch=FakeGeocoder())
    assert geo.lookup("Columbus").name == "Columbus, Ohio"
    assert geo.lookup("Columbus, GA").name == "Columbus, Georgia"
    assert geo.lookup("Columbus, Texas") is None


def test_a_zip_must_belong_to_the_place_it_matched():
    geo = Geocoder(fetch=FakeGeocoder())
    assert geo.lookup("43215").name == "Columbus, Ohio"
    assert geo.lookup("99999") is None


def test_the_lookup_sends_only_the_typed_place(build):
    fake = FakeGeocoder()
    Geocoder(fetch=fake).lookup("Columbus, OH")
    assert len(fake.urls) == 1
    assert "name=Columbus" in fake.urls[0] and "countryCode=US" in fake.urls[0] and "OH" not in fake.urls[0]


def test_a_failed_lookup_raises_after_one_retry():
    fake = FakeGeocoder(fail=urllib.error.URLError("down"))
    with pytest.raises(geocode.GeocodeError):
        Geocoder(fetch=fake).lookup("Columbus")
    assert len(fake.urls) == 2


# ---- levels -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("gust,hazards,level", [
    (10, set(), "quiet"), (25, set(), "quiet"), (30, set(), "watch"), (46, set(), "elevated"), (58, set(), "high"),
    (10, {"thunderstorm"}, "watch"), (10, {"heavy_rain"}, "watch"),
    (10, {"ice"}, "elevated"), (10, {"heavy_snow"}, "elevated"),
    (60, {"thunderstorm"}, "high"), (None, set(), "quiet"),
])
def test_level_follows_gusts_and_hazards(gust, hazards, level):
    assert outlook.level_for(gust, hazards)[0] == level


# ---- the route ----------------------------------------------------------------------------------------------------
def test_outlook_is_prediction_only_and_has_no_outage_numbers(build):
    out = build(FakeOpenMeteo(gust=62.0)).handle("outlook", {"query": "Columbus, OH"})
    assert out["status"] == "ok" and out["source"] == "forecast_only" and out["label"] == "Prediction only"
    assert out["place"] == {"name": "Columbus, Ohio"}
    assert out["outlook"]["level"] == "high" and out["outlook"]["peak_gust_mph"] == 62.0
    assert out["not_available"] == ["Outage risk rating", "Restoration time estimate", "Alert eligibility"]
    for key in ("risk", "restoration", "alerts", "band", "confidence", "messages"):
        assert key not in out
    assert "Prediction only" in out["notice"] and "Columbus, Ohio" in out["notice"]


def test_a_calm_forecast_still_gets_basic_preparation(build):
    out = build(FakeOpenMeteo(gust=10.0)).handle("outlook", {"query": "Columbus"})
    assert out["outlook"]["level"] == "quiet" and "calm" in out["outlook"]["summary"]
    assert out["preparation"]["actions"] and any(s["id"] == "emergency" for s in out["preparation"]["safety"])
    assert {s["id"] for s in out["preparation"]["safety"]} >= {"safety_downed_lines", "safety_generator"}


def test_stronger_weather_adds_preparation_steps(build):
    calm = build(FakeOpenMeteo(gust=10.0)).handle("outlook", {"query": "Columbus"})
    storm = build(FakeOpenMeteo(gust=62.0)).handle("outlook", {"query": "Columbus"})
    assert len(storm["preparation"]["actions"]) > len(calm["preparation"]["actions"])


def test_hazard_text_does_not_mention_a_risk_rating(build):
    out = build(FakeOpenMeteo(gust=15.0, code=66)).handle("outlook", {"query": "Columbus"})
    assert out["outlook"]["level"] == "elevated"
    assert out["hazards"] and all("risk rating" not in h["text"] for h in out["hazards"])
    assert "modelled" not in out["hazards"][0]


def test_the_response_has_no_point_coordinates_and_asks_weather_for_a_snapped_grid(build):
    fake = FakeOpenMeteo(gust=20.0)
    out = build(fake).handle("outlook", {"query": "Columbus"})
    text = json.dumps(out)
    assert "39.96118" not in text and "82.99879" not in text and "grid_lat" not in text and "latitude" not in text
    assert "latitude=39.95" in fake.urls[0] and "longitude=-83.00" in fake.urls[0]


def test_the_outlook_has_a_hexagon_around_the_place_for_the_map(build):
    ring = build(FakeOpenMeteo(gust=20.0)).handle("outlook", {"query": "Columbus"})["area"]["ring"]
    assert len(ring) == 6 and all(len(p) == 2 for p in ring)
    lon_c, lat_c = sum(p[0] for p in ring) / 6, sum(p[1] for p in ring) / 6
    assert (round(lat_c, 2), round(lon_c, 2)) == (39.96, -83.0)                 # centred on the place, rounded to 0.01
    for lon, lat in ring:                                                       # about one outage-map area wide
        assert 3.0 <= places.km_between(lat_c, lon_c, lat, lon) <= 3.4
    assert places.contains(ring, 39.96118, -82.99879)                           # the place itself is inside the hex


def test_an_unknown_place_is_not_found_not_an_error(build):
    out = build(geo=FakeGeocoder(results=[])).handle("outlook", {"query": "Nowhereville"})
    assert out == {"status": "not_found", "source": "forecast_only"}


@pytest.mark.parametrize("query", ["12 Main Street", "", "x" * 61, 5, None])
def test_street_addresses_and_junk_are_refused(build, query):
    with pytest.raises(BadRequest):
        build().handle("outlook", {"query": query})


def test_unknown_fields_are_refused(build):
    with pytest.raises(BadRequest):
        build().handle("outlook", {"query": "Columbus", "token": "p_0123456789abcdef"})


def test_turned_off_or_unreachable_services_are_503s(build):
    with pytest.raises(Unavailable):
        build(geocoder=False).handle("outlook", {"query": "Columbus"})
    with pytest.raises(Unavailable):
        build(geo=FakeGeocoder(fail=urllib.error.URLError("x"))).handle("outlook", {"query": "Columbus"})
    with pytest.raises(Unavailable):
        build(FakeOpenMeteo(fail=urllib.error.URLError("x"))).handle("outlook", {"query": "Columbus"})


def test_outlook_needs_no_outage_areas(rows, calibration):
    from concierge.restoration.tree import RestorationTree

    from ..restoration.fixtures import LABOR, TREE_DICT
    svc = Concierge(rows, calibration, LABOR, tree=RestorationTree.from_dict(TREE_DICT), restoration_engine="v2",
                    weather=OpenMeteo(fetch=FakeOpenMeteo(gust=12.0), clock=lambda: NOW.timestamp()),
                    geocoder=Geocoder(fetch=FakeGeocoder()))
    assert svc.handle("outlook", {"query": "Columbus"})["status"] == "ok"


def test_the_handler_serves_the_route(build, monkeypatch):
    monkeypatch.setattr(handler, "_service", build(FakeOpenMeteo(gust=50.0)))
    event = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/api/outlook",
             "body": json.dumps({"query": "43215"})}
    res = handler.lambda_handler(event)
    body = json.loads(res["body"])
    assert res["statusCode"] == 200 and body["outlook"]["level"] == "elevated"
