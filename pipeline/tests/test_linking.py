import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point

from aep_pipeline.config import load_thresholds
from aep_pipeline.contracts import default_config_dir, load_layer_contracts
from aep_pipeline.errors import CoverageError
from aep_pipeline.layers import read_layer
from aep_pipeline.linking import (
    CoverageEntry,
    OUT_OF_TOLERANCE,
    NO_LAYER,
    attach_downstream,
    build_crosswalk,
    enforce_coverage,
    link_devices,
    link_premises,
    snap_damage,
)
from tests.fixtures.builder import CHAINS, DEVICES, FixtureOptions, PREMISE_NUMBER
from tests.helpers import device_layers, read_all, snap_asset_layers, tokenizer, topology_and_graph


def _setup(info, config_dir):
    frames = read_all(info.raw_dir, config_dir)
    topology, graph = topology_and_graph(info.raw_dir, config_dir)
    return frames, topology, graph


# ---------------------------------------------------------------- 6.1 premises
def test_every_fixture_customer_gets_a_chain_and_tokens(fixture_raw, test_config_dir):
    frames, topology, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    assert len(links.premises) == len(CHAINS) and links.premises["has_chain"].all()
    assert links.coverage.rate == 1.0 and links.coverage.unmatched_primary == 0
    # chains: ordered nearest first, one row per device
    assert len(links.chains) == sum(len(c) for c in CHAINS.values())
    token = tok.token("premise", PREMISE_NUMBER["P01"])
    chain = links.chains[links.chains["premise_token"] == token].sort_values("position")
    assert chain["device_type"].tolist() == ["transformer", "fuse", "recloser", "circuit_breaker"]
    assert chain["position"].tolist() == [0, 1, 2, 3]
    # no raw premise number anywhere in the output tables
    for number in fixture_raw.premise_numbers.values():
        assert not links.premises.astype(str).apply(lambda col: col.str.contains(number)).any().any()
        assert not links.chains.astype(str).apply(lambda col: col.str.contains(number)).any().any()


def test_premise_missing_from_topology_has_no_chain_and_none_is_inferred(build_fixture, test_config_dir):
    info = build_fixture(FixtureOptions(customers_without_chain=2, topology_only_premises=1))
    frames, topology, graph = _setup(info, test_config_dir)
    tok = tokenizer()
    links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    orphan = tok.token("premise", info.premise_numbers["X00"])
    row = links.premises[links.premises["premise_token"] == orphan].iloc[0]
    assert bool(row["has_chain"]) is False and bool(row["in_customer_layer"]) is True
    assert links.chains[links.chains["premise_token"] == orphan].empty
    assert links.coverage.unmatched_primary == 2 and links.coverage.unmatched_other == 1
    assert links.coverage.rate == pytest.approx(len(CHAINS) / (len(CHAINS) + 2))
    only_topology = links.premises[~links.premises["in_customer_layer"]]
    assert len(only_topology) == 1 and bool(only_topology.iloc[0]["has_chain"]) is True


def test_circuit_token_follows_the_chain_breaker(fixture_raw, test_config_dir):
    frames, topology, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    row = links.premises[links.premises["premise_token"] == tok.token("premise", PREMISE_NUMBER["P15"])].iloc[0]
    assert row["circuit_token"] == tok.token("circuit", "0000002")
    assert links.circuit_agreement["customer_circuit_equals_breaker_key"] == len(CHAINS)


# ---------------------------------------------------------------- 6.2 devices
def test_device_matching_by_object_id_and_by_location_key(fixture_raw, test_config_dir):
    frames, _, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    links = link_devices(graph, device_layers(frames), tok)
    devices = links.devices.set_index("device_token")
    t1 = devices.loc[tok.token("device", "eo_power_xfrmr_inst:4001")]
    assert t1["matched_by"] == "object_id" and t1["gis_layer"] == "Transformer_Clip" and t1["downstream_premises"] == 3
    r1 = devices.loc[tok.token("device", "eo_isolating_eqpt_inst:7001")]
    assert r1["matched_by"] == "location_key" and r1["gis_layer"] == "Recloser_Bank_Clip"
    assert r1["downstream_premises"] == 13 and r1["device_type"] == "recloser"
    assert r1.geometry is not None
    # the GIS recloser row is tied to the topology device token
    assert tok.token("device", "eo_isolating_eqpt_inst:7001") in links.gis_tokens["Recloser_Bank_Clip"]
    breaker = devices.loc[tok.token("device", "eo_circuit:9001")]
    assert breaker["matched_by"] == "not_applicable" and breaker["device_type"] == "circuit_breaker"
    assert breaker["downstream_premises"] == 14


