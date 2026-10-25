"""Result schema with a hard output whitelist and identifier scan (design D9, D10)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import ENGINE_VERSION

_SCALAR = (str, int, float, bool, type(None))

# None = leaf (scalar or list of scalars); dict = nested object; [dict] = list of objects.
RESULT_SCHEMA: dict[str, Any] = {
    "engine_version": None,
    "config_hash": None,
    "premise_token": None,
    "status": None,
    "label": None,
    "severity": {"preset": None, "multiplier": None, "extrapolated": None},
    "band": None,
    "score": None,
    "confidence": {"level": None, "records": None, "reason": None},
    "band_evidence": {
        "backtest": None, "hit_rate": None, "ci95": None, "n_circuits": None,
        "hit_definition": None, "sentence": None,
    },
    "top_factors": [{"id": None, "direction": None, "weight": None, "text": None}],
    "signals": {"circuit_reports_per_1k": None, "protective_devices": None},
    "assumptions": None,
    "missing_data": None,
    "could_change": None,
    "fallback": {"reason_code": None, "message": None},
}

# Free-text values must never carry identifiers. Token and hash are exempt (they are opaque by design); `prompt_hash` is the hash of a
# prompt file, and `cache.key` is the assistant's cache key (a SHA-256 of the wording fields).
_EXEMPT_KEYS = {"premise_token", "config_hash", "token", "rules_table_hash", "prompt_hash"}
_IDENTIFIER = re.compile(r"CIR\d+|STATION|SUBSTATION|\d{6,}", re.IGNORECASE)


class WhitelistError(ValueError):
    """Output contained a key or value that is not allowed to leave the engine."""


def _check(value: Any, schema: Any, path: str) -> None:
    if value is None:
        return
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise WhitelistError(f"{path}: expected object")
        extra = set(value) - set(schema)
        if extra:
            raise WhitelistError(f"{path}: keys not allowed: {sorted(extra)}")
        for k, v in value.items():
            _check(v, schema[k], f"{path}.{k}")
    elif isinstance(schema, list):
        if not isinstance(value, list):
            raise WhitelistError(f"{path}: expected list")
        for i, item in enumerate(value):
            _check(item, schema[0], f"{path}[{i}]")
    else:
        if isinstance(value, _SCALAR):
            return
        if isinstance(value, list) and all(isinstance(v, _SCALAR) for v in value):
            return
        raise WhitelistError(f"{path}: leaf must be a scalar or list of scalars")


def _scan_strings(value: Any, path: str, exempt: bool = False) -> None:
    if isinstance(value, str):
        if not exempt and _IDENTIFIER.search(value):
            raise WhitelistError(f"{path}: text looks like it contains an identifier")
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_strings(v, f"{path}.{k}", exempt or k in _EXEMPT_KEYS or (k == "key" and path.endswith(".cache")))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_strings(v, f"{path}[{i}]", exempt)


def validate_output(d: dict) -> dict:
    """Raise WhitelistError on any key outside RESULT_SCHEMA or any identifier-like text."""
    _check(d, RESULT_SCHEMA, "result")
    _scan_strings(d, "result")
    return d


@dataclass(frozen=True)
class RiskResult:
    config_hash: str
    status: str                                   # "ok" | "insufficient_data"
    premise_token: str | None = None
    label: str | None = None
    severity: dict | None = None
    band: str | None = None
    score: float | None = None
    confidence: dict | None = None
    band_evidence: dict | None = None
    top_factors: list = field(default_factory=list)
    signals: dict | None = None
    assumptions: list = field(default_factory=list)
    missing_data: list = field(default_factory=list)
    could_change: list = field(default_factory=list)
    fallback: dict | None = None
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> dict:
        d = {
            "engine_version": self.engine_version,
            "config_hash": self.config_hash,
            "premise_token": self.premise_token,
            "status": self.status,
            "label": self.label,
            "severity": self.severity,
            "band": self.band,
            "score": self.score,
            "confidence": self.confidence,
            "band_evidence": self.band_evidence,
            "top_factors": list(self.top_factors),
            "signals": self.signals,
            "assumptions": list(self.assumptions),
            "missing_data": list(self.missing_data),
            "could_change": list(self.could_change),
            "fallback": self.fallback,
        }
        return validate_output(d)
