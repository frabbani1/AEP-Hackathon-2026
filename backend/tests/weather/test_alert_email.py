"""The "email me this alert" button: one real email, the address never kept, and the page always sees the text."""
import json
import logging
import smtplib

import pytest

from concierge.alerts import mailer as mailer_mod
from concierge.alerts.mailer import Mailer, RateLimit
from concierge.api.service import BadRequest
from concierge.weather.geocode import Geocoder

from .conftest import ZONE_A
from .fakes import FakeOpenMeteo
from .test_outlook import FakeGeocoder

ME = "faiz.rabbani@gmail.com"


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL. Records what it was asked to do; sends nothing."""

    outbox: list = []
    calls: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.calls.append(("connect", host, port, timeout, context is not None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls", context is not None))

    def login(self, user, password):
        if FakeSMTP.fail is not None:
            raise FakeSMTP.fail
        FakeSMTP.calls.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.outbox.append(msg)


@pytest.fixture(autouse=True)
def clean_smtp():
    FakeSMTP.outbox, FakeSMTP.calls, FakeSMTP.fail = [], [], None
    yield


@pytest.fixture()
def build(make_svc):
    def make(mailer="on", port=587, limiter=None, wx=None):
        svc = make_svc(fake_=wx)
        svc.geocoder = Geocoder(fetch=FakeGeocoder())
        if mailer == "on":
            svc.mailer = Mailer("smtp.example.org", port, "sender@example.org", "app-password",
                                limiter=limiter, smtp_factory=FakeSMTP)
        return svc
    return make


def area(**extra):
    return {"email": ME, "consent": True, "zone_id": ZONE_A, "severity": "feb-2025-like", **extra}


# ---- the address --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["a@b.co", "first.last+tag@sub.example.com", "x_y@my-site.org"])
def test_plain_addresses_are_accepted(value):
    assert mailer_mod.clean_address(f"  {value} ") == value


@pytest.mark.parametrize("value", [
    "", "no-at-sign", "a@b", "a@b.c", "a@@b.com", "two@a.com,b@c.com", "a@b.com\nBcc: x@y.com", "a b@c.com",
    '"quoted"@c.com', "<a@b.com>", "a@b.com;c@d.com", "x" * 250 + "@b.com", None, 5,
])
def test_anything_that_could_add_a_header_or_a_second_recipient_is_refused(value):
    with pytest.raises(ValueError):
        mailer_mod.clean_address(value)


def test_the_masked_address_hides_most_of_it():
    assert mailer_mod.mask_address(ME) == "f***@gmail.com"


# ---- sending ------------------------------------------------------------------------------------------------------
def test_an_area_email_is_sent_and_shown(build):
    out = build().handle("alerts/email", area())
    assert out["status"] == "sent" and out["kind"] == "area" and out["to"] == "f***@gmail.com"
    assert len(FakeSMTP.outbox) == 1
    msg = FakeSMTP.outbox[0]
    assert msg["To"] == ME and "Outage Concierge (prototype)" in msg["From"] and "sender@example.org" in msg["From"]
    assert msg["Subject"] == out["email"]["subject"] and "Area 1" in msg["Subject"]
    sent_text = msg.get_content()
    assert sent_text.strip() == out["email"]["body"].strip()                    # what the page shows is what was sent
    for needle in ("Your area: Area 1", "Outage risk:", "restoration could take", "Get ready:", "call 911",
                   "not saved", "not confirmed utility information"):
        assert needle in sent_text
    assert ("starttls", True) in FakeSMTP.calls and ("login", "sender@example.org") in FakeSMTP.calls


def test_the_address_is_never_in_the_reply_or_the_logs(build, caplog):
    caplog.set_level(logging.DEBUG)
    svc = build()
    FakeSMTP.fail = smtplib.SMTPRecipientsRefused({ME: (550, b"no")})
    failed = svc.handle("alerts/email", area())
    FakeSMTP.fail = None
    sent = svc.handle("alerts/email", area())
    for out in (failed, sent):
        assert ME not in json.dumps(out) and "faiz.rabbani" not in json.dumps(out)
    assert "faiz.rabbani" not in caplog.text


def test_port_465_uses_ssl_without_starttls(build):
    assert build(port=465).handle("alerts/email", area())["status"] == "sent"
    assert ("connect", "smtp.example.org", 465, mailer_mod.TIMEOUT_SECONDS, True) in FakeSMTP.calls
    assert not any(c[0] == "starttls" for c in FakeSMTP.calls)


def test_a_forecast_only_email_says_prediction_only(build):
    out = build(wx=FakeOpenMeteo(gust=62.0)).handle("alerts/email", {"email": ME, "consent": True, "query": "Columbus, OH"})
    assert out["status"] == "sent" and out["kind"] == "outlook"
    body = FakeSMTP.outbox[0].get_content()
    assert "PREDICTION ONLY" in body and "Columbus, Ohio" in body and "Not available for Columbus, Ohio" in body
    assert "outage risk rating" in body.lower() and "Open-Meteo" in body
    assert "prediction only" in out["email"]["subject"]


# ---- when it does not send ----------------------------------------------------------------------------------------
def test_without_smtp_settings_it_shows_the_email_and_says_nothing_was_sent(build):
    out = build(mailer="off").handle("alerts/email", area())
    assert out["status"] == "not_configured" and "nothing was sent" in out["message"]
    assert out["email"]["subject"] and out["email"]["body"] and FakeSMTP.outbox == []


def test_a_mail_server_failure_is_reported_not_hidden(build):
    FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, b"bad login")
    svc = build()
    out = svc.handle("alerts/email", area())
    assert out["status"] == "failed" and "couldn't send" in out["message"] and out["email"]["body"]
    FakeSMTP.fail = None
    for _ in range(mailer_mod.PER_RECIPIENT_LIMIT):                           # a failure did not use up the allowance
        assert svc.handle("alerts/email", area())["status"] == "sent"


def test_each_address_is_limited_per_hour(build):
    t = {"now": 0.0}
    svc = build(limiter=RateLimit(per_recipient=3, overall=100, window=3600, clock=lambda: t["now"]))
    assert [svc.handle("alerts/email", area())["status"] for _ in range(3)] == ["sent"] * 3
    blocked = svc.handle("alerts/email", area())
    assert blocked["status"] == "rate_limited" and "3 emails" in blocked["message"] and len(FakeSMTP.outbox) == 3
    assert svc.handle("alerts/email", area(email="someone.else@example.org"))["status"] == "sent"
    assert svc.handle("alerts/email", area(email=ME.upper()))["status"] == "rate_limited"       # case does not matter
    t["now"] = 3601.0
    assert svc.handle("alerts/email", area())["status"] == "sent"


def test_the_whole_server_is_limited_too(build):
    svc = build(limiter=RateLimit(per_recipient=5, overall=2, window=3600, clock=lambda: 0.0))
    assert svc.handle("alerts/email", area(email="a1@example.org"))["status"] == "sent"
    assert svc.handle("alerts/email", area(email="a2@example.org"))["status"] == "sent"
    assert svc.handle("alerts/email", area(email="a3@example.org"))["status"] == "rate_limited"


# ---- bad requests -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body", [
    {"consent": True, "zone_id": ZONE_A},                                           # no address
    {"email": "nope", "consent": True, "zone_id": ZONE_A},                          # bad address
    {"email": ME, "zone_id": ZONE_A},                                               # no consent
    {"email": ME, "consent": "yes", "zone_id": ZONE_A},                             # consent must be exactly true
    {"email": ME, "consent": True},                                                 # nothing to send
    {"email": ME, "consent": True, "zone_id": ZONE_A, "query": "Columbus"},         # both
    {"email": ME, "consent": True, "zone_id": "z_nope"},                            # unknown area
    {"email": ME, "consent": True, "query": "12 Main Street"},                      # street address
    {"email": ME, "consent": True, "zone_id": ZONE_A, "subject": "free text"},      # no caller-written text
    {"email": ME, "consent": True, "zone_id": ZONE_A, "body": "free text"},
])
def test_bad_requests_send_nothing(build, body):
    with pytest.raises(BadRequest):
        build().handle("alerts/email", body)
    assert FakeSMTP.outbox == []


def test_an_unknown_place_sends_nothing(build):
    svc = build()
    svc.geocoder = Geocoder(fetch=FakeGeocoder(results=[]))
    with pytest.raises(BadRequest):
        svc.handle("alerts/email", {"email": ME, "consent": True, "query": "Nowhereville"})
    assert FakeSMTP.outbox == []


# ---- settings -----------------------------------------------------------------------------------------------------
def test_settings_come_from_the_environment():
    assert mailer_mod.from_env({}) is None
    assert mailer_mod.from_env({"SMTP_HOST": "h", "SMTP_USER": "u"}) is None                 # no password
    m = mailer_mod.from_env({"SMTP_HOST": "h", "SMTP_USER": "me@example.org", "SMTP_PASSWORD": "p"})
    assert (m.host, m.port, m.from_address) == ("h", 587, "me@example.org")
    m = mailer_mod.from_env({"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_PORT": "465",
                             "SMTP_FROM": "alerts@example.org"})
    assert (m.port, m.from_address) == (465, "alerts@example.org")
    assert mailer_mod.from_env({"SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_PORT": "x"}) is None


def test_the_local_settings_file_is_read_without_overriding_the_shell(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("dev_api", Path(__file__).parents[3] / "scripts" / "dev_api.py")
    dev_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev_api)
    f = tmp_path / ".env.local"
    f.write_text("# comment\nSMTP_HOST=smtp.example.org\nexport SMTP_USER='me@example.org'\nSMTP_PASSWORD=\"secret\"\nSMTP_PORT=2525\n\nbad line\n")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setenv("SMTP_PORT", "587")                                         # already set: must win
    names = dev_api.load_env_file(f)
    assert set(names) == {"SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"}
    import os
    assert os.environ["SMTP_USER"] == "me@example.org" and os.environ["SMTP_PASSWORD"] == "secret"
    assert os.environ["SMTP_PORT"] == "587"
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert dev_api.load_env_file(tmp_path / "missing") == []