def test_unmatched_topology_device_stays_in_the_graph_without_gis_attributes(build_fixture, test_config_dir):
    info = build_fixture(FixtureOptions(extra_topology_device_not_in_gis=True))
    frames, _, graph = _setup(info, test_config_dir)
    tok = tokenizer()
    links = link_devices(graph, device_layers(frames), tok)
    devices = links.devices.set_index("device_token")
    ghost = devices.loc[tok.token("device", "eo_power_xfrmr_inst:4777")]
    assert ghost["matched_by"] == "unmatched" and bool(ghost["in_topology"]) is True
    assert ghost["gis_layer"] is None and pd.isna(ghost["circuit_alias"]) and ghost.geometry is None
    entry = links.coverage["topology_transformer_to_gis"]
    assert entry.unmatched_primary == 1 and entry.matched == 8 and entry.total == 9
    assert entry.detail["matched_by_object_id"] == 8


def test_gis_only_devices_are_included_with_unknown_downstream(fixture_raw, test_config_dir):
    frames, _, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    devices = link_devices(graph, device_layers(frames), tok).devices
    gis_only = devices[devices["matched_by"] == "gis_only"]
    assert set(gis_only["device_type"]) == {"transformer", "fuse", "switch"}
    assert len(gis_only) == 4  # one transformer, one fuse, two switches
    assert gis_only["downstream_premises"].isna().all() and not gis_only["in_topology"].any()
    assert devices["device_token"].is_unique


def test_recloser_coverage_counts_location_key_matches(fixture_raw, test_config_dir):
    frames, _, graph = _setup(fixture_raw, test_config_dir)
    links = link_devices(graph, device_layers(frames), tokenizer())
    entry = links.coverage["topology_recloser_to_gis"]
    assert (entry.matched, entry.total, entry.rate) == (2, 2, 1.0)
    assert entry.detail == {"matched_by_object_id": 0, "matched_by_location_key": 2}
    assert links.coverage["topology_fuse_to_gis"].rate == 1.0


# ---------------------------------------------------------------- 6.3 crosswalk
def test_circuit_with_two_aliases_lists_both_and_is_flagged(fixture_raw, test_config_dir):
    frames, _, _ = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    layers = {n: frames[n] for n in ("Transformer_Clip", "Fuse_Bank_Clip", "Recloser_Bank_Clip", "Switch_Clip", "Primary_ExportFeatures", "Secondary_ExportFeatures")}
    crosswalk = build_crosswalk(layers, tok)
    two = crosswalk[crosswalk["circuit_token"] == tok.token("circuit", "0000002")]
    assert sorted(two["circuit_alias"]) == ["CIR2", "CIR3"]
    assert two["ambiguous"].all()
    assert two.set_index("circuit_alias")["record_count"].to_dict() == {"CIR2": 2, "CIR3": 1}
    one = crosswalk[crosswalk["circuit_token"] == tok.token("circuit", "0000001")]
    assert one["circuit_alias"].nunique() == 1 and not one["ambiguous"].any()
    assert "0000001" not in crosswalk.astype(str).to_numpy().tolist().__str__()


@pytest.mark.real_data
def test_real_crosswalk_reports_fourteen_ambiguous_circuits(real_data_dir):
    contracts = load_layer_contracts()
    names = ("Transformer_Clip", "Fuse_Bank_Clip", "Recloser_Bank_Clip", "Switch_Clip", "Primary_ExportFeatures", "Secondary_ExportFeatures")
    layers = {n: read_layer(real_data_dir, contracts[n], with_geometry=False) for n in names}
    crosswalk = build_crosswalk(layers, tokenizer())
    assert crosswalk.loc[crosswalk["ambiguous"], "circuit_token"].nunique() == 14
    assert crosswalk["circuit_token"].nunique() == 121


# ---------------------------------------------------------------- 6.4 snapping
def _snap_inputs(fixture_raw, test_config_dir):
    frames, _, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    device_links = link_devices(graph, device_layers(frames), tok)
    return frames, device_links, snap_asset_layers(frames, device_links, tok), tok


