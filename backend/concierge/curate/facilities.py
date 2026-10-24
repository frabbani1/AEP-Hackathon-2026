"""Priority facilities as hex-level flags only. Names, addresses and exact points are never read or written."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from pyproj import Transformer

from . import geo
from .shapes import crs_is_web_mercator, iter_geometry_only

LAYERS = {
    "hospital": "Hospitals_Medic_Pairwis_Clip",
    "fire": "Fire_Stations_E_Pairwis_Clip",
    "ambulance": "Ambulance_Servi_Pairwis_Clip",
}


def facility_cells(data1: Path, res: int = 8) -> dict[str, set[str]]:
    """Reproject EPSG:3857 -> WGS84 where the .prj says so, then bucket each facility into a res-8 cell."""
    out: dict[str, set[str]] = {}
    for kind, stem in LAYERS.items():
        path = data1 / f"{stem}.shp"
        tf = Transformer.from_crs(3857, 4326, always_xy=True) if crs_is_web_mercator(path) else None
        cells = set()
        for pt in iter_geometry_only(path):
            if pt is None:
                continue
            lon, lat = tf.transform(pt[0], pt[1]) if tf else pt
            cells.add(geo.to_cell(lon, lat, res))
        out[kind] = cells
    return out


def nearby_flags(cell: str, facilities: Mapping[str, Iterable[str]], k: int = 1) -> dict[str, bool]:
    """True when a facility lies in this cell or an adjacent one (about 1-3 km). Works for any resolution <= 8."""
    res = geo.h3.get_resolution(cell)
    disk = geo.nearby_cells(cell, k)
    return {kind: any(geo.parent_at(fc, res) in disk for fc in cells) for kind, cells in facilities.items()}
