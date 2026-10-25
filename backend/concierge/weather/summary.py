"""Turns an Open-Meteo series into the weather block the API returns. Pure functions, no network.

Every figure here is copied or computed from the series. Wording is fixed text with those figures filled in, so the
weather block never passes through the language model.
"""
from __future__ import annotations

from collections.abc import Iterable

from .openmeteo import ATTRIBUTION, Hour, Series

# Prototype thresholds for the awareness flags below. They are assumptions for display, not utility or NWS policy.
GUST_STRONG_MPH = 46
GUST_DAMAGING_MPH = 58
HEAVY_RAIN_24H_IN = 2.0
HEAVY_SNOW_WINDOW_IN = 4.0
FREEZING_RAIN_CODES = {56, 57, 66, 67}
THUNDER_CODES = {95, 96, 99}
HEAVY_SNOW_CODES = {75, 86}
HEAVY_RAIN_CODES = {65, 82}

BASIS = "Weather model analysis and forecast for the area. Not a reading from a single measuring site."

_CODE_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
              51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Heavy freezing drizzle",
              61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Heavy freezing rain",
              71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
              82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
              96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail"}


def describe_code(code: int | None) -> str | None:
    return None if code is None else _CODE_TEXT.get(code, "Unsettled")


def _vals(hours: Iterable[Hour], field: str) -> list[float]:
    return [v for v in (getattr(h, field) for h in hours) if v is not None]


