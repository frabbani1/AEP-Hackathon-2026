"""Task 1.2: freeze restoration-v1 so a regression in the unchanged paths (fallback, validation, display) is visible.

golden_v1.json was captured from the v1 code before spec-03 changed anything. It holds three synthetic premises:
a simulated outage, a customer-entered damage list with restricted access, and a line with no damage history.
"""
import json
from pathlib import Path

import pytest

from concierge.restoration import model as m
from concierge.restoration import v1
from concierge.restoration.common import RestorationError, display

from .fixtures import LABOR, ROWS, SEVERITY

GOLDEN = json.loads((Path(__file__).parent / "golden_v1.json").read_text())

CASES = {
    "simulated_normal": dict(token="p_00000000000000a1", row=ROWS["p_00000000000000a1"],
                             base_confidence="Medium", crews=3, access="normal"),
    "damage_restricted": dict(token="p_00000000000000a2", row=ROWS["p_00000000000000a2"], base_confidence="High",
                              crews=2, access="restricted",
                              damage=[("pole", 2), ("primary_line", 1), ("transformer", 1)]),
    "floor_remote": dict(token="p_00000000000000a3", row={**ROWS["p_00000000000000a3"], "R": 0.0},
                         base_confidence="Medium", crews=1, access="remote"),
}


def run(engine_fn, name):
    return engine_fn(severity=SEVERITY, fallback=None, labor=LABOR, **CASES[name])


@pytest.mark.parametrize("name", sorted(CASES))
def test_v1_output_matches_golden(name):
    assert json.loads(json.dumps(run(v1.estimate, name))) == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(CASES))
def test_dispatcher_with_v1_matches_golden(name):
    assert json.loads(json.dumps(run(lambda **kw: m.estimate(engine="v1", **kw), name))) == GOLDEN[name]


def kw(**over):
    base = dict(token="p_00000000000000a1", row=ROWS["p_00000000000000a1"], severity=SEVERITY, fallback=None,
                base_confidence="Medium", labor=LABOR)
    base.update(over)
    return base


def test_v1_fallback_unknown_premise():
    out = v1.estimate(**kw(token=None, row=None, fallback={"reason_code": "unknown_premise", "message": "No such place."}))
    assert out["status"] == "insufficient_data" and out["estimate"] is None and out["steps"] == []
    assert out["fallback"] == {"reason_code": "unknown_premise",
                               "message": "No such place. We also can't estimate how long restoration would take."}


def test_v1_fallback_scored_row_without_line_context():
    row = {k: v for k, v in ROWS["p_00000000000000a1"].items() if k != "cn"}
    out = v1.estimate(**kw(row=row))
    assert out["status"] == "insufficient_data"
    assert out["fallback"]["reason_code"] == "no_network_context"
    assert out["fallback"]["message"].endswith("We also can't estimate how long restoration would take.")


def test_v1_fallback_risk_row_not_ok():
    out = v1.estimate(**kw(row={"st": "fb", "why": "circuit_too_small"},
                           fallback={"reason_code": "circuit_too_small", "message": "Too small."}))
    assert out["status"] == "insufficient_data" and out["fallback"]["reason_code"] == "circuit_too_small"


@pytest.mark.parametrize("bad, message", [
    (dict(crews=0), "crews must be between 1 and 100"),
    (dict(crews=101), "crews must be between 1 and 100"),
    (dict(access="muddy"), "access must be one of ['normal', 'restricted', 'remote']"),
    (dict(damage=[("meteor", 1)]), "damage items need a known asset_type and a quantity from 1 to 20"),
    (dict(damage=[("pole", 21)]), "damage items need a known asset_type and a quantity from 1 to 20"),
])
def test_v1_validation_messages(bad, message):
    with pytest.raises(RestorationError) as e:
        v1.estimate(**kw(**bad))
    assert str(e.value) == message


def test_v1_missing_labor_unit_is_an_error():
    from .fixtures import make_labor
    with pytest.raises(RestorationError):
        v1.estimate(**kw(labor=make_labor(drop="fuse_cutout"), damage=[("fuse_bank", 1)]))


@pytest.mark.parametrize("lo, hi, text", [
    (1, 1, "about 1 hour"), (6, 6, "about 6 hours"), (6, 14, "about 6 to 14 hours"), (20, 35, "about 20 to 35 hours"),
    (30, 36, "about 1 to 2 days"), (48, 48, "about 2 days"), (24, 100, "about 1 to 4 days"),
])
def test_display_rule(lo, hi, text):
    assert display(lo, hi) == text and m.display(lo, hi) == text


def test_dispatcher_exports_what_the_api_layer_uses():
    assert m.DEFAULT_CREWS == 3 and m.MAX_CREWS == 100 and m.MAX_JOBS_PER_ASSET == 20
    assert m.ACCESS_LEVELS == ("normal", "restricted", "remote")
    assert set(m.ASSET_LEVEL) == {"primary_line", "recloser", "switch", "capacitor_bank", "step_transformer",
                                  "fuse_bank", "pole", "transformer", "secondary_line", "light"}
    assert m.LEVELS == ("feeder", "lateral", "service")
