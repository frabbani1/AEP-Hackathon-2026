"""Hex-cell bucketing with small-group suppression. Exact coordinates never leave this module."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import h3

DEFAULT_RESOLUTIONS = (8, 7, 6)   # res 8 ~ 0.7 km2 per cell (>= ~0.5 km across); coarser levels as fallback


def to_cell(lon: float, lat: float, res: int) -> str:
    return h3.latlng_to_cell(lat, lon, res)


def assign_cells(
    points: Sequence[tuple[float, float] | None], *, min_group: int = 10,
    resolutions: Sequence[int] = DEFAULT_RESOLUTIONS,
) -> list[tuple[str | None, int | None]]:
    """Give each (lon, lat) a cell such that every published (cell, resolution) group has >= min_group points.

    Going from fine to coarse, a point is assigned when at least `min_group` still-unassigned points share its cell
    at that resolution. Counting only the unassigned points matters: otherwise leftovers could be published in a
    coarse cell alone, forming a group smaller than the threshold. Points that never reach the threshold (and points
    with no geometry) get (None, None), so a small cell can never single out a household.
    """
    out: list[tuple[str | None, int | None]] = [(None, None)] * len(points)
    remaining = [i for i, p in enumerate(points) if p]
    for res in resolutions:
        cells = {i: to_cell(points[i][0], points[i][1], res) for i in remaining}
        counts = Counter(cells.values())
        still = []
        for i in remaining:
            if counts[cells[i]] >= min_group:
                out[i] = (cells[i], res)
            else:
                still.append(i)
        remaining = still
    return out


def cell_center(cell: str) -> tuple[float, float]:
    lat, lon = h3.cell_to_latlng(cell)
    return round(lat, 4), round(lon, 4)


def nearby_cells(cell: str, k: int = 1) -> set[str]:
    return set(h3.grid_disk(cell, k))


def parent_at(cell: str, res: int) -> str:
    return h3.cell_to_parent(cell, res) if h3.get_resolution(cell) > res else cell
