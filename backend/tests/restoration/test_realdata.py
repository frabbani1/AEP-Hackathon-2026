"""Tasks 6.4 and 7.1 on the real artifacts (artifacts/risk and artifacts/curated; skipped when they are not built).

Build them with:  CONCIERGE_TOKEN_SALT=<secret> <py> scripts/curate.py --no-drawings
                  CONCIERGE_TOKEN_SALT=<secret> <py> scripts/build_risk_artifacts.py
"""
import json
import random
import re
import time
from pathlib import Path

import pytest

from concierge.api.service import Concierge
from concierge.restoration.schema import validate_output
from concierge.restoration.tree import RestorationTree
from concierge.risk.engine import RiskEngine
from concierge.risk.schema import _scan_strings

ROOT = Path(__file__).resolve().parents[3]
RISK = ROOT / "artifacts" / "risk"
LABOR = ROOT / "artifacts" / "curated" / "labor_standards.json"

pytestmark = [pytest.mark.realdata,
              pytest.mark.skipif(not ((RISK / "restoration_tree.json").exists() and LABOR.exists()),
                                 reason="build the artifacts first (see module docstring)")]


@pytest.fixture(scope="module")
def real():
    engine = RiskEngine.from_artifacts(RISK)
    tree = RestorationTree.from_dict(json.loads((RISK / "restoration_tree.json").read_text()))
    labor = json.loads(LABOR.read_text())
    calibration = json.loads((RISK / "calibration.json").read_text())
    svc = Concierge(engine._rows, calibration, labor, tree=tree, restoration_engine="v2")
    scored = sorted(t for t, r in engine._rows.items() if r["st"] == "ok" and "pn" in r)
    return svc, engine, scored


def p(svc, token, **kw):
    out = svc.handle("restoration", {"token": token, **kw})
    return out["estimate"]["p50_hours"], out["estimate"]["p90_hours"], out


# ---- 6.4 identifier scan and key whitelist --------------------------------------------------------------------------
def test_identifier_scan_and_whitelist_over_200_random_premises(real):
    svc, engine, scored = real
    rnd = random.Random(2026)
    findings = 0
    pat = re.compile(r"\bd_[0-9a-f]{12}\b|CIR\d|\b\d{6,}\b")
    for tok in rnd.sample(scored, 200):
        kw = {"crews": rnd.choice([1, 2, 3, 6, 12, 40]), "access": rnd.choice(["normal", "restricted", "remote"]),
              "severity": rnd.choice(["sep-2024-like", "typical", "feb-2025-like", "beyond-observed", 1.7])}
        mode = rnd.random()
        if mode < 0.3:
            kw["damage_count"] = rnd.choice([1, 5, 40, 150, 400])
        elif mode < 0.45:
            kw["damage"] = [{"asset_type": rnd.choice(["pole", "primary_line", "fuse_bank", "transformer"]),
                             "qty": rnd.randint(1, 20)}]
        out = svc.handle("restoration", {"token": tok, **kw})        # runs the service-level identifier scan
        try:
            validate_output(out)                                      # key whitelist + identifier scan
            _scan_strings(out, "response")
        except Exception:                                             # noqa: BLE001
            findings += 1
        body = {k: v for k, v in out.items() if k != "premise_token"}
        if pat.search(json.dumps(body)):
            findings += 1
        assert out["status"] == "ok" and out["simulation"]["model_path"] == "tree"
        assert 1 <= out["estimate"]["p50_hours"] <= out["estimate"]["p90_hours"]
    assert findings == 0


def test_summary_route_passes_the_scan_on_real_premises(real):
    svc, _, scored = real
    for tok in random.Random(5).sample(scored, 15):
        out = svc.handle("summary", {"token": tok, "damage_count": 25, "needs": {"medical_equipment": True}})
        assert out["restoration"]["estimate"]["typical_hours"] == out["restoration"]["estimate"]["p50_hours"]


def test_non_scored_and_unknown_tokens_fall_back(real):
    svc, engine, _ = real
    bad = next(t for t, r in engine._rows.items() if r["st"] != "ok")
    for tok in (bad, "p_0000000000000000"):
        out = svc.handle("restoration", {"token": tok})
        assert out["status"] == "insufficient_data" and out["estimate"] is None
        assert "We also can't estimate how long restoration would take." in out["fallback"]["message"]


# ---- 7.1 integration: the range moves in the expected direction -----------------------------------------------------------
def band_premises(engine, scored):
    picks, seen = {}, {"Low": [], "Medium": [], "High": []}
    for tok in scored:
        seen[engine.score(tok, "typical").band].append(tok)
    for band, toks in seen.items():
        picks[band] = toks[len(toks) // 2]
    return picks


def test_one_premise_per_risk_band_responds_in_the_expected_direction(real):
    svc, engine, scored = real
    for band, tok in band_premises(engine, scored).items():
        base50, base90, _ = p(svc, tok)
        c50, c90, _ = p(svc, tok, crews=6)
        assert c50 <= base50 and c90 <= base90, band                       # crews 3 -> 6
        r50, r90, _ = p(svc, tok, access="restricted")
        assert r50 >= base50 and r90 >= base90, band                       # access normal -> restricted
        d5, _, _ = p(svc, tok, damage_count=5)
        d40, _, _ = p(svc, tok, damage_count=40)
        assert d40 >= d5 - 1, band                                         # damage_count 5 -> 40


def test_direction_holds_across_random_premises(real):
    svc, _, scored = real
    for tok in random.Random(9).sample(scored, 40):
        a50, a90, _ = p(svc, tok)
        assert p(svc, tok, crews=6)[0] <= a50
        assert p(svc, tok, access="restricted")[0] >= a50
        assert p(svc, tok, damage_count=40)[0] >= p(svc, tok, damage_count=5)[0] - 1
        assert p(svc, tok, severity="beyond-observed")[0] >= p(svc, tok, severity="sep-2024-like")[0] - 1


def test_every_what_if_names_the_expected_changes(real):
    svc, _, scored = real
    _, _, out = p(svc, scored[len(scored) // 3], crews=4)
    assert [s["change"] for s in out["sensitivity"]] == ["One more crew", "One fewer crew", "Hard-to-reach work sites",
                                                         "25% more damage"]


def test_latency_is_well_under_the_budget_on_this_machine(real):
    """Not a Lambda measurement (see design.md); a guard against an accidental order-of-magnitude slowdown."""
    svc, engine, scored = real
    heavy = sorted(scored, key=lambda t: engine._rows[t]["R"] * engine._rows[t]["cn"], reverse=True)[:5]
    sample = heavy + random.Random(1).sample(scored, 45)
    times = []
    for tok in sample:
        t0 = time.perf_counter()
        svc.handle("restoration", {"token": tok, "severity": "beyond-observed"})
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    assert times[int(0.95 * len(times)) - 1] < 300 and times[-1] < 600
