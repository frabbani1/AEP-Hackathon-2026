"""Runtime lookup for map zones. Reads `zones.json` (see concierge.curate.zones); no AWS, no geometry library."""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from ..risk.tokens import TOKEN_RE

ZONE_ID_RE = re.compile(r"^z_[a-z]{12}\Z")
MIN_HOMES = 10


class ZoneError(ValueError):
    """The zones document is not usable. Raised at load time so a bad artifact fails at start-up, not on a request."""


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    ring: tuple[tuple[float, float], ...]      # (longitude, latitude), GeoJSON order
    rep: str | None                            # representative premise token, server side only; None when unrated
    fallback: str | None                       # reason code when the zone cannot be rated
    facility: bool                             # a critical facility is near; never says which or where
    homes: int                                 # server side only


class ZoneIndex:
    def __init__(self, zones: list[Zone], bounds: tuple[tuple[float, float], tuple[float, float]]):
        self._zones = {z.id: z for z in zones}
        if len(self._zones) != len(zones):
            raise ZoneError("zone ids must be unique")
        self.bounds = bounds                   # ((south, west), (north, east))

    @classmethod
    def from_dict(cls, doc: Mapping) -> "ZoneIndex":
        try:
            raw = doc["zones"]
            if not isinstance(raw, list) or not raw:
                raise ZoneError("zones must be a non-empty list")
            zones = [cls._zone(z) for z in raw]
            (s, w), (n, e) = doc["bounds"]
            bounds = ((float(s), float(w)), (float(n), float(e)))
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ZoneError):
                raise
            raise ZoneError(f"malformed zones document: {exc}") from None
        if not (-90 <= bounds[0][0] < bounds[1][0] <= 90 and -180 <= bounds[0][1] < bounds[1][1] <= 180):
            raise ZoneError("bounds must be [[south, west], [north, east]]")
        return cls(zones, bounds)

    @staticmethod
    def _zone(z: Mapping) -> Zone:
        zid, rep, fb, homes = z["id"], z["rep"], z["fb"], z["n"]
        if not isinstance(zid, str) or not ZONE_ID_RE.match(zid):
            raise ZoneError("zone id must look like z_ followed by 12 lowercase letters")
        if isinstance(homes, bool) or not isinstance(homes, int) or homes < MIN_HOMES:
            raise ZoneError(f"a zone must cover at least {MIN_HOMES} homes")
        if (rep is None) == (fb is None):
            raise ZoneError("a zone has either a representative premise or a fallback reason")
        if rep is not None and (not isinstance(rep, str) or not TOKEN_RE.match(rep)):
            raise ZoneError("representative premise must be an opaque token")
        ring = tuple((float(lng), float(lat)) for lng, lat in z["ring"])
        if len(ring) < 3 or any(not (-180 <= lng <= 180 and -90 <= lat <= 90) for lng, lat in ring):
            raise ZoneError("a zone outline needs at least 3 valid points")
        if not isinstance(z["name"], str) or not z["name"]:
            raise ZoneError("a zone needs a name")
        return Zone(zid, z["name"], ring, rep, fb, bool(z["facility"]), homes)

    def get(self, zone_id: object) -> Zone | None:
        return self._zones.get(zone_id) if isinstance(zone_id, str) else None

    def __iter__(self) -> Iterator[Zone]:
        return iter(self._zones.values())

    def __len__(self) -> int:
        return len(self._zones)
