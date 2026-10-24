"""Tunable parameters from ``config/thresholds.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .contracts import default_config_dir
from .errors import ConfigError


@dataclass(frozen=True)
class Thresholds:
    zone_minimum_premises: int
    snap_tolerance_m: float
    metric_crs: str
    distance_bands_km: tuple[float, ...]
    coverage_minimums: dict[str, float | None]
    damage_asset_layers: dict[str, str]

    def as_manifest(self) -> dict:
        return {
            "zone_minimum_premises": self.zone_minimum_premises,
            "snap_tolerance_m": self.snap_tolerance_m,
            "metric_crs": self.metric_crs,
            "distance_bands_km": list(self.distance_bands_km),
            "coverage_minimums": dict(self.coverage_minimums),
        }


def load_thresholds(config_dir: Path | None = None) -> Thresholds:
    path = (config_dir or default_config_dir()) / "thresholds.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file not found: {path}") from error
    try:
        bands = tuple(float(b) for b in data["distance_bands_km"])
        if list(bands) != sorted(bands) or not bands:
            raise ConfigError("distance_bands_km must be a non-empty ascending list")
        minimum = int(data["zone_minimum_premises"])
        if minimum < 1:
            raise ConfigError("zone_minimum_premises must be at least 1")
        return Thresholds(
            zone_minimum_premises=minimum,
            snap_tolerance_m=float(data["snap_tolerance_m"]),
            metric_crs=str(data["metric_crs"]),
            distance_bands_km=bands,
            coverage_minimums=dict(data["coverage_minimums"]),
            damage_asset_layers=dict(data["damage_asset_layers"]),
        )
    except KeyError as error:
        raise ConfigError(f"thresholds.yaml is missing the setting {error}") from error
