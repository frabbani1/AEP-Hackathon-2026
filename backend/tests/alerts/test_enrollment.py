"""Demo sign-up: fictional contacts only, masking, expiry, upsert, unenroll, and the Dynamo store."""
import json
from datetime import timedelta

import pytest
from concierge.alerts import enroll as en
from concierge.alerts.stores import AUDIT_TTL, DynamoStore, MemoryStore

TOKEN = "p_0123456789abcdef"


# ---- contact validation (4.1) ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["+1 614 555 0123", "(614) 555-0100", "614-555-0199", "555-0123", "1-614-555-0150"])
def test_fictional_phones_are_accepted(value):
    assert en.validate_channels([{"type": "sms", "value": value}])[0]["value"].endswith(("0123", "0100", "0199", "0150"))


@pytest.mark.parametrize("value", [
    "+1 614 867 5309",      # real-looking exchange
    "614-555-0200",         # 555 exchange but outside 0100-0199
    "614-555-0099",         # just below the fictional range
    "614-556-0123",         # near miss on the exchange
    "014-555-0123",         # invalid area code
    "+44 20 7946 0958",     # not North American
    "555-012", "", "call me", "614-555-0123; drop table", "+1 614 555 0123 x99"])
def test_everything_else_is_rejected_with_the_demo_message(value):
    with pytest.raises(en.EnrollError) as e:
        en.validate_channels([{"type": "voice", "value": value}])
    assert str(e.value) == en.DEMO_ONLY_MESSAGE


@pytest.mark.parametrize("value", ["a@example.com", "Test.User+x@example.org", "x@mail.example.net", "x@foo.test",
                                   "x@bar.invalid"])
def test_reserved_email_domains_are_accepted(value):
    assert en.validate_channels([{"type": "email", "value": value}])[0]["value"] == value


@pytest.mark.parametrize("value", ["someone@gmail.com", "x@example.com.evil.io", "x@notexample.com", "x@test",
                                   "x@@example.com", "x example@example.com", "@example.com", "x@example"])
def test_real_or_malformed_emails_are_rejected(value):
    with pytest.raises(en.EnrollError) as e:
        en.validate_channels([{"type": "email", "value": value}])
    assert str(e.value) == en.DEMO_ONLY_MESSAGE


def test_push_only_takes_app_and_shape_is_strict():
    assert en.validate_channels([{"type": "push", "value": "app"}]) == [{"type": "push", "value": "app"}]
    for bad in ([{"type": "push", "value": "device-token-123"}], [{"type": "fax", "value": "x"}],
                [{"type": "sms", "value": "555-0123", "name": "Pat"}], [], "sms",
                [{"type": "sms", "value": "555-0123"}, {"type": "sms", "value": "555-0124"}],
                [{"type": "sms", "value": "5" * 100}]):
        with pytest.raises(en.EnrollError):
            en.validate_channels(bad)


def test_message_types_validation():
    assert en.validate_message_types(None) == list(en.MESSAGE_TYPES)
    assert en.validate_message_types(["restoration_complete", "pre_storm"]) == ["pre_storm", "restoration_complete"]
    for bad in ([], ["gossip"], "pre_storm", [1]):
        with pytest.raises(en.EnrollError):
            en.validate_message_types(bad)


# ---- store behavior (4.2) --------------------------------------------------------------------------------------------
def make(clock, store=None):
    store = store or MemoryStore(clock)
    rec = en.enroll(store, TOKEN, [{"type": "sms", "value": "+1 614 555 0123"},
                                   {"type": "email", "value": "Pat@example.com"}], None, None, clock)
    return store, rec


def test_masked_on_read(clock):
    _, rec = make(clock)
    view = en.public_view(rec)
    assert view["channels"] == [{"type": "sms", "contact": "***-0123"}, {"type": "email", "contact": "P***@example.com"}]
    blob = json.dumps(view)
    assert "6145550123" not in blob and "Pat@" not in blob and TOKEN not in blob
    assert "demo" in view["demo_notice"].lower()


def test_expiry_is_24_hours_after_creation(clock):
    store, rec = make(clock)
    assert rec["expires_at"] == "2026-10-25T12:00:00Z" and rec["created_at"] == "2026-10-24T12:00:00Z"
    clock.advance(hours=23, minutes=59)
    assert store.get_enrollment(TOKEN) is not None
    clock.advance(minutes=2)
    assert store.get_enrollment(TOKEN) is None


