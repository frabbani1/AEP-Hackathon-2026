"""Keyed tokenization of identifiers.

Tokens are HMAC-SHA256 values truncated to 128 bits and written in hexadecimal.
The key comes only from the environment. It is never written to disk, logged, or
placed in a manifest. Only a short fingerprint derived from the key is recorded.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

from .errors import ConfigError

KEY_ENV_VAR = "AEP_TOKEN_KEY"
MIN_KEY_LENGTH = 16
TOKEN_BYTES = 16
_FINGERPRINT_MESSAGE = b"aep-pipeline-key-fingerprint-v1"


def load_key(environ: Mapping[str, str]) -> bytes:
    """Read the tokenization key from the environment or raise ConfigError."""
    value = environ.get(KEY_ENV_VAR, "")
    if not value:
        raise ConfigError(
            f"{KEY_ENV_VAR} is required. Set it to a secret of at least "
            f"{MIN_KEY_LENGTH} characters before running the pipeline."
        )
    if len(value) < MIN_KEY_LENGTH:
        raise ConfigError(
            f"{KEY_ENV_VAR} is too short. Use at least {MIN_KEY_LENGTH} characters."
        )
    return value.encode("utf-8")


class Tokenizer:
    """Derives stable, keyed, non-reversible tokens for identifiers."""

    def __init__(self, key: bytes) -> None:
        if not key:
            raise ConfigError("A tokenization key is required.")
        self._key = key

    def token(self, domain: str, value: object) -> str:
        """Token for ``value`` within a named domain.

        The domain keeps identifiers of different kinds apart, so a premise number
        and a device id with the same text never collide.
        """
        message = f"{domain}\x1f{value}".encode("utf-8")
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return digest[:TOKEN_BYTES].hex()

    def fingerprint(self) -> str:
        """Short value that identifies which key built an artifact, without revealing it."""
        digest = hmac.new(self._key, _FINGERPRINT_MESSAGE, hashlib.sha256).hexdigest()
        return digest[:12]

    def __repr__(self) -> str:  # never expose the key
        return "Tokenizer(key=<hidden>)"
