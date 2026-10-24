"""Deterministic artifact writers.

Rows are sorted by explicit keys and compression settings are fixed, so the same input
always produces byte-identical files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

COMPRESSION = "zstd"
COMPRESSION_LEVEL = 3


@dataclass(frozen=True)
class WrittenFile:
    path: str  # relative to the output root, posix style
    records: int
    bytes: int
    sha256: str


def _digest(path: Path) -> tuple[int, str]:
    with path.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    return path.stat().st_size, sha


def _sorted(frame: pd.DataFrame, sort_by: list[str] | None) -> pd.DataFrame:
    if sort_by:
        frame = frame.sort_values(sort_by, kind="mergesort", na_position="last")
    return frame.reset_index(drop=True)


def write_parquet(frame: pd.DataFrame, path: Path, root: Path, sort_by: list[str] | None = None) -> WrittenFile:
    """Write a table (or GeoParquet when the frame has geometry) with fixed settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = _sorted(frame, sort_by)
    if isinstance(frame, gpd.GeoDataFrame):
        frame.to_parquet(
            path,
            index=False,
            compression=COMPRESSION,
            compression_level=COMPRESSION_LEVEL,
            schema_version="1.0.0",
            write_covering_bbox=False,
        )
    else:
        frame.to_parquet(
            path,
            engine="pyarrow",
            index=False,
            compression=COMPRESSION,
            compression_level=COMPRESSION_LEVEL,
        )
    size, sha = _digest(path)
    return WrittenFile(path.relative_to(root).as_posix(), len(frame), size, sha)


def write_json(data: object, path: Path, root: Path, records: int | None = None) -> WrittenFile:
    """Write JSON with sorted keys and a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True, default=str) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    size, sha = _digest(path)
    if records is None:
        records = len(data) if isinstance(data, (list, dict)) else 1
    return WrittenFile(path.relative_to(root).as_posix(), records, size, sha)


def write_text(text: str, path: Path, root: Path) -> WrittenFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    size, sha = _digest(path)
    return WrittenFile(path.relative_to(root).as_posix(), text.count("\n"), size, sha)
