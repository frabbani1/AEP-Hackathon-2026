"""Protection zones and coarse facility-proximity bands.

A premise is placed in the first device on its upstream chain whose downstream premise
count reaches the configured minimum. A zone means the whole downstream set of that device,
so every published zone covers at least the minimum number of premises. If no device on the
chain qualifies, the premise is placed at circuit level and that zone is marked as not
meeting the minimum, so it is never published for customer-facing use.

Exact distances to priority facilities exist only in memory. Only a coarse band per zone
is returned.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from .linking import circuit_token, device_class, device_token
from .tokens import Tokenizer
from .topology import NodeKey, Topology, TopologyGraph

CIRCUIT_LEVEL = "circuit"
CATEGORY_COLUMNS = {
    "Hospitals_Medic_Pairwis_Clip": "hospital_band",
    "Fire_Stations_E_Pairwis_Clip": "fire_band",
    "Ambulance_Servi_Pairwis_Clip": "ambulance_band",
}
UNKNOWN_BAND = "unknown"


@dataclass
class ZoneAssignment:
    premise_zones: pd.DataFrame  # premise_token, zone_token, zone_level, zone_meets_minimum
    zones: pd.DataFrame  # zone_token, zone_level, circuit_token, premise_count, meets_minimum
    distribution: dict[str, dict[str, int]]  # level -> {"premises": n, "zones": z}
    members: dict[str, list[int]]  # zone_token -> premise row positions (in memory only)


def assign_zones(
    topology: Topology,
    graph: TopologyGraph,
    premises: pd.DataFrame,
    raw_by_token: dict[str, str],
    minimum: int,
    tokenizer: Tokenizer,
) -> ZoneAssignment:
    """Assign every premise to a protection zone. ``premises`` comes from ``link_premises``."""
    chains = topology.chains
    positions = {token: pos for pos, token in enumerate(premises["premise_token"])}
    device_tokens = {key: device_token(tokenizer, key) for key in graph.nodes}
    classes = {key: device_class(node) for key, node in graph.nodes.items()}
    circuit_totals = premises.groupby("circuit_token").size().to_dict()

    zone_token_of: list[str] = [""] * len(premises)
    zone_level_of: list[str] = [""] * len(premises)
    meets_of: list[bool] = [False] * len(premises)
    zone_nodes: dict[str, NodeKey] = {}

    premise_tokens = list(premises["premise_token"])
    circuit_tokens = list(premises["circuit_token"])
    for pos, token in enumerate(premise_tokens):
        number = raw_by_token[token]
        chain = chains.get(number)
        chosen: NodeKey | None = None
        if chain:
            for node in chain:
                if graph.downstream[node.key] >= minimum:
                    chosen = node.key
                    break
        if chosen is not None:
            zone_token = device_tokens[chosen]
            zone_nodes[zone_token] = chosen
            zone_token_of[pos] = zone_token
            zone_level_of[pos] = classes[chosen]
            meets_of[pos] = True
        else:
            zone_token_of[pos] = circuit_tokens[pos]
            zone_level_of[pos] = CIRCUIT_LEVEL
            meets_of[pos] = circuit_totals[circuit_tokens[pos]] >= minimum

    premise_zones = pd.DataFrame(
        {
            "premise_token": premise_tokens,
            "zone_token": zone_token_of,
            "zone_level": zone_level_of,
            "zone_meets_minimum": meets_of,
        }
    )

    # Zone membership: the downstream set of each device zone, or every premise on a circuit.
    members: dict[str, list[int]] = {token: [] for token in zone_nodes}
    node_to_zone = {key: token for token, key in zone_nodes.items()}
    for pos, token in enumerate(premise_tokens):
        chain = chains.get(raw_by_token[token])
        if not chain:
            continue
        for node in chain:
            zone = node_to_zone.get(node.key)
            if zone is not None:
                members[zone].append(pos)
    circuit_zone_tokens = {t for t, level in zip(zone_token_of, zone_level_of) if level == CIRCUIT_LEVEL}
    for token in circuit_zone_tokens:
        members[token] = [pos for pos, c in enumerate(circuit_tokens) if c == token]

    circuit_of_zone: dict[str, str] = {}
    for token, key in zone_nodes.items():
        circuit_of_zone[token] = circuit_token(tokenizer, graph.circuit_of[key])
    for token in circuit_zone_tokens:
        circuit_of_zone[token] = token

    zone_rows = []
    for token in sorted(members):
        if token in zone_nodes:
            key = zone_nodes[token]
            level, count, meets = classes[key], graph.downstream[key], True
        else:
            level, count = CIRCUIT_LEVEL, len(members[token])
            meets = count >= minimum
        zone_rows.append((token, level, circuit_of_zone[token], count, meets))
    zones = pd.DataFrame(zone_rows, columns=["zone_token", "zone_level", "circuit_token", "premise_count", "meets_minimum"])

    distribution: dict[str, dict[str, int]] = {}
    for level, group in premise_zones.groupby("zone_level"):
        distribution[str(level)] = {"premises": int(len(group)), "zones": int(group["zone_token"].nunique())}
    return ZoneAssignment(premise_zones, zones, distribution, members)


# --------------------------------------------------------------------------- proximity
def band_labels(edges_km: tuple[float, ...]) -> list[str]:
    def fmt(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else str(value).replace(".", "_")

    labels = [f"under_{fmt(edges_km[0])}_km"]
    for low, high in zip(edges_km, edges_km[1:]):
        labels.append(f"{fmt(low)}_to_{fmt(high)}_km")
    labels.append(f"over_{fmt(edges_km[-1])}_km")
    return labels


def band_for(distance_m: float, edges_km: tuple[float, ...]) -> str:
    """Band label for a distance. Band edges are inclusive on the lower side (2 km is '2 to 5')."""
    if distance_m is None or np.isnan(distance_m):
        return UNKNOWN_BAND
    labels = band_labels(edges_km)
    index = int(np.searchsorted(np.array(edges_km) * 1000.0, distance_m, side="right"))
    return labels[index]


def premise_facility_distances(
    premise_points: gpd.GeoSeries,
    facilities: dict[str, gpd.GeoDataFrame],
    metric_crs: str,
) -> dict[str, np.ndarray]:
    """Distance in metres from each premise to the nearest facility of each category (in memory only).

    ``premise_points`` has one point per premise row position, with None where unknown.
    """
    result: dict[str, np.ndarray] = {}
    metric_points = premise_points.to_crs(metric_crs)
    valid = ~metric_points.isna() & ~metric_points.is_empty
    for layer, column in CATEGORY_COLUMNS.items():
        distances = np.full(len(premise_points), np.nan)
        frame = facilities.get(layer)
        if frame is not None and len(frame) and valid.any():
            left = gpd.GeoDataFrame(geometry=metric_points[valid].reset_index(drop=True), crs=metric_crs)
            right = gpd.GeoDataFrame(geometry=frame.to_crs(metric_crs).geometry.reset_index(drop=True), crs=metric_crs)
            joined = gpd.sjoin_nearest(left, right, how="left", distance_col="d")
            joined = joined[~joined.index.duplicated(keep="first")]
            distances[np.flatnonzero(valid.to_numpy())] = joined["d"].to_numpy()
        result[column] = distances
    return result


def zone_proximity_bands(
    zones: pd.DataFrame,
    members: dict[str, list[int]],
    distances: dict[str, np.ndarray],
    edges_km: tuple[float, ...],
) -> pd.DataFrame:
    """Add one coarse band per facility category to each zone (median premise distance, banded)."""
    result = zones.copy()
    for column in CATEGORY_COLUMNS.values():
        values = distances.get(column)
        bands = []
        for token in result["zone_token"]:
            positions = members.get(token, [])
            if values is None or not positions:
                bands.append(UNKNOWN_BAND)
                continue
            sample = values[positions]
            sample = sample[~np.isnan(sample)]
            bands.append(band_for(float(np.median(sample)), edges_km) if len(sample) else UNKNOWN_BAND)
        result[column] = bands
    return result
