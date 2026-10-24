import openpyxl
import pyogrio
import pytest

from aep_pipeline.contracts import load_layer_contracts
from tests.fixtures.builder import CHAINS, PREMISE_NUMBER


def test_every_fixture_layer_has_the_real_field_list(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    assert len(contracts) == 12
    for name, contract in contracts.items():
        info = pyogrio.read_info(fixture_raw.raw_dir / contract.path)
        assert list(info["fields"]) == list(contract.fields), name
        assert [str(d) for d in info["dtypes"]] == [f.dtype for f in contract.fields.values()], name
        assert info["geometry_type"] == contract.geometry_type, name
        assert info["features"] >= 1, name


def test_fixture_priority_layers_use_the_projected_system_with_height(fixture_raw, test_config_dir):
    contracts = load_layer_contracts(test_config_dir)
    for name in ("Ambulance_Servi_Pairwis_Clip", "Fire_Stations_E_Pairwis_Clip", "Hospitals_Medic_Pairwis_Clip"):
        info = pyogrio.read_info(fixture_raw.raw_dir / contracts[name].path)
        assert "Pseudo-Mercator" in info["crs"]
        assert info["geometry_type"] == "Point Z"


def test_fixture_topology_has_crlf_and_the_expected_shape(fixture_raw):
    raw = (fixture_raw.raw_dir / "matched_upstream" / "matched_upstream.txt").read_bytes()
    assert raw.endswith(b"\r\n")
    lines = raw.decode("ascii").split("\r\n")[:-1]
    assert len(lines) == len(CHAINS)
    first = lines[0].split("\t")
    assert first[0] == PREMISE_NUMBER["P01"]
    assert all(len(part.split("*")) == 4 for part in first[1:])


def test_fixture_workbook_has_the_five_real_sheets(fixture_raw):
    wb = openpyxl.load_workbook(fixture_raw.raw_dir / "Install_Labor_HackOHIO.xlsx", read_only=True)
    assert wb.sheetnames == [
        "CNC",
        "Labor time material related",
        "Labor only work",
        "Indirect Labor Multiplier",
        "CU Group descriptions",
    ]


def test_fixture_contains_only_synthetic_premise_numbers(fixture_raw):
    # Synthetic premise numbers start with 9, and no real data is copied in.
    assert all(number.startswith("9") for number in fixture_raw.premise_numbers.values())
    assert fixture_raw.canary_values  # canary strings exist for leak tests


@pytest.mark.real_data
def test_contracts_match_the_real_layers_metadata(real_data_dir):
    """Every real layer's field list equals its contract (metadata only, no row reads)."""
    contracts = load_layer_contracts()
    for name, contract in contracts.items():
        info = pyogrio.read_info(real_data_dir / contract.path)
        assert list(info["fields"]) == list(contract.fields), name
        assert [str(d) for d in info["dtypes"]] == [f.dtype for f in contract.fields.values()], name
