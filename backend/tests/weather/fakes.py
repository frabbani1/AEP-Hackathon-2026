"""A fake Open-Meteo: builds answers shaped like the real ones, with the gust you ask for. No network."""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 25, 0, 50, tzinfo=timezone.utc)      # the clock every weather test uses
CURRENT_TIME = "2026-10-25T00:45"


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def hourly(start: datetime, n: int, gust, code: int = 0, snow: float = 0.0, precip: float = 0.0) -> dict:
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(n)]
    g = [gust(i) if callable(gust) else gust for i in range(n)]
    return {"time": times, "temperature_2m": [50.0] * n, "precipitation": [precip] * n, "snowfall": [snow] * n,
            "weather_code": [code] * n, "wind_speed_10m": [round(x * 0.5, 1) if x is not None else None for x in g],
            "wind_gusts_10m": g}


class FakeOpenMeteo:
    """Callable used as `OpenMeteo(fetch=...)`. `gust` is a number, or a function (lat, lon, hour_index) -> number."""

    def __init__(self, gust=15.0, code: int = 0, fail: Exception | None = None, snow: float = 0.0, precip: float = 0.0):
        self.gust, self.code, self.fail, self.snow, self.precip = gust, code, fail, snow, precip
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail
        q = _query(url)
        lats = [float(x) for x in q["latitude"].split(",")]
        lons = [float(x) for x in q["longitude"].split(",")]
        out = []
        for la, lo in zip(lats, lons):
            def g(i, la=la, lo=lo):
                return self.gust(la, lo, i) if callable(self.gust) else self.gust
            if "start_date" in q:
                start = datetime.fromisoformat(q["start_date"]).replace(tzinfo=timezone.utc)
                end = datetime.fromisoformat(q["end_date"]).replace(tzinfo=timezone.utc)
                n = int((end - start).days + 1) * 24
                item = {"latitude": la, "longitude": lo, "hourly": hourly(start, n, g, self.code, self.snow, self.precip)}
            else:
                start = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)     # 24 hours up to the current hour, then 48 ahead
                item = {"latitude": la, "longitude": lo,
                        "current": {"time": CURRENT_TIME, "interval": 900, "temperature_2m": 48.9,
                                    "apparent_temperature": 44.2, "relative_humidity_2m": 58, "precipitation": 0.0,
                                    "weather_code": self.code, "wind_speed_10m": 3.1, "wind_gusts_10m": 5.1,
                                    "wind_direction_10m": 41},
                        "hourly": hourly(start, 72, g, self.code, self.snow, self.precip)}
            out.append(item)
        return out if len(out) > 1 or "," in q["latitude"] else out[0]

    @property
    def calls(self) -> int:
        return len(self.urls)


def clock():
    return NOW
