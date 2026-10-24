import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from aep_pipeline.config import load_thresholds
from aep_pipeline.linking import link_premises
from aep_pipeline.zones import (
    CATEGORY_COLUMNS,
    UNKNOWN_BAND,
    assign_zones,
    band_for,
    band_labels,
    premise_facility_distances,
    zone_proximity_bands,
)
from tests.fixtures.builder import CHAINS, DEVICES, PREMISE_NUMBER
from tests.helpers import read_all, tokenizer, topology_and_graph


@pytest.fixture(scope="module")
def prepared(fixture_raw, test_config_dir):
    frames = read_all(fixture_raw.raw_dir, test_config_dir)
    topology, graph = topology_and_graph(fixture_raw.raw_dir, test_config_dir)
    tok = tokenizer()
    links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    return frames, topology, graph, tok, links


def _zone_of(assignment, tok, premise):
    token = tok.token("premise", PREMISE_NUMBER[premise])
    return assignment.premise_zones[assignment.premise_zones["premise_token"] == token].iloc[0]


def _assign(prepared, minimum):
    _, topology, graph, tok, links = prepared
    return assign_zones(topology, graph, links.premises, links.raw_by_token, minimum, tok)


# ---------------------------------------------------------------- 7.1 zone assignment
def test_default_minimum_merges_small_devices_into_the_recloser_zone(prepared):
    tok = prepared[3]
    zones = _assign(prepared, 10)
    p1 = _zone_of(zones, tok, "P01")  # T1 has 3, F1 has 7, R1 has 13
    assert p1["zone_level"] == "recloser" and p1["zone_token"] == tok.token("device", "eo_isolating_eqpt_inst:7001")
    assert bool(p1["zone_meets_minimum"])
    p14 = _zone_of(zones, tok, "P14")  # T6 1, F3 2, R2 1, B1 14
    assert p14["zone_level"] == "circuit_breaker"


def test_three_premise_transformer_is_merged_into_its_fuse_zone(prepared):
    tok = prepared[3]
    zones = _assign(prepared, 5)
    p1 = _zone_of(zones, tok, "P01")  # T1 has 3 (< 5), F1 has 7
    assert p1["zone_level"] == "fuse" and p1["zone_token"] == tok.token("device", "eo_isolating_eqpt_inst:5001")
    # T3 has exactly 5 so it keeps its own zone
    assert _zone_of(zones, tok, "P08")["zone_level"] == "transformer"


def test_minimum_is_configurable(prepared):
    tok = prepared[3]
    assert _zone_of(_assign(prepared, 3), tok, "P01")["zone_level"] == "transformer"
    assert _zone_of(_assign(prepared, 1), tok, "P14")["zone_level"] == "transformer"
    assert _zone_of(_assign(prepared, 14), tok, "P01")["zone_level"] == "circuit_breaker"


def test_circuit_with_too_few_premises_falls_back_to_circuit_level(prepared):
    tok = prepared[3]
    zones = _assign(prepared, 10)
    p15 = _zone_of(zones, tok, "P15")  # circuit 0000002 has only 2 premises
    assert p15["zone_level"] == "circuit" and not bool(p15["zone_meets_minimum"])
    assert p15["zone_token"] == tok.token("circuit", "0000002")
    row = zones.zones[zones.zones["zone_token"] == p15["zone_token"]].iloc[0]
    assert row["premise_count"] == 2 and not bool(row["meets_minimum"])


def test_every_published_zone_covers_at_least_the_minimum(prepared):
    for minimum in (1, 3, 5, 10):
        zones = _assign(prepared, minimum)
        published = zones.zones[zones.zones["meets_minimum"]]
        assert (published["premise_count"] >= minimum).all()
        # a premise marked as meeting the minimum is only ever in a published zone
        merged = zones.premise_zones.merge(zones.zones, on="zone_token")
        assert merged.loc[merged["zone_meets_minimum"], "meets_minimum"].all()


def test_every_premise_gets_exactly_one_zone(prepared):
    zones = _assign(prepared, 5)
    assert len(zones.premise_zones) == len(CHAINS)
    assert zones.premise_zones["premise_token"].is_unique
    assert (zones.premise_zones["zone_token"] != "").all()


def test_distribution_summary_counts_premises_and_zones_per_level(prepared):
    zones = _assign(prepared, 5)
    distribution = zones.distribution
    assert sum(level["premises"] for level in distribution.values()) == len(CHAINS)
    assert distribution["circuit"]["premises"] == 3  # P15, P16 (circuit 2 has 2) and P17 (circuit 3 has 1)
    assert distribution["fuse"]["premises"] >= 1


