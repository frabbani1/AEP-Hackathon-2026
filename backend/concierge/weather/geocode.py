"""Find a US city or ZIP code on the map, for the forecast-only outlook (places outside the outage data).

The outage map only knows the Roanoke Valley. For anywhere else the outlook needs a coordinate to ask the weather
source about, and the repository has no table for the rest of the country, so this asks Open-Meteo's free geocoding
service (https://open-meteo.com/en/docs/geocoding-api, data from GeoNames, CC BY 4.0).

What leaves the process: the typed city or 5-digit ZIP code, and nothing else. No token, no premise value, no street
address (the caller rejects anything with digits that is not a plain ZIP). The text is not stored, cached, logged or
echoed. What comes back is the place's public name and coordinates; the caller snaps the coordinates to a 5 km grid
before asking for weather, and never returns them.

Standard library only, so the Lambda package gets no new dependency.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .openmeteo import _http_get_json

log = logging.getLogger("concierge.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_QUERY_LENGTH = 60
RESULT_COUNT = 10

_ZIP = re.compile(r"^(\d{5})(?:-\d{4})?$")
_CITY = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\u2019-])*")

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_BY_NAME = {name.lower(): name for name in STATES.values()}
_FULL_NAMES_LONGEST_FIRST = sorted(_BY_NAME, key=len, reverse=True)

Fetcher = Callable[[str], Any]


class GeocodeError(RuntimeError):
    """The place lookup could not be used. The message is safe to log, not to show."""


@dataclass(frozen=True)
class Place:
    name: str            # "Columbus, Ohio": public geography, safe to show
    lat: float
    lon: float


def parse(query: object) -> tuple[str, str, str | None] | None:
    """(kind, term, state) for a typed ZIP or city, or None when it is neither.

    kind is "zip" or "city". `state` is a full state name, or None. Accepts "Columbus", "Columbus, OH",
    "Columbus OH" and "Columbus, Ohio".
    """
    if not isinstance(query, str):
        return None
    text = " ".join(query.split())
    if not text or len(text) > MAX_QUERY_LENGTH:
        return None
    m = _ZIP.match(text)
    if m:
        return ("zip", m.group(1), None)
    text = re.sub(r"^(city|town|village) of\s+", "", text, flags=re.IGNORECASE)
    state: str | None = None
    if "," in text:
        text, tail = (part.strip() for part in text.split(",", 1))
        state = _state(tail)
        if state is None:
            return None
    else:
        words = text.split(" ")
        if len(words) > 1 and words[-1].upper() in STATES:
            state, text = STATES[words[-1].upper()], " ".join(words[:-1])
        else:
            lower = text.lower()
            for name in _FULL_NAMES_LONGEST_FIRST:
                if lower.endswith(" " + name):
                    state, text = _BY_NAME[name], text[: -len(name) - 1].strip()
                    break
    if not text or not _CITY.fullmatch(text):
        return None
    return ("city", text, state)


def _state(value: str) -> str | None:
    value = value.strip().replace(".", "")
    return STATES.get(value.upper()) or _BY_NAME.get(value.lower())


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Geocoder:
    """Looks up one US place at a time. Nothing is cached, because the key would be the text the customer typed."""

    def __init__(self, fetch: Fetcher | None = None):
        self._fetch = fetch or _http_get_json

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
        raise GeocodeError(f"place lookup failed: {type(last).__name__}")

    def lookup(self, query: object) -> Place | None:
        """The place for a typed US city or ZIP code. None when it is not a plain city or ZIP, or nothing matches.
        Raises GeocodeError when the lookup service cannot be reached."""
        parsed = parse(query)
        if parsed is None:
            return None
        kind, term, state = parsed
        params = {"name": term, "count": RESULT_COUNT, "language": "en", "format": "json", "countryCode": "US"}
        answer = self._request(f"{GEOCODE_URL}?{urllib.parse.urlencode(params)}")
        results = answer.get("results") if isinstance(answer, dict) else None
        if not isinstance(results, list):
            return None
        for item in results:
            if not isinstance(item, dict):
                continue
            lat, lon, name = _num(item.get("latitude")), _num(item.get("longitude")), item.get("name")
            if lat is None or lon is None or not isinstance(name, str):
                continue
            admin1 = item.get("admin1") if isinstance(item.get("admin1"), str) else None
            if kind == "zip" and term not in (item.get("postcodes") or []):
                continue
            if kind == "city" and state is not None and (admin1 or "").lower() != state.lower():
                continue
            return Place(f"{name}, {admin1}" if admin1 else name, lat, lon)
        return None
