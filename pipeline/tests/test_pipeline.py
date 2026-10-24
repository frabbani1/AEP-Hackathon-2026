import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from aep_pipeline import pipeline as pipeline_module
from aep_pipeline.cli import main
from aep_pipeline.rawguard import checksum_tree
from aep_pipeline.tokens import Tokenizer
from tests.fixtures.builder import CHAINS, PREMISE_NUMBER, FixtureOptions

KEY = "integration-test-key-0123456789"
SERVER_FILES = {
    "premises.parquet", "premise_chains.parquet", "topology_edges.parquet", "devices.parquet",
    "assets_poles.parquet", "assets_primary.parquet", "assets_secondary.parquet", "damage_records.parquet",
    "circuit_crosswalk.parquet", "zones.parquet", "labor_items.parquet", "labor_mapping.json",
    "assumptions_register.json", "labor_parameters.json",
}


def run(raw: Path, out: Path, config: Path, key: str = KEY) -> int:
    return main(["run", "--raw", str(raw), "--out", str(out), "--config", str(config)], environ={"AEP_TOKEN_KEY": key})


@pytest.fixture(scope="module")
def completed(tmp_path_factory, fixture_raw, test_config_dir):
    """One successful run on the shared fixture."""
    out = tmp_path_factory.mktemp("curated")
    before = checksum_tree(fixture_raw.raw_dir)
    code = run(fixture_raw.raw_dir, out, test_config_dir)
    assert code == 0
    assert checksum_tree(fixture_raw.raw_dir) == before  # raw data unchanged after a successful run
    return out


def all_text(out: Path):
    """Every string in every curated file, for leak checks."""
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".parquet":
            frame = pd.read_parquet(path)
            for column in frame.columns:
                yield from (str(v) for v in frame[column].tolist())
            yield from (str(c) for c in frame.columns)
        else:
            yield path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 9.1 artifacts
def test_expected_artifacts_exist_and_nothing_else(completed):
    assert {p.name for p in (completed / "server").iterdir()} == SERVER_FILES
    assert {p.name for p in (completed / "public").iterdir()} == {"zones.parquet", "circuits.parquet"}
    assert {p.name for p in (completed / "reports").iterdir()} == {"manifest.json", "profile_report.md"}
    assert not [p for p in completed.iterdir() if p.name.startswith(".staging")]


def columns(path):
    return set(pd.read_parquet(path).columns)


def test_server_artifacts_have_the_contracted_keys_and_columns(completed):
    s = completed / "server"
    assert {"premise_token", "zone_token", "circuit_token", "has_chain", "service_kind", "customer_count"} <= columns(s / "premises.parquet")
    assert {"premise_token", "position", "device_token", "device_type", "device_subtype"} == columns(s / "premise_chains.parquet")
    assert {"child_token", "parent_token", "child_has_multiple_parents"} == columns(s / "topology_edges.parquet")
    assert {"device_token", "device_type", "circuit_token", "circuit_alias", "substation_alias", "downstream_premises", "matched_by", "nominal_voltage", "mounting", "structure", "normal_state"} <= columns(s / "devices.parquet")
    assert {"asset_token", "install_date", "install_date_is_placeholder"} <= columns(s / "assets_poles.parquet")
    for name in ("assets_primary.parquet", "assets_secondary.parquet"):
        assert {"asset_token", "circuit_token", "circuit_alias", "install_date", "install_date_is_placeholder"} <= columns(s / name)
    assert {"damage_token", "report_date", "repaired", "repaired_date", "asset_type", "damage_type", "circuit_alias", "substation_alias", "access_issue", "special_equipment", "media_count", "linked_asset_token", "link_status", "link_reason", "downstream_premises"} <= columns(s / "damage_records.parquet")
    assert {"circuit_token", "circuit_alias", "substation_alias", "record_count", "ambiguous"} == columns(s / "circuit_crosswalk.parquet")
    assert {"item_id", "group_code", "install_hours", "remove_hours", "unit_basis"} <= columns(s / "labor_items.parquet")