def test_upsert_keeps_one_record_with_the_latest_channels(clock):
    store, _ = make(clock)
    en.enroll(store, TOKEN, [{"type": "push", "value": "app"}], {"medical_equipment": True}, ["outage_confirmed"], clock)
    rec = store.get_enrollment(TOKEN)
    assert [c["type"] for c in rec["channels"]] == ["push"] and rec["message_types"] == ["outage_confirmed"]
    assert rec["needs"]["medical_equipment"] is True
    assert en.public_view(rec)["declared_needs"] == ["medical_equipment"]


def test_needs_are_only_stored_when_supplied(clock):
    _, rec = make(clock)
    assert rec["needs"] is None and en.public_view(rec)["declared_needs"] is None


def test_unenroll_removes_enrollment_and_outbox(clock):
    store, _ = make(clock)
    store.add_outbox(TOKEN, {"id": "o_abc", "ts": "2026-10-24T12:00:00Z", "text": "x"})
    res = en.unenroll(store, TOKEN)
    assert res == {"unenrolled": True, "outbox_cleared": 1}
    assert store.get_enrollment(TOKEN) is None and store.list_outbox(TOKEN) == []
    assert en.unenroll(store, TOKEN)["unenrolled"] is False


def test_audit_entries_expire_after_seven_days(clock):
    store = MemoryStore(clock)
    store.add_audit(TOKEN, {"ts": "2026-10-24T12:00:00Z", "decision_id": "d_a"})
    clock.advance(days=6)
    assert len(store.list_audit(TOKEN)) == 1
    clock.advance(days=1, seconds=1)
    assert store.list_audit(TOKEN) == [] and AUDIT_TTL == timedelta(days=7)


# ---- Dynamo store against a fake table -------------------------------------------------------------------------------
class FakeTable:
    """Just enough of a boto3 Table for DynamoStore: put_item, get_item, delete_item and the query form it uses."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        assert isinstance(Item["ttl"], int) and isinstance(Item["doc"], str)     # no floats, no Decimals
        self.items[(Item["token"], Item["sk"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["token"], Key["sk"]))
        return {"Item": item} if item else {}

    def delete_item(self, Key):
        self.items.pop((Key["token"], Key["sk"]), None)

    def query(self, KeyConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues, ScanIndexForward, Limit):
        assert KeyConditionExpression == "#t = :t AND begins_with(#s, :p)"
        t, p = ExpressionAttributeValues[":t"], ExpressionAttributeValues[":p"]
        rows = sorted((v for (tok, sk), v in self.items.items() if tok == t and sk.startswith(p)), key=lambda v: v["sk"])
        if not ScanIndexForward:
            rows.reverse()
        return {"Items": rows[:Limit]}


def test_dynamo_store_round_trip_and_ordering(clock):
    table = FakeTable()
    store = DynamoStore(table, clock)
    _, rec = make(clock, store)
    assert store.get_enrollment(TOKEN)["channels"][0]["type"] == "sms"
    assert table.items[(TOKEN, "ENROLL")]["ttl"] == int((clock.now + timedelta(hours=24)).timestamp())
    for i in range(3):
        clock.advance(seconds=1)
        store.add_audit(TOKEN, {"ts": "2026-10-24T12:00:0%dZ" % i, "decision_id": f"d_{i}"})
        store.add_outbox(TOKEN, {"id": f"o_{i}", "ts": "2026-10-24T12:00:0%dZ" % i, "text": "t"})
    assert [e["decision_id"] for e in store.list_audit(TOKEN)] == ["d_2", "d_1", "d_0"]      # newest first
    assert len(store.list_audit(TOKEN, 2)) == 2
    assert store.clear_outbox(TOKEN) == 3 and store.list_outbox(TOKEN) == []
    assert store.delete_enrollment(TOKEN) is True and store.get_enrollment(TOKEN) is None


def test_dynamo_store_ignores_rows_past_their_expiry(clock):
    store = DynamoStore(FakeTable(), clock)
    make(clock, store)
    clock.advance(hours=25)
    assert store.get_enrollment(TOKEN) is None
