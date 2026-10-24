"""Salted opaque keys for devices (premise tokens come from concierge.risk.tokens)."""
from __future__ import annotations

import hashlib
import hmac


def device_key(salt: bytes, device_class: str, device_id: str) -> str:
    digest = hmac.new(salt, f"device:{device_class}*{device_id}".encode(), hashlib.sha256).hexdigest()
    return "d_" + digest[:12]
