"""Identifier scan and the public view of a finished report (design D7, D8).

Standard library only, so the Lambda can import it without the evaluation code.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .config import LIMITS, NOTE, PLOT_FILES

PLOT_PREFIX = "serving/backtest"

# Anything shaped like a circuit label, a long number, a device key, a premise token, or an H3 cell.
_PATTERNS = (
    re.compile(r"CIR\d+", re.I), re.compile(r"SUBSTATION|STATION", re.I), re.compile(r"\d{6,}"),
    re.compile(r"\bd_[0-9a-f]{12}\b"), re.compile(r"\bp_[0-9a-f]{16}\b"), re.compile(r"\b[0-9a-f]{15}\b"),
)
_HASH = re.compile(r"(?:sha256:)?[0-9a-f]{40,64}")
_HASH_KEYS = frozenset({"config_hash", "git_hash", "spec02_config_hash", "inputs"})


class PrivacyError(RuntimeError):
    """An output contained something shaped like an identifier."""


def find_identifiers(text: str) -> list[str]:
    """Matches in free text. Hex digests are removed first, because they can contain long digit runs by chance."""
    cleaned = _HASH.sub("", text)
    return [m.group() for p in _PATTERNS for m in p.finditer(cleaned)]


def scan_value(value: Any, path: str = "report") -> None:
    """Scan every string in a JSON-like value (numbers are not text). Raises PrivacyError on a finding."""
    if isinstance(value, str):
        hits = find_identifiers(value)
        if hits:
            raise PrivacyError(f"{path}: looks like an identifier ({hits[0]!r})")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if k in _HASH_KEYS:
                continue
            scan_value(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            scan_value(v, f"{path}[{i}]")


def scan_text(text: str, name: str) -> None:
    hits = find_identifiers(text)
    if hits:
        raise PrivacyError(f"{name}: looks like an identifier ({hits[0]!r})")


def _band_view(b: Mapping) -> dict:
    if b.get("suppressed") or b.get("hit_rate") is None:
        return {"hidden": True}
    return {"n_circuits": b["n_circuits"], "hit_rate": b["hit_rate"], "ci95": b["ci95"], "lift": b["lift"],
            "difference": b["difference"]}


def evidence_view(report: Mapping, plot_prefix: str = PLOT_PREFIX) -> dict:
    """What GET /evidence returns: headline numbers, verdicts, plot locations, and the fixed limits list."""
    risk = report["risk"]["primary"]
    rest = report.get("restoration") or {}
    cut = next(iter((rest.get("cutoffs") or {}).values()), None)
    forecast = ((cut or {}).get("runs") or {}).get("forecast")
    view = {
        "status": "ok",
        "note": NOTE,
        "label": "Results for two past storms: calibrated on Sep 2024 and tested on Feb 2025, at circuit level.",
        "sample_run": bool(report.get("sample")),
        "risk": {
            "base_rate": risk["base_rate"],
            "bands": {name: _band_view(b) for name, b in risk["bands"].items()},
            "permutation_p": risk["permutation"]["p_value"],
            "auc": risk["auc"]["value"], "auc_ci95": risk["auc"]["ci95"],
        },
        "restoration": {
            "status": rest.get("status", "not_run"),
            "crews_used": (cut or {}).get("crews_used"),
            "calibration": (cut or {}).get("calibration"),
            "coverage_p50": forecast["coverage_p50"] if forecast else None,
            "coverage_p90": forecast["coverage_p90"] if forecast else None,
            "resolution_days": rest.get("resolution_days", 1),
        },
        "verdicts": [{"code": v["code"], "level": v["level"], "sentence": v["sentence"]} for v in report["verdicts"]],
        "plots": [{"name": f, "path": f"{plot_prefix}/{f}"} for f in PLOT_FILES],
        "limits": list(LIMITS),
    }
    scan_value(view, "evidence")
    return view


def unavailable_view() -> dict:
    return {"status": "unavailable", "message": "No backtest report has been published yet.", "note": NOTE,
            "limits": list(LIMITS)}