def _round(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


def _peak(hours: list[Hour], field: str) -> tuple[float | None, str | None]:
    best: tuple[float, str] | None = None
    for h in hours:
        v = getattr(h, field)
        if v is not None and (best is None or v > best[0]):
            best = (v, h.time)
    return (best[0], best[1]) if best else (None, None)


def _max_rolling(hours: list[Hour], field: str, width: int = 24) -> float | None:
    vals = [getattr(h, field) or 0.0 for h in hours]
    if not vals:
        return None
    if len(vals) <= width:
        return sum(vals)
    return max(sum(vals[i:i + width]) for i in range(len(vals) - width + 1))


def _block(hours: list[Hour]) -> dict | None:
    if not hours:
        return None
    gust, gust_time = _peak(hours, "gust_mph")
    wind, _ = _peak(hours, "wind_mph")
    temps = _vals(hours, "temp_f")
    return {
        "from": hours[0].time, "to": hours[-1].time,
        "peak_gust_mph": _round(gust), "peak_gust_time": gust_time, "peak_wind_mph": _round(wind),
        "precip_in": _round(sum(_vals(hours, "precip_in")), 2), "snow_in": _round(sum(_vals(hours, "snow_in")), 1),
        "max_24h_precip_in": _round(_max_rolling(hours, "precip_in"), 2),
        "min_temp_f": _round(min(temps)) if temps else None, "max_temp_f": _round(max(temps)) if temps else None,
    }


def _split(series: Series) -> tuple[list[Hour], list[Hour]]:
    """(recent, ahead). Live: hours up to the current hour are recent, later hours are forecast. Historical: all recent."""
    if series.kind != "live" or series.current is None:
        return list(series.hours), []
    now_hour = series.current.time[:13]
    recent = [h for h in series.hours if h.time[:13] <= now_hour]
    ahead = [h for h in series.hours if h.time[:13] > now_hour]
    return recent[-24:], ahead


def hazards(window: list[Hour], tense: str) -> list[dict]:
    """Awareness flags for the window. Only wind feeds the storm-strength scale; the rest are shown, not modelled."""
    out: list[dict] = []
    gust, _ = _peak(window, "gust_mph")
    verb = "are forecast" if tense == "forecast" else "were recorded"
    if gust is not None and gust >= GUST_STRONG_MPH:
        severe = gust >= GUST_DAMAGING_MPH
        out.append({"id": "damaging_wind" if severe else "strong_wind", "level": "severe" if severe else "elevated",
                    "modelled": True,
                    "text": f"{'Damaging' if severe else 'Strong'} gusts of about {round(gust)} mph {verb}."})
    codes = {h.code for h in window if h.code is not None}
    if codes & FREEZING_RAIN_CODES:
        out.append({"id": "ice", "level": "elevated", "modelled": False,
                    "text": "Freezing rain or drizzle " + ("is in the forecast." if tense == "forecast" else "occurred.")
                            + " Ice on lines and trees is not part of the risk rating."})
    if codes & THUNDER_CODES:
        out.append({"id": "thunderstorm", "level": "elevated", "modelled": False,
                    "text": "Thunderstorms " + ("are in the forecast." if tense == "forecast" else "occurred.")
                            + " Lightning is not part of the risk rating."})
    snow = sum(_vals(window, "snow_in"))
    if snow >= HEAVY_SNOW_WINDOW_IN or codes & HEAVY_SNOW_CODES:
        out.append({"id": "heavy_snow", "level": "elevated", "modelled": False,
                    "text": "Heavy snow " + ("is in the forecast." if tense == "forecast" else "fell.")
                            + " Snow load on lines and trees is not part of the risk rating."})
    rain = _max_rolling(window, "precip_in") or 0.0
    if rain >= HEAVY_RAIN_24H_IN or codes & HEAVY_RAIN_CODES:
        out.append({"id": "heavy_rain", "level": "elevated", "modelled": False,
                    "text": "Heavy rain " + ("is in the forecast." if tense == "forecast" else "fell.")
                            + " Flooding and saturated ground are not part of the risk rating."})
    return out


def _current_view(series: Series) -> dict | None:
    c = series.current
    if c is None:
        return None
    return {"time": c.time, "temp_f": _round(c.temp_f), "feels_like_f": _round(c.feels_f),
            "humidity_pct": _round(c.humidity_pct, 0), "wind_mph": _round(c.wind_mph), "gust_mph": _round(c.gust_mph),
            "wind_dir_deg": _round(c.wind_dir_deg, 0), "precip_in": _round(c.precip_in, 2),
            "conditions": describe_code(c.code)}


def summarize(series: Series, area: str | None = None) -> dict:
    """The weather block. `severity_window` names the hours the storm-strength scale reads."""
    recent, ahead = _split(series)
    live = series.kind == "live"
    window = ahead if live else recent            # live: the forecast hours; historical: the whole 48 hours
    tense = "forecast" if live else "recorded"
    gust, gust_time = _peak(window, "gust_mph")
    out = {
        "status": "ok",
        "source": "Open-Meteo",
        "attribution": ATTRIBUTION,
        "basis": BASIS,
        "kind": series.kind,
        "as_of": series.current.time if (live and series.current) else (recent[0].time if recent else None),
        "fetched_at": series.fetched_at,
        "location": {"area": area, "grid_lat": series.lat, "grid_lon": series.lon},
        "units": {"wind": "mph", "temperature": "F", "precipitation": "inch", "time": "UTC"},
        "current": _current_view(series),
        "recent_24h": _block(recent) if live else None,
        "ahead_48h": _block(ahead) if live else None,
        "window_48h": None if live else _block(recent),
        "hazards": hazards(window, tense),
        "hourly": [{"time": h.time, "wind_mph": _round(h.wind_mph), "gust_mph": _round(h.gust_mph),
                    "precip_in": _round(h.precip_in, 2), "snow_in": _round(h.snow_in, 1), "temp_f": _round(h.temp_f),
                    "conditions": describe_code(h.code), "forecast": h in ahead} for h in series.hours],
        "severity_window": {"label": "next 48 hours (forecast)" if live else "48 hours from the chosen date",
                            "peak_gust_mph": _round(gust), "peak_gust_time": gust_time},
    }
    return out


def unavailable(reason: str, area: str | None = None) -> dict:
    """Used when weather could not be loaded. The answer then falls back to the default scenario and says so."""
    return {"status": "unavailable", "source": "Open-Meteo", "attribution": ATTRIBUTION, "reason": reason,
            "location": {"area": area, "grid_lat": None, "grid_lon": None}}
