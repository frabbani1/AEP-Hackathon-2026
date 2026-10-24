"""Pipeline orchestration: validate, read, sanitize, link, zone, write, gate, publish."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from . import __version__
from .config import Thresholds, load_thresholds
from .contracts import (
    LayerContract,
    default_config_dir,
    load_layer_contracts,
    load_topology_contract,
    load_workbook_contract,
    validate_inputs,
)
from .dates import apply_placeholder_normalization, to_date
from .errors import ConfigError
from .labor import (
    UNKNOWN_ASSET,
    build_assumptions_register,
    check_mapping_covers,
    load_labor_mapping,
    mapping_as_output,
    read_workbook,
    validate_mapping,
)
from .layers import read_layer
from .leakgate import run_leak_gate
from .linking import (
    asset_token,
    attach_downstream,
    build_crosswalk,
    circuit_token,
    device_token,
    enforce_coverage,
    link_devices,
    link_premises,
    snap_damage,
    snap_layers,
)
from .privacy import load_privacy_rules
from .rawguard import RawGuard, assert_output_outside_raw
from .report import SAFE_CATEGORICALS, TOP_VALUES, render_report
from .tokens import Tokenizer
from .topology import build_graph, parse_topology
from .writers import WrittenFile, write_json, write_parquet, write_text
from .zones import CATEGORY_COLUMNS, assign_zones, premise_facility_distances, zone_proximity_bands

ARCOS = "arcos_prod_damage_09_28_Clip"
MANIFEST_SCHEMA_VERSION = 1
SERVING_DIRS = ("server", "public", "reports")


@dataclass
class RunResult:
    out_dir: Path
    manifest: dict
    outputs: list[WrittenFile]


# --------------------------------------------------------------------------- table builders
def _token_map(values: pd.Series, make) -> dict:
    return {value: make(value) for value in values.dropna().unique()}


def _geo(frame: pd.DataFrame, geometry, crs="EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(frame.reset_index(drop=True), geometry=list(geometry), crs=crs)


def pole_table(frame: gpd.GeoDataFrame, tok: Tokenizer) -> gpd.GeoDataFrame:
    table = pd.DataFrame(
        {
            "asset_token": [asset_token(tok, "Pole_Clip", i) for i in frame["id"]],
            "status": frame["status"].to_numpy(),
            "usage": frame["usage"].to_numpy(),
            "material": frame["material"].to_numpy(),
            "height": frame["height"].to_numpy(),
            "pole_class": frame["class"].to_numpy(),
            "install_date": frame["date_insta"].to_numpy(),
            "install_date_is_placeholder": frame["date_insta_is_placeholder"].to_numpy(),
            "manufacture_date": frame["date_manuf"].to_numpy(),
            "manufacture_date_is_placeholder": frame["date_manuf_is_placeholder"].to_numpy(),
            "owner_type": frame["owner_type"].to_numpy(),
        }
    )
    return _geo(table, frame.geometry.to_numpy())


def line_table(frame: gpd.GeoDataFrame, layer: str, tok: Tokenizer, secondary: bool) -> gpd.GeoDataFrame:
    circuits = _token_map(frame["circuit_id"], lambda v: circuit_token(tok, v))
    data = {
        "asset_token": [asset_token(tok, layer, i) for i in frame["id"]],
        "circuit_token": frame["circuit_id"].map(circuits).to_numpy(),
        "circuit_alias": frame["aep_circui"].to_numpy(),
        "substation_alias": frame["aep_statio"].to_numpy(),
        "status": frame["status"].to_numpy(),
        "network_type": frame["network_ty"].to_numpy(),
        "nominal_voltage": frame["nominal_vo"].to_numpy(),
        "usage": frame["usage"].to_numpy(),
        "phase_order": frame["phase_orde"].to_numpy(),
        "construction": frame["aep_constr"].to_numpy(),
        "mounting": frame["mounting"].to_numpy(),
        "reported_length": frame["report_len"].to_numpy(),
        "install_date": frame["aep_date_i"].to_numpy(),
        "install_date_is_placeholder": frame["aep_date_i_is_placeholder"].to_numpy(),
        "owner_type": frame["owner_type"].to_numpy(),
        "cable_count": frame["cable_coun"].to_numpy(),
    }
    if secondary:
        data["conductor_spec"] = frame["spec_name"].to_numpy()
    return _geo(pd.DataFrame(data), frame.geometry.to_numpy())


def damage_table(
    frame: gpd.GeoDataFrame, links: pd.DataFrame, mapping, tok: Tokenizer
) -> gpd.GeoDataFrame:
    asset_types = frame["Asset_Type"].fillna(UNKNOWN_ASSET).to_numpy()
    damage_types = frame["Inform_Wor"].to_numpy()
    groups, bases, assumptions = [], [], []
    for asset, damage in zip(asset_types, damage_types):
        entry = mapping.assets.get((asset, damage))
        groups.append(entry.labor_group if entry else None)
        bases.append(entry.basis if entry else "none")
        assumptions.append(entry.assumption_id if entry and entry.assumption_id else None)
    table = pd.DataFrame(
        {
            "damage_token": [tok.token("damage", i) for i in range(len(frame))],
            "report_date": frame["Report_Tim"].to_numpy(),
            "repaired": frame["Repaired"].map({"Yes": True, "No": False}).astype("boolean").to_numpy(),
            "repaired_date": frame["Repaired_D"].to_numpy(),
            "asset_type": asset_types,
            "damage_type": damage_types,
            "circuit_alias": frame["Circuit"].to_numpy(),
            "substation_alias": frame["Substation"].to_numpy(),
            "access_issue": frame["Access_Iss"].to_numpy(),
            "special_equipment": frame["Special_Eq"].to_numpy(),
            "media_count": frame["Media"].round().astype("Int64").to_numpy(),
        }
    )
    links = links.reset_index(drop=True)
    table = pd.concat([table, links], axis=1)
    table["labor_group"] = groups
    table["labor_basis"] = bases
    table["labor_assumption_ids"] = assumptions
    return _geo(table, frame.geometry.to_numpy())


# --------------------------------------------------------------------------- profiling helpers
def _profile_layer(name: str, contract: LayerContract, frame: pd.DataFrame) -> dict:
    classes = [f.sensitivity for f in contract.fields.values()]
    null_rates = {}
    for field in contract.fields.values():
        if field.sensitivity == "public" and field.name in frame.columns:
            null_rates[field.name] = float(frame[field.name].isna().mean())
    return {
        "name": name,
        "records": int(len(frame)),
        "kept_public": classes.count("public"),
        "kept_internal": classes.count("internal"),
        "dropped": classes.count("drop"),
        "undocumented": classes.count("undocumented"),
        "null_rates": null_rates,
    }


def _categoricals(frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, dict[str, int]]]:
    result: dict[str, dict[str, dict[str, int]]] = {}
    for name, fields in SAFE_CATEGORICALS.items():
        if name not in frames:
            continue
        entry = {}
        for field in fields:
            counts = frames[name][field].fillna("(missing)").astype(str).value_counts().head(TOP_VALUES)
            entry[field] = {str(k): int(v) for k, v in counts.items()}
        result[name] = entry
    return result


def _count_nonconforming(values: set, pattern: str) -> int:
    """How many distinct alias values do not follow the anonymized label form (counts only)."""
    return sum(1 for value in values if not re.fullmatch(pattern, str(value)))


def _busiest_week_share(dates: pd.Series) -> float:
    """Share of dated records that fall in the busiest 7-day window."""
    days = dates.dropna().dt.normalize().sort_values().to_numpy()
    if len(days) == 0:
        return 0.0
    ends = days.searchsorted(days + np.timedelta64(7, "D"), side="left")
    return float((ends - range(len(days))).max() / len(days))


# --------------------------------------------------------------------------- the run
def run_pipeline(*, raw_dir: Path, out_dir: Path, key: bytes, config_dir: Path | None = None) -> RunResult:
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    config_dir = Path(config_dir) if config_dir else default_config_dir()

    if not raw_dir.is_dir():
        raise ConfigError(f"Raw data directory not found: {raw_dir}")
    assert_output_outside_raw(raw_dir, out_dir)

    tok = Tokenizer(key)
    thresholds = load_thresholds(config_dir)
    rules = load_privacy_rules(config_dir)
    layer_contracts = load_layer_contracts(config_dir)
    topology_contract = load_topology_contract(config_dir)
    workbook_contract = load_workbook_contract(config_dir)
    mapping = load_labor_mapping(config_dir)

    guard = RawGuard(raw_dir)
    input_hashes = guard.snapshot()
    verified = False
    staging = out_dir / f".staging-{os.getpid()}"
    try:
        result = _run(
            raw_dir, out_dir, staging, tok, thresholds, rules, layer_contracts, topology_contract,
            workbook_contract, mapping, guard, input_hashes, config_dir,
        )
        verified = True
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not verified:
            guard.verify()


def _run(
    raw_dir, out_dir, staging, tok, thresholds: Thresholds, rules, layer_contracts, topology_contract,
    workbook_contract, mapping, guard: RawGuard, input_hashes: dict[str, str], config_dir: Path,
) -> RunResult:
    # ---- 1. contracts
    inputs = validate_inputs(raw_dir, config_dir)

    # ---- 2. read with sensitivity classes applied, then conform
    frames = {name: read_layer(raw_dir, contract) for name, contract in layer_contracts.items()}
    layer_profiles = [_profile_layer(name, layer_contracts[name], frames[name]) for name in sorted(frames)]
    categoricals = _categoricals(frames)
    for name in ("Pole_Clip", "Primary_ExportFeatures", "Secondary_ExportFeatures"):
        frames[name] = apply_placeholder_normalization(frames[name], layer_contracts[name])
    arcos = frames[ARCOS].copy()
    arcos["Report_Tim"] = to_date(arcos["Report_Tim"])
    arcos["Repaired_D"] = to_date(arcos["Repaired_D"])
    frames[ARCOS] = arcos

    # ---- 3. topology
    topology = parse_topology(raw_dir / topology_contract.path, topology_contract)
    graph = build_graph(topology)

    # ---- 4. links
    premise_links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    device_layers = {n: frames[n] for n in ("Transformer_Clip", "Fuse_Bank_Clip", "Recloser_Bank_Clip", "Switch_Clip")}
    device_links = link_devices(graph, device_layers, tok)
    crosswalk_layers = {
        n: frames[n]
        for n in ("Transformer_Clip", "Fuse_Bank_Clip", "Recloser_Bank_Clip", "Switch_Clip",
                  "Primary_ExportFeatures", "Secondary_ExportFeatures")
    }
    crosswalk = build_crosswalk(crosswalk_layers, tok)
    asset_layers = snap_layers(frames, device_links, tok)
    snapped = snap_damage(arcos, asset_layers, thresholds.damage_asset_layers, thresholds.snap_tolerance_m, thresholds.metric_crs)
    damage_links = attach_downstream(snapped.damage, device_links.devices)

    coverage = {"customer_to_topology": premise_links.coverage, **device_links.coverage, **snapped.coverage}
    enforce_coverage(coverage, thresholds.coverage_minimums)

    # ---- 5. zones and proximity
    assignment = assign_zones(
        topology, graph, premise_links.premises, premise_links.raw_by_token, thresholds.zone_minimum_premises, tok
    )
    customers = frames["Customer_Clip"]
    geometry_by_number = dict(zip(customers["premise_nu"].astype(str), customers.geometry))
    points = gpd.GeoSeries(
        [geometry_by_number.get(premise_links.raw_by_token[t]) for t in premise_links.premises["premise_token"]],
        crs="EPSG:4326",
    )
    facilities = {name: frames[name] for name in CATEGORY_COLUMNS}
    distances = premise_facility_distances(points, facilities, thresholds.metric_crs)
    zones = zone_proximity_bands(assignment.zones, assignment.members, distances, thresholds.distance_bands_km)
    del distances, points, geometry_by_number  # exact distances and coordinates never leave memory

    # ---- 6. labor reference
    items, labor_parameters = read_workbook(raw_dir / workbook_contract.path)
    validate_mapping(mapping, items)
    pairs = {(a if isinstance(a, str) else UNKNOWN_ASSET, d) for a, d in zip(arcos["Asset_Type"], arcos["Inform_Wor"])}
    check_mapping_covers(mapping, pairs, set(arcos["Special_Eq"].dropna()), set(arcos["Access_Iss"].dropna()))
    register = build_assumptions_register(mapping)

    # ---- 7. tables
    premises = premise_links.premises.merge(assignment.premise_zones, on="premise_token", how="left")
    device_tokens = {key: device_token(tok, key) for key in graph.nodes}
    edges = pd.DataFrame(
        {
            "child_token": [device_tokens[c] for c, _ in graph.edges],
            "parent_token": [device_tokens[p] for _, p in graph.edges],
            "child_has_multiple_parents": [c in graph.multi_parent for c, _ in graph.edges],
        }
    )
    minimum = thresholds.zone_minimum_premises
    published = zones[zones["meets_minimum"]].copy()
    public_zones = published[
        ["zone_token", "circuit_token", "zone_level", "premise_count", "hospital_band", "fire_band", "ambulance_band"]
    ]
    circuit_sizes = premises.groupby("circuit_token").size().rename("premise_count").reset_index()
    zone_counts = published.groupby("circuit_token").size().rename("zone_count").reset_index()
    public_circuits = circuit_sizes.merge(zone_counts, on="circuit_token", how="left")
    public_circuits["zone_count"] = public_circuits["zone_count"].fillna(0).astype("int64")
    public_circuits = public_circuits[public_circuits["premise_count"] >= minimum][
        ["circuit_token", "zone_count", "premise_count"]
    ]
    damage = damage_table(arcos, damage_links, mapping, tok)

    # ---- 8. write to staging
    if staging.exists():
        shutil.rmtree(staging)
    outputs: list[WrittenFile] = []
    server, public, reports = staging / "server", staging / "public", staging / "reports"

    def put(frame, path, sort_by):
        outputs.append(write_parquet(frame, path, staging, sort_by))

    put(premises, server / "premises.parquet", ["premise_token"])
    put(premise_links.chains, server / "premise_chains.parquet", ["premise_token", "position"])
    put(edges, server / "topology_edges.parquet", ["child_token", "parent_token"])
    put(device_links.devices, server / "devices.parquet", ["device_token"])
    put(pole_table(frames["Pole_Clip"], tok), server / "assets_poles.parquet", ["asset_token"])
    put(line_table(frames["Primary_ExportFeatures"], "Primary_ExportFeatures", tok, False), server / "assets_primary.parquet", ["asset_token"])
    put(line_table(frames["Secondary_ExportFeatures"], "Secondary_ExportFeatures", tok, True), server / "assets_secondary.parquet", ["asset_token"])
    put(damage, server / "damage_records.parquet", ["damage_token"])
    put(crosswalk, server / "circuit_crosswalk.parquet", ["circuit_token", "circuit_alias", "substation_alias"])
    put(zones, server / "zones.parquet", ["zone_token"])
    put(items, server / "labor_items.parquet", ["item_id"])
    outputs.append(write_json(mapping_as_output(mapping), server / "labor_mapping.json", staging))
    outputs.append(write_json(register, server / "assumptions_register.json", staging))
    outputs.append(write_json(labor_parameters, server / "labor_parameters.json", staging))
    put(public_zones, public / "zones.parquet", ["zone_token"])
    put(public_circuits, public / "circuits.parquet", ["circuit_token"])

    # ---- 9. profile report
    unlinked = damage_links[damage_links["link_status"] == "unlinked"]
    switch_devices = device_links.devices[device_links.devices["gis_layer"] == "Switch_Clip"]
    dates = arcos["Report_Tim"]
    placeholder_rates = {
        "poles": float(frames["Pole_Clip"]["date_insta_is_placeholder"].mean()),
        "primary lines": float(frames["Primary_ExportFeatures"]["aep_date_i_is_placeholder"].mean()),
        "secondary lines": float(frames["Secondary_ExportFeatures"]["aep_date_i_is_placeholder"].mean()),
    }
    context = {
        "pipeline_version": __version__,
        "layers": layer_profiles,
        "categoricals": categoricals,
        "coverage": {link: entry.as_manifest(thresholds.coverage_minimums.get(link)) for link, entry in coverage.items()},
        "topology": {
            "chains": graph.chain_count,
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "components": graph.components,
            "multi_parent_nodes": len(graph.multi_parent),
            "rejected_lines": topology.rejected_lines,
            "duplicate_premises": topology.duplicate_premises,
            "attribute_conflicts": topology.attribute_conflicts,
            "circuit_conflicts": graph.circuit_conflicts,
        },
        "crosswalk": {
            "circuits": int(crosswalk["circuit_token"].nunique()),
            "ambiguous_circuits": int(crosswalk.loc[crosswalk["ambiguous"], "circuit_token"].nunique()),
            "nonconforming_circuit_aliases": _count_nonconforming(
                set(crosswalk["circuit_alias"].dropna()) | set(arcos["Circuit"].dropna()), r"CIR\d+"
            ),
            "nonconforming_substation_aliases": _count_nonconforming(
                set(crosswalk["substation_alias"].dropna()) | set(arcos["Substation"].dropna()), r"STATION\d+"
            ),
        },
        "zone_minimum": minimum,
        "zone_distribution": assignment.distribution,
        "published_zones": int(len(public_zones)),
        "premises_unpublished": int((~assignment.premise_zones["zone_meets_minimum"]).sum()),
        "damage": {
            "records": int(len(damage_links)),
            "date_min": str(dates.min().date()) if dates.notna().any() else "n/a",
            "date_max": str(dates.max().date()) if dates.notna().any() else "n/a",
            "has_time_component": bool((dates.dropna() != dates.dropna().dt.normalize()).any()),
            "linked": int((damage_links["link_status"] == "linked").sum()),
            "unlinked": int(len(unlinked)),
            "unlinked_reasons": {str(k): int(v) for k, v in unlinked["link_reason"].value_counts().items()},
            "downstream_known": int(damage_links["downstream_impact_known"].sum()),
            "busiest_week_share": _busiest_week_share(dates),
        },
        "placeholder_rates": placeholder_rates,
        "switch_in_topology": {"total": int(len(switch_devices)), "in_topology": int(switch_devices["in_topology"].sum())},
        "labor": {
            "items": int(len(items)),
            "groups": int(items["group_code"].nunique()),
            "indirect_multiplier": labor_parameters["indirect_labor_multiplier"]["value"],
            "review_status": mapping.review.get("status"),
            "assumptions": len(register),
        },
    }
    outputs.append(write_text(render_report(context), reports / "profile_report.md", staging))

    # ---- 10. leak gate, then manifest, then the gate again over everything
    leak_check = run_leak_gate(staging, rules)
    guard.verify()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_version": __version__,
        "key_fingerprint": tok.fingerprint(),
        "configuration": thresholds.as_manifest(),
        "inputs": [
            {"path": rel, "sha256": sha, "bytes": (raw_dir / rel).stat().st_size} for rel, sha in sorted(input_hashes.items())
        ],
        "input_records": {
            "layers": {name: check.records for name, check in sorted(inputs.layers.items())},
            "topology_lines": inputs.topology_lines,
            "workbook_rows": dict(sorted(inputs.workbook_rows.items())),
        },
        "unlisted_fields": {name: list(c.unlisted_fields) for name, c in sorted(inputs.layers.items()) if c.unlisted_fields},
        "outputs": [
            {"path": o.path, "records": o.records, "bytes": o.bytes, "sha256": o.sha256}
            for o in sorted(outputs, key=lambda o: o.path)
        ],
        "coverage": context["coverage"],
        "link_agreement": premise_links.circuit_agreement,
        "topology": context["topology"],
        "crosswalk": context["crosswalk"],
        "zones": {
            "minimum_premises": minimum,
            "published": context["published_zones"],
            "distribution": assignment.distribution,
        },
        "damage": {k: v for k, v in context["damage"].items() if k != "has_time_component"},
        "placeholder_rates": {k: round(v, 6) for k, v in placeholder_rates.items()},
        "labor": context["labor"],
        "leak_check": leak_check,
        "raw_data_unchanged": True,
    }
    write_json(manifest, reports / "manifest.json", staging)
    run_leak_gate(staging, rules)

    # ---- 11. publish: replace previous outputs only after every check passed
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in SERVING_DIRS:
        target = out_dir / name
        if target.exists():
            shutil.rmtree(target)
        (staging / name).rename(target)
    return RunResult(out_dir=out_dir, manifest=manifest, outputs=outputs)

