"""Task 6.1 and 6.3: the D6 contract, label and meaning text, assumptions, shim fields, fallback, engine switch."""
import json

import pytest

from concierge.restoration import config
from concierge.restoration import model as m
from concierge.restoration import v1
from concierge.restoration.common import RestorationError
from concierge.restoration.schema import RESULT_SCHEMA, WhitelistError, validate_output

from .fixtures import LABOR, ROWS, SEVERITY, TOKENS

TOKEN = TOKENS[0]


_DEFAULT_ROW = object()


def est(tree, token=TOKEN, row=_DEFAULT_ROW, **kw):
    base = dict(token=token, row=ROWS[TOKEN] if row is _DEFAULT_ROW else row, severity=SEVERITY, fallback=None,
                base_confidence="Medium", labor=LABOR, tree=tree, engine="v2")
    base.update(kw)
    return m.estimate(**base)


def test_contract_keys_and_values(tree):
    out = est(tree)
    assert set(out) == set(RESULT_SCHEMA)
    assert out["engine_version"] == "restoration-v2" and out["status"] == "ok" and out["fallback"] is None
    assert out["label"] == "estimate, not confirmed"
    assert out["model_note"] == "This is a model estimate, not a restoration time confirmed by the utility."
    assert set(out["estimate"]) == {"p50_hours", "p90_hours", "low_hours", "typical_hours", "high_hours", "display",
                                    "meaning"}
    assert set(out["scenario"]) == {"crews", "access", "damage_source", "damage_count"}
    assert set(out["confidence"]) == {"level", "reason"}
    assert out["simulation"]["model_path"] == "tree"
    validate_output(out)
    json.dumps(out)


def test_shim_fields_match_the_new_fields(tree):
    for kw in (dict(), dict(damage_count=30), dict(crews=1, damage_count=300)):
        e = est(tree, **kw)["estimate"]
        assert e["low_hours"] == e["p50_hours"] and e["typical_hours"] == e["p50_hours"]
        assert e["high_hours"] == e["p90_hours"]
        assert all(isinstance(e[k], int) for k in ("p50_hours", "p90_hours", "low_hours", "typical_hours", "high_hours"))


def test_display_uses_hours_below_36_and_days_above(tree):
    e = est(tree, damage_count=2)["estimate"]
    assert e["p90_hours"] < 36 and e["display"] == m.display(e["p50_hours"], e["p90_hours"])
    assert e["display"].startswith("about ") and e["display"].endswith(" hours")
    big = est(tree, damage_count=400, crews=1)["estimate"]
    assert big["p90_hours"] >= 36 and big["display"].endswith(" days")


def test_meaning_calls_the_low_end_likely_not_guaranteed(tree):
    e = est(tree)["estimate"]
    assert f"Likely about {e['p50_hours']} hour" in e["meaning"] or "Likely about" in e["meaning"]
    assert "not a guarantee" in e["meaning"]
    assert "Half of our simulated outcomes finish within" in e["meaning"]
    assert "Nine in ten finish within" in e["meaning"]
    assert "could take up to" in e["meaning"]


@pytest.mark.parametrize("hours, text", [(1, "1 hour"), (6, "6 hours"), (35, "35 hours"), (36, "2 days"),
                                         (48, "2 days"), (60, "2 days"), (100, "4 days")])
def test_meaning_hour_text(hours, text):
    assert m._hours_text(hours) == text


def test_label_note_assumptions_and_could_change_are_present(tree):
    out = est(tree)
    assert len(out["assumptions"]) >= 4 and len(out["could_change"]) >= 1 and len(out["missing_data"]) >= 1
    text = " ".join(out["assumptions"])
    for needle in ("16 hours out of every 24", "4.5x multiplier", "step restoration", "engineering assumption",
                   "spread evenly", "clock starts when crews start", "not fitted"):
        assert needle in text, needle


