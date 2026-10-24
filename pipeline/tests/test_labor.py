import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from aep_pipeline.errors import ContractError
from aep_pipeline.labor import (
    INDIRECT_NOTE,
    UNKNOWN_ASSET,
    build_assumptions_register,
    check_mapping_covers,
    load_labor_mapping,
    mapping_as_output,
    read_workbook,
    select_adders,
    select_labor,
    validate_mapping,
)


@pytest.fixture(scope="module")
def fixture_labor(fixture_raw):
    return read_workbook(fixture_raw.raw_dir / "Install_Labor_HackOHIO.xlsx")


@pytest.fixture(scope="module")
def real_labor(real_data_dir):
    return read_workbook(real_data_dir / "Install_Labor_HackOHIO.xlsx")


@pytest.fixture(scope="module")
def mapping():
    return load_labor_mapping()


def item(items, item_id):
    return items.set_index("item_id").loc[item_id]


# ---------------------------------------------------------------- 8.1 workbook reader
def test_connector_rows_keep_their_values_and_source(fixture_labor):
    items, _ = fixture_labor
    row = item(items, "CNC:CNC-TEST-10")
    assert (row["install_hours"], row["remove_hours"]) == (0.101, 0.011)
    assert row["source_sheet"] == "CNC" and row["source_row"] == 2 and row["unit_basis"] == "per unit"
    assert items[items["group_code"] == "CNC"]["item_key"].is_unique


def test_valid_zero_hours_are_not_confused_with_missing(fixture_labor):
    items, _ = fixture_labor
    zero = item(items, "CNC:CNC-ZERO-0")
    assert zero["install_hours"] == 0.0 and not zero["install_missing"]


def test_size_based_sections_keep_size_keys_and_details(fixture_labor):
    items, _ = fixture_labor
    assert item(items, "POL:Small")["size_detail"] == "25\u201335 ft"
    assert item(items, "POL:Large")["install_hours"] == 3.5
    kva = item(items, "XFM:25kVA")
    assert kva["size_detail"] == "25" and kva["sample_count"] == 47 and kva["group_code_basis"] == "assigned"
    assert item(items, "ARM:average")["install_hours"] == 0.31
    assert item(items, "XCO:average")["remove_hours"] == 0.125


def test_item_without_hours_is_missing_not_zero(fixture_labor):
    items, _ = fixture_labor
    junction = item(items, "ADDER:Junction Pole Work")
    assert pd.isna(junction["install_hours"]) and bool(junction["install_missing"])
    assert not bool(junction["remove_missing"])  # the sheet has no removal column at all


def test_adders_carry_their_unit_basis(fixture_labor):
    items, _ = fixture_labor
    dig = item(items, "ADDER:Hand Dig")
    assert dig["install_hours"] == 1.5 and dig["unit_basis"] == "per cubic foot"
    access = item(items, "ADDER:Difficult Location")
    assert access["install_hours"] == 0.5 and access["unit_basis"] == "per occurrence"
    assert (items[items["group_code"] == "ADDER"]["unit_basis"].isin(["per occurrence", "per cubic foot"])).all()


def test_adders_without_a_label_column_keep_their_description(fixture_labor):
    items, _ = fixture_labor
    row = item(items, "ADDER:Access Constraint Adder")
    assert row["label"] is None and row["description"].startswith("Apply when")


def test_item_ids_are_unique_and_columns_are_typed(fixture_labor):
    items, _ = fixture_labor
    assert items["item_id"].is_unique
    assert str(items["install_hours"].dtype) == "float64" and str(items["sample_count"].dtype) == "Int64"


def test_missing_sheet_is_a_contract_error(tmp_path, fixture_raw):
    import openpyxl

    wb = openpyxl.load_workbook(fixture_raw.raw_dir / "Install_Labor_HackOHIO.xlsx")
    del wb["CNC"]
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ContractError) as error:
        read_workbook(path)
    assert "CNC" in str(error.value)