def test_fixture_damage_links_to_the_right_assets(fixture_raw, test_config_dir):
    frames, device_links, assets, tok = _snap_inputs(fixture_raw, test_config_dir)
    thresholds = load_thresholds(test_config_dir)
    damage = frames["arcos_prod_damage_09_28_Clip"]
    result = snap_damage(damage, assets, thresholds.damage_asset_layers, thresholds.snap_tolerance_m, thresholds.metric_crs)
    table = result.damage
    by_type = damage["Asset_Type"]
    fuse_near_f1 = table[(by_type == "Fuse Bank")].iloc[0]
    assert fuse_near_f1["link_status"] == "linked"
    assert fuse_near_f1["linked_asset_token"] == tok.token("device", "eo_isolating_eqpt_inst:5001")
    assert 2.0 < fuse_near_f1["snap_distance_m"] < 4.0  # about 3 m
    transformer = table[by_type == "Transformer"].iloc[0]
    assert transformer["linked_asset_token"] == tok.token("device", "eo_power_xfrmr_inst:4001")
    recloser = table[by_type == "Recloser Bank"].iloc[0]
    assert recloser["linked_asset_token"] == tok.token("device", "eo_isolating_eqpt_inst:7001")  # matched by location key
    for kind in ("Pole", "primary", "secondary", "Switch"):
        assert (table[by_type == kind]["link_status"] == "linked").all(), kind
    # types without a layer stay unlinked with the "no matching layer" reason
    for kind in ("Capacitor Bank", "Light"):
        row = table[by_type == kind].iloc[0]
        assert row["link_status"] == "unlinked" and row["link_reason"] == NO_LAYER and pd.isna(row["linked_asset_token"])
    # coverage excludes types with no layer
    assert set(result.coverage) == {
        "damage_snap_transformer", "damage_snap_fuse", "damage_snap_recloser", "damage_snap_switch",
        "damage_snap_pole", "damage_snap_primary", "damage_snap_secondary",
    }
    assert all(entry.rate == 1.0 for entry in result.coverage.values())


def test_record_beyond_the_tolerance_is_not_forced_onto_an_asset(build_fixture, test_config_dir):
    info = build_fixture(FixtureOptions(unlinked_fuse_damage=True))
    frames, device_links, assets, tok = _snap_inputs(info, test_config_dir)
    thresholds = load_thresholds(test_config_dir)
    damage = frames["arcos_prod_damage_09_28_Clip"]
    result = snap_damage(damage, assets, thresholds.damage_asset_layers, thresholds.snap_tolerance_m, thresholds.metric_crs)
    far = result.damage.iloc[-1]  # about 1 km from the nearest fuse
    assert far["link_status"] == "unlinked" and far["link_reason"] == OUT_OF_TOLERANCE
    assert pd.isna(far["linked_asset_token"]) and pd.isna(far["snap_distance_m"])
    assert result.coverage["damage_snap_fuse"].matched == 2 and result.coverage["damage_snap_fuse"].total == 3


def test_fifty_meters_is_out_and_three_meters_is_in():
    tol = 5.0
    asset_layers = {"Fuse_Bank_Clip": gpd.GeoDataFrame({"asset_token": ["fuse-token"]}, geometry=[Point(-80.0, 37.0)], crs=4326)}
    meters = lambda m: m / 111_320.0
    damage = gpd.GeoDataFrame(
        {"Asset_Type": ["Fuse Bank", "Fuse Bank", "Capacitor Bank"]},
        geometry=[Point(-80.0, 37.0 + meters(3)), Point(-80.0, 37.0 + meters(50)), Point(-80.0, 37.0)],
        crs=4326,
    )
    result = snap_damage(damage, asset_layers, {"Fuse Bank": "Fuse_Bank_Clip"}, tol, "EPSG:32617").damage
    assert result["link_status"].tolist() == ["linked", "unlinked", "unlinked"]
    assert result["link_reason"].tolist() == [None, OUT_OF_TOLERANCE, NO_LAYER]


def test_equidistant_assets_are_resolved_deterministically():
    layer = gpd.GeoDataFrame({"asset_token": ["b-token", "a-token"]}, geometry=[Point(-80.0, 37.0), Point(-80.0, 37.0)], crs=4326)
    damage = gpd.GeoDataFrame({"Asset_Type": ["Pole"]}, geometry=[Point(-80.0, 37.0)], crs=4326)
    result = snap_damage(damage, {"Pole_Clip": layer}, {"Pole": "Pole_Clip"}, 5.0, "EPSG:32617").damage
    assert result.iloc[0]["linked_asset_token"] == "a-token"


