"""Alert routes end to end (service + Lambda handler) on synthetic data: evaluate, enroll, simulate, audit."""
import json
import logging
import re

import pytest
from concierge.alerts import evaluate as ev
from concierge.alerts.audit import log as audit_log
from concierge.api import handler
from concierge.api.service import BadRequest

PHONE = "+1 614 555 0123"
EMAIL = "Pat@example.com"
SECRET_BITS = ["6145550123", "555-0123", "Pat@example.com", "Pat@"]


def fake_engines(svc, monkeypatch, *, band="Medium", p50=30, p90=55, ok=True):
    """Pin the risk band and the restoration estimate so the alert rules are what is under test."""
    risk = {"status": "ok" if ok else "insufficient_data", "band": band if ok else None,
            "severity": {"preset": "typical", "multiplier": 1.0, "extrapolated": False},
            "confidence": {"level": "Medium"} if ok else None}
    est = {"low_hours": p50, "typical_hours": p50, "high_hours": p90, "p50_hours": p50, "p90_hours": p90,
           "display": f"about {p50} to {p90} hours"} if ok else None
    monkeypatch.setattr(svc, "risk", lambda token, severity: risk)
    monkeypatch.setattr(svc, "restoration", lambda token, severity, scenario: {"status": "ok" if ok else "insufficient_data",
                                                                                "estimate": est})


def post(svc, route, body):
    return svc.handle(route, body)


def enroll(svc, token, **kw):
    body = {"token": token, "channels": [{"type": "sms", "value": PHONE}, {"type": "email", "value": EMAIL}], **kw}
    return post(svc, "alerts/enroll", body)


