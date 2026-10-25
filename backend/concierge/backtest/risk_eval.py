"""Risk band evaluation at circuit level (design D2).

Everything here reuses the spec-02 pieces (`weighted_percentile`, `band_for`, `wilson_interval`, `backtest`), so the
Low / Medium / High hit rates are the same numbers spec-02 stored. This module adds the base rate, lift, a circuit-size
baseline, a seeded permutation test, AUC and Spearman with bootstrap, and the sensitivity runs.
"""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..risk import calibrate as cal
from ..risk.config import BANDS, DEFAULT_CONFIG as RISK_DEFAULT, RiskConfig
from . import stats
from .config import BacktestConfig
from .split import Split

BAND_CODE = {b: i for i, b in enumerate(BANDS)}


class ReproductionError(RuntimeError):
    """The backtest's risk numbers differ from the stored spec-02 table."""


@dataclass(frozen=True)
class CircuitRow:
    """One evaluated circuit. `circuit` is only a sort key; it is never written to any output."""
    circuit: int
    n: int          # premises
    cal: int        # damage rows in the calibration storm
    eval: int       # damage rows in the evaluation storm

    @property
    def rate(self) -> float:
        return 1000.0 * self.cal / self.n

    @property
    def eval_rate(self) -> float:
        return 1000.0 * self.eval / self.n


def build_rows(sizes: Mapping[int, int], cal_counts: Mapping[int, int], eval_counts: Mapping[int, int],
               min_size: int) -> list[CircuitRow]:
    return [CircuitRow(c, sizes[c], cal_counts.get(c, 0), eval_counts.get(c, 0))
            for c in sorted(sizes) if sizes[c] >= min_size]


def _hit(r: CircuitRow, tau: float) -> int:
    return 1 if 1000 * r.eval >= tau * r.n else 0


def _group_stats(labels: Sequence[str], rows: Sequence[CircuitRow], hits: Sequence[int], base: float,
                 z: float) -> dict:
    """Per-band numbers for one labeling of the circuits. Same fields as the spec-02 table plus lift and difference."""
    out = {}
    for b in BANDS:
        idx = [i for i, lab in enumerate(labels) if lab == b]
        if not idx:
            out[b] = {"n_circuits": 0, "premises": 0, "hit_circuits": 0, "hit_rate": None, "ci95": None,
                      "eval_reports_per_1k": None, "lift": None, "difference": None, "vs_base": None,
                      "beats_base_rate": None}
            continue
        prem = sum(rows[i].n for i in idx)
        hit_prem = sum(rows[i].n for i in idx if hits[i])
        p = hit_prem / prem
        lo, hi = cal.wilson_interval(p, len(idx), z)
        vs = "above" if lo > base else "below" if hi < base else "overlaps"
        beats = {"High": vs == "above", "Low": vs == "below", "Medium": vs != "overlaps"}[b]
        out[b] = {"n_circuits": len(idx), "premises": prem, "hit_circuits": sum(hits[i] for i in idx),
                  "hit_rate": p, "ci95": [lo, hi],
                  "eval_reports_per_1k": 1000.0 * sum(rows[i].eval for i in idx) / prem,
                  "lift": p / base if base > 0 else None, "difference": p - base, "vs_base": vs,
                  "beats_base_rate": beats}
    return out


NO_SPREAD = {"value": None, "ci95": None}


def _spread(bands: Mapping) -> dict:
    """High minus Low hit rate, with an interval built from the two Wilson intervals (None values if a band is empty)."""
    h, l = bands["High"], bands["Low"]
    if h["hit_rate"] is None or l["hit_rate"] is None:
        return dict(NO_SPREAD)
    return {"value": h["hit_rate"] - l["hit_rate"], "ci95": [h["ci95"][0] - l["ci95"][1], h["ci95"][1] - l["ci95"][0]]}


