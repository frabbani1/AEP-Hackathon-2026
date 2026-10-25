"""Small, dependency-free statistics helpers. Every random step takes a seed so runs repeat exactly."""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def rng(seed: int, name: str) -> random.Random:
    """A generator that depends only on (seed, name), so adding one analysis never shifts another's stream."""
    return random.Random(f"{seed}:{name}")


def nearest_rank(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value whose rank share reaches q (0 < q <= 1)."""
    if not values:
        raise ValueError("no values")
    s = sorted(values)
    k = max(1, math.ceil(q * len(s) - 1e-12))
    return s[min(k, len(s)) - 1]


def median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("no values")
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def average_ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks; tied values share the mean of their ranks."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Probability a random positive outscores a random negative (ties count half). None if one class is empty."""
    n_pos = sum(1 for y in labels if y)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = average_ranks(scores)
    rank_sum = sum(r for r, y in zip(ranks, labels) if y)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Rank correlation with tie-averaged ranks. None when either side is constant."""
    return pearson(average_ranks(x), average_ranks(y))


def percentile_interval(samples: Sequence[float], level: float = 0.95) -> tuple[float, float] | None:
    if not samples:
        return None
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    return (nearest_rank(samples, max(lo_q, 1e-9)), nearest_rank(samples, hi_q))


def bootstrap(n_items: int, n_resamples: int, stat: Callable[[list[int]], float | None],
              gen: random.Random) -> list[float]:
    """Resample item indexes with replacement; keep the statistics that are defined."""
    out: list[float] = []
    for _ in range(n_resamples):
        idx = [gen.randrange(n_items) for _ in range(n_items)]
        v = stat(idx)
        if v is not None:
            out.append(v)
    return out
