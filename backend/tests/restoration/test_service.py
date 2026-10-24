"""Tasks 6.2 and 6.3 through the API layer: damage_count parsing, rejected inputs, engine gating, downstream routes."""
import json

import pytest

from concierge.api import handler
from concierge.api.service import BadRequest, Concierge
from concierge.restoration import config

from .fixtures import LABOR, TOKENS

TOKEN = TOKENS[0]


@pytest.fixture()
def svc(rows, calibration, tree):
    return Concierge(rows, calibration, LABOR, tree=tree, restoration_engine="v2")


def body(**kw):
    return {"token": TOKEN, **kw}


# ---- damage_count parsing --------------------------------------------------------------------------------------------
def test_restoration_accepts_damage_count(svc):
    out = svc.handle("restoration", body(damage_count=12, crews=4))
    assert out["engine_version"] == "restoration-v2" and out["status"] == "ok"
    assert out["scenario"] == {"crews": 4, "access": "normal", "damage_source": "you_provided", "damage_count": 12}
    assert out["estimate"]["p50_hours"] <= out["estimate"]["p90_hours"]


@pytest.mark.parametrize("route", ["restoration", "guidance", "alerts", "summary", "messages"])
def test_every_restoration_route_accepts_damage_count(svc, route):
    extra = {"message_type": "pre_storm", "use_llm": False} if route == "messages" else {}
    out = svc.handle(route, body(damage_count=9, **extra))
    assert out
    if route in ("restoration", "summary"):
        r = out if route == "restoration" else out["restoration"]
        assert r["scenario"]["damage_count"] == 9


def test_unknown_fields_are_still_rejected(svc):
    for route in ("restoration", "guidance", "alerts", "summary", "messages", "risk"):
        with pytest.raises(BadRequest) as e:
            svc.handle(route, body(damage_counts=5, message_type="pre_storm"))
        assert "unknown fields" in str(e.value)
    with pytest.raises(BadRequest):
        svc.handle("risk", body(damage_count=5))                  # the risk route has no damage inputs


def test_both_damage_inputs_are_rejected_with_a_plain_message(svc):
    with pytest.raises(BadRequest) as e:
        svc.handle("restoration", body(damage_count=5, damage=[{"asset_type": "pole", "qty": 1}]))
    assert str(e.value) == "send either damage_count or damage, not both"


@pytest.mark.parametrize("value", [0, 401, -1, 987654, True, 5.5, "7", [3], {"a": 1}])
def test_bad_damage_count_is_rejected_without_echo(svc, value):
    with pytest.raises(BadRequest) as e:
        svc.handle("restoration", body(damage_count=value))
    assert str(e.value) == "damage_count must be a whole number from 1 to 400"


@pytest.mark.parametrize("crews", [0, 101, -5, 2.5, "3", True])
def test_bad_crews_is_rejected(svc, crews):
    with pytest.raises(BadRequest) as e:
        svc.handle("restoration", body(crews=crews))
    assert str(e.value) == "crews must be a whole number from 1 to 100"


def test_damage_list_validation_is_unchanged(svc):
    for bad in ([], [{"asset_type": "pole"}], [{"asset_type": "meteor", "qty": 1}],
                [{"asset_type": "pole", "qty": 21}], "pole", [{"asset_type": "pole", "qty": 1}] * 11):
        with pytest.raises(BadRequest):
            svc.handle("restoration", body(damage=bad))
    out = svc.handle("restoration", body(damage=[{"asset_type": "pole", "qty": 2}]))
    assert out["scenario"]["damage_source"] == "you_provided" and out["scenario"]["damage_count"] == 2


# ---- downstream routes read the shim fields ----------------------------------------------------------------------------
def test_summary_works_with_the_v2_estimate(svc):
    out = svc.handle("summary", body(crews=5, damage_count=15, needs={"medical_equipment": True}))
    est = out["restoration"]["estimate"]
    assert est["typical_hours"] == est["p50_hours"] and est["high_hours"] == est["p90_hours"]
    assert out["guidance"]["items"] and out["guidance"]["duration_bucket"] != "unknown"
    assert est["display"] in out["guidance"]["headline"]
    assert out["alerts"]["eligibility"]["outage_confirmed"]["eligible"]
    json.dumps(out)


def test_messages_route_uses_the_new_display(svc):
    out = svc.handle("messages", body(message_type="outage_confirmed", use_llm=False))
    est = svc.handle("restoration", body())["estimate"]
    assert est["display"] in out["text"]


