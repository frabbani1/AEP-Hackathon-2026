import json

import pytest

from concierge.api.service import Concierge
from concierge.restoration.tree import RestorationTree
from concierge.risk.build import build_artifacts
from concierge.weather.openmeteo import OpenMeteo
from concierge.zones.index import ZoneIndex

from ..restoration.fixtures import LABOR, ROWS, TREE_DICT
from ..risk.fixtures import make_synth
from .fakes import NOW, FakeOpenMeteo, clock

ANCHORS = {"storms": [{"label": "Sep 2024", "preset": "sep-2024-like", "peak_gust_mph": 47.9},
                      {"label": "Feb 2025", "preset": "feb-2025-like", "peak_gust_mph": 55.3}]}
TOKENS = list(ROWS)
ZONE_A, ZONE_B, ZONE_C = "z_aaaaaaaaaaaa", "z_bbbbbbbbbbbb", "z_cccccccccccc"


@pytest.fixture(scope="session")
def calibration(tmp_path_factory):
    synth = make_synth(tmp_path_factory.mktemp("wx_synth"))
    out = tmp_path_factory.mktemp("wx_risk")
    build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf, topology_txt=synth.topology_txt,
                    device_dbfs=synth.device_dbfs, out_dir=out, salt="test-salt")
    return json.loads((out / "calibration.json").read_text())


@pytest.fixture()
def rows():
    r = {tok: dict(row) for tok, row in ROWS.items()}
    r["p_00000000000000b1"] = {"st": "fb", "why": "circuit_too_small"}
    return r


def hexagon(lat, lon, r=0.02):
    pts = [[round(lon + r * dx, 4), round(lat + r * dy, 4)]
           for dx, dy in ((1, 0), (0.5, 0.87), (-0.5, 0.87), (-1, 0), (-0.5, -0.87), (0.5, -0.87))]
    return pts


def zone_doc():
    return {"version": "zones-v1", "resolution": 6, "min_homes": 10, "bounds": [[36.9, -80.3], [37.4, -79.7]],
            "zones": [
                # centroid (37.30, -79.95): storm over this one in the tests
                {"id": ZONE_A, "name": "Area 1", "ring": hexagon(37.30, -79.95), "rep": TOKENS[0], "fb": None,
                 "facility": True, "n": 50},
                # centroid (37.10, -80.20)
                {"id": ZONE_B, "name": "Area 2", "ring": hexagon(37.10, -80.20), "rep": TOKENS[2], "fb": None,
                 "facility": False, "n": 40},
                # unrated
                {"id": ZONE_C, "name": "Area 3", "ring": hexagon(37.00, -80.00), "rep": None,
                 "fb": "circuit_too_small", "facility": False, "n": 12},
            ]}


@pytest.fixture()
def fake():
    return FakeOpenMeteo(gust=15.0)


@pytest.fixture()
def make_svc(rows, calibration, fake):
    def build(weather="on", zones=True, fake_=None):
        f = fake_ or fake
        wx = OpenMeteo(fetch=f, clock=lambda: NOW.timestamp()) if weather == "on" else None
        return Concierge(rows, calibration, LABOR, tree=RestorationTree.from_dict(TREE_DICT), restoration_engine="v2",
                         zones=ZoneIndex.from_dict(zone_doc()) if zones else None, weather=wx,
                         weather_anchors=ANCHORS, clock=clock)
    return build


@pytest.fixture()
def svc(make_svc):
    return make_svc()
