"""Shapefile access with an explicit column allow-list per call and a global forbidden-column guard."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import shapefile  # pyshp

# Sensitive or internal-only columns. Asking for one raises; none may appear in any curated table.
FORBIDDEN_COLUMNS = frozenset({
    # customer
    "critical_c", "special_op", "address", "address_nu", "road_name", "road_id", "city", "anno_strin",
    "macss_mete", "service_po", "service__1", "street_cod", "aep_xfmr_l", "aep_locati", "customer_c",
    # facilities (names and addresses of sensitive sites)
    "NAME", "ADDRESS", "ADDRESSBUI", "CITY", "ZIPCODE", "STATE", "GNIS_ID", "FOOT_ID", "COMPLEX_ID",
    # ARCOS internal fields
    "Outage_Num", "Incident_W", "Substation", "NEAR_FID", "NEAR_DIST", "TASK_Crew", "TASK_Job_I",
    "Crew_Area", "District", "Reporting_", "Reporting1", "Latitude", "Longitude",
    # device layers
    "scada_cont", "owner_name", "global_id", "aep_statio", "aep_stat_1",
    # raw identifiers
    "premise_nu",
})


class ForbiddenColumnError(ValueError):
    """A loader asked for a column that must never be read."""


def _check(columns: Sequence[str], allow: Sequence[str] = ()) -> list[str]:
    bad = sorted((set(columns) & FORBIDDEN_COLUMNS) - set(allow))
    if bad:
        raise ForbiddenColumnError(f"forbidden columns requested: {bad}")
    return list(columns)


def iter_records(path: str | Path, columns: Sequence[str], allow: Sequence[str] = ()) -> Iterator[dict]:
    """Attribute rows only (no geometry), decoding just `columns`. `allow` exempts named columns that the caller
    will tokenize or drop immediately (e.g. premise_nu)."""
    cols = _check(columns, allow)
    r = shapefile.Reader(str(path))
    for rec in r.iterRecords(fields=cols):
        yield rec.as_dict()


def iter_points(path: str | Path, columns: Sequence[str],
                allow: Sequence[str] = ()) -> Iterator[tuple[dict, tuple[float, float] | None]]:
    """(attributes, (lon, lat)) per point feature. Coordinates are returned for immediate bucketing only."""
    cols = _check(columns, allow)
    r = shapefile.Reader(str(path))
    for sr in r.iterShapeRecords(fields=cols):
        pts = sr.shape.points
        yield sr.record.as_dict(), ((pts[0][0], pts[0][1]) if pts else None)


def iter_geometry_only(path: str | Path) -> Iterator[tuple[float, float] | None]:
    """Geometry without decoding any attribute (used for facilities so names/addresses are never read)."""
    r = shapefile.Reader(str(path))
    for shp in r.iterShapes():
        yield (shp.points[0][0], shp.points[0][1]) if shp.points else None


def crs_is_web_mercator(path: str | Path) -> bool:
    prj = Path(str(path)).with_suffix(".prj")
    return prj.exists() and "Web_Mercator" in prj.read_text()
