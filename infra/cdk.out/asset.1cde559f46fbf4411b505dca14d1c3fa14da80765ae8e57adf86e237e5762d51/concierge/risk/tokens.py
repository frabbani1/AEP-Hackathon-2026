"""Opaque premise tokens. The raw premise number never leaves the build step."""
from __future__ import annotations

import hashlib
import hmac
import os
import re

TOKEN_RE = re.compile(r"^p_[0-9a-f]{16}$")
SALT_ENV = "CONCIERGE_TOKEN_SALT"


def get_salt(explicit: str | bytes | None = None) -> bytes:
    """Salt comes from the environment (never committed). Refuses to run without one."""
    salt = explicit if explicit is not None else os.environ.get(SALT_ENV)
    if not salt:
        raise RuntimeError(f"set {SALT_ENV} to a secret random string before building artifacts")
    return salt.encode() if isinstance(salt, str) else salt


def make_token(salt: bytes, premise_id: str) -> str:
    digest = hmac.new(salt, premise_id.encode(), hashlib.sha256).hexdigest()
    return "p_" + digest[:16]
