import json
import logging
import re

import pytest

from concierge.risk.build import build_artifacts
from concierge.risk.calibrate import BacktestError
from concierge.risk.engine import RiskEngine, SeverityError
from concierge.risk.schema import RESULT_SCHEMA, WhitelistError, validate_output
from concierge.risk.tokens import TOKEN_RE, get_salt, make_token

from .conftest import SALT, load_rows
from .fixtures import MAIN_IDS, QUIET_BIG_ID, QUIET_SMALL_ID, SMALL_ID, make_synth


def _first(synth, pred):
    return next(p for p in synth.premise_ids if pred(p))


# ---- build outputs -----------------------------------------------------------------------------------------
def test_build_finds_two_storms_and_counts(built, synth):
    _, cal = built
    assert [s["start"] for s in cal["storms"]] == ["2024-09-27", "2025-02-12"]
    c = cal["counts"]
    assert c["circuits"] == 15 and c["premises"] == len(synth.premise_ids) + len(synth.no_chain_ids)
    assert c["fallbacks"]["circuit_too_small"] == 30 and c["fallbacks"]["no_circuit"] == 5
    assert c["excluded_records"]["no_circuit_key"] == 3 and c["excluded_records"]["outside_storm_windows"] == 6
    assert c["excluded_records"]["label_not_in_crosswalk"] == 2 and c["unmapped_circuits"] == 0
    assert cal["backtest"]["gate"] == {"monotone": True, "override": False}
    assert cal["thresholds"]["medium"] < cal["thresholds"]["high"]


def test_damage_lands_on_the_right_circuit_via_crosswalk(built, synth, tok):
    """Labels are shifted (circuit 22 is labelled CIR23). Records must follow the crosswalk, not the digits."""
    from .fixtures import MAIN_IDS, MAIN_S1, MAIN_S2
    rows = {r["t"]: r for r in load_rows(built[0])}
    for idx, c in enumerate(MAIN_IDS):
        pid = next(p for p in synth.premise_ids if synth.circuit_of[p] == c)
        r = rows[tok(pid)]
        expected = MAIN_S1[idx] + MAIN_S2[idx]
        if expected == 0:
            assert r["st"] == "fb"
        else:
            assert r["n"] == expected, f"circuit {c}"


def test_circuit_without_a_label_is_unmapped_fallback(tmp_path):
    s = make_synth(tmp_path / "unl", unlabelled=frozenset({MAIN_IDS[-1]}))
    out = tmp_path / "o"
    cal = build_artifacts(customer_dbf=s.customer_dbf, arcos_dbf=s.arcos_dbf, topology_txt=s.topology_txt,
                          device_dbfs=s.device_dbfs, out_dir=out, salt=SALT, allow_non_monotone=True)
    assert cal["counts"]["unmapped_circuits"] == 1 and cal["counts"]["fallbacks"]["circuit_unmapped"] == 100


def test_every_band_has_evidence(built):
    _, cal = built
    for band in ("Low", "Medium", "High"):
        ev = cal["band_evidence"][band]
        assert ev["backtest"] == "Sep 2024 -> Feb 2025" and ev["ci95"][0] <= ev["hit_rate"] <= ev["ci95"][1]
        assert "at least 2 damage reports per 1,000 homes" in ev["hit_definition"]


def test_build_is_deterministic(synth, built, tmp_path):
    out2 = tmp_path / "again"
    cal2 = build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf,
                           topology_txt=synth.topology_txt, device_dbfs=synth.device_dbfs, out_dir=out2, salt=SALT)
    assert cal2["config_hash"] == built[1]["config_hash"]
    assert (out2 / "premise_risk.jsonl").read_text() == (built[0] / "premise_risk.jsonl").read_text()


def test_non_monotone_backtest_fails_unless_overridden(tmp_path):
    s = make_synth(tmp_path / "rev", reverse_s2=True)
    kw = dict(customer_dbf=s.customer_dbf, arcos_dbf=s.arcos_dbf, topology_txt=s.topology_txt,
              device_dbfs=s.device_dbfs, salt=SALT)
    with pytest.raises(BacktestError):
        build_artifacts(out_dir=tmp_path / "o1", **kw)
    cal = build_artifacts(out_dir=tmp_path / "o2", allow_non_monotone=True, **kw)
    assert cal["backtest"]["gate"] == {"monotone": False, "override": True}


def test_salt_is_required(monkeypatch):
    monkeypatch.delenv("CONCIERGE_TOKEN_SALT", raising=False)
    with pytest.raises(RuntimeError):
        get_salt()


# ---- 6.2 privacy of artifacts -----------------------------------------------------------------------------
def test_artifacts_contain_no_identifiers(built, synth):
    out, _ = built
    text = (out / "premise_risk.jsonl").read_text() + (out / "calibration.json").read_text()
    for pid in synth.premise_ids[:50] + synth.life_support_ids + synth.no_chain_ids:
        assert pid not in text
    for needle in ("MAIN ST", "ROANOKE", "NOWHERE", "LS Life", "critical", "CIR", "premise_nu", "circuit_id"):
        assert needle not in text