def test_keys_are_unique(completed):
    s = completed / "server"
    for name, key in (("premises", "premise_token"), ("devices", "device_token"), ("assets_poles", "asset_token"), ("damage_records", "damage_token"), ("zones", "zone_token"), ("labor_items", "item_id")):
        frame = pd.read_parquet(s / f"{name}.parquet")
        assert frame[key].is_unique, name
    chains = pd.read_parquet(s / "premise_chains.parquet")
    assert not chains.duplicated(["premise_token", "position"]).any()


def test_public_artifacts_are_browser_safe(completed):
    zones = pd.read_parquet(completed / "public" / "zones.parquet")
    assert list(zones.columns) == ["zone_token", "circuit_token", "zone_level", "premise_count", "hospital_band", "fire_band", "ambulance_band"]
    assert (zones["premise_count"] >= 10).all()
    circuits = pd.read_parquet(completed / "public" / "circuits.parquet")
    assert list(circuits.columns) == ["circuit_token", "zone_count", "premise_count"]
    # no geometry, no coordinates, no exact distances, no aliases in public files
    import pyarrow.parquet as pq

    for path in (completed / "public").glob("*.parquet"):
        assert b"geo" not in (pq.read_schema(path).metadata or {})
    forbidden = {"geometry", "latitude", "longitude", "distance", "alias"}
    assert not any(f in c.lower() for c in set(zones.columns) | set(circuits.columns) for f in forbidden)


def test_server_geometry_is_two_dimensional_wgs84(completed):
    for name in ("devices", "assets_poles", "assets_primary", "assets_secondary", "damage_records"):
        frame = gpd.read_parquet(completed / "server" / f"{name}.parquet")
        assert frame.crs.to_epsg() == 4326, name
        assert not frame.geometry.dropna().has_z.any(), name


def test_premises_have_no_coordinates_or_raw_ids(completed):
    premises = pd.read_parquet(completed / "server" / "premises.parquet")
    assert "geometry" not in premises.columns and len(premises) == len(CHAINS)
    chains = pd.read_parquet(completed / "server" / "premise_chains.parquet")
    assert len(chains) == sum(len(c) for c in CHAINS.values())
    tok = Tokenizer(KEY.encode())
    assert set(premises["premise_token"]) == {tok.token("premise", n) for n in PREMISE_NUMBER.values()}


def test_zones_and_premises_agree(completed):
    premises = pd.read_parquet(completed / "server" / "premises.parquet")
    zones = pd.read_parquet(completed / "server" / "zones.parquet")
    assert set(premises["zone_token"]) <= set(zones["zone_token"])
    published = pd.read_parquet(completed / "public" / "zones.parquet")
    assert set(published["zone_token"]) == set(zones.loc[zones["meets_minimum"], "zone_token"])
    assert set(premises["zone_level"]) <= {"transformer", "step_transformer", "fuse", "recloser", "circuit_breaker", "circuit"}


def test_damage_and_labor_columns_follow_the_mapping(completed):
    damage = gpd.read_parquet(completed / "server" / "damage_records.parquet")
    by_type = damage.set_index("asset_type")
    assert by_type.loc["Pole", "labor_group"] == "POL" and by_type.loc["Pole", "labor_basis"] == "direct"
    assert by_type.loc["Light", "labor_basis"] == "none" and pd.isna(by_type.loc["Light", "labor_group"])
    wire = damage[damage["asset_type"] == "primary"].iloc[0]
    assert wire["labor_assumption_ids"] == "A-WIRE-01"
    transformer = damage[damage["asset_type"] == "Transformer"].iloc[0]
    assert transformer["downstream_premises"] == 3 and bool(transformer["downstream_impact_known"])
    assert damage["report_date"].dt.normalize().equals(damage["report_date"])  # date-only


