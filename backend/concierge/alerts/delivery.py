"""Mock delivery. Nothing here talks to a network: no SMS, voice, push or email client is imported or called.

A "send" writes an outbox item with status `not_sent_demo`. The real utility would swap in a different Delivery
implementation; this prototype never does.
"""
from __future__ import annotations

from typing import Protocol

from .audit import new_id
from .stores import AlertStore, Clock, iso, utc_now

STATUS_NOT_SENT = "not_sent_demo"


class Delivery(Protocol):
    def deliver(self, token: str, message_type: str, channel: str, text: str) -> dict: ...


class MockDelivery:
    def __init__(self, store: AlertStore, clock: Clock = utc_now):
        self.store, self.clock = store, clock

    def deliver(self, token: str, message_type: str, channel: str, text: str) -> dict:
        item = {"id": new_id("o"), "ts": iso(self.clock()), "message_type": message_type, "channel": channel,
                "text": text, "status": STATUS_NOT_SENT}
        self.store.add_outbox(token, item)
        return item
