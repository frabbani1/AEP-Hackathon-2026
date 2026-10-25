import urllib.error
from datetime import date

import pytest

from concierge.weather import openmeteo as om

from .fakes import NOW, FakeOpenMeteo, _query


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(om.time, "sleep", lambda s: None)


class Tick:
    def __init__(self):
        self.t = NOW.timestamp()

    def __call__(self):
        return self.t


def client(fake=None, tick=None, ttl=900):
    fake = fake or FakeOpenMeteo()
    return om.OpenMeteo(fetch=fake, clock=tick or Tick(), live_ttl=ttl), fake


def test_snap_rounds_to_the_grid():
    assert om.snap(37.2817) == 37.3 and om.snap(-79.9766) == -80.0 and om.snap(37.02) == 37.0
    assert om.snap_point(37.2817, -79.9566) == (37.3, -79.95)


def test_live_parses_current_and_hours():
    c, fake = client()
    s = c.live([(37.28, -79.97)])[(37.3, -79.95)]
    assert s.kind == "live" and len(s.hours) == 72
    assert s.current.time == "2026-10-25T00:45" and s.current.gust_mph == 5.1 and s.current.temp_f == 48.9
    assert s.hours[0].gust_mph == 15.0 and s.fetched_at == "2026-10-25T00:50Z"


def test_request_sends_only_rounded_coordinates_and_units():
    c, fake = client()
    c.live([(37.2817, -79.9566)])
    q = _query(fake.urls[0])
    assert q["latitude"] == "37.30" and q["longitude"] == "-79.95"
    assert q["wind_speed_unit"] == "mph" and q["temperature_unit"] == "fahrenheit" and q["timezone"] == "GMT"
    assert q["past_hours"] == "24" and q["forecast_hours"] == "48"
    assert set(q) == {"latitude", "longitude", "current", "hourly", "past_hours", "forecast_hours", "wind_speed_unit",
                      "temperature_unit", "precipitation_unit", "timezone"}


def test_points_in_one_grid_cell_share_one_request():
    c, fake = client()
    out = c.live([(37.281, -79.971), (37.299, -79.949), (37.31, -79.96)])
    assert list(out) == [(37.3, -79.95)] and fake.calls == 1


def test_many_points_are_chunked():
    c, fake = client()
    pts = [(36.0 + i * 0.05, -80.0 + j * 0.05) for i in range(10) for j in range(12)]       # 120 grid cells
    out = c.live(pts)
    assert len(out) == 120 and fake.calls == 3
    assert all(len(_query(u)["latitude"].split(",")) <= om.CHUNK for u in fake.urls)


def test_live_answers_are_cached_until_the_ttl():
    tick = Tick()
    c, fake = client(tick=tick, ttl=900)
    c.live([(37.3, -79.95)])
    c.live([(37.3, -79.95)])
    assert fake.calls == 1
    tick.t += 901
    c.live([(37.3, -79.95)])
    assert fake.calls == 2


def test_a_failed_request_is_retried_once():
    calls = {"n": 0}
    good = FakeOpenMeteo()

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("boom")
        return good(url)

    out = om.OpenMeteo(fetch=flaky, clock=Tick()).live([(37.3, -79.95)])
    assert out and calls["n"] == 2


def test_two_failures_raise_weather_error():
    c, fake = client(FakeOpenMeteo(fail=urllib.error.URLError("down")))
    with pytest.raises(om.WeatherError):
        c.live([(37.3, -79.95)])
    assert fake.calls == 2


def test_a_client_error_is_not_retried():
    err = urllib.error.HTTPError("u", 400, "bad", {}, None)
    c, fake = client(FakeOpenMeteo(fail=err))
    with pytest.raises(om.WeatherError):
        c.live([(37.3, -79.95)])
    assert fake.calls == 1


def test_null_values_become_none():
    fake = FakeOpenMeteo(gust=lambda la, lo, i: None if i % 2 else 20.0)
    c, _ = client(fake)
    s = c.live([(37.3, -79.95)])[(37.3, -79.95)]
    assert s.hours[0].gust_mph == 20.0 and s.hours[1].gust_mph is None


def test_an_answer_of_the_wrong_length_is_rejected():
    bad = lambda url: [{"hourly": {"time": ["2026-10-25T00:00"], "wind_gusts_10m": [1]}}] * 3  # noqa: E731
    with pytest.raises(om.WeatherError):
        om.OpenMeteo(fetch=bad, clock=Tick()).live([(37.3, -79.95)])


def test_recent_history_uses_the_forecast_api_and_old_history_the_archive():
    c, fake = client()
    recent = c.historical([(37.3, -79.95)], date(2026, 10, 20))
    old = c.historical([(37.3, -79.95)], date(2025, 2, 16))
    assert fake.urls[0].startswith(om.FORECAST_URL) and fake.urls[1].startswith(om.ARCHIVE_URL)
    assert _query(fake.urls[1])["start_date"] == "2025-02-16" and _query(fake.urls[1])["end_date"] == "2025-02-17"
    assert len(old[(37.3, -79.95)].hours) == 48 and old[(37.3, -79.95)].kind == "historical"
    assert recent[(37.3, -79.95)].current is None


def test_historical_end_date_never_reaches_today():
    c, fake = client()
    c.historical([(37.3, -79.95)], date(2026, 10, 24))          # yesterday: only one day exists
    assert _query(fake.urls[0])["end_date"] == "2026-10-24"


@pytest.mark.parametrize("day", [date(2026, 10, 25), date(2027, 1, 1)])
def test_historical_needs_a_past_date(day):
    c, _ = client()
    with pytest.raises(om.WeatherError):
        c.historical([(37.3, -79.95)], day)


def test_historical_answers_are_cached_for_good():
    tick = Tick()
    c, fake = client(tick=tick)
    c.historical([(37.3, -79.95)], date(2025, 2, 16))
    tick.t += 10 ** 7
    c.historical([(37.3, -79.95)], date(2025, 2, 16))
    assert fake.calls == 1


def test_a_failed_chunk_stops_the_rest_but_keeps_what_was_loaded():
    good = FakeOpenMeteo()
    calls = {"n": 0}

    def second_chunk_fails(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return good(url)
        raise urllib.error.URLError("down")

    pts = [(36.0 + i * 0.05, -80.0 + j * 0.05) for i in range(10) for j in range(12)]
    out = om.OpenMeteo(fetch=second_chunk_fails, clock=Tick()).live(pts)
    assert len(out) == om.CHUNK and calls["n"] == 3          # chunk 1 ok; chunk 2 tried twice (retry), chunk 3 never tried


def test_the_time_budget_stops_further_requests(monkeypatch):
    fake = FakeOpenMeteo()
    t = {"now": 0.0}
    monkeypatch.setattr(om.time, "monotonic", lambda: t["now"])

    def slow(url):
        t["now"] += 13.0
        return fake(url)

    pts = [(36.0 + i * 0.05, -80.0 + j * 0.05) for i in range(10) for j in range(12)]
    out = om.OpenMeteo(fetch=slow, clock=Tick()).live(pts)
    assert len(out) == om.CHUNK
