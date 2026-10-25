"""Circuit-level view of the restoration engine, for the spec-04 backtest.

The customer-facing engine answers "how long until THIS home has power?" and stops scheduling at the customer's last
path repair. The backtest compares with observed repair days, which are per damaged asset on a circuit, so it needs the
time until ALL repairs on the circuit are done. This module runs the same pieces (labor pricing in `jobs.py`, the
upstream-first priority scheduler in `schedule.py`, the seeded streams and per-job factor table in `montecarlo.py`)
with the whole circuit as the "path", and returns the pooled completion time of every trial.

    engine = load_engine("artifacts/curated/tables")
    hours = engine.completion_hours(circuit_number, crews=3, damage=<OracleDamage | ForecastDamage>, seed=1)

`damage` is read by duck typing, so this package does not import the backtest:
    damage.counts            asset type -> number of damaged assets (the oracle run: actual counts, placed at random on
                             the nodes of the matching level)
    damage.expected_events   expected number of damaged assets (the forecast run: Poisson draw per trial, asset types
                             from the historical mix)

Assumptions, all echoed in the backtest report:
* Every trial has at least one repair. Observed repair days only exist where something was damaged, so the prediction
  is conditional on damage, like the customer-facing estimate is conditional on an outage.
* A circuit with no history (expected events below `MIN_EXPECTED_EVENTS`) is treated as having that minimum, the same
  floor the customer-facing engine uses. It is a placeholder, not a forecast.
* Normal access, and the "Medium" shared spread (`SHARED_SIGMA`), because a circuit has no per-premise confidence.
* Circuits whose breaker serves under 10 premises have no tree; asking for one returns no trials.

Build time only (it reads `device_dim` with pyarrow). The API never imports this module.
"""
from __future__ import annotations

import json
import math
import random
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import config
from .jobs import ASSET_LEVEL, job_hours, mix_for
from .montecarlo import (crews_at_work, job_factor_table, nearest_rank, poisson_from_uniform, trial_count,
                         trial_streams)
from .schedule import PathInfo, schedule_trial
from .tree import LEVEL_INDEX, CircuitTree, RestorationTree

__all__ = ["CircuitEngine", "WholeCircuit", "circuit_index", "load_engine"]

DEVICE_COLUMNS = ["device_key", "device_class", "device_type", "parent_key", "circuit", "depth", "is_protective",
                  "n_premises_served"]
SHARED_CONFIDENCE = "Medium"


class WholeCircuit(PathInfo):
    """A `PathInfo` whose path is every node, so the scheduler runs until all jobs on the circuit are done."""

    def __init__(self, circuit: CircuitTree):            # noqa: super().__init__ would build a single path
        nodes = tuple(range(len(circuit)))
        self.pn = 0
        self.nodes = nodes
        self.node_set = frozenset(nodes)
        self.level = {v: circuit.level[v] for v in nodes}


def circuit_index(devices: Iterable[Mapping], min_served: int = config.MIN_SERVED) -> dict[int, int]:
    """Circuit number -> opaque tree index, using the rule of `curate.restoration_tree.build_tree`.

    The artifact orders circuits by the (salted) key of their breaker and drops circuits whose breaker serves fewer than
    `min_served` premises. Repeating that order here is what lets the backtest find a circuit's tree by its number.
    """
    roots = {}
    for d in devices:
        if d["parent_key"] is None:
            if d["device_class"] != "breaker" or d["circuit"] in roots:
                raise ValueError("every circuit needs exactly one breaker at the top of its device tree")
            roots[d["circuit"]] = d
    kept = sorted((d["device_key"], c) for c, d in roots.items() if d["n_premises_served"] >= min_served)
    return {circuit: i for i, (_, circuit) in enumerate(kept)}