def test_premises_without_a_chain_are_placed_at_circuit_level(build_fixture, test_config_dir):
    from tests.fixtures.builder import FixtureOptions

    info = build_fixture(FixtureOptions(customers_without_chain=1))
    frames = read_all(info.raw_dir, test_config_dir)
    topology, graph = topology_and_graph(info.raw_dir, test_config_dir)
    tok = tokenizer()
    links = link_premises(frames["Customer_Clip"], topology, graph, tok)
    zones = assign_zones(topology, graph, links.premises, links.raw_by_token, 10, tok)
    orphan = tok.token("premise", info.premise_numbers["X00"])
    row = zones.premise_zones[zones.premise_zones["premise_token"] == orphan].iloc[0]
    assert row["zone_level"] == "circuit" and row["zone_token"] == tok.token("circuit", "0000001")
    assert bool(row["zone_meets_minimum"])  # circuit 0000001 has 15 premises


# ---------------------------------------------------------------- 7.2 proximity bands
def test_band_labels_follow_the_configured_edges():
    assert band_labels((2.0, 5.0, 10.0)) == ["under_2_km", "2_to_5_km", "5_to_10_km", "over_10_km"]
    assert band_labels((0.5, 1.5)) == ["under_0_5_km", "0_5_to_1_5_km", "over_1_5_km"]


@pytest.mark.parametrize(
    "meters,label",
    [(0, "under_2_km"), (1999.9, "under_2_km"), (2000, "2_to_5_km"), (4999, "2_to_5_km"), (5000, "5_to_10_km"), (10000, "over_10_km"), (34000, "over_10_km")],
)
def test_band_edges_are_inclusive_on_the_lower_side(meters, label):
    assert band_for(meters, (2.0, 5.0, 10.0)) == label


def test_missing_distance_is_unknown():
    assert band_for(float("nan"), (2.0, 5.0, 10.0)) == UNKNOWN_BAND


def test_each_zone_gets_three_bands_and_no_numeric_distance(prepared):
    frames, _, _, tok, links = prepared
    zones = _assign(prepared, 5)
    customers = frames["Customer_Clip"]
    by_number = {n: g for n, g in zip(customers["premise_nu"].astype(str), customers.geometry)}
    points = gpd.GeoSeries([by_number[links.raw_by_token[t]] for t in links.premises["premise_token"]], crs=4326)
    facilities = {k: frames[k] for k in CATEGORY_COLUMNS}
    distances = premise_facility_distances(points, facilities, "EPSG:32617")
    result = zone_proximity_bands(zones.zones, zones.members, distances, (0.1, 0.5, 1.0))
    labels = set(band_labels((0.1, 0.5, 1.0))) | {UNKNOWN_BAND}
    for column in ("hospital_band", "fire_band", "ambulance_band"):
        assert column in result.columns
        assert set(result[column]) <= labels
        assert UNKNOWN_BAND not in set(result[column])
    # nothing numeric except counts
    numeric = [c for c in result.columns if pd.api.types.is_numeric_dtype(result[c]) and result[c].dtype != bool]
    assert numeric == ["premise_count"]
    assert not any("dist" in c.lower() for c in result.columns)


def test_band_is_the_band_of_the_median_premise_distance():
    zones = pd.DataFrame({"zone_token": ["z"]})
    members = {"z": [0, 1, 2]}
    distances = {
        "hospital_band": np.array([100.0, 3000.0, 9000.0]),  # median 3 km
        "fire_band": np.array([100.0, 200.0, 12000.0]),  # median 0.2 km
        "ambulance_band": np.array([np.nan, np.nan, np.nan]),
    }
    result = zone_proximity_bands(zones, members, distances, (2.0, 5.0, 10.0)).iloc[0]
    assert result["hospital_band"] == "2_to_5_km"
    assert result["fire_band"] == "under_2_km"
    assert result["ambulance_band"] == UNKNOWN_BAND


def test_distances_are_computed_in_a_metric_projection():
    premises = gpd.GeoSeries([Point(-80.0, 37.0), None], crs=4326)
    facility = gpd.GeoDataFrame(geometry=[Point(-80.0, 37.0 + 1000 / 111_320.0)], crs=4326)
    distances = premise_facility_distances(premises, {"Hospitals_Medic_Pairwis_Clip": facility}, "EPSG:32617")
    assert distances["hospital_band"][0] == pytest.approx(1000, abs=5)
    assert np.isnan(distances["hospital_band"][1])
    assert np.isnan(distances["fire_band"]).all()  # category with no layer