def test_rows_have_only_whitelisted_keys_and_valid_tokens(built):
    allowed = {"t", "st", "why", "R", "Re", "svc", "P", "H", "n", "c"}
    for r in load_rows(built[0]):
        assert set(r) <= allowed and TOKEN_RE.match(r["t"])


def test_token_is_salted_hmac():
    assert make_token(b"a", "123") != make_token(b"b", "123")
    assert make_token(b"a", "123") == make_token(b"a", "123") and "123" not in make_token(b"a", "123")


# ---- 6.1 output whitelist ---------------------------------------------------------------------------------
def test_unknown_top_level_key_raises():
    with pytest.raises(WhitelistError):
        validate_output({"premise_nu": "123"})


def test_unknown_nested_key_raises():
    with pytest.raises(WhitelistError):
        validate_output({"confidence": {"level": "High", "circuit": "CIR2"}})
    with pytest.raises(WhitelistError):
        validate_output({"top_factors": [{"id": "hist", "device_id": 5}]})


def test_identifier_like_text_raises_but_token_is_exempt():
    with pytest.raises(WhitelistError):
        validate_output({"assumptions": ["served by CIR60"]})
    with pytest.raises(WhitelistError):
        validate_output({"assumptions": ["premise 123456789"]})
    validate_output({"premise_token": "p_0123456789abcdef", "config_hash": "sha256:" + "0" * 64})


def test_result_schema_has_no_sensitive_keys():
    flat = json.dumps(RESULT_SCHEMA)
    for bad in ("premise_nu", "circuit_id", "circuit_name", "address", "lat\"", "lon\"", "critical", "substation",
                "device_id", "scada"):
        assert bad not in flat


# ---- engine behaviour --------------------------------------------------------------------------------------
def test_ok_response_is_complete_and_labelled(engine, synth, tok):
    pid = _first(synth, lambda p: synth.circuit_of[p] == 21 and synth.service_of[p] == "Overhead")
    r = engine.score(tok(pid), "typical").to_dict()
    assert r["status"] == "ok" and r["band"] in ("Low", "Medium", "High")
    assert r["label"] == "circuit-level historical susceptibility, not a per-premise forecast"
    assert r["assumptions"] and r["could_change"] and r["missing_data"]
    assert r["band_evidence"]["hit_rate"] is not None
    assert 1 <= len(r["top_factors"]) <= 3
    assert r["confidence"]["level"] in ("Low", "Medium", "High")


def test_deterministic(engine, synth, tok):
    t = tok(synth.premise_ids[1100])
    assert engine.score(t, 1.3).to_dict() == engine.score(t, 1.3).to_dict()


def test_unknown_premise_falls_back(engine):
    for bad in ("p_ffffffffffffffff", "not-a-token", "", None, 5, "123456789"):
        r = engine.score(bad).to_dict()
        assert r["status"] == "insufficient_data" and r["band"] is None and r["score"] is None
        assert r["fallback"]["reason_code"] == "unknown_premise" and r["fallback"]["message"]
        assert r["premise_token"] is None          # never echo untrusted input


@pytest.mark.parametrize("circuit,reason", [(SMALL_ID, "circuit_too_small"), (QUIET_SMALL_ID, "no_history_small_circuit"),
                                            (MAIN_IDS[0], "no_history_small_circuit")])
def test_circuit_fallbacks(engine, synth, tok, circuit, reason):
    pid = _first(synth, lambda p: synth.circuit_of[p] == circuit)
    r = engine.score(tok(pid)).to_dict()
    assert r["status"] == "insufficient_data" and r["fallback"]["reason_code"] == reason and r["band"] is None


def test_premise_without_chain_falls_back(engine, synth, tok):
    r = engine.score(tok(synth.no_chain_ids[0])).to_dict()
    assert r["fallback"]["reason_code"] == "no_circuit"


def test_zero_history_big_circuit_is_low_band_low_confidence(engine, synth, tok):
    pid = _first(synth, lambda p: synth.circuit_of[p] == QUIET_BIG_ID and synth.service_of[p] == "Overhead")
    r = engine.score(tok(pid), "feb-2025-like").to_dict()
    assert r["band"] == "Low" and r["confidence"]["level"] == "Low" and r["confidence"]["records"] == 0


@pytest.mark.parametrize("bad", [10, 0.1, 3.01, -1, float("nan"), float("inf"), "monsoon", True, [1]])
def test_bad_severity_rejected(engine, synth, tok, bad):
    with pytest.raises(SeverityError):
        engine.score(tok(synth.premise_ids[0]), bad)


def test_severity_never_lowers_band_and_can_raise_it(engine, synth, tok):
    order = {"Low": 0, "Medium": 1, "High": 2}
    raised = False
    for pid in synth.premise_ids[::7]:
        a, b = engine.score(tok(pid), "sep-2024-like"), engine.score(tok(pid), "feb-2025-like")
        if a.status != "ok":
            continue
        assert b.score >= a.score and order[b.band] >= order[a.band]
        raised |= order[b.band] > order[a.band]
    assert raised