def _permutation(rows: Sequence[CircuitRow], hits: Sequence[int], labels: Sequence[str], n_perm: int,
                 gen) -> dict:
    """Shuffle band labels across circuits; one-sided p-value for the premise-weighted High minus Low gap."""
    codes = [BAND_CODE[b] for b in labels]
    w = [r.n for r in rows]
    wh = [r.n * h for r, h in zip(rows, hits)]

    def gap(cs: Sequence[int]) -> float | None:
        prem, hit = [0.0] * 3, [0.0] * 3
        for c, a, b in zip(cs, w, wh):
            prem[c] += a
            hit[c] += b
        if prem[0] == 0 or prem[2] == 0:
            return None
        return hit[2] / prem[2] - hit[0] / prem[0]

    observed = gap(codes)
    if observed is None:
        return {"n_shuffles": n_perm, "observed_gap": None, "p_value": None}
    shuffled = list(codes)
    at_least = 0
    for _ in range(n_perm):
        gen.shuffle(shuffled)
        g = gap(shuffled)
        if g is not None and g >= observed - 1e-12:
            at_least += 1
    return {"n_shuffles": n_perm, "observed_gap": observed, "p_value": (at_least + 1) / (n_perm + 1)}


def evaluate(rows: Sequence[CircuitRow], *, tau: float, seed: int, name: str, cfg: BacktestConfig, min_size: int,
             risk_cfg: RiskConfig = RISK_DEFAULT) -> dict:
    """The full D2 result for one (calibration storm, evaluation storm, tau, circuit set) combination."""
    if not rows:
        raise ValueError("no circuits to evaluate")
    weights = [r.n for r in rows]
    rates = [r.rate for r in rows]
    thresholds = {"medium": cal.weighted_percentile(rates, weights, risk_cfg.p_medium),
                  "high": cal.weighted_percentile(rates, weights, risk_cfg.p_high)}
    labels = [cal.band_for(x, thresholds) for x in rates]
    hits = [_hit(r, tau) for r in rows]

    total_prem = sum(weights)
    base_w = sum(r.n for r, h in zip(rows, hits) if h) / total_prem
    base_c = sum(hits) / len(rows)
    z = risk_cfg.wilson_z
    bands = _group_stats(labels, rows, hits, base_w, z)

    # Circuit-size baseline: rank by premise count and cut into the same three group sizes (small -> Low).
    sizes_per_band = [sum(1 for lab in labels if lab == b) for b in BANDS]
    order = sorted(range(len(rows)), key=lambda i: (rows[i].n, rows[i].circuit))
    size_labels = [""] * len(rows)
    pos = 0
    for b, k in zip(BANDS, sizes_per_band):
        for i in order[pos:pos + k]:
            size_labels[i] = b
        pos += k
    size_bands = _group_stats(size_labels, rows, hits, base_w, z)

    perm = _permutation(rows, hits, labels, cfg.n_permutations, stats.rng(seed, f"perm:{name}"))

    auc_value = stats.auc(rates, hits)
    boots = stats.bootstrap(
        len(rows), cfg.n_bootstrap,
        lambda idx: stats.auc([rates[i] for i in idx], [hits[i] for i in idx]),
        stats.rng(seed, f"auc:{name}"))
    ci = stats.percentile_interval(boots)
    rho = stats.spearman(rates, [r.eval_rate for r in rows])

    lo_r, mid_r, hi_r = (bands[b]["hit_rate"] for b in BANDS)
    monotone = None if None in (lo_r, mid_r, hi_r) else (lo_r <= mid_r + 1e-12 and mid_r <= hi_r + 1e-12)
    return {
        "tau_per_1k": tau,
        "min_circuit_premises": min_size,
        "n_circuits": len(rows),
        "thresholds": thresholds,
        "base_rate": {"premise_weighted": base_w, "per_circuit": base_c},
        "bands": bands,
        "spread": _spread(bands),
        "monotone": monotone,
        "size_baseline": {"bands": size_bands, "spread": _spread(size_bands)},
        "permutation": perm,
        "auc": {"value": auc_value, "ci95": list(ci) if ci else None, "n_resamples": len(boots)},
        "spearman": {"value": rho, "n_circuits": len(rows)},
    }


