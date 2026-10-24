"""Circuit and premise features (design D2)."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .config import DEFAULT_CONFIG, RiskConfig


@dataclass(frozen=True)
class PremiseInput:
    """One premise after joining topology and customer data. circuit is None when the premise has no chain."""
    premise_id: str
    circuit: int | None
    service: str
    protective: int | None
    hops: int | None


@dataclass(frozen=True)
class CircuitStats:
    circuit: int
    n_premises: int      # N_c
    records: int         # D_c: damage records in storm windows
    rate: float          # R_c: reports per 1,000 premises per average storm
    k_bar: float         # K_c: premise-weighted mean of T*E on the circuit


def premise_te(p: PremiseInput, cfg: RiskConfig) -> float:
    return cfg.type_factor(p.service) * cfg.exposure(p.protective or 0, p.hops or 0)


def circuit_stats(
    premises: Iterable[PremiseInput],
    damage_by_circuit: Mapping[int, int],
    n_storms: int,
    cfg: RiskConfig = DEFAULT_CONFIG,
) -> dict[int, CircuitStats]:
    """R_c = 1000 * (D_c/St + k*rbar) / (N_c + k), and K_c = mean(T*E) over the circuit's premises."""
    sizes: Counter[int] = Counter()
    te_sum: Counter[int] = Counter()
    for p in premises:
        if p.circuit is None:
            continue
        sizes[p.circuit] += 1
        te_sum[p.circuit] += premise_te(p, cfg)

    total_n = sum(sizes.values())
    total_d = sum(damage_by_circuit.get(c, 0) for c in sizes)
    rbar = total_d / (n_storms * total_n) if total_n else 0.0  # per premise per storm
    k = cfg.shrinkage_k

    out: dict[int, CircuitStats] = {}
    for c, n in sizes.items():
        d = damage_by_circuit.get(c, 0)
        rate = 1000.0 * (d / n_storms + k * rbar) / (n + k)
        out[c] = CircuitStats(c, n, d, rate, te_sum[c] / n)
    return out


def raw_score(r_eff: float, service: str, protective: float, hops: float, multiplier: float,
              cfg: RiskConfig = DEFAULT_CONFIG) -> float:
    """S = R_eff * T * E * m, with R_eff = R_c / K_c."""
    return r_eff * cfg.type_factor(service) * cfg.exposure(protective, hops) * multiplier