def test_crews_appear_in_the_assumptions(tree):
    assert "6 crews are assumed to work 16 hours out of every 24." in est(tree, crews=6)["assumptions"]
    assert "1 crew is assumed to work 16 hours out of every 24." in est(tree, crews=1)["assumptions"]


def test_multiplier_in_the_assumptions_comes_from_the_sheet(tree):
    from .fixtures import make_labor
    assert any("6x multiplier" in a for a in est(tree, labor=make_labor(multiplier=6.0))["assumptions"])


def test_damage_source_is_listed_in_scenario_and_assumptions(tree):
    out = est(tree, damage_count=12)
    assert out["scenario"] == {"crews": 3, "access": "normal", "damage_source": "you_provided", "damage_count": 12}
    assert any("damage_source: you_provided" in a and "12 repair jobs" in a for a in out["assumptions"])
    assert out["confidence"]["reason"] == "Based on the damage you entered, not on past reports."
    path = est(tree, damage=[("pole", 2), ("transformer", 1)])
    assert path["scenario"]["damage_source"] == "you_provided" and path["scenario"]["damage_count"] == 3
    assert any("damage_source: you_provided" in a for a in path["assumptions"])
    sim = est(tree)
    assert sim["scenario"]["damage_source"] == "simulated" and sim["scenario"]["damage_count"] >= 1
    assert any("damage_source: simulated" in a for a in sim["assumptions"])
    assert sim["confidence"]["reason"] == "Based on past damage reports on your line."


def test_access_assumption_only_when_access_is_not_normal(tree):
    assert not any("reach" in a for a in est(tree)["assumptions"])
    assert any("restricted to reach" in a for a in est(tree, access="restricted")["assumptions"])
    assert any("remote to reach" in a for a in est(tree, access="remote")["assumptions"])


# ---- fallback -------------------------------------------------------------------------------------------------------
def test_unknown_premise_gets_the_insufficient_data_fallback(tree):
    fb = {"reason_code": "unknown_premise", "message": "We can't find that location."}
    out = est(tree, token=None, row=None, fallback=fb)
    assert out["status"] == "insufficient_data" and out["estimate"] is None and out["steps"] == []
    assert out["label"] is None and out["simulation"] is None and out["sensitivity"] == []
    assert out["fallback"]["reason_code"] == "unknown_premise"
    assert out["fallback"]["message"] == "We can't find that location. We also can't estimate how long restoration would take."
    validate_output(out)


def test_fallback_matches_v1_wording_and_shape(tree):
    fb = {"reason_code": "circuit_too_small", "message": "Too small."}
    row = {"st": "fb", "why": "circuit_too_small"}
    v2 = est(tree, row=row, fallback=fb)
    old = v1.estimate(token=TOKEN, row=row, severity=SEVERITY, fallback=fb, base_confidence=None, labor=LABOR)
    assert v2["fallback"] == old["fallback"] and v2["status"] == old["status"]
    assert v2["estimate"] is None and old["estimate"] is None
    # a scored row without line context falls back the same way
    row = {k: v for k, v in ROWS[TOKEN].items() if k != "nf"}
    assert est(tree, row=row)["fallback"] == v1.estimate(token=TOKEN, row=row, severity=SEVERITY, fallback=None,
                                                         base_confidence="Medium", labor=LABOR)["fallback"]


# ---- model path --------------------------------------------------------------------------------------------------------
def test_row_without_tree_position_uses_the_level_path(tree):
    row = {k: v for k, v in ROWS[TOKEN].items() if k not in ("pn", "pc")}
    out = est(tree, row=row)
    assert out["status"] == "ok" and out["simulation"]["model_path"] == "v1_levels"
    assert any("Detailed line data is not available" in a for a in out["assumptions"])
    assert 1 <= out["estimate"]["p50_hours"] <= out["estimate"]["p90_hours"]
    assert est(None, row=row)["simulation"]["model_path"] == "v1_levels"          # no tree artifact at all
    assert est(tree, row={**row, "pc": 0, "pn": 99})["simulation"]["model_path"] == "v1_levels"
    assert est(tree, row={**row, "pc": 5, "pn": 0})["simulation"]["model_path"] == "v1_levels"


