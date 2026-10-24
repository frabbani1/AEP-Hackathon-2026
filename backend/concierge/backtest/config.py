"""Every tunable constant for the backtest (design D1 to D6). None of these is fitted to the scores."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

BACKTEST_VERSION = "backtest-v1"

# Fixed wording, shown in docs/backtest.md and returned by GET /evidence. Exactly six items (spec: "limits list").
LIMITS = (
    "Two storms only: Sep 2024 and Feb 2025. Two points cannot show how the model behaves in other storms.",
    "Sep 2024 is mostly repair backlog: most of its repairs took 20 days or more, so few usable records exist to "
    "calibrate on.",
    "Crew counts were never recorded. Crews are a free setting, not an observation, and cannot be checked against data.",
    "Repair time is not outage length. The data records when a damaged asset was fixed, not when customers got power.",
    "Repair days are whole days, so nothing finer than about one day can be claimed.",
    "Circuit level only. There are no outage labels for individual homes, so nothing here measures accuracy for a "
    "single address.",
)

NOTE = ("These results describe two past storms. They are a check on the model, not a guarantee, and not "
        "confirmed utility information.")

PLOT_FILES = ("risk_bands.svg", "restoration_scatter.svg", "repair_hist.svg")


@dataclass(frozen=True)
class BacktestConfig:
    seed: int = 2026
    sample: bool = False                  # quick run: fewer shuffles and resamples; never published

    # Risk (D2)
    n_permutations: int = 10_000
    n_bootstrap: int = 2_000
    taus: tuple[float, ...] = (1.0, 2.0, 5.0)
    drop_largest: int = 3
    sensitivity_min_size: int = 100
    min_group: int = 10                   # project privacy rule: no group or count under 10 is published

    # Restoration (D3 to D5)
    backlog_primary: int = 3              # repairs taking more than this many days are backlog
    backlog_sensitivity: int = 7
    min_circuit_records: int = 10
    default_crews: int = 3
    k_min: int = 1
    k_max: int = 20
    fit_min_records: int = 30
    fit_min_circuits: int = 8
    fit_min_circuit_records: int = 3
    k_bootstrap: int = 500
    within_days: float = 1.0
    coverage_p90_low: float = 0.80
    coverage_p90_high: float = 0.97
    hist_max_days: int = 10               # histogram shows 0..hist_max_days, then one tail bin

    # Verdicts (D6)
    permutation_alpha: float = 0.05

    def cutoffs(self) -> tuple[int, ...]:
        """Primary cutoff first, then the sensitivity cutoff (skipped when the two are equal)."""
        return tuple(dict.fromkeys((self.backlog_primary, self.backlog_sensitivity)))

    def scaled(self) -> "BacktestConfig":
        """Smaller loops for `--sample`. Same code paths, noisier numbers."""
        from dataclasses import replace
        return replace(self, sample=True, n_permutations=500, n_bootstrap=200, k_bootstrap=50)

    def as_dict(self) -> dict:
        return asdict(self)

    def config_hash(self) -> str:
        blob = json.dumps({"version": BACKTEST_VERSION, **self.as_dict(), "limits": LIMITS}, sort_keys=True)
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


DEFAULT_CONFIG = BacktestConfig()