@pytest.mark.real_data
def test_real_workbook_values(real_labor):
    items, _ = real_labor
    cnc = item(items, "CNC:CNC-HTA-10")
    assert (cnc["install_hours"], cnc["remove_hours"]) == (0.096, 0.016)
    assert len(items[items["group_code"] == "CNC"]) == 95
    dig = item(items, "ADDER:Hand Dig")
    assert dig["install_hours"] == 1.5 and dig["unit_basis"] == "per cubic foot"
    restricted = item(items, "ADDER:Difficult Location")
    assert restricted["install_hours"] == 0.5 and restricted["unit_basis"] == "per occurrence"
    assert restricted["label"] == "Restricted Access Work"
    junction = item(items, "ADDER:Junction Pole Work")
    assert pd.isna(junction["install_hours"]) and bool(junction["install_missing"])
    assert item(items, "POL:Small")["install_hours"] == 1.9416103896103902
    assert len(items[items["group_code"] == "XFM"]) == 12
    assert items["item_id"].is_unique
    assert items["group_code"].nunique() == 18  # CNC + 16 material groups + ADDER


# ---------------------------------------------------------------- 8.2 indirect labor multiplier
def test_indirect_multiplier_is_an_explicit_parameter(fixture_labor):
    _, parameters = fixture_labor
    ilm = parameters["indirect_labor_multiplier"]
    assert ilm["value"] == 4.0 and ilm["source_sheet"] == "Indirect Labor Multiplier" and ilm["overridable"] is True
    assert ilm["note"] == INDIRECT_NOTE
    assert "No separate travel or non-productive values were provided" in ilm["note"]
    assert set(ilm) == {"value", "source_sheet", "source_cell", "applies", "note", "overridable"}  # one combined value only


@pytest.mark.real_data
def test_real_indirect_multiplier_is_4_5_with_the_note(real_labor):
    _, parameters = real_labor
    ilm = parameters["indirect_labor_multiplier"]
    assert ilm["value"] == 4.5
    assert "No separate travel or non-productive values were provided" in ilm["note"]
    assert parameters["workbook_group_descriptions"]["POL"].startswith("Perform work")


def test_non_positive_multiplier_is_rejected(tmp_path, fixture_raw):
    import openpyxl

    wb = openpyxl.load_workbook(fixture_raw.raw_dir / "Install_Labor_HackOHIO.xlsx")
    wb["Indirect Labor Multiplier"]["B1"] = 0
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ContractError):
        read_workbook(path)


# ---------------------------------------------------------------- 8.3 mapping
def test_mapping_is_valid_against_the_fixture_and_the_real_workbook(fixture_labor, mapping):
    validate_mapping(mapping, fixture_labor[0])


@pytest.mark.real_data
def test_mapping_is_valid_against_the_real_workbook(real_labor, mapping):
    validate_mapping(mapping, real_labor[0])


def test_non_direct_rows_must_cite_an_assumption(fixture_labor, mapping):
    key = ("Fuse Bank", "Damaged Switch")
    broken = dataclasses.replace(mapping, assets={**mapping.assets, key: dataclasses.replace(mapping.assets[key], assumption_id=None)})
    with pytest.raises(ContractError) as error:
        validate_mapping(broken, fixture_labor[0])
    assert "Fuse Bank" in str(error.value)


def test_mapping_pointing_to_an_unknown_group_is_rejected(fixture_labor, mapping):
    key = ("Pole", "Damaged Pole")
    broken = dataclasses.replace(mapping, assets={**mapping.assets, key: dataclasses.replace(mapping.assets[key], labor_group="NOPE")})
    with pytest.raises(ContractError):
        validate_mapping(broken, fixture_labor[0])


@pytest.mark.real_data
def test_every_type_in_the_real_damage_layer_has_a_mapping_entry(real_data_dir, mapping):
    import pyogrio

    frame = pyogrio.read_dataframe(
        real_data_dir / "Data1" / "arcos_prod_damage_09_28_Clip.shp",
        columns=["Asset_Type", "Inform_Wor", "Special_Eq", "Access_Iss"],
        read_geometry=False,
    )
    pairs = {(a if isinstance(a, str) else UNKNOWN_ASSET, d) for a, d in zip(frame["Asset_Type"], frame["Inform_Wor"])}
    check_mapping_covers(mapping, pairs, set(frame["Special_Eq"].dropna()), set(frame["Access_Iss"].dropna()))
    assert len(pairs) == 11


