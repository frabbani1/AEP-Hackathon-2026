import pytest

from concierge.alerts.table import load_table
from concierge.api.service import Concierge
from concierge.risk.build import build_artifacts
from concierge.risk.engine import RiskEngine
from tests.risk.fixtures import make_synth

SALT = "test-salt"
LABOR = {"units": [], "adders": [], "indirect_labor_multiplier": 4.5}


class Clock:
    """Manual clock so expiry and ordering tests are exact."""

    def __init__(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        from datetime import timedelta
        self.now = self.now + timedelta(**kw)


@pytest.fixture(scope="session")
def table():
    return load_table()


@pytest.fixture(scope="session")
def risk_engine(tmp_path_factory):
    synth = make_synth(tmp_path_factory.mktemp("alerts_synth"))
    out = tmp_path_factory.mktemp("alerts_artifacts")
    build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf, topology_txt=synth.topology_txt,
                    device_dbfs=synth.device_dbfs, out_dir=out, salt=SALT)
    return RiskEngine.from_artifacts(out)


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def svc(risk_engine, clock):
    from concierge.alerts.stores import MemoryStore
    return Concierge(risk_engine._rows, risk_engine._cal, LABOR, alert_store=MemoryStore(clock), clock=clock)


@pytest.fixture(scope="session")
def ok_token(risk_engine):
    return next(t for t, r in risk_engine._rows.items() if r["st"] == "ok")


@pytest.fixture(scope="session")
def fallback_token(risk_engine):
    return next(t for t, r in risk_engine._rows.items() if r["st"] != "ok")
