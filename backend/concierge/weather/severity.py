"""Peak gust -> storm-strength multiplier, anchored on the two storms in the damage data.

The risk and restoration engines read storm strength as a multiplier m (1.0 = the average of the two past storms).
Without weather, m is a scenario the customer picks. With weather, m comes from the forecast peak gust in the area:

    gust at or below CALM_GUST_MPH            -> severity_min (the mildest scenario)
    gust at each past storm's measured peak   -> that storm's own multiplier (from calibration.json presets)
    gust between two points                   -> straight line between them
    gust above the strongest past storm       -> the same line continued, capped at severity_max

`anchors.json` holds the measured peak gust of each past storm (Open-Meteo archive, area centre, storm window from
calibration.json). `scripts/calibrate_weather.py` regenerates it.

What this is not: a fitted outage model. It is two points and a line, so the scale is steep and the result is an
assumption that needs utility review. Only wind gust is used, because both past storms were wind events. Ice, snow
load, rain and lightning are shown in the weather block but do not move the multiplier.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

ANCHORS_FILE = Path(__file__).with_name("anchors.json")
CALM_GUST_MPH = 25.0       # at or below this the day is not a storm day; assumption


class AnchorError(ValueError):
    """The anchor storms cannot define an increasing gust-to-severity scale."""


@lru_cache(maxsize=1)
def load_anchors(path: str | None = None) -> dict:
    return json.loads(Path(path or ANCHORS_FILE).read_text(encoding="utf-8"))


def points_from(anchors: Mapping, presets: Mapping[str, float]) -> list[tuple[float, float]]:
    """(gust, multiplier) per anchor storm, multipliers taken from the engine's own presets."""
    pts = sorted((float(s["peak_gust_mph"]), float(presets[s["preset"]])) for s in anchors["storms"])
    for (g1, m1), (g2, m2) in zip(pts, pts[1:]):
        if not g2 > g1 or m2 < m1:
            raise AnchorError("anchor storms must have increasing gusts and non-decreasing multipliers")
    if not pts:
        raise AnchorError("no anchor storms")
    return pts


def multiplier_for_gust(gust: float, points: Sequence[tuple[float, float]], *, m_min: float, m_max: float,
                        calm: float = CALM_GUST_MPH) -> float:
    if gust <= calm:
        return m_min
    pts = list(points)
    g_first, m_first = pts[0]
    if gust <= g_first:
        lo = (calm, m_min)
        hi = (g_first, max(m_first, m_min))
        return _line(gust, lo, hi, m_min, m_max)
    for (g1, m1), (g2, m2) in zip(pts, pts[1:]):
        if gust <= g2:
            return _line(gust, (g1, m1), (g2, m2), m_min, m_max)
    if len(pts) >= 2:
        (g1, m1), (g2, m2) = pts[-2], pts[-1]
    else:
        (g1, m1), (g2, m2) = (calm, m_min), pts[-1]
    return _line(gust, (g1, m1), (g2, m2), m_min, m_max)


def _line(x: float, a: tuple[float, float], b: tuple[float, float], lo: float, hi: float) -> float:
    (x1, y1), (x2, y2) = a, b
    y = y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return min(max(y, lo), hi)


def severity_from_gust(gust: float, anchors: Mapping, presets: Mapping[str, float], *, m_min: float,
                       m_max: float) -> dict:
    """The severity block returned with weather-driven answers."""
    pts = points_from(anchors, presets)
    m = round(multiplier_for_gust(gust, pts, m_min=m_min, m_max=m_max), 3)
    return {
        "source": "weather",
        "multiplier": m,
        "peak_gust_mph": round(gust, 1),
        "method": ("Peak gust in the window, placed on a straight line between the two past storms in our damage data. "
                   "Only wind gust is used. This is an assumption, not a fitted outage model."),
        "anchors": [{"storm": s["label"], "peak_gust_mph": s["peak_gust_mph"], "multiplier": round(presets[s["preset"]], 2)}
                    for s in sorted(anchors["storms"], key=lambda s: s["peak_gust_mph"])],
        "calm_gust_mph": CALM_GUST_MPH,
    }
