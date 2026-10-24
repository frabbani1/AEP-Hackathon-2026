import pytest

from aep_pipeline.contracts import load_layer_contracts
from aep_pipeline.layers import read_layer
from aep_pipeline.privacy import load_privacy_rules

DROPPED_CONCEPTS = [
    "address", "address_nu", "road_name", "street_cod", "city", "macss_mete", "anno_strin",
    "service_po", "service__1", "critical_c", "esri_defau", "scada_cont", "global_id",
    "NAME", "ADDRESS", "TASK_Crew", "TASK_Job_I", "Crew_Area", "Reporting_", "Reporting1",
]


def test_dropped_and_undocumented_columns_never_enter_any_table(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    rules = load_privacy_rules(test_config_dir)
    for name, contract in contracts.items():
        frame = read_layer(fixture_raw.raw_dir, contract)
        allowed = set(contract.columns("public", "internal"))
        assert set(frame.columns) - {"geometry"} == allowed, name
        for forbidden in contract.columns("drop", "undocumented"):
            assert forbidden not in frame.columns, (name, forbidden)
        for concept in DROPPED_CONCEPTS:
            assert concept not in frame.columns, (name, concept)
        # no kept column name may match the forbidden list, except the internal ids that
        # are tokenized before anything is written
        for column in frame.columns:
            if column == "geometry":
                continue
            reason = rules.forbidden_column_reason(column)
            if reason:
                assert contract.fields[column].sensitivity == "internal", (name, column, reason)


def test_customer_table_has_no_life_support_or_address_values(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    frame = read_layer(fixture_raw.raw_dir, contracts["Customer_Clip"])
    text = frame.drop(columns="geometry").astype(str).to_numpy().ravel()
    joined = "\n".join(text)
    assert "LS Life Support" not in joined
    assert "CANARY ADDRESS" not in joined and "CANARY ROAD" not in joined and "CANARY CITY" not in joined


def test_facility_names_and_addresses_are_not_read(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    for layer in ("Ambulance_Servi_Pairwis_Clip", "Fire_Stations_E_Pairwis_Clip", "Hospitals_Medic_Pairwis_Clip"):
        frame = read_layer(fixture_raw.raw_dir, contracts[layer])
        assert set(frame.columns) == {"FTYPE", "FCODE", "geometry"}


def test_layers_are_conformed_to_one_reference_system_without_height(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    for name, contract in contracts.items():
        frame = read_layer(fixture_raw.raw_dir, contract)
        assert frame.crs.to_epsg() == 4326, name
        assert not frame.geometry.has_z.any(), name


def test_priority_facility_coordinates_are_converted_from_the_projected_system(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    frame = read_layer(fixture_raw.raw_dir, contracts["Hospitals_Medic_Pairwis_Clip"])
    point = frame.geometry.iloc[0]
    assert -81 < point.x < -79 and 36 < point.y < 38


def test_geometry_can_be_skipped(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    frame = read_layer(fixture_raw.raw_dir, contracts["Fuse_Bank_Clip"], with_geometry=False)
    assert "geometry" not in frame.columns


@pytest.mark.real_data
def test_real_customer_layer_is_read_without_sensitive_columns(real_data_dir):
    contract = load_layer_contracts()["Customer_Clip"]
    frame = read_layer(real_data_dir, contract, with_geometry=False)
    assert len(frame) == 117642
    assert set(frame.columns) == {"premise_nu", "circuit_id", "aep_xfmr_l", "customer_c", "kind_of_se"}