def test_a_type_missing_from_the_mapping_fails_the_check(mapping):
    with pytest.raises(ContractError) as error:
        check_mapping_covers(mapping, {("Pole", "Damaged Pole"), ("Recloser Bank", "Exploded")}, set(), set())
    assert "Exploded" in str(error.value)
    with pytest.raises(ContractError):
        check_mapping_covers(mapping, set(), {"Hovercraft"}, set())


def test_unmapped_types_have_missing_hours_not_zero(fixture_labor, mapping):
    items, _ = fixture_labor
    for asset, damage in (("Light", "Damaged Light"), ("Capacitor Bank", "Damaged Equipment"), ("primary", "Damaged Wire")):
        selection = select_labor(items, mapping, asset, damage)
        assert selection.missing and selection.install_hours is None and selection.remove_hours is None
        assert selection.basis == "none" and selection.labor_group is None and selection.assumption_ids
    unknown = select_labor(items, mapping, None, "Damaged Wire")
    assert unknown.asset_type == UNKNOWN_ASSET and unknown.missing
    unlisted = select_labor(items, mapping, "Spaceship", "Damaged Hull")
    assert unlisted.missing and unlisted.basis == "none"


def test_wire_damage_cites_the_conductor_assumption(mapping):
    assert mapping.assets[("primary", "Damaged Wire")].assumption_id == "A-WIRE-01"
    assert "conductor" in mapping.assumptions["A-WIRE-01"]["description"]


def test_direct_mappings_select_their_group_average(fixture_labor, mapping):
    items, _ = fixture_labor
    recloser = select_labor(items, mapping, "Recloser Bank", "Damaged Switch")
    assert (recloser.labor_group, recloser.install_hours, recloser.remove_hours, recloser.basis) == ("REC", 0.6, 0.5, "direct")
    assert not recloser.assumed and recloser.assumption_ids == []
    fuse = select_labor(items, mapping, "Fuse Bank", "Damaged Switch")
    assert fuse.labor_group == "XCO" and fuse.basis == "approximate" and fuse.assumption_ids == ["A-FUSE-01"]


# ---------------------------------------------------------------- 8.4 size selection
def test_forty_foot_pole_selects_the_medium_group(fixture_labor, mapping):
    selection = select_labor(fixture_labor[0], mapping, "Pole", "Damaged Pole", {"height": "40 Feet"})
    assert selection.item_id == "POL:Medium" and selection.install_hours == 2.5
    assert not selection.assumed and selection.assumption_ids == []


@pytest.mark.parametrize(
    "height,group",
    [("25 Feet", "POL:Small"), ("35 Feet", "POL:Small"), ("55 Feet", "POL:Medium"), ("60 Feet", "POL:Large"), ("100 Feet", "POL:Large")],
)
def test_pole_height_boundaries_follow_the_workbook_ranges(fixture_labor, mapping, height, group):
    selection = select_labor(fixture_labor[0], mapping, "Pole", "Damaged Pole", {"height": height})
    assert selection.item_id == group and not selection.assumed


def test_unknown_pole_height_uses_medium_and_is_flagged_assumed(fixture_labor, mapping):
    for height in ("Unknown", None, "n/a"):
        selection = select_labor(fixture_labor[0], mapping, "Pole", "Damaged Pole", {"height": height})
        assert selection.item_id == "POL:Medium" and selection.assumed
        assert "A-POLE-01" in selection.assumption_ids


def test_height_outside_the_ranges_uses_the_nearest_group_and_is_flagged(fixture_labor, mapping):
    for height, group in (("20 Feet", "POL:Small"), ("37 Feet", "POL:Small"), ("39 Feet", "POL:Medium")):
        selection = select_labor(fixture_labor[0], mapping, "Pole", "Damaged Pole", {"height": height})
        assert selection.item_id == group and selection.assumed and "A-POLE-02" in selection.assumption_ids


