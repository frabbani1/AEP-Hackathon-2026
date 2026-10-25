"""Open-Meteo client. Standard library only, so the Lambda package gets no new dependency.

What it sends: rounded area coordinates (0.05 degrees, about 5 km) and nothing else. No token, no premise value, no
customer text. The coordinates come from the centre of a published area (at least 10 homes), then get rounded further.

What it gets back (mph, degrees F, inches, UTC):
  live        current conditions plus the last 24 hours and the next 48 hours, from the forecast API
  historical  48 hours starting on a past date, from the forecast API (last ~80 days) or the archive API (older)

Open-Meteo "current" and past hours come from weather models fed by observations. They are not a reading from one
weather station, and the response says so. Data licence: CC BY 4.0, attribution required (see ATTRIBUTION).
The free tier is for non-commercial use; a commercial deployment needs a paid key.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("concierge.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

GRID_DEG = 0.05              # coordinates are snapped to this grid before they leave the process
CHUNK = 50                   # locations per request
LIVE_TTL_SECONDS = 900       # live answers are reused for 15 minutes
CACHE_SIZE = 1024
TIMEOUT_SECONDS = 4.0
DEADLINE_SECONDS = 12.0      # total time one live()/historical() call may spend; the Lambda timeout is 20 s
PAST_HOURS = 24
FORECAST_HOURS = 48
HISTORICAL_HOURS = 48
FORECAST_API_MAX_BACK_DAYS = 80    # the forecast API serves dated requests up to about 92 days back

CURRENT_VARS = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation", "weather_code",
                "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m")
HOURLY_VARS = ("temperature_2m", "precipitation", "snowfall", "weather_code", "wind_speed_10m", "wind_gusts_10m")
UNITS = {"wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "precipitation_unit": "inch", "timezone": "GMT"}

Point = tuple[float, float]
Fetcher = Callable[[str], Any]


class WeatherError(RuntimeError):
    """The weather source could not be used. The message is safe to log, not to show."""


@dataclass(frozen=True)
class Hour:
    time: str                      # "2026-10-25T00:00", UTC
    temp_f: float | None
    precip_in: float | None
    snow_in: float | None
    code: int | None
    wind_mph: float | None
    gust_mph: float | None


@dataclass(frozen=True)
class Current:
    time: str
    temp_f: float | None
    feels_f: float | None
    humidity_pct: float | None
    precip_in: float | None
    code: int | None
    wind_mph: float | None
    gust_mph: float | None
    wind_dir_deg: float | None


@dataclass(frozen=True)
class Series:
    lat: float                     # snapped grid coordinates, not a premise or a zone centre
    lon: float
    kind: str                      # "live" | "historical"
    current: Current | None
    hours: tuple[Hour, ...]
    fetched_at: str                # ISO, UTC


def snap(value: float) -> float:
    return round(round(value / GRID_DEG) * GRID_DEG, 2)


def snap_point(lat: float, lon: float) -> Point:
    return (snap(lat), snap(lon))


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "aep-outage-concierge/1.0 (hackathon prototype)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - fixed https hosts only
        return json.loads(resp.read().decode("utf-8"))


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _parse_current(d: Any) -> Current | None:
    if not isinstance(d, dict) or not isinstance(d.get("time"), str):
        return None
    return Current(
        time=d["time"], temp_f=_num(d.get("temperature_2m")), feels_f=_num(d.get("apparent_temperature")),
        humidity_pct=_num(d.get("relative_humidity_2m")), precip_in=_num(d.get("precipitation")),
        code=_int(d.get("weather_code")), wind_mph=_num(d.get("wind_speed_10m")),
        gust_mph=_num(d.get("wind_gusts_10m")), wind_dir_deg=_num(d.get("wind_direction_10m")))


def _parse_hours(d: Any) -> tuple[Hour, ...]:
    if not isinstance(d, dict) or not isinstance(d.get("time"), list):
        return ()

    def col(name: str) -> list:
        v = d.get(name)
        return v if isinstance(v, list) and len(v) == len(d["time"]) else [None] * len(d["time"])

    t, temp, pr, sn, code, w, g = (d["time"], col("temperature_2m"), col("precipitation"), col("snowfall"),
                                   col("weather_code"), col("wind_speed_10m"), col("wind_gusts_10m"))
    return tuple(Hour(str(t[i]), _num(temp[i]), _num(pr[i]), _num(sn[i]), _int(code[i]), _num(w[i]), _num(g[i]))
                 for i in range(len(t)) if isinstance(t[i], str))


class OpenMeteo:
    """Fetches and caches weather for a set of points. Safe to share between threads."""

    def __init__(self, fetch: Fetcher | None = None, clock: Callable[[], float] = time.time,
                 live_ttl: float = LIVE_TTL_SECONDS):
        self._fetch = fetch or _http_get_json
        self._clock = clock
        self._ttl = live_ttl
        self._cache: OrderedDict[tuple, tuple[float | None, Series]] = OrderedDict()
        self._lock = threading.Lock()

    # ---- cache --------------------------------------------------------------------------------------------------
    def _get(self, key: tuple) -> Series | None:
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                return None
            expires, series = hit
            if expires is not None and self._clock() >= expires:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return series

    def _put(self, key: tuple, series: Series, live: bool) -> None:
        with self._lock:
            self._cache[key] = (self._clock() + self._ttl if live else None, series)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)

    # ---- fetching -----------------------------------------------------------------------------------------------
    def _request(self, url: str) -> Any:
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._fetch(url)
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except Exception as e:  # noqa: BLE001 - timeouts, DNS, bad JSON
                last = e
            if attempt == 1:
                time.sleep(0.2)
        raise WeatherError(f"weather request failed: {type(last).__name__}")

    def _series_from(self, items: Any, chunk: Sequence[Point], kind: str) -> dict[Point, Series]:
        items = items if isinstance(items, list) else [items]
        if len(items) != len(chunk):
            raise WeatherError("weather answer did not match the request")
        stamp = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        out: dict[Point, Series] = {}
        for pt, item in zip(chunk, items):
            if not isinstance(item, dict) or item.get("error"):
                continue
            hours = _parse_hours(item.get("hourly"))
            if hours:
                out[pt] = Series(pt[0], pt[1], kind, _parse_current(item.get("current")), hours, stamp)
        return out

    @staticmethod
    def _unique(points: Sequence[Point]) -> list[Point]:
        return sorted({snap_point(la, lo) for la, lo in points})

    @staticmethod
    def _chunks(points: list[Point]) -> list[list[Point]]:
        return [points[i:i + CHUNK] for i in range(0, len(points), CHUNK)]

    @staticmethod
    def _coords(chunk: Sequence[Point]) -> dict[str, str]:
        return {"latitude": ",".join(f"{la:.2f}" for la, _ in chunk), "longitude": ",".join(f"{lo:.2f}" for _, lo in chunk)}

    def live(self, points: Sequence[Point]) -> dict[Point, Series]:
        """Current conditions, the last 24 hours and the next 48 hours. Keys are snapped points."""
        wanted = self._unique(points)
        out: dict[Point, Series] = {}
        missing: list[Point] = []
        for pt in wanted:
            s = self._get(("live", pt))
            if s is not None:
                out[pt] = s
            else:
                missing.append(pt)
        errors = 0
        started = time.monotonic()
        for chunk in self._chunks(missing):
            if time.monotonic() - started > DEADLINE_SECONDS:
                errors += 1
                break
            params = {**self._coords(chunk), "current": ",".join(CURRENT_VARS), "hourly": ",".join(HOURLY_VARS),
                      "past_hours": PAST_HOURS, "forecast_hours": FORECAST_HOURS, **UNITS}
            try:
                got = self._series_from(self._request(f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"), chunk, "live")
            except WeatherError as e:
                errors += 1
                log.warning("%s", e)
                break           # the source is down or slow: keep what we have instead of waiting on every chunk
            for pt, s in got.items():
                self._put(("live", pt), s, live=True)
            out.update(got)
        if wanted and not out:
            raise WeatherError("no weather could be loaded" if errors else "weather source returned no data")
        return out

    def historical(self, points: Sequence[Point], day: date, today: date | None = None) -> dict[Point, Series]:
        """48 hours starting at 00:00 UTC on `day`. Past dates only; analysis data, not a forecast."""
        today = today or datetime.fromtimestamp(self._clock(), timezone.utc).date()
        if day >= today:
            raise WeatherError("historical weather needs a date before today")
        wanted = self._unique(points)
        out: dict[Point, Series] = {}
        missing: list[Point] = []
        for pt in wanted:
            s = self._get(("hist", pt, day))
            if s is not None:
                out[pt] = s
            else:
                missing.append(pt)
        end = min(day + timedelta(days=HISTORICAL_HOURS // 24 - 1), today - timedelta(days=1))
        base = FORECAST_URL if (today - day).days <= FORECAST_API_MAX_BACK_DAYS else ARCHIVE_URL
        errors = 0
        started = time.monotonic()
        for chunk in self._chunks(missing):
            if time.monotonic() - started > DEADLINE_SECONDS:
                errors += 1
                break
            params = {**self._coords(chunk), "hourly": ",".join(HOURLY_VARS), "start_date": day.isoformat(),
                      "end_date": end.isoformat(), **UNITS}
            try:
                got = self._series_from(self._request(f"{base}?{urllib.parse.urlencode(params)}"), chunk, "historical")
            except WeatherError as e:
                errors += 1
                log.warning("%s", e)
                break
            for pt, s in got.items():
                self._put(("hist", pt, day), s, live=False)
            out.update(got)
        if wanted and not out:
            raise WeatherError("no weather could be loaded" if errors else "weather source returned no data")
        return out
