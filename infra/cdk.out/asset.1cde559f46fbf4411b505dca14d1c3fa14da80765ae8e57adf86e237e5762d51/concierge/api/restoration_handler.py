"""Lambda for POST /restoration.

Placeholder until the restoration engine exists (labor hours x damage, indirect multiplier, crews, step restoration).
The route, Lambda, IAM and throttling are deployed now so the engine can be added without touching the infrastructure.
It reads nothing from the request and returns no numbers.
"""
from __future__ import annotations

from .http import error


def handler(event: dict, context=None) -> dict:
    return error(501, "not_implemented", "Restoration estimates are not available yet.")