def redact(result: Mapping, min_group: int) -> dict:
    """Remove numbers for any band that holds fewer than `min_group` circuits (project privacy rule).

    The spread depends on the High and Low rates, so it goes too when either band is hidden.
    """
    out = copy.deepcopy(dict(result))

    def scrub(bands: dict) -> bool:
        hidden = False
        for b in bands.values():
            small = 0 < b["n_circuits"] < min_group
            b["suppressed"] = small
            if small:
                hidden = True
                for k in list(b):
                    if k not in ("suppressed",):
                        b[k] = None
        return hidden

    hid_model = scrub(out["bands"])
    hid_size = scrub(out["size_baseline"]["bands"])
    if hid_model:
        out["spread"] = dict(NO_SPREAD)
        out["monotone"] = None
        out["permutation"]["observed_gap"] = None
    if hid_model or hid_size:
        out["size_baseline"]["spread"] = dict(NO_SPREAD)
    return out


def _json_equal(a, b, tol: float = 1e-12) -> bool:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return set(a) == set(b) and all(_json_equal(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_json_equal(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=tol, abs_tol=tol)
    return a == b


def spec02_tables(split: Split, risk_cfg: RiskConfig = RISK_DEFAULT) -> dict:
    """Re-run the spec-02 forward, reverse and tau tables through spec-02's own `backtest` function."""
    s1, s2, sizes = split.s1_counts(), split.s2_counts(), split.circuit_sizes
    return {
        "forward": cal.backtest(sizes, s1, s2, cfg=risk_cfg),
        "reverse": cal.backtest(sizes, s2, s1, cfg=risk_cfg),
        "sensitivity": {f"{t:g}": cal.backtest(sizes, s1, s2, tau=t, cfg=risk_cfg) for t in risk_cfg.tau_sensitivity},
    }


def reproduce_spec02(split: Split, stored_backtest: Mapping, risk_cfg: RiskConfig = RISK_DEFAULT) -> dict:
    """Fail the run unless the re-computed spec-02 tables equal the stored `calibration.json["backtest"]`."""
    fresh = json.loads(json.dumps(spec02_tables(split, risk_cfg)))
    for key in ("forward", "reverse", "sensitivity"):
        if not _json_equal(fresh[key], stored_backtest[key]):
            raise ReproductionError(f"spec-02 {key} table does not equal the stored calibration.json")
    return fresh


def check_primary_matches_spec02(primary: Mapping, spec02_forward: Mapping) -> None:
    """The primary result must carry the same band numbers as spec-02's forward table."""
    keys = ("n_circuits", "premises", "hit_circuits", "hit_rate", "ci95", "eval_reports_per_1k")
    if not _json_equal(primary["thresholds"], spec02_forward["thresholds"]):
        raise ReproductionError("band thresholds differ from spec-02")
    for b in BANDS:
        got, want = primary["bands"][b], spec02_forward["bands"][b]
        if not _json_equal({k: got[k] for k in keys}, {k: want[k] for k in keys}):
            raise ReproductionError(f"{b} band differs from spec-02")


def run_risk(split: Split, cfg: BacktestConfig, risk_cfg: RiskConfig = RISK_DEFAULT) -> dict:
    """Primary forward result plus every sensitivity run, all with the same structure."""
    s1, s2, sizes = split.s1_counts(), split.s2_counts(), split.circuit_sizes
    min_size = risk_cfg.min_circuit_premises
    fwd = build_rows(sizes, s1, s2, min_size)
    primary_tau = risk_cfg.hit_tau_per_1k

    def run(rows, tau, name, size=min_size):
        return evaluate(rows, tau=tau, seed=cfg.seed, name=name, cfg=cfg, min_size=size, risk_cfg=risk_cfg)

    primary = run(fwd, primary_tau, "primary")
    by_tau = {f"{t:g}": (primary if t == primary_tau else run(fwd, t, f"tau{t:g}")) for t in cfg.taus}
    biggest = {r.circuit for r in sorted(fwd, key=lambda r: (-r.n, r.circuit))[:cfg.drop_largest]}
    sensitivity = {
        "tau": by_tau,
        "reverse": run(build_rows(sizes, s2, s1, min_size), primary_tau, "reverse"),
        f"drop_largest_{cfg.drop_largest}": run([r for r in fwd if r.circuit not in biggest], primary_tau, "drop"),
        f"min_size_{cfg.sensitivity_min_size}": run(build_rows(sizes, s1, s2, cfg.sensitivity_min_size),
                                                    primary_tau, "minsize", cfg.sensitivity_min_size),
    }
    return {"primary": primary, "sensitivity": sensitivity}
