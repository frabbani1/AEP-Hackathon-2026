"""Output whitelist for the restoration section of the fact card (spec-03 design D6, D7).

Same mechanism as the risk engine's `risk/schema.py`: any key outside the contract, or any text that looks like an
identifier, raises before the result leaves the engine. The contract has no node numbers, customer counts per device,
circuit or device identifiers, coordinates or flags.
"""
from __future__ import annotations

from typing import Any

from ..risk.schema import WhitelistError, _check, _scan_strings

# None = leaf (scalar or list of scalars); dict = nested object; [dict] = list of objects.
RESULT_SCHEMA: dict[str, Any] = {
    "engine_version": None,
    "premise_token": None,
    "status": None,
    "label": None,
    "model_note": None,
    "severity": {"preset": None, "multiplier": None, "extrapolated": None},
    "scenario": {"crews": None, "access": None, "damage_source": None, "damage_count": None},
    "estimate": {"p50_hours": None, "p90_hours": None, "low_hours": None, "typical_hours": None,
                 "high_hours": None, "display": None, "meaning": None},
    "steps": [{"stage": None, "label": None, "done_by_hours": None}],
    "sensitivity": [{"change": None, "p50_hours": None}],
    "confidence": {"level": None, "reason": None},
    "assumptions": None,
    "missing_data": None,
    "could_change": None,
    "simulation": {"trials": None, "seed_policy": None, "model_path": None},
    "fallback": {"reason_code": None, "message": None},
}

def validate_output(d: dict) -> dict:
    """Raise WhitelistError on any key outside RESULT_SCHEMA or any identifier-like text."""
    _check(d, RESULT_SCHEMA, "restoration")
    _scan_strings(d, "restoration", False)
    return d


__all__ = ["RESULT_SCHEMA", "WhitelistError", "validate_output"]
