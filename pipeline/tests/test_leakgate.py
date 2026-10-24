import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from aep_pipeline.errors import LeakError
from aep_pipeline.leakgate import run_leak_gate, scan_tree
from aep_pipeline.privacy import load_privacy_rules


@pytest.fixture()
def rules(test_config_dir):
    return load_privacy_rules(test_config_dir)


@pytest.fixture()
def tree(tmp_path):
    for sub in ("server", "public", "reports"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _clean_tables(tree):
    pd.DataFrame({"premise_token": ["a" * 32, "b" * 32], "zone_level": ["fuse", "recloser"]}).to_parquet(
        tree / "server" / "premises.parquet"
    )
    pd.DataFrame({"zone_token": ["c" * 32], "premise_count": [12]}).to_parquet(tree / "public" / "zones.parquet")
    (tree / "reports" / "manifest.json").write_text(json.dumps({"inputs": [{"sha256": "f" * 64}], "count": 117599}))
    (tree / "reports" / "profile_report.md").write_text("# Report\n\nCustomers: 117,599\n")


def test_clean_tree_passes_and_returns_a_summary(tree, rules):
    _clean_tables(tree)
    summary = run_leak_gate(tree, rules)
    assert summary == {"passed": True, "files_scanned": 4}


def test_forbidden_column_fails_naming_artifact_and_column(tree, rules):
    _clean_tables(tree)
    pd.DataFrame({"premise_token": ["a" * 32], "road_name": ["x"]}).to_parquet(tree / "server" / "bad.parquet")
    with pytest.raises(LeakError) as error:
        run_leak_gate(tree, rules)
    assert error.value.exit_code == 3
    assert "server/bad.parquet" in str(error.value)
    assert "road_name" in str(error.value)


def test_forbidden_fragment_in_a_column_name_fails(tree, rules):
    _clean_tables(tree)
    pd.DataFrame({"has_life_support_flag": [True]}).to_parquet(tree / "server" / "flags.parquet")
    with pytest.raises(LeakError) as error:
        run_leak_gate(tree, rules)
    assert "has_life_support_flag" in str(error.value)


def test_raw_premise_number_value_fails(tree, rules):
    _clean_tables(tree)
    pd.DataFrame({"note": ["900000001", "fine"]}).to_parquet(tree / "server" / "values.parquet")
    with pytest.raises(LeakError) as error:
        run_leak_gate(tree, rules)
    assert "server/values.parquet" in str(error.value)
    assert "column note" in str(error.value)
    assert "raw identifier" in str(error.value)


def test_tokens_are_not_mistaken_for_raw_identifiers(tree, rules):
    pd.DataFrame({"t": ["0123456789abcdef0123456789abcdef", "123456789abcdef"]}).to_parquet(tree / "server" / "t.parquet")
    run_leak_gate(tree, rules)


def test_geometry_is_allowed_server_side_but_not_in_public(tree, rules):
    frame = gpd.GeoDataFrame({"asset_token": ["a" * 32]}, geometry=[Point(-80, 37)], crs=4326)
    frame.to_parquet(tree / "server" / "assets.parquet")
    run_leak_gate(tree, rules)
    frame.to_parquet(tree / "public" / "assets.parquet")
    with pytest.raises(LeakError) as error:
        run_leak_gate(tree, rules)
    assert "public/assets.parquet" in str(error.value)
    assert "geometry" in str(error.value)


def test_coordinate_and_distance_columns_are_not_allowed_in_public(tree, rules):
    pd.DataFrame({"zone_token": ["a" * 32], "latitude": [37.0]}).to_parquet(tree / "public" / "z.parquet")
    pd.DataFrame({"zone_token": ["a" * 32], "distance_km": [1.2]}).to_parquet(tree / "public" / "d.parquet")
    findings, _ = scan_tree(tree, rules)
    assert {f.artifact for f in findings} == {"public/z.parquet", "public/d.parquet"}
    # the same columns are acceptable server-side
    for name in ("z.parquet", "d.parquet"):
        (tree / "public" / name).rename(tree / "server" / name)
    assert scan_tree(tree, rules)[0] == []


def test_json_keys_and_values_are_checked(tree, rules):
    (tree / "reports" / "a.json").write_text(json.dumps({"nested": {"premise_nu": "x"}}))
    (tree / "reports" / "b.json").write_text(json.dumps({"items": ["900000002"]}))
    findings, _ = scan_tree(tree, rules)
    kinds = {(f.artifact, f.location.split(" ")[0]) for f in findings}
    assert ("reports/a.json", "key") in kinds
    assert ("reports/b.json", "value") in kinds


def test_text_report_with_a_raw_number_fails_but_formatted_counts_pass(tree, rules):
    (tree / "reports" / "ok.md").write_text("Premises: 117,599 and 1,234,567 and 42\n")
    assert scan_tree(tree, rules)[0] == []
    (tree / "reports" / "bad.md").write_text("Premise 900000003 was affected\n")
    findings, _ = scan_tree(tree, rules)
    assert [f.artifact for f in findings] == ["reports/bad.md"]


def test_unexpected_file_types_are_flagged(tree, rules):
    (tree / "server" / "dump.csv").write_text("a,b\n1,2\n")
    findings, _ = scan_tree(tree, rules)
    assert findings and findings[0].artifact == "server/dump.csv"


def test_message_lists_at_most_ten_findings(tree, rules):
    for i in range(14):
        pd.DataFrame({"address": ["x"]}).to_parquet(tree / "server" / f"f{i:02d}.parquet")
    with pytest.raises(LeakError) as error:
        run_leak_gate(tree, rules)
    assert "14 finding(s)" in str(error.value)
    assert "+4 more" in str(error.value)
