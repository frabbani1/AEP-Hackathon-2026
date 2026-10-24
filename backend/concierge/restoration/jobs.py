"""Repair jobs: pricing from the labor sheet and job generation (spec-03 design D1, D2).

A job is one damaged asset at one tree node. Its hours are

    job.hours = (unit_hours(asset) + access_adder(access)) x M          M = labor["indirect_labor_multiplier"]

`unit_hours` sums install and removal hours of the hardware replaced (`ASSET_LABOR`). The adder applies once per
job, before the multiplier. The multiplier is read from the sheet every time and is never hard-coded.

`ASSET_LABOR`, `ASSET_LEVEL` and `ASSET_COUNTS` are engineering assumptions that need review by a utility engineer.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import accumulate

from . import config
from .common import RestorationError
from .schedule import PathInfo
from .tree import LEVEL_INDEX, CircuitTree

# Where each ARCOS asset type sits on the path home -> substation.
ASSET_LEVEL = {
    "primary_line": "feeder", "recloser": "feeder", "switch": "feeder", "capacitor_bank": "feeder",
    "step_transformer": "feeder",
    "fuse_bank": "lateral", "pole": "lateral",
    "transformer": "service", "secondary_line": "service", "light": "service",
}
# Historical mix: damage rows with an asset type that map to a topology circuit (ETL run of 2026-10-24, 1,644 rows).
ASSET_COUNTS = {"primary_line": 696, "pole": 354, "fuse_bank": 278, "secondary_line": 154, "transformer": 73,
                "recloser": 58, "switch": 19, "capacitor_bank": 6, "light": 5, "step_transformer": 1}
# Hardware replaced in one repair job (labor-sheet category, or (category, size) for sized units).
ASSET_LABOR: dict[str, list] = {
    "primary_line": ["crossarm", "insulator", "pin", "dead_end"],
    "secondary_line": ["secondary_conductor_attachment", "service"],
    "pole": [("pole", "Medium")],
    "fuse_bank": ["fuse_cutout"],
    "transformer": [("transformer", 50)],
    "recloser": ["recloser"],
    "switch": ["switch"],
    "capacitor_bank": ["equipment_lead", "bracket"],
    "light": ["bracket"],
    "step_transformer": [("transformer", 167)],
}
ASSETS = tuple(ASSET_LEVEL)


# ---- pricing -------------------------------------------------------------------------------------------------------
def unit_hours(labor: Mapping, spec) -> float:
    """Install + removal hours of one labor-sheet unit. `spec` is a category or a (category, size) pair."""
    cat, key = (spec, None) if isinstance(spec, str) else spec
    for u in labor["units"]:
        if u["category"] == cat and (key is None or key in (u.get("size_group"), u.get("size_kva"))):
            return (u["install_hours"] or 0.0) + (u["remove_hours"] or 0.0)
    raise RestorationError(f"labor sheet has no unit for {spec!r}")


def adder_hours(labor: Mapping, access: str) -> float:
    """0 for normal access, otherwise the per-job adder from the sheet."""
    key = config.ACCESS_ADDER_KEY[access]
    if key is None:
        return 0.0
    for a in labor["adders"]:
        if a["key"] == key and a["hours"] is not None:
            return float(a["hours"])
    raise RestorationError(f"labor sheet has no adder {key!r}")


def multiplier(labor: Mapping) -> float:
    """Indirect labor multiplier (travel and non-productive time), read from the sheet."""
    m = labor.get(config.MULTIPLIER_KEY)
    if not isinstance(m, (int, float)) or isinstance(m, bool) or m <= 0:
        raise RestorationError("labor sheet has no indirect labor multiplier")
    return float(m)


def job_hours(asset: str, labor: Mapping, access: str) -> float:
    """Crew-hours for one repair job, including the access adder and the indirect labor multiplier."""
    direct = sum(unit_hours(labor, s) for s in ASSET_LABOR[asset]) + adder_hours(labor, access)
    return direct * multiplier(labor)


# ---- damage mix ----------------------------------------------------------------------------------------------------
def mix_for() -> tuple[tuple[str, ...], list[float]]:
    """Assets in the historical mix and their cumulative weights (the last weight is exactly 1)."""
    assets = tuple(ASSET_COUNTS)
    total = sum(ASSET_COUNTS.values())
    cum = [c / total for c in accumulate(ASSET_COUNTS.values())]
    cum[-1] = 1.0
    return assets, cum


# ---- placement -----------------------------------------------------------------------------------------------------
SOURCES = ("simulated", "count", "path")          # D2 modes: Simulated, Damage count, Customer path


def path_node_for_level(circuit: CircuitTree, pn: int, level: str) -> int:
    """The customer's own path node that carries a repair of this level (Customer path mode).

    The deepest path node of that level; if the path has none, the nearest path node on a higher level, and
    finally the top of the circuit.
    """
    want = LEVEL_INDEX[level]
    best: dict[int, int] = {}
    for v in circuit.path(pn):                      # from the customer's node up to the breaker
        best.setdefault(circuit.level[v], v)
    for lv in range(want, -1, -1):
        if lv in best:
            return best[lv]
    return circuit.path(pn)[-1]


# ---- the job plan of one scenario ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    """Everything a trial needs to draw jobs. Built once per scenario; sensitivity runs change a few fields.

    `assets[i]` has base hours `prices[i]` (access adder and multiplier included) and lands on one of
    `level_nodes[i]`. In "path" mode the jobs are fixed: job k is asset `fixed_assets[k]` at node `fixed_nodes[k]`.
    """
    circuit: CircuitTree
    path: PathInfo
    source: str                          # "simulated" | "count" | "path"
    assets: tuple[str, ...]
    prices: tuple[float, ...]
    mix_cum: tuple[float, ...]           # cumulative historical mix over `assets` (simulated and count modes)
    level_nodes: tuple[tuple[int, ...], ...]
    path_nodes: tuple[int, ...] = ()     # per asset: the customer's own path node a repair of that asset would sit on
    fixed_assets: tuple[int, ...] = ()
    fixed_nodes: tuple[int, ...] = ()
    mean_jobs: float = 0.0               # simulated mode: expected jobs per trial (Poisson mean)
    count: int = 0                       # count mode: exact jobs per trial

    @property
    def expected_jobs(self) -> float:
        """Jobs per trial, used for the trial-count rule and reported as scenario.damage_count."""
        if self.source == "simulated":
            return self.mean_jobs
        return float(len(self.fixed_assets) if self.source == "path" else self.count)


def make_plan(*, circuit: CircuitTree, pn: int, labor: Mapping, access: str, source: str,
              damage: Sequence[tuple[str, int]] | None = None, count: int = 0, mean_jobs: float = 0.0) -> Plan:
    """Price the assets and place them on the circuit. Raises RestorationError if the sheet cannot price one."""
    if source not in SOURCES:
        raise RestorationError("unknown damage source")
    path = PathInfo(circuit, pn)
    if source == "path":
        if not damage:
            raise RestorationError("a damage list is needed for customer-path mode")
        assets = tuple(sorted({a for a, _ in damage}))
        fixed_assets = tuple(assets.index(a) for a, q in damage for _ in range(q))
        fixed_nodes = tuple(path_node_for_level(circuit, pn, ASSET_LEVEL[assets[i]]) for i in fixed_assets)
        mix_cum = ()
    else:
        assets, cum = mix_for()
        mix_cum = tuple(cum)
        fixed_assets, fixed_nodes = (), ()
    return Plan(
        circuit=circuit, path=path, source=source, assets=assets,
        prices=tuple(job_hours(a, labor, access) for a in assets), mix_cum=mix_cum,
        level_nodes=tuple(circuit.nodes_at(LEVEL_INDEX[ASSET_LEVEL[a]]) for a in assets),
        path_nodes=tuple(path_node_for_level(circuit, pn, ASSET_LEVEL[a]) for a in assets),
        fixed_assets=fixed_assets, fixed_nodes=fixed_nodes, mean_jobs=float(mean_jobs), count=int(count))
