"""Seeded Monte Carlo over labor-hour and crew-size uncertainty (spec-03 design D5).

Each trial draws, from two streams that depend only on the seed and the trial number:

stream A (damage and labor), in this fixed order
    1. shared labor factor L           lognormal, mean 1, sigma by confidence
    2. one uniform for the job count   Poisson inversion (Simulated mode; unused otherwise)
    3. two uniforms for the on-path rule: which drawn repair moves onto the customer's path (or, if no repair was
       drawn, the asset type and factor of one added repair); used only if no repair is on the path
    4. for each job k = 1, 2, ...      one uniform that picks the asset type and, from its position inside that type's
                                       slice, the placement; one uniform for the per-job factor
       (the per-job factor is a lookup in a table of lognormal(mean 1, sigma 0.25) quantiles)
stream B (crews)
    one uniform per declared crew; crew i is present when its uniform is below CREW_PRESENT_PROBABILITY

The seed depends on the premise only (see derive_seed). A longer job list only appends to stream A, and more crews
only append to stream B, so a scenario that changes the crew count, the access level or the amount of damage reuses
the same draws for everything it did not change (common random numbers). That is what makes "one more crew never
raises the P50" hold trial by trial.

Percentiles are nearest-rank on the sorted trial results. Every sigma, probability and trial value is an assumption
from `config.py`, not a fitted number.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

from . import config
from .jobs import Plan
from .schedule import schedule_trial

_MASK = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
_STREAM_B = 0x5851F42D4C957F2D
_NORMAL = NormalDist()


# ---- seed and trial count ------------------------------------------------------------------------------------------
def derive_seed(*, engine_version: str, token: str) -> int:
    """First 8 bytes of SHA-256 over the engine version and the premise token.

    Design D5 also hashed the scenario inputs (crews, access, damage). Leaving them out is deliberate: the same premise
    then reuses the same draws when a slider moves, so the range moves smoothly and in the expected direction (more
    crews never raise the P50, restricted access never lowers it) instead of jumping with fresh random noise. The
    output is still a pure function of the inputs, so the same request always gives the same answer.
    """
    material = json.dumps([engine_version, token], separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


def trial_count(expected_jobs: float) -> int:
    """min(1000, max(200, budget // jobs)): fewer trials when each trial is expensive."""
    jobs = max(1, round(expected_jobs))
    return min(config.TRIALS_MAX, max(config.TRIALS_MIN, config.TRIAL_BUDGET // jobs))


def sensitivity_trial_count(expected_jobs: float, trials: int) -> int:
    """Trials for the what-if runs that redraw the jobs. Never more than the base run has."""
    jobs = max(1, round(expected_jobs))
    n = min(config.SENSITIVITY_TRIALS, max(config.SENSITIVITY_MIN_TRIALS, config.SENSITIVITY_BUDGET // jobs))
    return min(n, trials)


# ---- draws ------------------------------------------------------------------------------------------------------
def poisson_from_uniform(mean: float, u: float) -> int:
    """Poisson quantile for one uniform. Exact inversion for small means, normal approximation above.

    Monotone in both arguments, so a higher mean never gives fewer jobs on the same draw.
    """
    if mean <= 0.0:
        return 0
    if mean <= config.POISSON_EXACT_MAX:
        p = cum = math.exp(-mean)
        k = 0
        while u > cum and k < 1000:
            k += 1
            p *= mean / k
            cum += p
        return k
    z = _NORMAL.inv_cdf(min(max(u, 1e-12), 1.0 - 1e-12))
    return max(0, int(mean + z * math.sqrt(mean) + 0.5))


def nearest_rank(sorted_values: Sequence[float], pct: int) -> float:
    """Nearest-rank percentile: the value at rank ceil(pct/100 x n) of the sorted list."""
    n = len(sorted_values)
    rank = -(-pct * n // 100)
    return sorted_values[max(1, rank) - 1]


def whole_hours(x: float) -> int:
    """Round half up to whole hours, at least 1."""
    return max(1, int(math.floor(x + 0.5)))


# ---- one scenario ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TrialResults:
    """Clock hours per trial, in trial order: when the main-line, branch and all path repairs were done."""
    feeder: list[float]
    lateral: list[float]
    total: list[float]

    def __len__(self) -> int:
        return len(self.total)

    def head(self, n: int) -> "TrialResults":
        return TrialResults(self.feeder[:n], self.lateral[:n], self.total[:n])


_FACTOR_BINS = 4096
_factor_table: list[float] | None = None


def job_factor_table() -> list[float]:
    """Per-job labor factors: the lognormal(mean 1, sigma JOB_SIGMA) quantiles at 4,096 evenly spaced probabilities.

    One table lookup replaces a normal draw and an exp per job. The table is rescaled so its mean is exactly 1.
    """
    global _factor_table
    if _factor_table is None:
        raw = [math.exp(config.JOB_SIGMA * _NORMAL.inv_cdf((i + 0.5) / _FACTOR_BINS)) for i in range(_FACTOR_BINS)]
        mean = sum(raw) / _FACTOR_BINS
        _factor_table = [x / mean for x in raw]
    return _factor_table


def draw_jobs(plan: Plan, rng: random.Random, sigma_shared: float) -> tuple[list[int], list[int], list[float]]:
    """One trial's job list from stream A: (node of each job, asset index of each job, hours of each job).

    Hours already include the shared labor factor and the per-job factor.
    """
    ra = rng.random
    source, prices = plan.source, plan.prices
    table = job_factor_table()

    shared = math.exp(sigma_shared * rng.gauss(0.0, 1.0) - 0.5 * sigma_shared * sigma_shared)
    u_count = ra()
    u_cause, u_cause_factor = ra(), ra()

    if source == "path":
        n = len(plan.fixed_assets)
    elif source == "count":
        n = plan.count
    else:
        n = min(config.SIMULATED_JOBS_CAP, poisson_from_uniform(plan.mean_jobs, u_count))
    path_set = plan.path.node_set
    nodes: list[int] = []
    assets: list[int] = []
    hours: list[float] = []
    on_path = False
    if source == "path":
        fixed_assets, fixed_nodes = plan.fixed_assets, plan.fixed_nodes
        for k in range(n):
            ra()                                    # keeps the stream aligned with the other modes
            a, v = fixed_assets[k], fixed_nodes[k]
            nodes.append(v)
            assets.append(a)
            hours.append(prices[a] * shared * table[int(ra() * _FACTOR_BINS)])
        on_path = True
    else:
        cum, level_nodes, last = plan.mix_cum, plan.level_nodes, len(plan.mix_cum) - 1
        for _ in range(n):
            u = ra()
            a = bisect_right(cum, u)
            if a > last:
                a = last
            lo = cum[a - 1] if a else 0.0
            choices = level_nodes[a]
            k = len(choices)
            i = int((u - lo) / (cum[a] - lo) * k)       # the position inside the asset's slice is a fresh uniform
            v = choices[i if i < k else k - 1]
            nodes.append(v)
            assets.append(a)
            hours.append(prices[a] * shared * table[int(ra() * _FACTOR_BINS)])
            if v in path_set:
                on_path = True
    if not on_path:
        # The estimate is conditional on losing power, so the trial must have a repair on the customer's own path.
        # A repair at the path node of its own level is the cause. The job count never changes for a drawn repair, so a
        # Damage count run keeps exactly `count` jobs. (Design D2 added a service-level job instead; that made the
        # P50 fall as damage grew for some premises, see design.md.)
        if n > 0:
            j = min(int(u_cause * n), n - 1)
            nodes[j] = plan.path_nodes[assets[j]]
        else:
            a = min(bisect_right(plan.mix_cum, u_cause), len(plan.mix_cum) - 1)      # no repair drawn: add one
            nodes.append(plan.path_nodes[a])
            assets.append(a)
            hours.append(prices[a] * shared * table[int(u_cause_factor * _FACTOR_BINS)])
    return nodes, assets, hours


def crews_at_work_multi(rng: random.Random, wanted: Sequence[int]) -> dict[int, int]:
    """Stream B for several crew counts at once: crews at work (at least one) for each count in `wanted`.

    One uniform per declared crew, so the count for c + 1 crews is never below the count for c on the same stream.
    """
    rb, p = rng.random, config.CREW_PRESENT_PROBABILITY
    present = drawn = 0
    out: dict[int, int] = {}
    for c in sorted(set(wanted)):
        while drawn < c:
            if rb() < p:
                present += 1
            drawn += 1
        out[c] = present if present > 0 else 1
    return out


def crews_at_work(rng: random.Random, crews: int) -> int:
    return crews_at_work_multi(rng, [crews])[crews]


def trial_streams(seed: int, t: int, rng_a: random.Random, rng_b: random.Random) -> None:
    """Point both streams at trial t. The streams depend only on the seed and the trial number."""
    s = (seed + (t + 1) * _GOLDEN) & _MASK
    rng_a.seed(s)
    rng_b.seed(s ^ _STREAM_B)


def run_trials_multi(plan: Plan, *, crews: Sequence[int], seed: int, trials: int,
                     sigma_shared: float) -> dict[int, TrialResults]:
    """Simulate `trials` outages once and score them for several crew counts (same jobs, same crew draws).

    Returns clock hours per trial for each crew count in `crews`.
    """
    clock = config.CLOCK_FACTOR
    wanted = sorted(set(crews))
    rng_a, rng_b = random.Random(), random.Random()
    out = {c: ([], [], []) for c in wanted}
    circuit, path = plan.circuit, plan.path
    for t in range(trials):
        trial_streams(seed, t, rng_a, rng_b)
        nodes, _, hours = draw_jobs(plan, rng_a, sigma_shared)
        tf, tl, ts = schedule_trial(circuit, path, nodes, hours)
        for c, present in crews_at_work_multi(rng_b, wanted).items():
            scale = clock / present
            f, l, tot = out[c]
            f.append(tf * scale)
            l.append(tl * scale)
            tot.append(ts * scale)
    return {c: TrialResults(*v) for c, v in out.items()}


def run_trials(plan: Plan, *, crews: int, seed: int, trials: int, sigma_shared: float) -> TrialResults:
    """Simulate `trials` outages for one customer and return clock hours per trial."""
    return run_trials_multi(plan, crews=[crews], seed=seed, trials=trials, sigma_shared=sigma_shared)[crews]


# ---- summaries -------------------------------------------------------------------------------------------------------
def percentiles(values: Sequence[float]) -> tuple[float, float]:
    """(P50, P90) of the trial results, unrounded."""
    s = sorted(values)
    lo, hi = config.PERCENTILES
    return nearest_rank(s, lo), nearest_rank(s, hi)


def summarize(results: TrialResults) -> dict:
    """Rounded P50 and P90 for the customer plus the median milestone of each stage."""
    p50, p90 = percentiles(results.total)
    return {"trials": len(results), "p50_raw": p50, "p90_raw": p90,
            "p50_hours": whole_hours(p50), "p90_hours": max(whole_hours(p50), whole_hours(p90)),
            "feeder_median": nearest_rank(sorted(results.feeder), 50),
            "lateral_median": nearest_rank(sorted(results.lateral), 50)}


def paired_p50(base: TrialResults, variant: TrialResults, base_p50_raw: float) -> int:
    """P50 of a what-if run, as the base P50 plus the change seen on the trials both runs share.

    Both runs use the same draws, so the change in P50 is measured on matched trials. That keeps the what-if
    consistent with the headline number: one more crew can only lower it, one fewer can only raise it.
    """
    n = min(len(base), len(variant))
    b50, _ = percentiles(base.total[:n])
    v50, _ = percentiles(variant.total[:n])
    return whole_hours(base_p50_raw + (v50 - b50))
