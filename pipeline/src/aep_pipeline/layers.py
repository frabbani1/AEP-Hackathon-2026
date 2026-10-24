"""Layer reading with sensitivity classes applied at read time.

Only fields classed ``public`` or ``internal`` are requested from the file. Fields classed
``drop`` or ``undocumented`` are never read, so they cannot reach any table.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

from .contracts import LayerContract
from .errors import ContractError

TARGET_CRS = "EPSG:4326"


def to_wgs84_2d(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Convert to the single geographic reference system and drop any height component."""
    if frame.crs is None:
        raise ContractError("A layer has no coordinate reference system and cannot be conformed.")
    converted = frame.to_crs(TARGET_CRS)
    converted = converted.set_geometry(shapely.force_2d(converted.geometry.values))
    return converted


def read_layer(
    raw_dir: Path,
    contract: LayerContract,
    *,
    with_geometry: bool = True,
    conform: bool = True,
) -> gpd.GeoDataFrame | pd.DataFrame:
    """Read one layer, requesting only the public and internal fields of its contract."""
    path = Path(raw_dir) / contract.path
    columns = contract.columns("public", "internal")
    frame = pyogrio.read_dataframe(path, columns=columns, read_geometry=with_geometry)
    if with_geometry and conform:
        frame = to_wgs84_2d(frame)
    return frame
