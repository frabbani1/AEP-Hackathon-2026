"""Confidence levels and the insufficient-data fallback (design D7)."""
from __future__ import annotations

from .config import DEFAULT_CONFIG, LEVELS, RiskConfig

FALLBACK_MESSAGES: dict[str, str] = {
    "unknown_premise": (
        "We couldn't match that location, so we can't rate your outage risk. "
        "Prepare as if an outage is possible."
    ),
    "no_circuit": (
        "We don't have enough information about the line that serves this location to rate it. "
        "Prepare as if an outage is possible."
    ),
    "circuit_unmapped": (
        "We can't connect your line to our storm history, so we can't rate it. "
        "Prepare as if an outage is possible."
    ),
    "circuit_too_small": (
        "Your line serves too few homes for us to share a rating without risking privacy. "
        "Prepare as if an outage is possible."
    ),
    "no_history_small_circuit": (
        "We don't have enough storm history for your line to rate it. "
        "Prepare as if an outage is possible."
    ),
}


def downgrade(level: str, steps: int = 1) -> str:
    return LEVELS[max(0, LEVELS.index(level) - steps)]


def assess(
    *,
    has_circuit: bool,
    circuit_mapped: bool,
    n_premises: int,
    records: int,
    unknown_service: bool,
    cfg: RiskConfig = DEFAULT_CONFIG,
) -> tuple[str | None, str | None]:
    """Return (fallback_reason, base_confidence). Exactly one of them is None. First matching rule wins."""
    if not has_circuit:
        return "no_circuit", None
    if not circuit_mapped:
        return "circuit_unmapped", None
    if n_premises < cfg.min_circuit_premises:
        return "circuit_too_small", None
    if records == 0 and n_premises < cfg.large_circuit_premises:
        return "no_history_small_circuit", None

    if records >= cfg.records_high:
        level = "High"
    elif records >= cfg.records_medium:
        level = "Medium"
    else:
        level = "Low"  # 1-4 records, or 0 records on a large circuit
    if unknown_service:
        level = downgrade(level)
    return None, level


def reason_text(records: int, n_storms: int) -> str:
    if records == 0:
        return f"No damage reports on your line in the {n_storms} past storms we use."
    noun = "report" if records == 1 else "reports"
    return f"{records} damage {noun} on your line in the {n_storms} past storms we use."
