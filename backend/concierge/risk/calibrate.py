"""Band thresholds, Wilson intervals and the out-of-sample backtest (design D3, D4, D6)."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .config import BANDS, DEFAULT_CONFIG, RiskConfig

EPS = 1e-12


class BacktestError(RuntimeError):
    """Band hit rates are not ordered Low <= Medium <= High."""


def weighted_percentile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    """Smallest value whose cumulative weight share reaches q."""
    if not values:
        raise ValueError("no values")
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum / total >= q - EPS:
            return v
    return pairs[-1][0]


def compute_thresholds(scores: Sequence[float], cfg: RiskConfig = DEFAULT_CONFIG) -> dict[str, float]:
    """Fixed band cut-offs from baseline (m = 1) premise scores. Each premise has weight 1."""
    ones = [1.0] * len(scores)
    return {
        "medium": weighted_percentile(scores, ones, cfg.p_medium),
        "high": weighted_percentile(scores, ones, cfg.p_high),
    }


def band_for(score: float, thresholds: Mapping[str, float]) -> str:
    """Thresholds are inclusive upper edges: Low <= t_medium < Medium <= t_high < High."""
    if score <= thresholds["medium"]:
        return "Low"
    if score <= thresholds["high"]:
        return "Medium"
    return "High"


def wilson_interval(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion p observed over n units."""
    if n <= 0:
        return (0.0, 1.0)
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = (z / den) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def severity_presets(storm_records: Sequence[int], cfg: RiskConfig = DEFAULT_CONFIG) -> dict[str, float]:
    """Multipliers anchored to observed storm sizes: m = storm records / mean storm records."""
    mean = sum(storm_records) / len(storm_records)
    first, last = storm_records[0], storm_records[-1]
    return {
        "sep-2024-like": first / mean,
        "typical": 1.0,
        "feb-2025-like": last / mean,
        "beyond-observed": cfg.beyond_observed_multiplier,
    }


def backtest(
    circuit_sizes: Mapping[int, int],
    cal_counts: Mapping[int, int],
    eval_counts: Mapping[int, int],
    *,
    tau: float | None = None,
    cfg: RiskConfig = DEFAULT_CONFIG,
) -> dict:
    """Calibrate on one storm's counts and evaluate band hit rates on another storm.

    A circuit is a hit when it has at least `tau` reports per 1,000 premises in the evaluation storm.
    Band hit rate is premise-weighted; the Wilson interval uses the number of circuits as n, because premises on
    one circuit share an outcome.
    """
    tau = cfg.hit_tau_per_1k if tau is None else tau
    sizes = {c: n for c, n in circuit_sizes.items() if n >= cfg.min_circuit_premises}
    circuits = sorted(sizes)
    rates = {c: 1000.0 * cal_counts.get(c, 0) / sizes[c] for c in circuits}
    weights = [sizes[c] for c in circuits]
    thresholds = {
        "medium": weighted_percentile([rates[c] for c in circuits], weights, cfg.p_medium),
        "high": weighted_percentile([rates[c] for c in circuits], weights, cfg.p_high),
    }

    stats = {b: {"circuits": 0, "premises": 0, "hit_circuits": 0, "hit_premises": 0, "eval_reports": 0}
             for b in BANDS}
    for c in circuits:
        s = stats[band_for(rates[c], thresholds)]
        n, d = sizes[c], eval_counts.get(c, 0)
        s["circuits"] += 1
        s["premises"] += n
        s["eval_reports"] += d
        if 1000 * d >= tau * n:
            s["hit_circuits"] += 1
            s["hit_premises"] += n

    bands = {}
    for b, s in stats.items():
        if s["circuits"] == 0:
            bands[b] = {"n_circuits": 0, "premises": 0, "hit_rate": None, "ci95": None,
                        "hit_circuits": 0, "eval_reports_per_1k": None}
            continue
        p = s["hit_premises"] / s["premises"]
        lo, hi = wilson_interval(p, s["circuits"], cfg.wilson_z)
        bands[b] = {
            "n_circuits": s["circuits"],
            "premises": s["premises"],
            "hit_rate": p,
            "ci95": [lo, hi],
            "hit_circuits": s["hit_circuits"],
            "eval_reports_per_1k": 1000.0 * s["eval_reports"] / s["premises"],
        }
    return {"tau_per_1k": tau, "thresholds": thresholds, "n_circuits": len(circuits), "bands": bands}


def is_monotone(report: Mapping) -> bool:
    rates = [report["bands"][b]["hit_rate"] for b in BANDS]
    if any(r is None for r in rates):
        return False
    return rates[0] <= rates[1] + EPS and rates[1] <= rates[2] + EPS


def check_monotone(report: Mapping, *, allow_non_monotone: bool = False) -> dict:
    """Gate (D6 step 7). Returns {"monotone": bool, "override": bool}; raises when not monotone and no override."""
    ok = is_monotone(report)
    if not ok and not allow_non_monotone:
        got = {b: report["bands"][b]["hit_rate"] for b in BANDS}
        raise BacktestError(f"band hit rates are not ordered Low <= Medium <= High: {got}")
    return {"monotone": ok, "override": (not ok) and allow_non_monotone}