# ---------------------------------------------------------------- 9.2 manifest
def test_manifest_has_every_required_field_and_no_key(completed):
    manifest = json.loads((completed / "reports" / "manifest.json").read_text())
    for field in ("inputs", "input_records", "outputs", "coverage", "configuration", "pipeline_version", "key_fingerprint", "leak_check", "topology", "raw_data_unchanged"):
        assert field in manifest, field
    assert manifest["leak_check"]["passed"] is True and manifest["leak_check"]["files_scanned"] >= 17
    assert manifest["raw_data_unchanged"] is True
    assert manifest["configuration"]["coverage_minimums"]["customer_to_topology"] == 0.995
    assert manifest["input_records"]["layers"]["Customer_Clip"] == len(CHAINS)
    assert manifest["topology"]["chains"] == len(CHAINS) and manifest["topology"]["multi_parent_nodes"] == 1
    listed = {o["path"] for o in manifest["outputs"]}
    assert "server/premises.parquet" in listed and "public/zones.parquet" in listed and "reports/profile_report.md" in listed
    assert all(len(o["sha256"]) == 64 and o["bytes"] > 0 for o in manifest["outputs"])
    assert all(len(i["sha256"]) == 64 for i in manifest["inputs"])
    assert manifest["coverage"]["customer_to_topology"]["rate"] == 1.0
    assert manifest["coverage"]["topology_recloser_to_gis"]["detail"]["matched_by_location_key"] == 2


def test_the_key_is_in_no_output_file(completed):
    key = KEY.encode()
    for path in completed.rglob("*"):
        if path.is_file():
            assert key not in path.read_bytes(), path
    manifest = json.loads((completed / "reports" / "manifest.json").read_text())
    assert manifest["key_fingerprint"] == Tokenizer(KEY.encode()).fingerprint()
    assert KEY not in json.dumps(manifest)


# ---------------------------------------------------------------- 9.3 report
def test_report_states_the_known_gaps(completed):
    report = (completed / "reports" / "profile_report.md").read_text()
    assert "No weather or storm characteristics are provided" in report
    assert "no time of day" in report
    assert "no outage identifier and no damage quantity" in report
    assert "Switches are absent from the topology file" in report
    assert "Structure diagrams (PDF)" in report
    assert "## Link coverage" in report and "## Protection zones" in report


def test_report_and_all_outputs_contain_no_sensitive_values(completed, fixture_raw):
    assert fixture_raw.canary_values
    blob = "\n".join(all_text(completed))
    for value in fixture_raw.canary_values:
        assert value not in blob, f"sensitive value leaked: {value[:12]}"
    for token in ("LS Life Support", "CANARY"):
        assert token not in blob


def test_source_file_metadata_never_reaches_reports_or_manifest(completed, fixture_raw):
    blob = "\n".join(all_text(completed))
    for canary in fixture_raw.metadata_canaries:
        assert canary not in blob
    assert str(fixture_raw.raw_dir) not in blob and "/Users/" not in blob


def test_no_life_support_signal_in_any_output(completed):
    blob = "\n".join(all_text(completed)).lower()
    for fragment in ("life support", "life-support", "lifesupport", "critical"):
        assert fragment not in blob, fragment


# ---------------------------------------------------------------- determinism and raw data
def test_two_runs_with_the_same_key_are_byte_identical(tmp_path, fixture_raw, test_config_dir):
    first, second = tmp_path / "one", tmp_path / "two"
    assert run(fixture_raw.raw_dir, first, test_config_dir) == 0
    assert run(fixture_raw.raw_dir, second, test_config_dir) == 0
    names = sorted(p.relative_to(first).as_posix() for p in first.rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(second).as_posix() for p in second.rglob("*") if p.is_file())
    assert names  # includes the manifest, which has no timestamps
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_a_different_key_changes_every_token(tmp_path, fixture_raw, test_config_dir, completed):
    other = tmp_path / "other"
    assert run(fixture_raw.raw_dir, other, test_config_dir, key="a-completely-different-key-9876") == 0
    a = set(pd.read_parquet(completed / "server" / "premises.parquet")["premise_token"])
    b = set(pd.read_parquet(other / "server" / "premises.parquet")["premise_token"])
    assert a.isdisjoint(b) and len(a) == len(b)
    fp = lambda p: json.loads((p / "reports" / "manifest.json").read_text())["key_fingerprint"]
    assert fp(completed) != fp(other)


