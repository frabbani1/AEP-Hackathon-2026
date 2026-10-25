"""Tasks 3.3 and 7.5 on real premises: no row value in any prompt, and the identifier scan over every assistant response.

Needs built artifacts with the restoration tree. Skipped otherwise. Build them with:
  CONCIERGE_TOKEN_SALT=<secret> <py> scripts/curate.py --no-drawings
  CONCIERGE_TOKEN_SALT=<secret> <py> scripts/build_risk_artifacts.py
or point ASSISTANT_REAL_RISK_DIR and ASSISTANT_REAL_LABOR at another build (the same salt must be used for both steps).
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import pytest
from concierge.api.service import Concierge
from concierge.assistant import factcard
from concierge.assistant.compose import Assistant
from concierge.assistant.phrase import BedrockPhraser
from concierge.restoration.tree import RestorationTree
from concierge.risk.engine import RiskEngine
from concierge.risk.schema import _scan_strings

from .conftest import FakeBedrock, reply_from_call

ROOT = Path(__file__).resolve().parents[3]
RISK = Path(os.environ.get("ASSISTANT_REAL_RISK_DIR", ROOT / "artifacts" / "risk"))
LABOR = Path(os.environ.get("ASSISTANT_REAL_LABOR", ROOT / "artifacts" / "curated" / "labor_standards.json"))
pytestmark = [pytest.mark.realdata,
              pytest.mark.skipif(not ((RISK / "restoration_tree.json").exists() and LABOR.exists()),
                                 reason="build the artifacts first (see module docstring)")]

IDENT = re.compile(r"CIR\d|SUBSTATION|STATION|\d{6,}", re.I)


@pytest.fixture(scope="module")
def real():
    engine = RiskEngine.from_artifacts(RISK)
    tree = RestorationTree.from_dict(json.loads((RISK / "restoration_tree.json").read_text()))
    client = FakeBedrock(reply_from_call)
    assistant = Assistant(BedrockPhraser(client, "test-model"), "test-model")
    svc = Concierge(engine._rows, json.loads((RISK / "calibration.json").read_text()), json.loads(LABOR.read_text()),
                    tree=tree, restoration_engine="v2", assistant=assistant)
    scored = sorted(t for t, r in engine._rows.items() if r["st"] == "ok" and "pn" in r)
    other = sorted(t for t, r in engine._rows.items() if r["st"] != "ok")
    return svc, engine, client, scored, other


def run_all_kinds(svc, token, needs=None):
    needs = needs or {"medical_equipment": False, "mobility_limited": False, "electric_heat_or_cooling": False}
    risk, rest = svc.risk(token, "typical"), svc.restoration(token, "typical", svc.scenario({}))
    guidance = svc.guidance(risk, rest, needs)
    est = rest.get("estimate")
    outs = []
    for kind in factcard.KINDS:
        card = factcard.build(kind=kind, risk=risk, restoration=rest, channel="sms",
                              has_declared_need=any(needs.values()), prior_display="about 4 to 9 hours")
        outs.append(svc.assistant.respond(kind, card=card, guidance=guidance,
                                          typical_hours=est["p50_hours"] if est else None, needs=needs))
    return outs


def test_prompts_for_200_random_real_premises_hold_no_row_value(real):
    svc, engine, client, scored, other = real
    rnd = random.Random(2026)
    picks = rnd.sample(scored, 170) + rnd.sample(other, min(30, len(other)))
    for token in picks:
        row = engine._rows[token]
        before = len(client.calls)
        outs = run_all_kinds(svc, token)
        assert all(o["source"] in ("model", "cache") for o in outs), [o["guard"] for o in outs if o["source"] == "template"]
        recorded = json.dumps(client.calls[before:], default=str)
        user_turns = " ".join(c["messages"][0]["content"][0]["text"] for c in client.calls[before:])
        assert token not in recorded and token[2:] not in recorded
        assert not re.search(r"\d", user_turns)                       # no row value, count, hex cell or circuit number can be there
        assert not re.search(r"CIR|circuit|station|[0-9a-f]{15}", user_turns, re.I)
        for key in ("pc", "pn", "cn", "ap"):                          # fields that identify a place on the network
            if row.get(key) is not None and not isinstance(row.get(key), (int, float)):
                assert str(row[key]) not in recorded


def test_identifier_scan_over_assistant_responses_for_200_random_real_premises(real):
    svc, engine, client, scored, other = real
    rnd = random.Random(7)
    picks = rnd.sample(scored, 170) + rnd.sample(other, min(30, len(other)))
    needs = {"medical_equipment": True, "mobility_limited": True, "electric_heat_or_cooling": True}
    findings = 0
    for token in picks:
        for out in run_all_kinds(svc, token, needs):
            _scan_strings(out, "response")       # the same scan the service runs before a response leaves
            blob = json.dumps({k: v for k, v in out.items() if k not in ("cache", "prompt_hash")})
            findings += bool(IDENT.search(blob)) + (token in blob)
    assert findings == 0