def test_transformer_without_a_rating_uses_the_average_and_is_flagged(fixture_labor, mapping):
    items, _ = fixture_labor
    selection = select_labor(items, mapping, "Transformer", "Damaged Equipment", {})
    rows = items[items["group_code"] == "XFM"]
    assert selection.install_hours == pytest.approx(rows["install_hours"].mean())
    assert selection.remove_hours == pytest.approx(rows["remove_hours"].mean())
    assert selection.assumed and "A-XFMR-01" in selection.assumption_ids and selection.basis == "approximate"


def test_transformer_with_a_rating_uses_that_size(fixture_labor, mapping):
    items, _ = fixture_labor
    exact = select_labor(items, mapping, "Transformer", "Damaged Equipment", {"kva": 25})
    assert exact.item_id == "XFM:25kVA" and exact.install_hours == 1.2 and not exact.assumed
    near = select_labor(items, mapping, "Transformer", "Damaged Equipment", {"kva": 30})
    assert near.item_id == "XFM:25kVA" and near.assumed


@pytest.mark.real_data
def test_real_pole_and_transformer_selection(real_labor, mapping):
    items, _ = real_labor
    pole = select_labor(items, mapping, "Pole", "Damaged Pole", {"height": "45 Feet"})
    assert pole.item_id == "POL:Medium" and pole.install_hours == 2.50295207667732
    xfmr = select_labor(items, mapping, "Transformer", "Damaged Equipment", {})
    assert 1.1 < xfmr.install_hours < 1.4 and xfmr.assumed


# ---------------------------------------------------------------- adders
def test_access_issue_maps_to_the_access_adder(fixture_labor, mapping):
    adders = select_adders(fixture_labor[0], mapping, "Yes", None)
    assert [(a.adder, a.install_hours, a.unit_basis, a.basis) for a in adders] == [
        ("Access Constraint Adder", 0.5, "per occurrence", "approximate")
    ]
    assert select_adders(fixture_labor[0], mapping, "No", None) == []


def test_equipment_values_map_to_adders_or_to_none(fixture_labor, mapping):
    items, _ = fixture_labor
    backyard = select_adders(items, mapping, "No", "Backyard Bucket")[0]
    assert backyard.adder == "Backyard Adder" and backyard.install_hours == 0.5
    big = select_adders(items, mapping, "No", "Big Bucket")[0]
    assert big.adder is None and big.install_hours is None and big.basis == "none"
    assert big.assumption_ids == ["A-ADDER-05"]
    with pytest.raises(ContractError):
        select_adders(items, mapping, "No", "Hovercraft")


# ---------------------------------------------------------------- 8.5 assumptions register
def test_every_non_direct_mapping_has_a_register_entry(mapping):
    register = {entry["id"]: entry for entry in build_assumptions_register(mapping)}
    non_direct = [e for e in list(mapping.assets.values()) + list(mapping.adders.values()) if e.basis != "direct"]
    assert non_direct
    for entry in non_direct:
        assert entry.assumption_id in register, entry
        assert register[entry.assumption_id]["description"].strip()
        assert register[entry.assumption_id]["id"] == entry.assumption_id
    # selection-rule assumptions are published as well
    assert {"A-POLE-01", "A-POLE-02", "A-XFMR-01"} <= set(register)


def test_register_lists_what_each_assumption_affects(mapping):
    register = {entry["id"]: entry for entry in build_assumptions_register(mapping)}
    assert "asset: primary / Damaged Wire" in register["A-WIRE-01"]["affects"]
    assert "special equipment: Track Hoe" in register["A-ADDER-03"]["affects"]
    assert all(entry["id"].startswith("A-") for entry in register.values())


def test_mapping_output_carries_the_review_block(mapping):
    output = mapping_as_output(mapping)
    assert output["review"]["status"] in ("proposed", "reviewed")
    assert {r["basis"] for r in output["asset_mappings"]} <= {"direct", "approximate", "assumed", "none"}
    assert len(output["asset_mappings"]) == 11


def test_a_reviewed_mapping_records_who_and_when(mapping):
    review = mapping.review
    if review["status"] == "reviewed":
        assert review["reviewed_by"] and review["reviewed_on"]