def test_level_path_supports_every_input(tree):
    row = {k: v for k, v in ROWS[TOKEN].items() if k not in ("pn", "pc")}
    for kw in (dict(damage_count=20), dict(damage=[("pole", 2)]), dict(crews=9, access="remote")):
        out = est(tree, row=row, **kw)
        assert out["status"] == "ok" and out["sensitivity"]


def test_alternate_feed_gets_a_note(tree):
    assert not any("another line" in a for a in est(tree)["assumptions"])
    assert any("another line" in a for a in est(tree, row={**ROWS[TOKEN], "ap": 1})["assumptions"])


def test_response_never_contains_tree_counts_or_positions(tree):
    text = json.dumps(est(tree, damage_count=20))
    for key in ("served", "pn", "pc", "cn", "n0", "nl", "nf", "parent", "depth", "node"):
        assert f'"{key}"' not in text
    for bad in ("device", "circuit", "premise_nu", "CIR"):
        assert bad not in text


# ---- schema ---------------------------------------------------------------------------------------------------------
def test_schema_rejects_extra_keys_and_identifiers(tree):
    out = est(tree)
    for mutate in (lambda d: d.update(node=3), lambda d: d["estimate"].update(served=40),
                   lambda d: d["scenario"].update(circuit="CIR12"), lambda d: d["simulation"].update(seed=1),
                   lambda d: d["assumptions"].append("fed by CIR60"), lambda d: d["assumptions"].append("id 1234567"),
                   lambda d: d["sensitivity"][0].update(typical_hours=4)):
        bad = json.loads(json.dumps(out))
        mutate(bad)
        with pytest.raises(WhitelistError):
            validate_output(bad)


# ---- engine switch (6.3) -------------------------------------------------------------------------------------------------
def test_engine_comes_from_the_environment(tree, monkeypatch):
    monkeypatch.delenv("RESTORATION_ENGINE", raising=False)
    assert config.engine_from_env() == config.DEFAULT_ENGINE
    monkeypatch.setenv("RESTORATION_ENGINE", "v1")
    assert config.engine_from_env() == "v1"
    kw = dict(token=TOKEN, row=ROWS[TOKEN], severity=SEVERITY, fallback=None, base_confidence="Medium", labor=LABOR,
              tree=tree)
    assert m.estimate(**kw)["engine_version"] == "restoration-v1"
    monkeypatch.setenv("RESTORATION_ENGINE", "v2")
    assert m.estimate(**kw)["engine_version"] == "restoration-v2"
    monkeypatch.setenv("RESTORATION_ENGINE", "v3")
    with pytest.raises(ValueError):
        config.engine_from_env()
    with pytest.raises(RestorationError):
        m.estimate(**kw, engine="v3")


def test_explicit_engine_overrides_the_environment(tree, monkeypatch):
    monkeypatch.setenv("RESTORATION_ENGINE", "v1")
    assert est(tree)["engine_version"] == "restoration-v2"


def test_v1_engine_has_no_damage_count(tree):
    with pytest.raises(RestorationError) as e:
        est(tree, engine="v1", damage_count=5)
    assert "v1" in str(e.value)


def test_v1_through_the_dispatcher_ignores_the_tree(tree):
    a = est(tree, engine="v1")
    b = est(None, engine="v1")
    assert a == b and a["engine_version"] == "restoration-v1" and "label" not in a


def test_default_engine_is_v2(monkeypatch):
    monkeypatch.delenv("RESTORATION_ENGINE", raising=False)
    assert config.DEFAULT_ENGINE == "v2" and config.engine_from_env() == "v2"
