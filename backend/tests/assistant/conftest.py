"""Fixtures for the assistant: engine-shaped inputs for each band, a recording fake Bedrock client, and a clock."""
from __future__ import annotations

import json

import pytest
from concierge.assistant import factcard
from concierge.assistant.compose import Assistant
from concierge.assistant.phrase import BedrockPhraser, Breaker


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def risk_for(band: str | None, *, confidence="Medium", extrapolated=False) -> dict:
    """What RiskEngine.score(...).to_dict() returns, trimmed to the keys the assistant reads."""
    if band is None:
        return {"status": "insufficient_data", "band": None, "confidence": None, "top_factors": [],
                "assumptions": [], "missing_data": [], "could_change": [], "label": None,
                "severity": {"preset": "typical", "multiplier": 1.0, "extrapolated": False},
                "fallback": {"reason_code": "no_history_small_circuit",
                             "message": "We don't have enough storm history for your line to rate it. "
                                        "Prepare as if an outage is possible."}}
    return {
        "status": "ok", "band": band,
        "label": "circuit-level historical susceptibility, not a per-premise forecast",
        "severity": {"preset": "typical", "multiplier": 1.0, "extrapolated": extrapolated},
        "confidence": {"level": confidence, "records": 12,
                       "reason": "12 damage reports on your line in the 2 past storms we use."},
        "top_factors": [
            {"id": "hist", "direction": "raises", "weight": 0.6,
             "text": "Your line had more storm damage than most lines in past storms (about 4.2 reports per 1,000 homes)."},
            {"id": "dev", "direction": "raises", "weight": 0.3,
             "text": "Power reaches you through 4 protective devices; more devices means more places a fault can cut service."},
        ],
        "assumptions": ["Storm strength is a scenario you choose, not a weather forecast.",
                        "Risk comes from damage reports on your line in 2 past storms.",
                        "How much your service type and place on the line matter is an assumption, not something we measured."],
        "missing_data": ["No weather data is used.",
                         "Damage reports show where repairs were needed, not which homes lost power."],
        "could_change": ["A storm that hits your area harder or softer than past storms.",
                         "Tree trimming or line upgrades done since the last storm."],
        "fallback": None,
    }


def restoration_for(p50=6, p90=14, *, ok=True) -> dict:
    from concierge.restoration.common import display
    if not ok:
        return {"status": "insufficient_data", "estimate": None}
    return {"status": "ok", "label": "estimate, not confirmed",
            "estimate": {"p50_hours": p50, "p90_hours": p90, "low_hours": p50, "typical_hours": p50,
                         "high_hours": p90, "display": display(p50, p90), "meaning": "m"}}


def make_card(kind="msg_pre_storm", band="Medium", *, channel="sms", need=False, p50=6, p90=14, prior=None,
              stage=None, ok=True, confidence="Medium") -> dict:
    risk = risk_for(band, confidence=confidence)
    rest = restoration_for(p50, p90, ok=band is not None and ok)
    return factcard.build(kind=kind, risk=risk, restoration=rest, channel=channel, has_declared_need=need,
                          prior_display=prior, stage=stage)