# ---------------------------------------------------------------- 6.5 downstream impact
def test_downstream_counts_for_devices_and_unknown_not_zero_otherwise(fixture_raw, test_config_dir):
    frames, device_links, assets, tok = _snap_inputs(fixture_raw, test_config_dir)
    thresholds = load_thresholds(test_config_dir)
    damage = frames["arcos_prod_damage_09_28_Clip"]
    snapped = snap_damage(damage, assets, thresholds.damage_asset_layers, thresholds.snap_tolerance_m, thresholds.metric_crs).damage
    table = attach_downstream(snapped, device_links.devices)
    by_type = damage["Asset_Type"]
    transformer = table[by_type == "Transformer"].iloc[0]
    assert transformer["downstream_premises"] == 3 and bool(transformer["downstream_impact_known"])
    recloser = table[by_type == "Recloser Bank"].iloc[0]
    assert recloser["downstream_premises"] == 13
    fuses = table[by_type == "Fuse Bank"]
    assert fuses.iloc[0]["downstream_premises"] == 7  # F1
    gis_only_fuse = fuses.iloc[1]  # a fuse that is not in the topology
    assert pd.isna(gis_only_fuse["downstream_premises"]) and not gis_only_fuse["downstream_impact_known"]
    for kind in ("Pole", "primary", "secondary", "Switch", "Light"):
        sub = table[by_type == kind]
        assert sub["downstream_premises"].isna().all() and not sub["downstream_impact_known"].any(), kind
    assert (table["downstream_premises"].dropna() > 0).all()  # unknown is null, never zero


# ---------------------------------------------------------------- 6.6 coverage thresholds
def test_enforcement_names_the_link_rate_and_threshold():
    entries = {"customer_to_topology": CoverageEntry("customer_to_topology", matched=90, total=100, unmatched_primary=10)}
    with pytest.raises(CoverageError) as error:
        enforce_coverage(entries, {"customer_to_topology": 0.995})
    message = str(error.value)
    assert "customer_to_topology" in message and "90.00%" in message and "99.50%" in message
    assert error.value.exit_code == 2


def test_links_without_a_minimum_or_without_records_are_not_enforced():
    entries = {
        "a": CoverageEntry("a", matched=0, total=10, unmatched_primary=10),
        "b": CoverageEntry("b", matched=0, total=0, unmatched_primary=0),
    }
    enforce_coverage(entries, {"a": None, "b": 0.99})


def test_fixture_match_rate_below_threshold_fails(build_fixture, test_config_dir):
    info = build_fixture(FixtureOptions(customers_without_chain=1))
    frames, topology, graph = _setup(info, test_config_dir)
    links = link_premises(frames["Customer_Clip"], topology, graph, tokenizer())
    thresholds = load_thresholds(test_config_dir)
    with pytest.raises(CoverageError) as error:
        enforce_coverage({"customer_to_topology": links.coverage}, thresholds.coverage_minimums)
    assert "customer_to_topology" in str(error.value)


def test_clean_fixture_meets_every_enforced_minimum(fixture_raw, test_config_dir):
    frames, topology, graph = _setup(fixture_raw, test_config_dir)
    tok = tokenizer()
    thresholds = load_thresholds(test_config_dir)
    entries = {"customer_to_topology": link_premises(frames["Customer_Clip"], topology, graph, tok).coverage}
    device_links = link_devices(graph, device_layers(frames), tok)
    entries.update(device_links.coverage)
    entries.update(snap_damage(
        frames["arcos_prod_damage_09_28_Clip"], snap_asset_layers(frames, device_links, tok),
        thresholds.damage_asset_layers, thresholds.snap_tolerance_m, thresholds.metric_crs).coverage)
    enforce_coverage(entries, thresholds.coverage_minimums)


def test_initial_thresholds_match_the_design_table():
    minimums = load_thresholds(default_config_dir()).coverage_minimums
    assert minimums["customer_to_topology"] == 0.995
    assert minimums["topology_transformer_to_gis"] == 0.99
    assert minimums["topology_fuse_to_gis"] == 0.97
    assert minimums["topology_recloser_to_gis"] == 0.99
    for key in ("damage_snap_transformer", "damage_snap_fuse", "damage_snap_recloser"):
        assert minimums[key] == 0.94
    # set from the first full run on the real data
    for key in ("damage_snap_pole", "damage_snap_primary", "damage_snap_secondary"):
        assert minimums[key] == 0.98
    assert minimums["damage_snap_switch"] == 0.80
    thresholds = load_thresholds(default_config_dir())
    assert thresholds.zone_minimum_premises == 10 and thresholds.snap_tolerance_m == 5.0
    assert thresholds.distance_bands_km == (2.0, 5.0, 10.0)