def test_estimate_updated_message_can_compare_with_a_prior_range(svc):
    out = svc.handle("messages", body(message_type="estimate_updated", use_llm=False,
                                      prior_estimate={"low_hours": 4, "high_hours": 9}))
    assert "about 4 to 9 hours" in out["text"]


# ---- engine gating ---------------------------------------------------------------------------------------------------------
def test_v1_engine_keeps_the_old_contract_and_rejects_damage_count(rows, calibration, tree):
    svc = Concierge(rows, calibration, LABOR, tree=tree, restoration_engine="v1")
    out = svc.handle("restoration", body())
    assert out["engine_version"] == "restoration-v1" and "p50_hours" not in out["estimate"]
    with pytest.raises(BadRequest) as e:
        svc.handle("restoration", body(damage_count=5))
    assert "v1" in str(e.value)


def test_engine_is_read_from_the_environment_at_start_up(rows, calibration, monkeypatch):
    monkeypatch.setenv("RESTORATION_ENGINE", "v2")
    assert Concierge(rows, calibration, LABOR).restoration_engine == "v2"
    monkeypatch.setenv("RESTORATION_ENGINE", "v1")
    assert Concierge(rows, calibration, LABOR).restoration_engine == "v1"
    monkeypatch.setenv("RESTORATION_ENGINE", "fast")
    with pytest.raises(ValueError):
        Concierge(rows, calibration, LABOR)
    with pytest.raises(ValueError):
        Concierge(rows, calibration, LABOR, restoration_engine="fast")


def test_missing_tree_falls_back_to_the_level_path(rows, calibration):
    svc = Concierge(rows, calibration, LABOR, tree=None, restoration_engine="v2")
    out = svc.handle("restoration", body(crews=2))
    assert out["status"] == "ok" and out["simulation"]["model_path"] == "v1_levels"


def test_unknown_token_and_fallback_rows(svc, rows):
    out = svc.handle("restoration", {"token": "p_ffffffffffffffff"})
    assert out["status"] == "insufficient_data" and out["estimate"] is None
    assert out["fallback"]["reason_code"] == "unknown_premise"
    assert out["fallback"]["message"].endswith("We also can't estimate how long restoration would take.")
    assert out["premise_token"] is None
    rows["p_00000000000000b1"] = {"st": "fb", "why": "circuit_too_small"}
    out = svc.handle("restoration", {"token": "p_00000000000000b1"})
    assert out["status"] == "insufficient_data" and out["fallback"]["reason_code"] == "circuit_too_small"


def test_extrapolated_severity_lowers_confidence(svc):
    out = svc.handle("restoration", body(severity="beyond-observed"))
    assert out["severity"]["extrapolated"] is True
    assert out["confidence"]["level"] == "Low"      # Medium row, downgraded one step


# ---- through the Lambda handler ----------------------------------------------------------------------------------------
def call(svc, monkeypatch, payload, path="/restoration"):
    monkeypatch.setattr(handler, "_service", svc)
    event = {"requestContext": {"http": {"method": "POST"}}, "rawPath": path, "body": json.dumps(payload)}
    resp = handler.lambda_handler(event)
    return resp["statusCode"], json.loads(resp["body"])


def test_handler_returns_400_with_plain_messages(svc, monkeypatch):
    status, out = call(svc, monkeypatch, body(damage_count=5, damage=[{"asset_type": "pole", "qty": 1}]))
    assert status == 400 and out == {"error": {"code": "bad_request",
                                               "message": "send either damage_count or damage, not both"}}
    status, out = call(svc, monkeypatch, body(damage_count=0))
    assert status == 400 and out["error"]["message"] == "damage_count must be a whole number from 1 to 400"
    status, out = call(svc, monkeypatch, body(crews=101))
    assert status == 400 and "estimate" not in out
    status, out = call(svc, monkeypatch, body(damage_count=20), path="/api/restoration")
    assert status == 200 and out["estimate"]["p90_hours"] >= out["estimate"]["p50_hours"]


def test_scenario_changes_recompute_in_the_same_call(svc):
    first = svc.handle("restoration", body(crews=1, damage_count=40))["estimate"]["p50_hours"]
    second = svc.handle("restoration", body(crews=8, damage_count=40))["estimate"]["p50_hours"]
    third = svc.handle("restoration", body(crews=1, damage_count=40))["estimate"]["p50_hours"]
    assert second < first and third == first                      # nothing is cached across different inputs


def test_default_engine_matches_the_config(rows, calibration, monkeypatch):
    monkeypatch.delenv("RESTORATION_ENGINE", raising=False)
    assert Concierge(rows, calibration, LABOR).restoration_engine == config.DEFAULT_ENGINE