class FakeBedrock:
    """Records every Converse call. `reply` is a string, a list of strings (used in turn), or a callable(kwargs)->str.
    `error` (an exception instance, or a list of them used in turn) is raised instead of replying."""

    def __init__(self, reply="", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def converse(self, **kw):
        self.calls.append(kw)
        err = self.error
        if isinstance(err, list):
            err = err.pop(0) if err else None
        if err is not None:
            raise err
        reply = self.reply
        if callable(reply):
            reply = reply(kw)
        elif isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        return {"output": {"message": {"content": [{"text": reply}]}}}

    @property
    def recorded_text(self) -> str:
        return json.dumps(self.calls, default=str)


class Throttle(Exception):
    def __init__(self):
        super().__init__("Too many requests")
        self.response = {"Error": {"Code": "ThrottlingException"}}


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def fake():
    return FakeBedrock()


@pytest.fixture()
def make_assistant(clock):
    def build(client, model_id="test-model", **kw):
        breaker = Breaker(clock=clock)
        return Assistant(BedrockPhraser(client, model_id, breaker), model_id, breaker=breaker, clock=clock, **kw)
    return build


GOOD = {
    "explanation": {"text": "Your area has a moderate chance of losing power in this scenario. We are fairly sure, "
                            "because the line has some storm history. If you lose power, our model estimates restoration "
                            "could take {{range}}. This is an estimate."},
    "msg_pre_storm": {"text": "Storm alert: your area has a moderate chance of power outages. If you lose power, our "
                              "model estimates restoration could take {{range}}. Please charge your devices."},
    "msg_outage_confirmed": {"text": "We confirmed an outage at your location. Our estimate for restoring power is "
                                     "{{range}}. This is an estimate and it may change."},
    "msg_estimate_updated": {"text": "Update: our estimate is now {{range}}, changed from {{prior_range}}. "
                                     "It is still an estimate."},
    "msg_restoration_complete": {"text": "Power is back at your location. If it is still out, please report it."},
}


def good_reply(kind, items=None):
    """A draft that passes the guard. `items` is [(id, text)] for the item kinds (the model repeats them in its own words)."""
    if kind in GOOD:
        return json.dumps(GOOD[kind])
    intro = ("Power is back. Here is what to do next." if kind == "actions_after"
             else "Here is a short plan for your situation. If you lose power, our model estimates it could take {{range}}.")
    return json.dumps({"intro": intro, "items": [{"id": i, "text": t} for i, t in (items or [])]})


# ---- service-level fixtures (synthetic premises, same builder the alerts tests use) ---------------------------------
LABOR = {"units": [], "adders": [], "indirect_labor_multiplier": 4.5}


@pytest.fixture(scope="session")
def risk_engine(tmp_path_factory):
    from concierge.risk.build import build_artifacts
    from concierge.risk.engine import RiskEngine
    from tests.risk.fixtures import make_synth
    synth = make_synth(tmp_path_factory.mktemp("assistant_synth"))
    out = tmp_path_factory.mktemp("assistant_artifacts")
    build_artifacts(customer_dbf=synth.customer_dbf, arcos_dbf=synth.arcos_dbf, topology_txt=synth.topology_txt,
                    device_dbfs=synth.device_dbfs, out_dir=out, salt="test-salt")
    return RiskEngine.from_artifacts(out)


@pytest.fixture(scope="session")
def ok_token(risk_engine):
    return next(t for t, r in risk_engine._rows.items() if r["st"] == "ok")


@pytest.fixture(scope="session")
def fallback_token(risk_engine):
    return next(t for t, r in risk_engine._rows.items() if r["st"] != "ok")


NO_RANGE = {
    "explanation": {"text": "We cannot rate your outage risk with confidence, so please prepare as if an outage is possible."},
    "msg_pre_storm": {"text": "Storm alert: your area has a moderate chance of power outages. Please charge your devices."},
    "msg_outage_confirmed": {"text": "We confirmed an outage at your location. We do not have a reliable estimate yet."},
    "msg_estimate_updated": {"text": "Update: we are still working on an estimate for your outage."},
}


def reply_from_call(call):
    """A model that behaves: repeats the items it was given (if any) and uses only the placeholders the card allows."""
    user = call["messages"][0]["content"][0]["text"]
    system = call["system"][0]["text"]
    card = json.loads(user.split("<card>")[1].split("</card>")[0])
    kind = card["kind"]
    items = None
    if "<items>" in user:
        listing = json.loads(user.split("<items>")[1].split("</items>")[0])
        items = [(i["id"], i["text"]) for i in listing]
    if card["range"] is None and kind in NO_RANGE:
        return json.dumps(NO_RANGE[kind])
    if card["range"] is None and kind in ("prep_before", "actions_during"):
        return json.dumps({"intro": "Here is a short plan for your situation.",
                           "items": [{"id": i, "text": t} for i, t in (items or [])]})
    return good_reply(kind, items)


@pytest.fixture()
def make_service(risk_engine, clock):
    from concierge.api.service import Concierge

    def build(client=None, model_id="test-model"):
        assistant = None
        if client is not None:
            assistant = Assistant(BedrockPhraser(client, model_id, Breaker(clock=clock)), model_id, clock=clock)
        return Concierge(risk_engine._rows, risk_engine._cal, LABOR, assistant=assistant)
    return build
