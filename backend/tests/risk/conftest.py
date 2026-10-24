import json

import pytest

from concierge.risk.build import build_artifacts
from concierge.risk.engine import RiskEngine
from concierge.risk.tokens import make_token

from .fixtures import make_synth

SALT = "test-salt"


@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    return make_synth(tmp_path_factory.mktemp("synth"))


@pytest.fixture(scope="session")
def built(synth, tmp_path_factory):
    out = tmp_path_factory.mktemp("artifacts")
    cal = build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf,
                          topology_txt=synth.topology_txt, device_dbfs=synth.device_dbfs, out_dir=out, salt=SALT)
    return out, cal


@pytest.fixture(scope="session")
def engine(built):
    return RiskEngine.from_artifacts(built[0])


@pytest.fixture(scope="session")
def tok():
    return lambda pid: make_token(SALT.encode(), pid)


def load_rows(path):
    return [json.loads(l) for l in open(path / "premise_risk.jsonl")]
