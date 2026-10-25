"""Task 5.2: GET /evidence through the service and the Lambda handler, plus a real-data end-to-end run."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from concierge.api import handler
from concierge.api.service import Concierge
from concierge.backtest import evidence, report as rp, split as sp
from concierge.backtest.config import DEFAULT_CONFIG, LIMITS, NOTE
from concierge.risk.build import build_artifacts
from concierge.risk.engine import RiskEngine
from tests.risk.fixtures import make_synth

from .fixtures import StubEngine, make_split
from .test_rest_eval import rich_s1

CFG = replace(DEFAULT_CONFIG, n_permutations=200, n_bootstrap=100, k_bootstrap=30)
LABOR = {"units": [], "adders": [], "indirect_labor_multiplier": 4.5}
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def risk_engine(tmp_path_factory):
    synth = make_synth(tmp_path_factory.mktemp("bt_synth"))
    out = tmp_path_factory.mktemp("bt_artifacts")
    build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf, topology_txt=synth.topology_txt,
                    device_dbfs=synth.device_dbfs, out_dir=out, salt="test-salt")
    return RiskEngine.from_artifacts(out)


@pytest.fixture(scope="module")
def published():
    return rp.build_report(make_split(s1=rich_s1()), CFG, engine=StubEngine(), check_counts=False)


def make_service(risk_engine, loader):
    return Concierge(risk_engine._rows, risk_engine._cal, LABOR, evidence_loader=loader)


def test_evidence_returns_verdicts_plots_and_the_six_limits(risk_engine, published):
    out = make_service(risk_engine, lambda: published).evidence()
    assert out["status"] == "ok" and out["note"] == NOTE and out["limits"] == list(LIMITS) and len(out["limits"]) == 6
    assert {v["code"] for v in out["verdicts"]} >= {"risk_high_vs_base", "risk_permutation", "rest_vs_baseline"}
    assert [p["path"] for p in out["plots"]] == ["serving/backtest/risk_bands.svg",
                                                  "serving/backtest/restoration_scatter.svg",
                                                  "serving/backtest/repair_hist.svg"]
    assert out["risk"]["base_rate"]["premise_weighted"] > 0
    evidence.scan_value(out)
    assert evidence.find_identifiers(json.dumps(out["verdicts"] + out["limits"])) == []


def test_evidence_response_has_no_per_circuit_data(risk_engine, published):
    text = json.dumps(make_service(risk_engine, lambda: published).evidence())
    assert '"points"' not in text and '"circuit":' not in text and "inputs" not in text


def test_evidence_says_unavailable_until_a_report_is_published(risk_engine):
    out = make_service(risk_engine, lambda: None).evidence()
    assert out["status"] == "unavailable" and out["limits"] == list(LIMITS)
    assert make_service(risk_engine, None).evidence()["status"] == "unavailable"


def test_a_published_report_is_picked_up_without_a_restart(risk_engine, published, monkeypatch):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return published if calls["n"] >= 2 else None

    svc = make_service(risk_engine, loader)
    now = [1000.0]
    monkeypatch.setattr("concierge.api.service.time.monotonic", lambda: now[0])
    assert svc.evidence()["status"] == "unavailable"
    now[0] += 5
    assert svc.evidence()["status"] == "unavailable" and calls["n"] == 1      # not asked again within a minute
    now[0] += 60
    assert svc.evidence()["status"] == "ok" and calls["n"] == 2
    now[0] += 600
    svc.evidence()
    assert calls["n"] == 2                                                      # found once, then cached


def test_a_loader_error_gives_unavailable_not_a_crash(risk_engine):
    def boom():
        raise RuntimeError("s3 down")
    assert make_service(risk_engine, boom).evidence()["status"] == "unavailable"


def lam(method, path):
    return handler.lambda_handler({"rawPath": path, "requestContext": {"http": {"method": method}}})


def test_handler_serves_get_evidence_under_both_prefixes(risk_engine, published, monkeypatch):
    monkeypatch.setattr(handler, "_service", make_service(risk_engine, lambda: published))
    for path in ("/evidence", "/api/evidence"):
        r = lam("GET", path)
        body = json.loads(r["body"])
        assert r["statusCode"] == 200 and body["status"] == "ok" and len(body["limits"]) == 6


def test_handler_rejects_post_to_evidence(risk_engine, monkeypatch):
    monkeypatch.setattr(handler, "_service", make_service(risk_engine, None))
    r = lam("POST", "/evidence")
    assert r["statusCode"] == 405


def test_store_reads_a_local_evidence_file(tmp_path, monkeypatch, published):
    from concierge.api import store
    p = tmp_path / "backtest.json"
    monkeypatch.setenv("EVIDENCE_FILE", str(p))
    assert store.load_evidence() is None
    p.write_text(json.dumps(published))
    assert store.load_evidence()["backtest_version"] == published["backtest_version"]


# ---- real data end to end (needs artifacts/curated and artifacts/risk) ------------------------------------------------
TABLES, CAL = ROOT / "artifacts" / "curated" / "tables", ROOT / "artifacts" / "risk" / "calibration.json"


@pytest.mark.skipif(not (CAL.exists() and (TABLES / "damage_fact.parquet").exists()), reason="artifacts not built")
def test_real_data_report_end_to_end(tmp_path):
    calibration = json.loads(CAL.read_text())
    rep = rp.build_report(sp.load_split(TABLES), CFG.scaled(), engine=None, stored_backtest=calibration["backtest"])
    assert rep["spec02_check"] == "passed" and rep["restoration"]["status"] == "not_run"
    levels = {v["code"]: v["level"] for v in rep["verdicts"]}
    # High's range reaches the base rate, so it must not be reported as supported.
    assert levels["risk_high_vs_base"] == "mixed" and levels["risk_medium_vs_high"] == "mixed"
    assert levels["risk_low_vs_base"] == "supports" and levels["rest_vs_baseline"] == "not_testable"
    assert levels["rest_calibrated"] == "not_testable"
    paths = rp.write_outputs(rep, tmp_path, tmp_path / "backtest.md")
    assert all(p.exists() for p in paths.values())
    view = evidence.evidence_view(json.loads(paths["backtest.json"].read_text()))
    assert view["sample_run"] is True and len(view["limits"]) == 6
