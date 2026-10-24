import json

import pytest

from concierge.risk.build import build_artifacts
from concierge.restoration.tree import RestorationTree

from ..risk.fixtures import make_synth
from .fixtures import LABOR, ROWS, TREE_DICT


@pytest.fixture(scope="session")
def tree():
    return RestorationTree.from_dict(TREE_DICT)


@pytest.fixture(scope="session")
def labor():
    return LABOR


@pytest.fixture(scope="session")
def calibration(tmp_path_factory):
    """A real calibration.json built from the synthetic risk fixtures (only its thresholds and presets are used)."""
    synth = make_synth(tmp_path_factory.mktemp("synth"))
    out = tmp_path_factory.mktemp("risk")
    build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf, topology_txt=synth.topology_txt,
                    device_dbfs=synth.device_dbfs, out_dir=out, salt="test-salt")
    return json.loads((out / "calibration.json").read_text())


@pytest.fixture()
def rows():
    return {tok: dict(row) for tok, row in ROWS.items()}
