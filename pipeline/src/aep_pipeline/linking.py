"""Links between customers, topology chains, GIS devices, circuits and damage records.

Every link reports its coverage. Entries without a link are counted with a reason and
are never dropped silently or guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd

from .errors import CoverageError
from .tokens import Tokenizer
from .topology import Node, NodeKey, Topology, TopologyGraph

TRANSFORMER = "eo_power_xfrmr_inst"
ISOLATING = "eo_isolating_eqpt_inst"
CIRCUIT = "eo_circuit"

# Topology device -> GIS layer used to enrich it. Breakers have no GIS device layer.
GIS_DEVICE_LAYERS = {
    "transformer": "Transformer_Clip",
    "fuse": "Fuse_Bank_Clip",
    "recloser": "Recloser_Bank_Clip",
    "sectionalizer": "Switch_Clip",
}
GIS_ONLY_CLASS = {
    "Transformer_Clip": "transformer",
    "Fuse_Bank_Clip": "fuse",
    "Recloser_Bank_Clip": "recloser",
    "Switch_Clip": "switch",
}
GIS_ATTRIBUTE_MAP = {
    "aep_circui": "circuit_alias",
    "aep_statio": "substation_alias",
    "nominal_vo": "nominal_voltage",
    "source_nom": "nominal_voltage",
    "load_nomin": "load_voltage",
    "mounting": "mounting",
    "structure_": "structure",
    "normal_sta": "normal_state",
    "device_typ": "gis_device_type",
    "bank_confi": "bank_configuration",
    "aep_custom": "gis_customer_count",
    "status": "status",
    "owner_type": "owner_type",
}
DEVICE_ATTRIBUTE_COLUMNS = sorted(set(GIS_ATTRIBUTE_MAP.values()))

DAMAGE_COVERAGE_KEY = {
    "Transformer": "transformer",
    "Fuse Bank": "fuse",
    "Recloser Bank": "recloser",
    "Switch": "switch",
    "Pole": "pole",
    "primary": "primary",
    "secondary": "secondary",
}


# --------------------------------------------------------------------------- coverage
@dataclass
class CoverageEntry:
    link: str
    matched: int
    total: int  # primary side
    unmatched_primary: int
    other_total: int | None = None  # records on the other side, when meaningful
    unmatched_other: int | None = None
    detail: dict = field(default_factory=dict)

    @property
    def rate(self) -> float | None:
        return None if self.total == 0 else self.matched / self.total

    def as_manifest(self, minimum: float | None) -> dict:
        rate = self.rate
        return {
            "matched": self.matched,
            "primary_total": self.total,
            "unmatched_primary": self.unmatched_primary,
            "other_total": self.other_total,
            "unmatched_other": self.unmatched_other,
            "rate": None if rate is None else round(rate, 6),
            "minimum": minimum,
            "enforced": minimum is not None and rate is not None,
            "passed": True if (minimum is None or rate is None) else rate >= minimum,
            "detail": self.detail,
        }


def enforce_coverage(entries: dict[str, CoverageEntry], minimums: dict[str, float | None]) -> None:
    """Raise CoverageError for the first enforced link whose rate is below its minimum."""
    for name in sorted(entries):
        minimum = minimums.get(name)
        entry = entries[name]
        rate = entry.rate
        if minimum is None or rate is None:
            continue
        if rate < minimum:
            raise CoverageError(
                f"Link '{name}' coverage is {rate:.2%} ({entry.matched} of {entry.total}), "
                f"below the minimum {minimum:.2%}."
            )


# --------------------------------------------------------------------------- device classes
def snap_layers(
    frames: dict[str, gpd.GeoDataFrame], device_links: "DeviceLinks", tokenizer: Tokenizer
) -> dict[str, gpd.GeoDataFrame]:
    """Asset layers carrying the tokens that damage records are linked to.

    Devices use the tokens of the device table, so a damage record points at the same
    ``device_token`` as the topology. Poles and line segments get asset tokens.
    """
    layers: dict[str, gpd.GeoDataFrame] = {}
    for layer in GIS_DEVICE_LAYERS.values():
        frame = frames[layer].reset_index(drop=True).copy()
        frame["asset_token"] = device_links.gis_tokens[layer]
        layers[layer] = frame
    for layer in ("Pole_Clip", "Primary_ExportFeatures", "Secondary_ExportFeatures"):
        frame = frames[layer].reset_index(drop=True).copy()
        frame["asset_token"] = [asset_token(tokenizer, layer, gis_id) for gis_id in frame["id"]]
        layers[layer] = frame
    return layers


def device_class(node: Node) -> str:
    if node.object_type == TRANSFORMER:
        return "step_transformer" if node.subtype.strip().lower() == "step" else "transformer"
    if node.object_type == CIRCUIT:
        return "circuit_breaker"
    subtype = node.subtype.strip().lower()
    if subtype.startswith("fuse"):
        return "fuse"
    if subtype.startswith("recloser"):
        return "recloser"
    if subtype.startswith("sectionalizer"):
        return "sectionalizer"
    return "other_isolating_device"


def _gis_layer_for(cls: str) -> str | None:
    if cls == "step_transformer":
        return GIS_DEVICE_LAYERS["transformer"]
    return GIS_DEVICE_LAYERS.get(cls)


def device_token(tokenizer: Tokenizer, key: NodeKey) -> str:
    return tokenizer.token("device", f"{key[0]}:{key[1]}")


def gis_only_token(tokenizer: Tokenizer, layer: str, gis_id: object) -> str:
    return tokenizer.token("device", f"gis:{layer}:{gis_id}")


def circuit_token(tokenizer: Tokenizer, numeric_circuit_id: object) -> str:
    return tokenizer.token("circuit", str(numeric_circuit_id))


def asset_token(tokenizer: Tokenizer, layer: str, gis_id: object) -> str:
    """Token for a pole or line segment, which are not part of the device topology."""
    return tokenizer.token("asset", f"{layer}:{gis_id}")


# --------------------------------------------------------------------------- 6.1 premises
@dataclass
class PremiseLinks:
    premises: pd.DataFrame  # one row per premise (customer layer plus topology-only premises)
    chains: pd.DataFrame  # long format: premise_token, position, device_token, device_type, device_subtype
    coverage: CoverageEntry
    circuit_agreement: dict
    raw_by_token: dict[str, str]  # premise_token -> raw premise number (in memory only, never written)


def link_premises(
    customers: pd.DataFrame,
    topology: Topology,
    graph: TopologyGraph,
    tokenizer: Tokenizer,
) -> PremiseLinks:
    """Join customers to their topology chains on the raw premise number, then tokenize.

    A customer without a topology entry gets ``has_chain`` false. No chain is inferred.
    """
    chains = topology.chains
    customer_numbers = customers["premise_nu"].astype(str)
    chain_numbers = set(chains)
    in_topology = customer_numbers.isin(chain_numbers)

    customer_set = set(customer_numbers)
    topology_only = sorted(chain_numbers - customer_set)

    token_cache: dict[str, str] = {}

    def prem_token(number: str) -> str:
        token = token_cache.get(number)
        if token is None:
            token = tokenizer.token("premise", number)
            token_cache[number] = token
        return token

    rows = []
    agree_circuit = 0
    agree_xfmr = 0
    with_chain = 0
    for number, circuit_id, xfmr_key, count, kind in zip(
        customer_numbers,
        customers["circuit_id"].astype(str),
        customers["aep_xfmr_l"].astype(str),
        customers["customer_c"],
        customers["kind_of_se"],
    ):
        chain = chains.get(number)
        if chain:
            with_chain += 1
            breaker_key = chain[-1].device_key
            agree_circuit += int(breaker_key == circuit_id)
            agree_xfmr += int(chain[0].device_key == xfmr_key)
            circuit = breaker_key
        else:
            circuit = circuit_id
        rows.append(
            (
                prem_token(number),
                True,
                bool(chain),
                circuit_token(tokenizer, circuit),
                kind,
                int(count),
            )
        )
    for number in topology_only:
        rows.append((prem_token(number), False, True, circuit_token(tokenizer, chains[number][-1].device_key), None, None))

    premises = pd.DataFrame(
        rows,
        columns=["premise_token", "in_customer_layer", "has_chain", "circuit_token", "service_kind", "customer_count"],
    )
    premises["customer_count"] = premises["customer_count"].astype("Int64")

    device_tokens = {key: device_token(tokenizer, key) for key in graph.nodes}
    chain_rows = []
    for number in sorted(chains):
        token = prem_token(number)
        for position, node in enumerate(chains[number]):
            chain_rows.append((token, position, device_tokens[node.key], device_class(node), node.subtype))
    chain_frame = pd.DataFrame(
        chain_rows, columns=["premise_token", "position", "device_token", "device_type", "device_subtype"]
    )

    total = len(customer_numbers)
    matched = int(in_topology.sum())
    coverage = CoverageEntry(
        link="customer_to_topology",
        matched=matched,
        total=total,
        unmatched_primary=total - matched,
        other_total=len(chains),
        unmatched_other=len(topology_only),
        detail={"reason_unmatched_primary": "no topology entry for the premise"},
    )
    agreement = {
        "customers_with_chain": with_chain,
        "customer_circuit_equals_breaker_key": agree_circuit,
        "customer_transformer_key_equals_first_device_key": agree_xfmr,
    }
    raw_by_token = {tok: num for num, tok in token_cache.items()}
    return PremiseLinks(premises, chain_frame, coverage, agreement, raw_by_token)


# --------------------------------------------------------------------------- 6.2 devices
@dataclass
class DeviceLinks:
    devices: gpd.GeoDataFrame
    coverage: dict[str, CoverageEntry]
    gis_tokens: dict[str, list[str]]  # layer -> device token for each GIS row position


def _gis_attributes(row: pd.Series) -> dict:
    out = {}
    for source, target in GIS_ATTRIBUTE_MAP.items():
        if source in row.index:
            out[target] = row[source]
    return out


def link_devices(
    graph: TopologyGraph,
    gis_layers: dict[str, gpd.GeoDataFrame],
    tokenizer: Tokenizer,
) -> DeviceLinks:
    """Match topology devices to GIS device records, then add GIS-only devices.

    The object id is tried first. Where it does not match, a device location key is used
    if it points to exactly one unclaimed GIS record. The matching identifier is recorded.
    """
    frames = {name: frame.reset_index(drop=True) for name, frame in gis_layers.items()}
    id_index: dict[str, dict[str, int]] = {}
    key_index: dict[str, dict[str, list[int]]] = {}
    for name, frame in frames.items():
        ids: dict[str, int] = {}
        duplicated = set()
        for pos, value in enumerate(frame["id"].astype(str)):
            if value in ids:
                duplicated.add(value)
            ids[value] = pos
        for value in duplicated:
            ids.pop(value, None)
        id_index[name] = ids
        keys: dict[str, list[int]] = {}
        for pos, value in enumerate(frame["location_n"].astype(str)):
            keys.setdefault(value, []).append(pos)
        key_index[name] = keys

    claimed: dict[str, dict[int, NodeKey]] = {name: {} for name in frames}
    outcome: dict[NodeKey, tuple[str, int | None, str | None]] = {}  # node -> (matched_by, gis pos, note)
    by_layer: dict[str, list[NodeKey]] = {name: [] for name in frames}
    classes: dict[NodeKey, str] = {key: device_class(node) for key, node in graph.nodes.items()}

    for key in sorted(graph.nodes):
        cls = classes[key]
        if cls == "circuit_breaker":
            outcome[key] = ("not_applicable", None, "circuit breakers have no GIS device layer")
            continue
        layer = _gis_layer_for(cls)
        if layer is None or layer not in frames:
            outcome[key] = ("unmatched", None, "no GIS layer for this device type")
            continue
        by_layer[layer].append(key)

    for layer, keys in by_layer.items():
        for key in keys:  # pass 1: object id
            pos = id_index[layer].get(key[1])
            if pos is not None and pos not in claimed[layer]:
                claimed[layer][pos] = key
                outcome[key] = ("object_id", pos, None)
        for key in keys:  # pass 2: location key
            if key in outcome:
                continue
            candidates = [p for p in key_index[layer].get(graph.nodes[key].device_key, []) if p not in claimed[layer]]
            if len(candidates) == 1:
                claimed[layer][candidates[0]] = key
                outcome[key] = ("location_key", candidates[0], None)
            elif len(candidates) > 1:
                outcome[key] = ("unmatched", None, "location key matches several GIS records")
            else:
                outcome[key] = ("unmatched", None, "no GIS record with this id or location key")

    rows: list[dict] = []
    geometries: list = []
    gis_tokens: dict[str, list[str]] = {name: [""] * len(frame) for name, frame in frames.items()}

    for key in sorted(graph.nodes):
        node = graph.nodes[key]
        matched_by, pos, note = outcome[key]
        layer = _gis_layer_for(classes[key])
        token = device_token(tokenizer, key)
        row = {
            "device_token": token,
            "device_type": classes[key],
            "device_subtype": node.subtype,
            "circuit_token": circuit_token(tokenizer, graph.circuit_of[key]),
            "in_topology": True,
            "downstream_premises": graph.downstream[key],
            "multi_parent": key in graph.multi_parent,
            "matched_by": matched_by,
            "match_note": note,
            "gis_layer": layer if pos is not None else None,
        }
        geometry = None
        if pos is not None:
            gis_row = frames[layer].iloc[pos]
            row.update(_gis_attributes(gis_row))
            geometry = gis_row.geometry
            gis_tokens[layer][pos] = token
        rows.append(row)
        geometries.append(geometry)

    for layer, frame in frames.items():
        for pos in range(len(frame)):
            if pos in claimed[layer]:
                continue
            gis_row = frame.iloc[pos]
            token = gis_only_token(tokenizer, layer, gis_row["id"])
            row = {
                "device_token": token,
                "device_type": GIS_ONLY_CLASS[layer],
                "device_subtype": gis_row.get("device_typ"),
                "circuit_token": circuit_token(tokenizer, gis_row["circuit_id"]),
                "in_topology": False,
                "downstream_premises": None,
                "multi_parent": False,
                "matched_by": "gis_only",
                "match_note": "not present in the topology file",
                "gis_layer": layer,
            }
            row.update(_gis_attributes(gis_row))
            rows.append(row)
            geometries.append(gis_row.geometry)
            gis_tokens[layer][pos] = token

    devices = pd.DataFrame(rows)
    for column in DEVICE_ATTRIBUTE_COLUMNS:
        if column not in devices.columns:
            devices[column] = None
    devices["downstream_premises"] = devices["downstream_premises"].astype("Int64")
    devices["gis_customer_count"] = devices["gis_customer_count"].astype("Int64")
    devices = gpd.GeoDataFrame(devices, geometry=gpd.GeoSeries(geometries, crs="EPSG:4326"), crs="EPSG:4326")

    coverage: dict[str, CoverageEntry] = {}
    for cls, link in (
        ("transformer", "topology_transformer_to_gis"),
        ("fuse", "topology_fuse_to_gis"),
        ("recloser", "topology_recloser_to_gis"),
        ("sectionalizer", "topology_sectionalizer_to_gis"),
    ):
        layer = GIS_DEVICE_LAYERS[cls]
        keys = [k for k in graph.nodes if classes[k] in ((cls, "step_transformer") if cls == "transformer" else (cls,))]
        matched = [k for k in keys if outcome[k][0] in ("object_id", "location_key")]
        coverage[link] = CoverageEntry(
            link=link,
            matched=len(matched),
            total=len(keys),
            unmatched_primary=len(keys) - len(matched),
            other_total=len(frames.get(layer, [])),
            unmatched_other=len(frames.get(layer, [])) - len(claimed.get(layer, {})),
            detail={
                "matched_by_object_id": sum(1 for k in matched if outcome[k][0] == "object_id"),
                "matched_by_location_key": sum(1 for k in matched if outcome[k][0] == "location_key"),
            },
        )
    return DeviceLinks(devices, coverage, gis_tokens)


# --------------------------------------------------------------------------- 6.3 crosswalk
def build_crosswalk(layers: dict[str, pd.DataFrame], tokenizer: Tokenizer) -> pd.DataFrame:
    """Relate numeric circuit ids to circuit and substation aliases across all layers.

    A numeric id that carries more than one alias keeps all of them and is flagged ambiguous.
    """
    parts = []
    for name, frame in layers.items():
        needed = {"circuit_id", "aep_circui", "aep_statio"}
        if not needed <= set(frame.columns):
            continue
        part = frame[["circuit_id", "aep_circui", "aep_statio"]].copy()
        part["layer"] = name
        parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["circuit_token", "circuit_alias", "substation_alias", "record_count", "ambiguous"])
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.dropna(subset=["circuit_id", "aep_circui"])
    grouped = (
        combined.groupby(["circuit_id", "aep_circui", "aep_statio"], dropna=False)
        .size()
        .reset_index(name="record_count")
    )
    alias_counts = grouped.groupby("circuit_id")["aep_circui"].nunique()
    grouped["ambiguous"] = grouped["circuit_id"].map(alias_counts).gt(1)
    grouped["circuit_token"] = [circuit_token(tokenizer, value) for value in grouped["circuit_id"]]
    result = grouped.rename(columns={"aep_circui": "circuit_alias", "aep_statio": "substation_alias"})
    result = result[["circuit_token", "circuit_alias", "substation_alias", "record_count", "ambiguous"]]
    return result.sort_values(["circuit_token", "circuit_alias", "substation_alias"], kind="mergesort").reset_index(drop=True)


# --------------------------------------------------------------------------- 6.4 / 6.5 damage
NO_LAYER = "no matching layer"
OUT_OF_TOLERANCE = "no asset within tolerance"


@dataclass
class DamageLinks:
    damage: pd.DataFrame  # original index preserved; adds link columns
    coverage: dict[str, CoverageEntry]


def snap_damage(
    damage: gpd.GeoDataFrame,
    asset_layers: dict[str, gpd.GeoDataFrame],
    asset_type_to_layer: dict[str, str],
    tolerance_m: float,
    metric_crs: str,
) -> DamageLinks:
    """Link each damage record to the nearest asset of the matching layer within a tolerance.

    ``asset_layers`` maps a layer name to a GeoDataFrame with an ``asset_token`` column.
    Records outside the tolerance, or whose asset type has no layer, stay unlinked with a reason.
    """
    result = pd.DataFrame(
        {
            "linked_asset_token": pd.Series([None] * len(damage), index=damage.index, dtype="object"),
            "link_status": "unlinked",
            "link_reason": NO_LAYER,
            "snap_distance_m": np.nan,
        },
        index=damage.index,
    )
    coverage: dict[str, CoverageEntry] = {}
    metric_damage = damage.to_crs(metric_crs)

    for asset_type, layer_name in asset_type_to_layer.items():
        mask = damage["Asset_Type"] == asset_type
        count = int(mask.sum())
        if count == 0:
            continue
        layer = asset_layers.get(layer_name)
        matched = 0
        if layer is None or len(layer) == 0:
            result.loc[mask, "link_reason"] = OUT_OF_TOLERANCE
        else:
            left = metric_damage.loc[mask, ["geometry"]]
            right = layer[["asset_token", "geometry"]].to_crs(metric_crs).reset_index(drop=True)
            joined = gpd.sjoin_nearest(left, right, how="left", max_distance=tolerance_m, distance_col="dist_m")
            joined = joined.sort_values(["dist_m", "asset_token"], kind="mergesort", na_position="last")
            joined = joined[~joined.index.duplicated(keep="first")]
            hit = joined["asset_token"].notna()
            result.loc[joined.index[hit], "linked_asset_token"] = joined.loc[hit, "asset_token"]
            result.loc[joined.index[hit], "link_status"] = "linked"
            result.loc[joined.index[hit], "link_reason"] = None
            result.loc[joined.index[hit], "snap_distance_m"] = joined.loc[hit, "dist_m"].round(3)
            result.loc[joined.index[~hit], "link_reason"] = OUT_OF_TOLERANCE
            matched = int(hit.sum())
        key = DAMAGE_COVERAGE_KEY.get(asset_type, asset_type.lower().replace(" ", "_"))
        coverage[f"damage_snap_{key}"] = CoverageEntry(
            link=f"damage_snap_{key}",
            matched=matched,
            total=count,
            unmatched_primary=count - matched,
            detail={"asset_type": asset_type, "layer": layer_name, "tolerance_m": tolerance_m},
        )
    return DamageLinks(result, coverage)


def attach_downstream(damage_links: pd.DataFrame, devices: pd.DataFrame) -> pd.DataFrame:
    """Add downstream premise counts for linked damage on topology devices.

    A damaged device missing from the topology (or any asset that is not a device) has an
    unknown downstream impact. It is null here, never zero.
    """
    lookup = devices.loc[devices["in_topology"], ["device_token", "downstream_premises"]].drop_duplicates("device_token")
    merged = damage_links.merge(lookup, how="left", left_on="linked_asset_token", right_on="device_token")
    merged.index = damage_links.index
    out = damage_links.copy()
    out["downstream_premises"] = merged["downstream_premises"].astype("Int64")
    out["downstream_impact_known"] = out["downstream_premises"].notna()
    return out