def test_thresholds_are_fixed_and_high_share_grows(engine, built, synth, tok):
    before = dict(built[1]["thresholds"])
    shares = {}
    for sev in ("typical", "feb-2025-like"):
        res = [engine.score(tok(p), sev) for p in synth.premise_ids]
        ok = [r for r in res if r.status == "ok"]
        shares[sev] = sum(r.band == "High" for r in ok) / len(ok)
    assert engine._cal["thresholds"] == before
    assert shares["feb-2025-like"] > shares["typical"]


def test_extrapolated_severity_lowers_confidence(engine, synth, tok):
    pid = _first(synth, lambda p: synth.circuit_of[p] == 22 and synth.service_of[p] == "Overhead")
    typ, beyond = engine.score(tok(pid), "typical"), engine.score(tok(pid), "beyond-observed")
    assert typ.confidence["level"] == "High" and beyond.confidence["level"] == "Medium"
    assert beyond.severity["extrapolated"] and any("extrapolation" in a for a in beyond.assumptions)
    assert not typ.severity["extrapolated"]


def test_custom_multiplier_above_largest_observed_is_extrapolated(engine, synth, tok):
    t = tok(synth.premise_ids[1100])
    top = engine._max_observed                        # 188 / mean(115, 188) for the synthetic storms
    assert engine.score(t, top - 0.01).severity["extrapolated"] is False
    assert engine.score(t, top + 0.1).severity["extrapolated"] is True


def test_unknown_service_flags_missing_data_and_lowers_confidence(engine, synth, tok):
    pid = _first(synth, lambda p: synth.circuit_of[p] == 22 and synth.service_of[p] == "Unknown")
    r = engine.score(tok(pid))
    assert r.confidence["level"] == "Medium" and any("service type" in m for m in r.missing_data)


def test_underground_lowers_in_top_factors(engine, synth, tok):
    seen = 0
    for pid in synth.premise_ids:
        if synth.service_of[pid] != "Underground":
            continue
        r = engine.score(tok(pid), "typical")
        if r.status != "ok":
            continue
        for f in r.top_factors:
            if f["id"] == "type":
                seen += 1
                assert f["direction"] == "lowers"
    assert seen > 0


def test_factor_text_has_no_identifiers(engine, synth, tok):
    for pid in synth.premise_ids[::13]:
        r = engine.score(tok(pid), "feb-2025-like").to_dict()      # to_dict runs the identifier scan
        for f in r["top_factors"]:
            assert not re.search(r"CIR|STATION|\d{6,}", f["text"])


def test_audit_log_has_no_raw_ids(engine, synth, tok, caplog):
    pid = synth.premise_ids[1150]
    with caplog.at_level(logging.INFO, logger="concierge.risk.audit"):
        engine.score(tok(pid), "typical")
    line = json.loads(caplog.records[-1].message)
    assert line["token"] == tok(pid) and line["config_hash"].startswith("sha256:")
    assert pid not in caplog.text


# ---- real data ---------------------------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

DATA = Path(__file__).resolve().parents[3] / "data"
NEEDS_DATA = pytest.mark.skipif(not (DATA / "Data1" / "Customer_Clip.dbf").exists()
                                or (DATA / "Data1" / "Customer_Clip.dbf").stat().st_size < 10_000,
                                reason="real data (git lfs pull) not available")


@pytest.mark.realdata
@NEEDS_DATA
def test_real_data_build(tmp_path):
    cal = build_artifacts(
        customer_dbf=DATA / "Data1" / "Customer_Clip.dbf",
        arcos_dbf=DATA / "Data1" / "arcos_prod_damage_09_28_Clip.dbf",
        topology_txt=DATA / "matched_upstream" / "matched_upstream.txt",
        device_dbfs=[DATA / "Data1" / "Fuse_Bank_Clip.dbf", DATA / "Data2" / "Transformer_Clip.dbf",
                     DATA / "Data2" / "Switch_Clip.dbf", DATA / "Data2" / "Recloser_Bank_Clip.dbf"],
        out_dir=tmp_path, salt=SALT)
    assert [(s["start"], s["end"]) for s in cal["storms"]] == [("2024-09-27", "2024-10-02"), ("2025-02-12", "2025-02-18")]
    assert [s["records"] for s in cal["storms"]] == [193, 1254]
    c = cal["counts"]
    assert c["circuits"] == 110 and c["premises"] == 117646 and c["topology_lines_skipped"] == 0
    assert c["scored"] + sum(c["fallbacks"].values()) == c["premises"]
    assert cal["backtest"]["gate"]["monotone"] is True
    hr = [cal["band_evidence"][b]["hit_rate"] for b in ("Low", "Medium", "High")]
    assert hr == sorted(hr)

    text = (tmp_path / "calibration.json").read_text()
    assert "CIR" not in text and "critical" not in text
    eng = RiskEngine.from_artifacts(tmp_path)
    bands = {eng.score(t, "typical").band for t, r in list(eng._rows.items())[:5000] if r["st"] == "ok"}
    assert bands == {"Low", "Medium", "High"}
    assert eng.score("p_0000000000000000").status == "insufficient_data"
