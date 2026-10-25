"""Place names for the outage map: names for areas, and a lookup from a typed city or ZIP code to an area.

Everything here is public geography. Place coordinates come from OpenStreetMap (ODbL, (c) OpenStreetMap contributors) and
ZIP centroids from the US Census Bureau ZCTA gazetteer. No customer field is used: an area is named only from where its
outline sits, and the lookup only compares what the customer typed with these two public tables. The typed text is never
logged, never stored and never echoed back.

Pure standard library, no geometry package, so it can run in the Lambda.

Two jobs:
  * `name_zones`  (build time, and `scripts/relabel_zones.py`): "Salem", "Northeast Roanoke", ... instead of "Area 7".
  * `locate`      (runtime, `POST /zones/find`): "24019" or "Salem" -> the area that contains that point.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# (name, latitude, longitude, rank). Rank: 3 city, 2 town or large community, 1 village, 0 hamlet. The service data covers
# the Roanoke Valley and the counties around it, so these are places in and around that footprint.
_PLACE_ROWS: tuple[tuple[str, float, float, int], ...] = (
    ("Roanoke", 37.2710, -79.9414, 3), ("Salem", 37.2932, -80.0557, 3),
    ("Vinton", 37.2800, -79.8976, 2), ("Hollins", 37.3482, -79.9504, 2), ("Cave Spring", 37.2301, -80.0085, 2),
    ("Daleville", 37.4155, -79.9133, 2), ("Rocky Mount", 36.9962, -79.8909, 2), ("New Castle", 37.5029, -80.1100, 2),
    ("Cloverdale", 37.3651, -79.9056, 1), ("Troutville", 37.4182, -79.8748, 1), ("Fincastle", 37.4993, -79.8773, 1),
    ("Buchanan", 37.5274, -79.6798, 1), ("Montvale", 37.3846, -79.7311, 1), ("Catawba", 37.3826, -80.1095, 1),
    ("Boones Mill", 37.1147, -79.9540, 1), ("Moneta", 37.1806, -79.6199, 1), ("Westlake Corner", 37.1200, -79.7130, 1),
    ("Union Hall", 36.9984, -79.6895, 1), ("Penhook", 36.9824, -79.6281, 1), ("Ferrum", 36.9203, -80.0129, 1),
    ("Floyd", 36.9112, -80.3201, 1), ("Henry Fork", 36.9663, -79.8703, 1), ("Mason Cove", 37.3710, -80.0673, 1),
    ("Bonsack", 37.3260, -79.8659, 1), ("Shawsville", 37.1685, -80.2553, 1), ("Bent Mountain", 37.1412, -80.1192, 1),
    ("Glenvar", 37.2740, -80.1339, 0), ("Hardy", 37.2318, -79.8106, 0), ("Stewartsville", 37.2707, -79.7925, 0),
    ("Goodview", 37.2143, -79.7250, 0), ("Elliston", 37.2165, -80.2325, 0), ("Blue Ridge", 37.3785, -79.8070, 0),
    ("Callaway", 37.0115, -80.0495, 0), ("Check", 37.0349, -80.1651, 0), ("Burnt Chimney", 37.1051, -79.8156, 0),
    ("Wirtz", 37.0765, -79.8895, 0), ("Redwood", 37.0199, -79.8120, 0), ("Lithia", 37.4782, -79.7514, 0),
    ("Craig Springs", 37.4848, -80.2920, 0), ("Sinking Creek", 37.4190, -80.2650, 0),
    ("Haymakertown", 37.4662, -79.9706, 0), ("Trinity", 37.4560, -79.8898, 0), ("Nace", 37.4396, -79.8239, 0),
    ("Irving", 37.3587, -79.6706, 0), ("Thaxton", 37.3529, -79.6164, 0), ("Shady Grove", 37.3199, -79.7211, 0),
    ("Falling Creek", 37.2968, -79.8598, 0), ("Garden City", 37.2318, -79.9284, 0), ("Riverdale", 37.2551, -79.9139, 0),
    ("Mount Pleasant", 37.2329, -79.9136, 0), ("Chamblissburg", 37.2543, -79.7070, 0),
    ("Diamond Hill", 37.1918, -79.6875, 0), ("Meador", 37.2004, -79.6564, 0), ("Poages Mill", 37.1982, -80.0564, 0),
    ("Airpoint", 37.1712, -80.1142, 0), ("Wright", 37.1654, -79.9736, 0), ("Naff", 37.1482, -80.0123, 0),
    ("Red Valley", 37.1351, -79.8614, 0), ("Copper Hill", 37.0818, -80.1342, 0), ("Algoma", 37.0560, -80.0873, 0),
    ("Gogginsville", 37.0404, -79.9259, 0), ("Dillons Mill", 37.0765, -79.7775, 0), ("Pizarro", 36.9379, -80.2203, 0),
    ("Hemlock", 37.0693, -80.2156, 0), ("Terrys Fork", 37.0460, -80.2645, 0), ("Simpsons", 37.0379, -80.2045, 0),
    ("Huffville", 37.0624, -80.2853, 0), ("Piedmont", 37.1001, -80.2189, 0), ("Ironto", 37.2187, -80.2781, 0),
    ("Bradshaw", 37.2999, -80.2242, 0), ("McDonalds Mill", 37.3001, -80.2756, 0), ("Webbs Mill", 37.3999, -80.2084, 0),
    ("Abbott", 37.4294, -80.1398, 0), ("Looney", 37.4712, -80.1625, 0), ("Gunn", 37.4935, -80.2353, 0),
    ("Kyles Mills", 37.5493, -79.8328, 0), ("Springwood", 37.5490, -79.7448, 0), ("Arcadia", 37.5454, -79.6239, 0),
    ("Munford", 37.5099, -79.6134, 0), ("Pico", 37.4982, -79.6948, 0), ("Howell Mills", 37.5010, -79.9114, 0),
    ("Amsterdam", 37.4274, -79.9061, 0), ("Spec", 37.4546, -79.7714, 0), ("Marshalltown", 37.5382, -80.0598, 0),
    ("Villamont", 37.3954, -79.7761, 0), ("Penicks Mill", 37.3882, -79.6145, 0), ("Flint Hill", 37.2579, -79.6373, 0),
    ("Dundee", 37.2410, -79.8817, 0),
)

# 5-digit ZIP -> (latitude, longitude) of the ZIP's centre, for ZIPs in and around the footprint.
ZIP_CENTRES: dict[str, tuple[float, float]] = {
    "24011": (37.27085, -79.94149), "24012": (37.31043, -79.90305), "24013": (37.26650, -79.92310),
    "24014": (37.22286, -79.91519), "24015": (37.25676, -79.98133), "24016": (37.27344, -79.95469),
    "24017": (37.29764, -79.99097), "24018": (37.21306, -80.04174), "24019": (37.34652, -79.95354),
    "24020": (37.35822, -79.94418), "24059": (37.16373, -80.13048), "24064": (37.37631, -79.77038),
    "24065": (37.11704, -79.99733), "24066": (37.53705, -79.68100), "24067": (37.03180, -80.05990),
    "24070": (37.35611, -80.22583), "24072": (37.04199, -80.24238), "24077": (37.36621, -79.90402),
    "24079": (37.05663, -80.16094), "24083": (37.40517, -79.92019), "24087": (37.21401, -80.25677),
    "24090": (37.52170, -79.88655), "24092": (37.01203, -79.75785), "24095": (37.22321, -79.73727),
    "24101": (37.17356, -79.79639), "24121": (37.16892, -79.64741), "24122": (37.42620, -79.69213),
    "24127": (37.47360, -80.23105), "24131": (37.57065, -80.23287), "24137": (36.92755, -79.65750),
    "24138": (37.04284, -80.30641), "24151": (36.95402, -79.86520), "24153": (37.30251, -80.11433),
    "24162": (37.12945, -80.24972), "24174": (37.35172, -79.67310), "24175": (37.42800, -79.93969),
    "24176": (37.00555, -79.69250), "24179": (37.28557, -79.80984), "24184": (37.08854, -79.77924),
}

MAX_NEAREST_KM = 8.0          # a typed place farther than this from every area is "not covered"
NEARBY_KM = 10.0
MAX_NEARBY = 3
MAX_QUERY_LENGTH = 60
_STATE_OK = {"va", "virginia"}
_ZIP = re.compile(r"^(\d{5})(?:-\d{4})?$")
_COMPASS = ("North", "Northeast", "East", "Southeast", "South", "Southwest", "West", "Northwest")


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    rank: int


PLACES: tuple[Place, ...] = tuple(Place(*row) for row in _PLACE_ROWS)
_BY_NAME = {p.name.lower(): p for p in PLACES}


# ---- geometry (flat-earth maths is exact enough at 10 km) -------------------------------------------------------------
def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111.2
    dx = (lon2 - lon1) * 111.2 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def centroid(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    """(lat, lon) of the mean of an outline given as (lon, lat) pairs."""
    return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))


def contains(ring: Sequence[Sequence[float]], lat: float, lon: float) -> bool:
    """Ray casting point-in-polygon on an outline of (lon, lat) pairs."""
    inside, n = False, len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def compass(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> str:
    dy = to_lat - from_lat
    dx = (to_lon - from_lon) * math.cos(math.radians(from_lat))
    bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return _COMPASS[int((bearing + 22.5) // 45) % 8]


# ---- naming areas ------------------------------------------------------------------------------------------------------
def name_zones(zones: Sequence[Mapping]) -> dict[str, str]:
    """Readable, unique names for areas. `zones` are mappings with `id` and `ring` ((lon, lat) pairs).

    An area is named for the biggest place inside its outline, or else the closest place. When several areas share a
    place, the one holding it keeps the plain name and the others get a compass word: "Salem", "Northeast Roanoke".
    Any name still shared gets a number so every name is unique.
    """
    picks: dict[str, tuple[Place, tuple[float, float], bool]] = {}
    for z in zones:
        lat, lon = centroid(z["ring"])
        inside = [p for p in PLACES if contains(z["ring"], p.lat, p.lon)]
        if inside:
            anchor = max(inside, key=lambda p: (p.rank, -km_between(lat, lon, p.lat, p.lon)))
        else:
            anchor = min(PLACES, key=lambda p: km_between(lat, lon, p.lat, p.lon))
        picks[z["id"]] = (anchor, (lat, lon), bool(inside))

    groups: dict[str, list[str]] = defaultdict(list)
    for zid, (anchor, _, _) in picks.items():
        groups[anchor.name].append(zid)

    names: dict[str, str] = {}
    for anchor_name, members in groups.items():
        anchor = _BY_NAME[anchor_name.lower()]
        members.sort(key=lambda zid: (not picks[zid][2], km_between(*picks[zid][1], anchor.lat, anchor.lon), zid))
        for i, zid in enumerate(members):
            lat, lon = picks[zid][1]
            names[zid] = anchor.name if i == 0 else f"{compass(anchor.lat, anchor.lon, lat, lon)} {anchor.name}"

    seen: dict[str, int] = {}
    for z in zones:                                    # input order decides who keeps the bare name
        zid = z["id"]
        count = seen.get(names[zid], 0) + 1
        seen[names[zid]] = count
        if count > 1:
            names[zid] = f"{names[zid]} {count}"
    return names


# ---- finding an area from typed text --------------------------------------------------------------------------------
def resolve_point(query: object) -> tuple[float, float, str] | None:
    """(lat, lon, kind) for a typed ZIP code or place name, or None when it is not in the tables.

    `kind` is "zip" or "place". "Salem", "Salem, VA" and "salem va" all work; "Salem, OH" does not, because the data is
    for Virginia places only.
    """
    if not isinstance(query, str):
        return None
    text = " ".join(query.split())
    if not text or len(text) > MAX_QUERY_LENGTH:
        return None
    m = _ZIP.match(text)
    if m:
        centre = ZIP_CENTRES.get(m.group(1))
        return (centre[0], centre[1], "zip") if centre else None
    text = text.lower().replace(".", "")
    text = re.sub(r"^(city|town|village) of\s+", "", text)
    state = None
    if "," in text:
        text, state = (part.strip() for part in text.split(",", 1))
    else:
        tail = re.match(r"^(.*?)\s+(va|virginia)$", text)
        if tail:
            text, state = tail.group(1), tail.group(2)
    if state is not None and state not in _STATE_OK:
        return None
    place = _BY_NAME.get(text)
    return (place.lat, place.lon, "place") if place else None


def locate(query: object, zones: Sequence[tuple[str, str, Sequence[Sequence[float]]]]) -> dict | None:
    """The area for a typed ZIP or place. `zones` are (id, name, ring) triples. None when nothing is close enough.

    Returns `zone_id`, `zone_name`, `match` ("zip" or "place"), `exact` (the point sits inside the area) and `nearby`
    (up to three other areas within 10 km, closest first).
    """
    point = resolve_point(query)
    if point is None or not zones:
        return None
    lat, lon, kind = point
    ranked = sorted(((km_between(lat, lon, *centroid(ring)), zid, name, ring) for zid, name, ring in zones),
                    key=lambda item: (item[0], item[1]))
    best = next((item for item in ranked if contains(item[3], lat, lon)), None)
    exact = best is not None
    if best is None:
        best = ranked[0]
        if best[0] > MAX_NEAREST_KM:
            return None
    nearby = [{"zone_id": zid, "zone_name": name} for dist, zid, name, _ in ranked
              if zid != best[1] and dist <= NEARBY_KM][:MAX_NEARBY]
    return {"zone_id": best[1], "zone_name": best[2], "match": kind, "exact": exact, "nearby": nearby}


def covered_places(zones: Sequence[tuple[str, str, Sequence[Sequence[float]]]], min_rank: int = 2) -> list[str]:
    """Names of the larger places that have an area within reach, biggest first. Used to say what is covered."""
    centres = [centroid(ring) for _, _, ring in zones]
    found = [p for p in PLACES if p.rank >= min_rank
             and any(km_between(p.lat, p.lon, la, lo) <= MAX_NEAREST_KM for la, lo in centres)]
    return [p.name for p in sorted(found, key=lambda p: (-p.rank, p.name))]
