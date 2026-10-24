"""All tunable constants for the risk engine (design D2, D4, D6, D7, D8)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

ENGINE_VERSION = "risk-v1"
LABEL = "circuit-level historical susceptibility, not a per-premise forecast"

LEVELS = ("Low", "Medium", "High")
BANDS = ("Low", "Medium", "High")

# Display rounding. The engine rounds once so downstream number guards can compare exact values.
ROUND_SCORE = 1
ROUND_RATE = 1
ROUND_WEIGHT = 2

PRESET_ORDER = ("sep-2024-like", "typical", "feb-2025-like", "beyond-observed")


@dataclass(frozen=True)
class RiskConfig:
    # Score (D2)
    exposure_a: float = 0.10          # per protective device upstream
    exposure_b: float = 0.05          # per hop to the breaker
    type_overhead: float = 1.0
    type_underground: float = 0.4
    type_unknown: float = 1.0         # treated like overhead (conservative) and flagged
    shrinkage_k: float = 0.0          # empirical-Bayes strength toward the regional rate

    # Storm window detection (D5)
    storm_candidate_min_day: int = 10
    storm_merge_gap_days: int = 2
    storm_extend_min_day: int = 3
    storm_min_total: int = 100
    expected_storms: int = 2

    # Bands (D3)
    p_medium: float = 0.50
    p_high: float = 0.85

    # Backtest (D6)
    hit_tau_per_1k: float = 2.0
    tau_sensitivity: tuple[float, ...] = (1.0, 5.0)
    wilson_z: float = 1.96

    # Confidence / fallback (D7)
    min_circuit_premises: int = 50
    large_circuit_premises: int = 500
    records_high: int = 20
    records_medium: int = 5

    # Severity (D4)
    severity_min: float = 0.25
    severity_max: float = 3.0
    beyond_observed_multiplier: float = 2.5

    # Explanation (D8)
    factor_min_abs: float = 0.05
    hist_eps: float = 0.1             # reports/1k, log floor only
    top_n: int = 3

    # Privacy (D10)
    min_group_premises: int = 10

    def type_factor(self, service: str) -> float:
        return {
            "Overhead": self.type_overhead,
            "Underground": self.type_underground,
        }.get(service, self.type_unknown)

    def exposure(self, protective: float, hops: float) -> float:
        return 1.0 + self.exposure_a * protective + self.exposure_b * hops

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        d = dict(d)
        if "tau_sensitivity" in d:
            d["tau_sensitivity"] = tuple(d["tau_sensitivity"])
        return cls(**d)


DEFAULT_CONFIG = RiskConfig()