def test_raw_files_are_unchanged_after_a_failed_run(tmp_path, build_fixture, test_config_dir, capsys):
    info = build_fixture(FixtureOptions(customers_without_chain=3))  # coverage will fail
    before = checksum_tree(info.raw_dir)
    code = run(info.raw_dir, tmp_path / "out", test_config_dir)
    assert code == 2
    assert checksum_tree(info.raw_dir) == before


def test_rerun_replaces_previous_outputs(tmp_path, fixture_raw, test_config_dir):
    out = tmp_path / "out"
    assert run(fixture_raw.raw_dir, out, test_config_dir) == 0
    (out / "server" / "stale.txt").write_text("old")
    assert run(fixture_raw.raw_dir, out, test_config_dir) == 0
    assert not (out / "server" / "stale.txt").exists()


# ---------------------------------------------------------------- exit codes through the CLI
def test_coverage_below_threshold_exits_2_and_writes_nothing(tmp_path, build_fixture, test_config_dir, capsys):
    info = build_fixture(FixtureOptions(customers_without_chain=1))
    out = tmp_path / "out"
    assert run(info.raw_dir, out, test_config_dir) == 2
    err = capsys.readouterr().err
    assert "customer_to_topology" in err and "99.50%" in err
    assert not out.exists() or not any(out.iterdir())


def test_leak_in_an_artifact_exits_3_names_it_and_leaves_no_outputs(tmp_path, fixture_raw, test_config_dir, monkeypatch, capsys):
    real_write = pipeline_module.write_parquet

    def leaky(frame, path, root, sort_by=None):
        if path.name == "premises.parquet":
            frame = frame.copy()
            frame["road_name"] = "x"
            frame["note"] = PREMISE_NUMBER["P01"]
        return real_write(frame, path, root, sort_by)

    monkeypatch.setattr(pipeline_module, "write_parquet", leaky)
    out = tmp_path / "out"
    assert run(fixture_raw.raw_dir, out, test_config_dir) == 3
    err = capsys.readouterr().err
    assert "server/premises.parquet" in err and "road_name" in err and "note" in err
    leftovers = [p.name for p in out.iterdir()] if out.exists() else []
    assert leftovers == []  # no serving artifact and no staging directory remains


def test_a_leak_does_not_replace_the_previous_good_outputs(tmp_path, fixture_raw, test_config_dir, monkeypatch):
    out = tmp_path / "out"
    assert run(fixture_raw.raw_dir, out, test_config_dir) == 0
    before = (out / "reports" / "manifest.json").read_bytes()
    real_write = pipeline_module.write_parquet
    monkeypatch.setattr(
        pipeline_module, "write_parquet",
        lambda frame, path, root, sort_by=None: real_write(
            frame.assign(address="x") if path.name == "zones.parquet" and path.parent.name == "public" else frame, path, root, sort_by),
    )
    assert run(fixture_raw.raw_dir, out, test_config_dir) == 3
    assert (out / "reports" / "manifest.json").read_bytes() == before


def test_unmatched_fixture_extras_are_reported_not_hidden(tmp_path, build_fixture, test_config_dir):
    info = build_fixture(FixtureOptions(malformed_topology_lines=2, topology_only_premises=1))
    out = tmp_path / "out"
    assert run(info.raw_dir, out, test_config_dir) == 0
    manifest = json.loads((out / "reports" / "manifest.json").read_text())
    assert manifest["topology"]["rejected_lines"] == 2
    assert manifest["coverage"]["customer_to_topology"]["unmatched_other"] == 1


@pytest.mark.real_data
def test_full_run_on_the_real_data(tmp_path, real_data_dir):
    """Slow end-to-end check on the real inputs with the real configuration."""
    out = tmp_path / "curated"
    before = checksum_tree(real_data_dir)
    code = main(["run", "--raw", str(real_data_dir), "--out", str(out)], environ={"AEP_TOKEN_KEY": KEY})
    assert code == 0
    assert checksum_tree(real_data_dir) == before
    manifest = json.loads((out / "reports" / "manifest.json").read_text())
    assert manifest["leak_check"]["passed"] and manifest["topology"]["chains"] == 117599