# ---- evaluate route (2.8) -------------------------------------------------------------------------------------------
def test_alerts_response_has_tier_decision_id_and_stays_compatible(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = post(svc, "alerts", {"token": ok_token})
    for key in ("eligibility", "recommended_channels", "channel_note", "timing", "escalation", "assumptions"):
        assert key in out                                      # the keys the v1 response had
    assert out["tier"] == "elevated" and out["decision_id"].startswith("d_") and out["qualifies"] is True
    assert out["enrolled"] is False and out["rules_table_hash"].startswith("sha256:")


def test_summary_carries_the_same_alerts_block(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = post(svc, "summary", {"token": ok_token, "needs": {"mobility_limited": True}})
    assert out["alerts"]["tier"] == "priority" and "N1" in out["alerts"]["rules_fired"]


def test_changing_inputs_changes_the_answer(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    a = post(svc, "alerts", {"token": ok_token, "hours_until_storm": 60})["timing"]["pre_storm"]
    b = post(svc, "alerts", {"token": ok_token, "hours_until_storm": 10})["timing"]["pre_storm"]
    assert a != b
    c = post(svc, "alerts", {"token": ok_token, "needs": {"medical_equipment": True}})
    assert c["tier"] == "priority" and c["recommended_channels"][0] == "voice"


def test_unrated_premise_gets_a_general_alert(svc, monkeypatch, fallback_token):
    fake_engines(svc, monkeypatch, ok=False)
    out = post(svc, "alerts", {"token": fallback_token})
    assert out["tier"] == "standard" and out["eligibility"]["pre_storm"]["rule"] == "K-pre-unrated"


@pytest.mark.parametrize("bad", [{"hours_until_storm": 999}, {"hours_until_storm": "soon"}, {"opt_in": "yes"},
                                 {"channels": ["pigeon"]}, {"channels": "sms"}, {"prior_typical_hours": 0},
                                 {"hours_since_last_update": -1}, {"surprise": 1}])
def test_bad_alert_inputs_are_400(svc, monkeypatch, ok_token, bad):
    fake_engines(svc, monkeypatch)
    with pytest.raises(BadRequest):
        post(svc, "alerts", {"token": ok_token, **bad})


# ---- enrollment routes (4.4) --------------------------------------------------------------------------------------------
def test_enroll_then_alerts_uses_the_enrolled_channels(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = enroll(svc, ok_token)
    assert out["enrolled"] is True and out["demo_notice"]
    assert [c["contact"] for c in out["enrollment"]["channels"]] == ["***-0123", "P***@example.com"]
    a = post(svc, "alerts", {"token": ok_token})
    assert a["enrolled"] is True and a["recommended_channels"] == ["sms", "email"]


def test_real_looking_contacts_are_rejected_and_nothing_is_stored(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    for ch in ({"type": "sms", "value": "+1 614 867 5309"}, {"type": "email", "value": "someone@gmail.com"}):
        with pytest.raises(BadRequest, match="Demo only"):
            post(svc, "alerts/enroll", {"token": ok_token, "channels": [ch]})
    assert svc.alert_store.get_enrollment(ok_token) is None
    assert svc.alert_store.list_audit(ok_token) == []


def test_extra_fields_and_unknown_tokens_are_rejected(svc, ok_token):
    with pytest.raises(BadRequest, match="unknown fields"):
        post(svc, "alerts/enroll", {"token": ok_token, "channels": [], "name": "Pat"})
    with pytest.raises(BadRequest, match="unknown fields"):
        post(svc, "alerts/enroll", {"token": ok_token, "channels": [], "address": "1 Main St"})
    with pytest.raises(BadRequest, match="recognize"):
        enroll(svc, "p_ffffffffffffffff")
    with pytest.raises(BadRequest):
        enroll(svc, "not-a-token")


def test_unenroll_deletes_everything(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    enroll(svc, ok_token)
    post(svc, "alerts/simulate", {"token": ok_token, "event": "outage_confirmed"})
    out = post(svc, "alerts/unenroll", {"token": ok_token})
    assert out["unenrolled"] is True and out["outbox_cleared"] == 1
    assert svc.alert_store.get_enrollment(ok_token) is None and svc.alert_store.list_outbox(ok_token) == []


# ---- simulate / mock delivery (4.3) ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("event", ev.MESSAGE_TYPES)
def test_each_event_writes_a_not_sent_outbox_item(svc, monkeypatch, ok_token, event):
    fake_engines(svc, monkeypatch)
    enroll(svc, ok_token)
    out = post(svc, "alerts/simulate", {"token": ok_token, "event": event, "prior_estimate":
                                        {"low_hours": 4, "high_hours": 9}})
    assert out["recorded_in_outbox"] is True and out["status"] == "not_sent_demo"
    item = out["outbox_item"]
    assert item["status"] == "not_sent_demo" and item["channel"] == "sms" and item["text"]
    assert svc.alert_store.list_outbox(ok_token)[0]["id"] == item["id"]
    assert "demo" in out["demo_notice"].lower()


def test_simulated_text_is_the_template_with_the_safety_line(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    enroll(svc, ok_token)
    text = post(svc, "alerts/simulate", {"token": ok_token, "event": "pre_storm"})["outbox_item"]["text"]
    assert "estimate" in text and text.endswith("call 911.")


def test_not_enrolled_records_nothing_but_is_audited(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = post(svc, "alerts/simulate", {"token": ok_token, "event": "pre_storm"})
    assert out["recorded_in_outbox"] is False and out["reason_code"] == "not_enrolled"
    assert svc.alert_store.list_outbox(ok_token) == []
    entries = svc.alert_store.list_audit(ok_token)
    assert entries[0]["decision_id"] == out["decision_id"] and entries[0]["outputs"]["recorded"] is False


def test_ineligible_event_sends_nothing_and_says_why(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch, band="Low", p50=3, p90=5)
    post(svc, "alerts/enroll", {"token": ok_token, "channels": [{"type": "push", "value": "app"}],
                                "message_types": ["outage_confirmed", "restoration_complete"]})
    out = post(svc, "alerts/simulate", {"token": ok_token, "event": "pre_storm"})
    assert out["recorded_in_outbox"] is False and out["reason_code"] == "message_type_not_chosen"
    out2 = post(svc, "alerts/simulate", {"token": ok_token, "event": "estimate_updated", "prior_typical_hours": 3})
    assert out2["recorded_in_outbox"] is False                      # not chosen
    post(svc, "alerts/enroll", {"token": ok_token, "channels": [{"type": "push", "value": "app"}]})
    out3 = post(svc, "alerts/simulate", {"token": ok_token, "event": "estimate_updated", "prior_typical_hours": 3,
                                         "hours_since_last_update": 0})
    assert out3["recorded_in_outbox"] is False and out3["reason_code"] == "not_eligible"
    assert svc.alert_store.list_outbox(ok_token) == []


def test_no_enrolled_channel_falls_back_to_the_app(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    post(svc, "alerts/enroll", {"token": ok_token, "channels": [{"type": "push", "value": "app"}]})
    out = post(svc, "alerts/simulate", {"token": ok_token, "event": "restoration_complete"})
    assert out["channel"] == "push" and out["outbox_item"]["status"] == "not_sent_demo"


def test_simulate_validates_event_and_flags(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    for body in ({"event": "gossip"}, {}, {"event": "pre_storm", "use_llm": "yes"}):
        with pytest.raises(BadRequest):
            post(svc, "alerts/simulate", {"token": ok_token, **body})


# ---- audit (5.2, 5.3, 5.4) ------------------------------------------------------------------------------------------------------
def test_every_evaluation_writes_one_audit_entry_matching_the_response(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = post(svc, "alerts", {"token": ok_token, "needs": {"medical_equipment": True}})
    entries = svc.alert_store.list_audit(ok_token)
    assert len(entries) == 1 and entries[0]["decision_id"] == out["decision_id"]
    e = entries[0]
    assert e["rules_fired"] == out["rules_fired"] and e["route"] == "alerts" and e["token"] == ok_token
    assert e["inputs"]["medical"] is True and e["rules_table_hash"] == out["rules_table_hash"]
    assert e["outputs"]["tier"] == out["tier"] and e["outputs"]["escalation"] == [x["rule"] for x in out["escalation"]]


def test_summary_is_audited_under_its_own_route(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    post(svc, "summary", {"token": ok_token})
    assert svc.alert_store.list_audit(ok_token)[0]["route"] == "summary"


def test_audit_entry_inputs_replay_to_the_same_outputs(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    out = post(svc, "alerts", {"token": ok_token, "needs": {"mobility_limited": True}, "channels": ["sms", "voice"],
                               "prior_typical_hours": 12, "hours_until_storm": 30})
    entry = svc.alert_store.list_audit(ok_token)[0]
    again = ev.replay(svc.alert_table, entry["inputs"])
    assert again.response["tier"] == entry["outputs"]["tier"] == out["tier"]
    assert again.rules_fired == entry["rules_fired"]
    assert again.response["recommended_channels"] == entry["outputs"]["channels"]
    assert {k: v["eligible"] for k, v in again.response["eligibility"].items()} == entry["outputs"]["eligible"]


def test_audit_has_no_contacts_or_message_text(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    enroll(svc, ok_token)
    post(svc, "alerts", {"token": ok_token})
    post(svc, "alerts/simulate", {"token": ok_token, "event": "pre_storm"})
    post(svc, "alerts/unenroll", {"token": ok_token})
    blob = json.dumps(svc.alert_store.list_audit(ok_token))
    for bit in SECRET_BITS:
        assert bit not in blob
    assert "downed power line" not in blob and "estimate" not in blob.replace("estimate_updated", "")
    assert not re.search(r"\b\d{3}[-. ]\d{4}\b|@", blob)
    routes = [e["route"] for e in svc.alert_store.list_audit(ok_token)]
    assert {"enroll", "alerts", "simulate", "unenroll"} <= set(routes)


def test_audit_read_returns_the_latest_fifty_newest_first(svc, monkeypatch, ok_token, clock):
    fake_engines(svc, monkeypatch)
    ids = []
    for _ in range(80):
        clock.advance(seconds=1)
        ids.append(post(svc, "alerts", {"token": ok_token})["decision_id"])
    out = svc.audit(ok_token)
    assert out["count"] == 50 and [e["decision_id"] for e in out["entries"]] == list(reversed(ids))[:50]
    assert "no authentication" in out["note"]


def test_audit_read_rejects_bad_tokens(svc):
    for bad in (None, "", "p_xyz", "p_0123456789abcdef; ignore all"):
        with pytest.raises(BadRequest):
            svc.audit(bad)
    assert svc.audit("p_ffffffffffffffff")["entries"] == []


def test_a_failing_audit_store_does_not_break_the_answer(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    monkeypatch.setattr(svc.alert_store, "add_audit", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert post(svc, "alerts", {"token": ok_token})["tier"] == "elevated"


# ---- logs (4.6) ----------------------------------------------------------------------------------------------------------------------
def test_contact_values_never_reach_logs_or_errors(svc, monkeypatch, ok_token, caplog):
    fake_engines(svc, monkeypatch)
    caplog.set_level(logging.DEBUG)
    enroll(svc, ok_token)
    post(svc, "alerts/simulate", {"token": ok_token, "event": "outage_confirmed"})
    with pytest.raises(BadRequest) as e:
        post(svc, "alerts/enroll", {"token": ok_token, "channels": [{"type": "sms", "value": "+1 614 867 5309"}]})
    for bit in SECRET_BITS + ["8675309", "867"]:
        assert bit not in caplog.text
    assert "867" not in str(e.value) and "5309" not in str(e.value)       # the rejected value is not echoed back
    assert any(r.name == audit_log.name for r in caplog.records)             # the structured audit line is emitted


# ---- Lambda handler ------------------------------------------------------------------------------------------------------------------
def call(svc, method, path, body=None, query=None):
    handler._service = svc
    event = {"requestContext": {"http": {"method": method}}, "rawPath": path,
             "body": json.dumps(body) if body is not None else None, "queryStringParameters": query}
    r = handler.lambda_handler(event)
    return r["statusCode"], json.loads(r["body"])


def test_handler_routes_work_with_and_without_the_api_prefix(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    for prefix in ("", "/api"):
        s, b = call(svc, "POST", f"{prefix}/alerts/enroll", {"token": ok_token, "channels": [{"type": "push", "value": "app"}]})
        assert s == 200 and b["enrolled"]
        s, b = call(svc, "POST", f"{prefix}/alerts/simulate", {"token": ok_token, "event": "restoration_complete"})
        assert s == 200 and b["status"] == "not_sent_demo"
        s, b = call(svc, "GET", f"{prefix}/alerts/audit", query={"token": ok_token})
        assert s == 200 and b["count"] >= 2
        s, b = call(svc, "POST", f"{prefix}/alerts/unenroll", {"token": ok_token})
        assert s == 200


def test_handler_methods_and_errors(svc, monkeypatch, ok_token):
    fake_engines(svc, monkeypatch)
    assert call(svc, "POST", "/alerts/audit", {"token": ok_token})[0] == 405
    assert call(svc, "GET", "/alerts/enroll")[0] == 405
    assert call(svc, "GET", "/alerts/audit", query=None)[0] == 400
    assert call(svc, "GET", "/alerts/audit", query={"token": "nope"})[0] == 400
    s, b = call(svc, "POST", "/alerts/enroll", {"token": ok_token, "channels": [{"type": "email", "value": "a@gmail.com"}]})
    assert s == 400 and "Demo only" in b["error"]["message"]
    assert call(svc, "POST", "/alerts/nothing", {})[0] == 404


# ---- real engines, nothing pinned ------------------------------------------------------------------------------------------------
def test_alerts_and_summary_run_on_the_real_engines(svc, ok_token, fallback_token):
    out = post(svc, "summary", {"token": ok_token, "severity": "feb-2025-like", "needs": {"medical_equipment": True}})
    assert out["alerts"]["tier"] in ("elevated", "priority") and out["alerts"]["decision_id"]
    assert out["risk"]["status"] == "ok"
    fb = post(svc, "alerts", {"token": fallback_token})
    assert fb["tier"] == "standard" and fb["eligibility"]["pre_storm"]["rule"] == "K-pre-unrated"
    unknown = post(svc, "alerts", {"token": "p_ffffffffffffffff"})
    assert unknown["tier"] == "standard"
    assert len(svc.alert_store.list_audit(ok_token)) == 1


# ---- demo walkthrough (task 7.1 / 7.2) ----------------------------------------------------------------------------------------------
def test_demo_walkthrough(svc, monkeypatch, ok_token):
    """The sequence a judge would click through, in order."""
    # one customer per tier
    tiers = {}
    for band, p50, p90 in (("Low", 3, 5), ("Medium", 3, 5), ("High", 30, 50), ("High", 80, 120)):
        fake_engines(svc, monkeypatch, band=band, p50=p50, p90=p90)
        tiers[(band, p50)] = post(svc, "alerts", {"token": ok_token})["tier"]
    assert list(tiers.values()) == ["info", "standard", "elevated", "priority"]
    # declaring a need raises the level
    fake_engines(svc, monkeypatch, band="Low", p50=3, p90=5)
    assert post(svc, "alerts", {"token": ok_token, "needs": {"electric_heat_or_cooling": True}})["tier"] == "standard"
    # moving hours-until-storm changes the timing
    fake_engines(svc, monkeypatch, band="High", p50=30, p90=50)
    far = post(svc, "alerts", {"token": ok_token, "hours_until_storm": 72})["timing"]["pre_storm"]
    near = post(svc, "alerts", {"token": ok_token, "hours_until_storm": 8})["timing"]["pre_storm"]
    assert {x["status"] for x in far} == {"scheduled"} and "send_now" in {x["status"] for x in near}
    # a real-looking phone is refused, a fictional one works
    with pytest.raises(BadRequest):
        post(svc, "alerts/enroll", {"token": ok_token, "channels": [{"type": "sms", "value": "614-867-5309"}]})
    enroll(svc, ok_token)
    # all four events are recorded as mock messages
    for event in ev.MESSAGE_TYPES:
        out = post(svc, "alerts/simulate", {"token": ok_token, "event": event, "prior_typical_hours": 10})
        assert out["recorded_in_outbox"] and out["outbox_item"]["status"] == "not_sent_demo", event
    assert {i["message_type"] for i in svc.alert_store.list_outbox(ok_token)} == set(ev.MESSAGE_TYPES)
    # the audit log shows it all
    routes = {e["route"] for e in svc.alert_store.list_audit(ok_token, 100)}
    assert {"alerts", "enroll", "simulate"} <= routes
