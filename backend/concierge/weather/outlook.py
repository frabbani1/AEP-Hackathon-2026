"""Forecast-only outlook for a place that has no outage data. Pure functions, no network.

This is deliberately NOT the outage report. The risk and restoration engines are fitted to damage reports from one
service territory, so for anywhere else they have nothing to say. What can honestly be said about any US place is what
the weather forecast says, and what that kind of weather generally does to power lines. So the outlook has:

  * a four-step weather level (quiet, watch, elevated, high) read from the 48-hour forecast
  * the forecast facts behind it, and awareness flags for ice, snow, thunderstorms and heavy rain
  * general preparation steps, chosen by that level
  * an explicit list of what it does NOT give: no outage risk rating, no restoration time, no alert eligibility

The level thresholds reuse the prototype's own wind numbers (`summary.py`, `severity.CALM_GUST_MPH`). They are display
assumptions, not a fitted outage model and not National Weather Service policy, and the answer says so. Wording is
fixed text with the forecast figures filled in. It never passes through the language model.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from ..guidance import prepare
from ..zones.view import SAFETY_NOTICES
from . import summary
from .severity import CALM_GUST_MPH

SOURCE = "forecast_only"
LABEL = "Prediction only"
NOT_AVAILABLE = ("Outage risk rating", "Restoration time estimate", "Alert eligibility")

LEVELS = ("quiet", "watch", "elevated", "high")
LEVEL_LABEL = {"quiet": "Quiet", "watch": "Watch", "elevated": "Elevated", "high": "High"}
# Which preparation list each level gets: the guidance engine's own item rules, keyed by its Low/Medium/High bands.
_PREP_BAND = {"quiet": "Low", "watch": "Medium", "elevated": "High", "high": "High"}
# The guidance engine's two safety item ids, shown with the same level and title the outage map uses.
_SAFETY_IDS = {"safety_downed_lines": SAFETY_NOTICES["downed_lines"], "safety_generator": SAFETY_NOTICES["generator_co"]}

_HAZARD_TEXT = {
    "ice": "Freezing rain or drizzle is in the forecast. Ice can weigh down power lines and trees.",
    "heavy_snow": "Heavy snow is in the forecast. Wet snow can weigh down power lines and trees.",
    "thunderstorm": "Thunderstorms are in the forecast. Lightning and sudden gusts can cause short outages.",
    "heavy_rain": "Heavy rain is in the forecast. Saturated ground can bring down trees onto power lines.",
}

METHOD = (
    "The level comes from the peak wind gust forecast for the next 48 hours, plus ice, heavy snow, thunderstorms and "
    "heavy rain when they are forecast. The cut-offs are prototype assumptions, not a fitted outage model, because "
    "there are no outage records for this place to fit one to."
)


HEX_RADIUS_KM = 3.2        # about the size of one outage-map area (an H3 resolution-6 cell, roughly 36 km2)
CENTRE_DECIMALS = 2        # the hexagon is centred on the place rounded to 0.01 degree, about 1 km


def hexagon(lat: float, lon: float, radius_km: float = HEX_RADIUS_KM) -> list[list[float]]:
    """Outline of a regular hexagon around a place, as [lon, lat] pairs (GeoJSON order, not closed), like the outage map's
    areas. Flat-earth maths is exact enough at this size. The centre is rounded so the shape is an area, not a point."""
    lat, lon = round(lat, CENTRE_DECIMALS), round(lon, CENTRE_DECIMALS)
    dlat = radius_km / 111.2
    dlon = radius_km / (111.2 * math.cos(math.radians(lat)))
    angles = [math.radians(30 + 60 * k) for k in range(6)]          # pointy-top, like the map's areas
    return [[round(lon + dlon * math.cos(a), 4), round(lat + dlat * math.sin(a), 4)] for a in angles]


def level_for(gust: float | None, hazard_ids: set[str]) -> tuple[str, list[str]]:
    """(level, reasons). `reasons` are short fixed phrases naming what raised the level."""
    reasons: list[str] = []
    level = 0
    g = gust or 0.0
    if g >= summary.GUST_DAMAGING_MPH:
        level = 3
        reasons.append(f"damaging gusts of about {round(g)} mph")
    elif g >= summary.GUST_STRONG_MPH:
        level = 2
        reasons.append(f"strong gusts of about {round(g)} mph")
    elif g > CALM_GUST_MPH:
        level = 1
        reasons.append(f"gusts of about {round(g)} mph")
    for hazard, floor, text in (("ice", 2, "freezing rain"), ("heavy_snow", 2, "heavy snow"),
                                ("thunderstorm", 1, "thunderstorms"), ("heavy_rain", 1, "heavy rain")):
        if hazard in hazard_ids:
            level = max(level, floor)
            reasons.append(text)
    return LEVELS[level], reasons


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _summary_text(level: str, name: str, reasons: list[str]) -> str:
    if level == "quiet":
        return (f"The 48-hour forecast for {name} looks calm. No strong wind, ice, snow or storms are forecast, "
                "so this is a good time to make sure you are ready for any outage.")
    because = _join(reasons)
    if level == "watch":
        return (f"The forecast for {name} shows {because}. That is unsettled but not severe. "
                "Basic readiness is enough, and it is worth checking the forecast again before the weather arrives.")
    if level == "elevated":
        return (f"The forecast for {name} shows {because}. Weather like this can bring down trees and power lines, "
                "so a power outage is possible. Prepare now.")
    return (f"The forecast for {name} shows {because}. Wind this strong often brings down trees and power lines, "
            "so a power outage is a real possibility. Prepare now and stay away from anything that looks damaged.")


def _preparation(level: str) -> dict:
    built = prepare.build(band=_PREP_BAND[level], typical_hours=None, estimate_display=None, needs=None,
                          insufficient_data=False)
    safety = []
    actions = []
    for item in built["items"]:
        if item["safety"]:
            level_, title = _SAFETY_IDS.get(item["id"], ("caution", "Safety"))
            safety.append({"id": item["id"], "level": level_, "title": title, "text": item["text"]})
        else:
            actions.append({"id": item["id"], "text": item["text"]})
    level_, title = SAFETY_NOTICES["emergency"]
    safety.append({"id": "emergency", "level": level_, "title": title, "text": built["emergency_notice"]})
    return {"safety": safety, "actions": actions}


def build(place_name: str, wx: Mapping, lat: float, lon: float) -> dict:
    """The outlook for one place. `wx` is `summary.summarize(...)` for a live series; `place_name` and the place's
    coordinates are public geography. The coordinates only become the hexagon in `area`; they are not returned as such."""
    window = wx.get("severity_window") or {}
    gust = window.get("peak_gust_mph")
    found = list(wx.get("hazards") or [])
    level, reasons = level_for(gust, {h["id"] for h in found})
    # The weather block's flag text says "not part of the risk rating". Here these flags do set the level, so reword.
    hazards = [{"id": h["id"], "level": h["level"], "text": _HAZARD_TEXT.get(h["id"], h["text"])} for h in found]
    # Leave out the hour-by-hour list, the grid coordinates and the hazards (returned once, above).
    compact = {k: v for k, v in wx.items() if k not in ("hourly", "severity_window", "location", "window_48h", "hazards")}
    return {
        "source": SOURCE,
        "label": LABEL,
        "place": {"name": place_name},
        "area": {"ring": hexagon(lat, lon)},             # drawn on the map in purple, like an outage-map area
        "notice": (f"Prediction only. We have no outage records for {place_name}, so this is a forecast-based outlook. "
                   "It is not an outage risk rating, a restoration time or confirmed utility information."),
        "outlook": {
            "level": level, "label": LEVEL_LABEL[level], "steps": LEVELS.index(level) + 1, "of": len(LEVELS),
            "summary": _summary_text(level, place_name, reasons),
            "window": window.get("label"), "peak_gust_mph": gust, "peak_gust_time": window.get("peak_gust_time"),
            "method": METHOD,
        },
        "hazards": hazards,
        "weather": compact,
        "preparation": _preparation(level),
        "not_available": list(NOT_AVAILABLE),
    }