class CircuitEngine:
    """Pooled repair completion per circuit. Deterministic for a given seed."""

    engine_version = config.ENGINE_VERSION

    def __init__(self, tree: RestorationTree, index: Mapping[int, int], labor: Mapping):
        self._tree, self._index, self._labor = tree, dict(index), labor
        self._assets, self._mix_cum = mix_for()
        self._prices = [job_hours(a, labor, "normal") for a in self._assets]
        self._sigma = config.SHARED_SIGMA[SHARED_CONFIDENCE]
        self._whole: dict[int, WholeCircuit] = {}
        self._level_nodes: dict[int, list[tuple[int, ...]]] = {}

    def has_circuit(self, circuit: int) -> bool:
        return circuit in self._index and self._tree.circuit(self._index[circuit]) is not None

    def _prepare(self, pc: int, tree: CircuitTree):
        if pc not in self._whole:
            self._whole[pc] = WholeCircuit(tree)
            self._level_nodes[pc] = [tree.nodes_at(LEVEL_INDEX[ASSET_LEVEL[a]]) for a in self._assets]
        return self._whole[pc], self._level_nodes[pc]

    def completion_hours(self, circuit: int, *, crews: int, damage, seed: int) -> list[float]:
        """Clock hours until every repair on the circuit is done, one value per Monte Carlo trial (empty: no tree)."""
        if crews < 1:
            raise ValueError("crews must be at least 1")
        pc = self._index.get(circuit)
        tree = self._tree.circuit(pc) if pc is not None else None
        if tree is None:
            return []
        whole, level_nodes = self._prepare(pc, tree)
        asset_pos = {a: i for i, a in enumerate(self._assets)}

        if hasattr(damage, "counts"):
            fixed = [asset_pos[a] for a, q in sorted(damage.counts.items()) if a in asset_pos for _ in range(int(q))]
            if not fixed:
                return []
            mean_jobs = float(len(fixed))
        else:
            fixed = None
            mean_jobs = max(float(damage.expected_events), config.MIN_EXPECTED_EVENTS)

        trials = trial_count(mean_jobs)
        table = job_factor_table()
        bins, cum, prices, sigma = len(table), self._mix_cum, self._prices, self._sigma
        clock = config.CLOCK_FACTOR
        rng_a, rng_b = random.Random(), random.Random()
        out: list[float] = []
        for t in range(trials):
            trial_streams(seed, t, rng_a, rng_b)
            ra = rng_a.random
            shared = math.exp(sigma * rng_a.gauss(0.0, 1.0) - 0.5 * sigma * sigma)
            u_count = ra()
            n = len(fixed) if fixed is not None else min(config.SIMULATED_JOBS_CAP, max(1, poisson_from_uniform(mean_jobs, u_count)))
            nodes: list[int] = []
            hours: list[float] = []
            for k in range(n):
                u = ra()
                if fixed is not None:
                    a = fixed[k]
                    choices = level_nodes[a]
                    i = int(u * len(choices))
                else:
                    a = min(bisect_right(cum, u), len(cum) - 1)
                    lo = cum[a - 1] if a else 0.0
                    choices = level_nodes[a]
                    i = int((u - lo) / (cum[a] - lo) * len(choices))
                nodes.append(choices[min(i, len(choices) - 1)])
                hours.append(prices[a] * shared * table[int(ra() * bins)])
            _, _, total = schedule_trial(tree, whole, nodes, hours)
            out.append(total * clock / crews_at_work(rng_b, crews))
        return out

    def p50_p90_hours(self, circuit: int, **kw) -> tuple[float, float] | None:
        hours = sorted(self.completion_hours(circuit, **kw))
        return (nearest_rank(hours, 50), nearest_rank(hours, 90)) if hours else None


def _read_devices(tables_dir: Path) -> list[dict]:
    import pyarrow.parquet as pq
    return pq.read_table(tables_dir / "device_dim.parquet", columns=DEVICE_COLUMNS).to_pylist()


def load_engine(tables_dir: str | Path, labor_path: str | Path | None = None) -> CircuitEngine:
    """Build the circuit engine from the clean `device_dim` table and the labor sheet next to the tables."""
    from concierge.curate.restoration_tree import build_tree

    tables = Path(tables_dir)
    devices = _read_devices(tables)
    doc, _, _ = build_tree(devices, [])
    labor = json.loads(Path(labor_path or tables.parent / "labor_standards.json").read_text())
    return CircuitEngine(RestorationTree.from_dict(doc), circuit_index(devices), labor)

