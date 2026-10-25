"""Restoration range evaluation at circuit level (design D3, D4, D5).

The restoration engine is injected. Spec-03 (`restoration-v2`) has to provide a `CircuitEngine`; until it does, pass
`engine=None` and this module reports everything that does not need a model (backlog shares, observed repair
statistics, whether the crews fit is identifiable, and the three baselines) and marks the model runs `not_run`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from . import stats
from .config import BacktestConfig
from .split import Damage, Split


# ---- the engine contract -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class OracleDamage:
    """Actual asset counts for the circuit in the evaluated storm (asset type -> number of damaged assets)."""
    counts: Mapping[str, int]


@dataclass(frozen=True)
class ForecastDamage:
    """Expected number of damaged assets: S1 circuit count scaled to S2 severity. The engine draws the actual count."""
    expected_events: float


class CircuitEngine(Protocol):
    def completion_hours(self, circuit: int, *, crews: int, damage: "OracleDamage | ForecastDamage",
                         seed: int) -> Sequence[float]:
        """Pooled job-completion time of all jobs on the circuit, in clock hours, one value per Monte Carlo trial.

        Return an empty sequence when the circuit has no jobs. Must be deterministic for a given seed.
        """


@dataclass(frozen=True)
class Prediction:
    p50_days: float
    p90_days: float


def predict(engine: CircuitEngine, circuit: int, *, crews: int, damage, seed: int) -> Prediction | None:
    hours = list(engine.completion_hours(circuit, crews=crews, damage=damage, seed=seed))
    if not hours:
        return None
    return Prediction(stats.nearest_rank(hours, 0.5) / 24.0, stats.nearest_rank(hours, 0.9) / 24.0)


def _seed(seed: int, tag: str, circuit: int) -> int:
    return stats.rng(seed, f"{tag}:{circuit}").getrandbits(62)


# ---- observed repair statistics -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class CircuitObs:
    n: int                   # usable records
    median: float
    p90: float
    backlog: int             # repaired records beyond the cutoff on this circuit
    days: tuple[int, ...]


@dataclass
class Observed:
    cutoff: int
    counts: dict[str, int]
    included: dict[int, CircuitObs]
    excluded_small: int
    histogram_days: Counter

    def public(self) -> dict:
        n = self.counts
        rows = n["rows"]
        repaired_mapped = n["usable"] + n["backlog"]
        return {
            "cutoff_days": self.cutoff, **n,
            "backlog_share": n["backlog"] / repaired_mapped if repaired_mapped else None,
            "excluded_share": 1.0 - n["usable"] / rows if rows else None,
            "circuits_included": len(self.included), "excluded_small": self.excluded_small,
            "records_in_included_circuits": sum(c.n for c in self.included.values()),
        }


def observe(rows: Iterable[Damage], cutoff: int, min_records: int) -> Observed:
    """Classify each row once, in this order: censored, unmapped circuit, backlog, usable.

    Backlog means more than `cutoff` days (the data has nothing at 7 days, so B = 7 gives the curated `is_backlog` set).
    """
    counts = {"rows": 0, "censored": 0, "unmapped": 0, "backlog": 0, "usable": 0}
    days: dict[int, list[int]] = {}
    backlog: Counter[int] = Counter()
    hist: Counter[int] = Counter()
    for r in rows:
        counts["rows"] += 1
        if not r.repaired or r.repair_days is None:
            counts["censored"] += 1
            continue
        hist[r.repair_days] += 1
        if not r.mapped:
            counts["unmapped"] += 1
        elif r.repair_days > cutoff:
            counts["backlog"] += 1
            backlog[r.circuit] += 1
        else:
            counts["usable"] += 1
            days.setdefault(r.circuit, []).append(r.repair_days)
    included, small = {}, 0
    for c, d in days.items():
        if len(d) < min_records:
            small += 1
            continue
        included[c] = CircuitObs(len(d), stats.median(d), stats.nearest_rank(d, 0.9), backlog[c], tuple(sorted(d)))
    return Observed(cutoff, counts, included, small, hist)


# ---- crews fit (S1 only) ---------------------------------------------------------------------------------------------
def _asset_counts(rows: Iterable[Damage]) -> dict[int, Counter]:
    out: dict[int, Counter] = {}
    for r in rows:
        if r.mapped and r.asset_type:
            out.setdefault(r.circuit, Counter())[r.asset_type] += 1
    return out


def _fit_grid(obs: Mapping[int, CircuitObs], preds: Mapping[int, Mapping[int, float]], ks: Sequence[int],
              n_boot: int, gen) -> dict:
    """Pick the k with the lowest mean absolute error of predicted P50 vs observed median, with a bootstrap interval."""
    circuits = [c for c in obs if c in preds]
    err = {k: [abs(preds[c][k] - obs[c].median) for c in circuits] for k in ks}

    def best(idx: Sequence[int]) -> int:
        return min(ks, key=lambda k: (sum(err[k][i] for i in idx), k))

    k_hat = best(range(len(circuits)))
    boots = stats.bootstrap(len(circuits), n_boot, lambda idx: float(best(idx)), gen)
    ci = stats.percentile_interval(boots)
    return {"crews": k_hat, "ci95": [int(ci[0]), int(ci[1])] if ci else None, "circuits": len(circuits),
            "mae_by_crews": {str(k): sum(err[k]) / len(circuits) for k in ks} if circuits else {}}


def fit_crews(s1_rows: Sequence[Damage], engine: CircuitEngine | None, cfg: BacktestConfig, cutoff: int) -> dict:
    """Fit the effective crews on Sep 2024 only. Takes S1 rows and nothing else, so S2 cannot leak in.

    Not identifiable (too few usable records or circuits) means: use the default crews and call the result uncalibrated.
    """
    obs = observe(s1_rows, cutoff, cfg.fit_min_circuit_records)
    records = obs.counts["usable"]
    circuits = len(obs.included)
    req = {"min_records": cfg.fit_min_records, "min_circuits": cfg.fit_min_circuits,
           "min_records_per_circuit": cfg.fit_min_circuit_records}
    base = {"records": records, "circuits_with_enough_records": circuits, "required": req}
    if records < cfg.fit_min_records or circuits < cfg.fit_min_circuits:
        return {**base, "status": "not_identifiable", "crews": cfg.default_crews, "label": "uncalibrated",
                "ci95": None, "reason": "Sep 2024 has too few usable repair records to fit the number of crews."}
    if engine is None:
        return {**base, "status": "not_run", "crews": cfg.default_crews, "label": "uncalibrated", "ci95": None,
                "reason": "The restoration engine is not available."}
    ks = list(range(cfg.k_min, cfg.k_max + 1))
    assets = _asset_counts(s1_rows)
    preds: dict[int, dict[int, float]] = {}
    for c in obs.included:
        row: dict[int, float] = {}
        for k in ks:
            p = predict(engine, c, crews=k, damage=OracleDamage(dict(assets.get(c, {}))),
                        seed=_seed(cfg.seed, "fit", c))
            if p is None:
                break
            row[k] = p.p50_days
        else:
            preds[c] = row
    fit = _fit_grid(obs.included, preds, ks, cfg.k_bootstrap, stats.rng(cfg.seed, f"fit:{cutoff}"))
    return {**base, "status": "fitted", "label": "fitted on Sep 2024 only", "reason": None, **fit}


def s2_ceiling(split: Split, engine: CircuitEngine | None, cfg: BacktestConfig, cutoff: int) -> dict | None:
    """In-sample best crews on Feb 2025. A ceiling, not a test: it uses the outcome it is scored on."""
    if engine is None:
        return None
    obs = observe(split.s2.rows(), cutoff, cfg.min_circuit_records)
    assets = _asset_counts(split.s2.rows())
    ks = list(range(cfg.k_min, cfg.k_max + 1))
    preds: dict[int, dict[int, float]] = {}
    for c in obs.included:
        row = {}
        for k in ks:
            p = predict(engine, c, crews=k, damage=OracleDamage(dict(assets.get(c, {}))),
                        seed=_seed(cfg.seed, "ceiling", c))
            if p is None:
                break
            row[k] = p.p50_days
        else:
            preds[c] = row
    if not preds:
        return None
    fit = _fit_grid(obs.included, preds, ks, cfg.k_bootstrap, stats.rng(cfg.seed, f"ceiling:{cutoff}"))
    return {"label": "not a test: fitted on the same storm it is scored on", **fit}


# ---- predictions -----------------------------------------------------------------------------------------------------
def forecast_predictions(split: Split, engine: CircuitEngine, *, crews: int, seed: int) -> dict[int, Prediction | None]:
    """Forecast-damage run for every circuit. Runs inside the forecast scope: S2 damage rows cannot be read."""
    with split.forecast_scope():
        ratio = split.severity_ratio()
        s1 = split.s1_counts()
        return {c: predict(engine, c, crews=crews, damage=ForecastDamage(s1.get(c, 0) * ratio),
                           seed=_seed(seed, "forecast", c))
                for c in sorted(split.circuit_sizes)}


def oracle_predictions(split: Split, engine: CircuitEngine, circuits: Iterable[int], *, crews: int,
                       seed: int) -> dict[int, Prediction | None]:
    """Oracle-damage run: uses the actual S2 asset counts, so it is labeled as an oracle in every output."""
    assets = _asset_counts(split.s2.rows())
    return {c: predict(engine, c, crews=crews, damage=OracleDamage(dict(assets.get(c, {}))),
                       seed=_seed(seed, "oracle", c))
            for c in sorted(circuits)}


# ---- scoring ---------------------------------------------------------------------------------------------------------
def baseline_predictions(s1_rows: Sequence[Damage], circuits: Iterable[int], cutoff: int) -> dict[str, dict[int, float]]:
    """(a) S1 global median, (b) S1 circuit median where the circuit has data, else global, (c) the constant 1 day."""
    obs = observe(s1_rows, cutoff, 1)
    all_days = [d for o in obs.included.values() for d in o.days]
    out: dict[str, dict[int, float]] = {"one_day": {c: 1.0 for c in circuits}}
    if all_days:
        g = stats.median(all_days)
        out["constant"] = {c: g for c in circuits}
        out["circuit_median"] = {c: (obs.included[c].median if c in obs.included else g) for c in circuits}
    return out


def _paired_bootstrap(model_err: Sequence[float], base_err: Sequence[float], n_boot: int, gen) -> dict:
    n = len(model_err)
    diffs = [m - b for m, b in zip(model_err, base_err)]
    boots = stats.bootstrap(n, n_boot, lambda idx: sum(diffs[i] for i in idx) / len(idx), gen) if n else []
    ci = stats.percentile_interval(boots)
    return {"mae_difference": sum(diffs) / n if n else None, "ci95": list(ci) if ci else None,
            "beats": bool(ci and ci[1] < 0)}


def baseline_table(obs: Observed, baselines: Mapping[str, Mapping[int, float]], model: Mapping[int, Prediction | None] | None,
                   cfg: BacktestConfig, tag: str) -> dict:
    """MAE of each baseline against the observed S2 median; with a model run, the paired bootstrap against it."""
    out = {}
    for name, pred in baselines.items():
        cs = [c for c in obs.included if c in pred and (model is None or model.get(c) is not None)]
        errs = [abs(pred[c] - obs.included[c].median) for c in cs]
        values = {pred[c] for c in cs}
        row = {"prediction_days": next(iter(values)) if len(values) == 1 else None,
               "mae": sum(errs) / len(errs) if errs else None, "circuits": len(cs)}
        if model is not None and cs:
            m_err = [abs(model[c].p50_days - obs.included[c].median) for c in cs]
            row["model_vs_baseline"] = _paired_bootstrap(m_err, errs, cfg.n_bootstrap, stats.rng(cfg.seed, f"{tag}:{name}"))
        out[name] = row
    return out


def score_run(preds: Mapping[int, Prediction | None], obs: Observed, cfg: BacktestConfig) -> dict:
    """Coverage, MAE and within-one-day over the included circuits that have a prediction."""
    cs = [c for c in sorted(obs.included) if preds.get(c) is not None]
    no_pred = len(obs.included) - len(cs)
    if not cs:
        return {"circuits": 0, "no_prediction": no_pred, "records": 0, "coverage_p50": None, "coverage_p90": None,
                "coverage_p50_backlog_as_miss": None, "coverage_p90_backlog_as_miss": None, "mae_days": None,
                "within_one_day": None, "points": []}
    rec = sum(obs.included[c].n for c in cs)
    back = sum(obs.included[c].backlog for c in cs)
    hit50 = sum(sum(1 for d in obs.included[c].days if d <= preds[c].p50_days) for c in cs)
    hit90 = sum(sum(1 for d in obs.included[c].days if d <= preds[c].p90_days) for c in cs)
    errs = [abs(preds[c].p50_days - obs.included[c].median) for c in cs]
    points = sorted(({"predicted_p50_days": round(preds[c].p50_days, 1), "observed_median_days": round(obs.included[c].median, 1),
                      "records": obs.included[c].n} for c in cs),
                    key=lambda p: (p["predicted_p50_days"], p["observed_median_days"], p["records"]))
    return {
        "circuits": len(cs), "no_prediction": no_pred, "records": rec,
        "coverage_p50": hit50 / rec, "coverage_p90": hit90 / rec,
        "coverage_p50_backlog_as_miss": hit50 / (rec + back), "coverage_p90_backlog_as_miss": hit90 / (rec + back),
        "mae_days": sum(errs) / len(errs), "within_one_day": sum(1 for e in errs if e <= cfg.within_days) / len(errs),
        "points": points,
    }


def merge_small_bins(hist: Mapping[int, int], max_day: int, min_group: int) -> list[dict]:
    """Per-day counts for days 0..max_day plus one open-ended tail bin, with no published bin under `min_group`.

    A bin holding 1 to `min_group - 1` records merges into its right neighbour (the left one for the last bin) until
    none is left (project privacy rule). `to_day` is None for an open-ended bin. Empty trailing bins are dropped.
    """
    bins = [[d, d, hist.get(d, 0)] for d in range(max_day + 1)]
    bins.append([max_day + 1, None, sum(v for d, v in hist.items() if d > max_day)])
    while len(bins) > 1:
        i = next((k for k, b in enumerate(bins) if 0 < b[2] < min_group), None)
        if i is None:
            break
        lo_i = i if i + 1 < len(bins) else i - 1
        a, b = bins[lo_i], bins[lo_i + 1]
        bins[lo_i:lo_i + 2] = [[a[0], b[1], a[2] + b[2]]]
    while len(bins) > 1 and bins[-1][2] == 0:
        bins.pop()
    return [{"from_day": lo, "to_day": hi, "count": c if (c == 0 or c >= min_group) else None} for lo, hi, c in bins]


# ---- orchestration -----------------------------------------------------------------------------------------------------
def _hist_with_bands(obs: Observed, cfg: BacktestConfig, band: tuple[float, float] | None) -> dict:
    return {"bins": merge_small_bins(obs.histogram_days, cfg.hist_max_days, cfg.min_group),
            "backlog_from_day": obs.cutoff + 1, "predicted_p50_to_p90_days": list(band) if band else None}


def run_restoration(split: Split, engine: CircuitEngine | None, cfg: BacktestConfig) -> dict:
    """The full D3 to D5 evaluation at every backlog cutoff. `engine=None` skips the model runs."""
    out: dict = {"status": "ok" if engine is not None else "not_run",
                 "reason": None if engine is not None else
                 "The restoration engine (spec-03, restoration-v2) is not available, so no model run was scored.",
                 "resolution_days": 1, "cutoffs": {}}
    s2_rows = split.s2.rows()
    cache: dict[tuple, dict] = {}
    for B in cfg.cutoffs():
        obs1 = observe(split.s1, B, cfg.min_circuit_records)
        obs2 = observe(s2_rows, B, cfg.min_circuit_records)
        fit = fit_crews(split.s1, engine, cfg, B)
        crews = fit["crews"]
        res: dict = {
            "backlog": {"S1": obs1.public(), "S2": obs2.public()},
            "fit": fit, "crews_used": crews, "calibration": fit["label"],
        }
        base = baseline_predictions(split.s1, sorted(obs2.included), B)
        res["baselines"] = baseline_table(obs2, base, None, cfg, f"base:{B}")
        res["runs"] = {}
        band = None
        if engine is not None:
            key = ("forecast", crews)
            if key not in cache:
                cache[key] = forecast_predictions(split, engine, crews=crews, seed=cfg.seed)
            runs = {"forecast": cache[key],
                    "oracle": oracle_predictions(split, engine, obs2.included, crews=crews, seed=cfg.seed)}
            for name, preds in runs.items():
                metrics = score_run(preds, obs2, cfg)
                metrics["baselines"] = baseline_table(obs2, base, preds, cfg, f"{name}:{B}")
                res["runs"][name] = metrics
            res["ceiling"] = s2_ceiling(split, engine, cfg, B)
            fc = [(c, p) for c, p in runs["forecast"].items() if c in obs2.included and p is not None]
            if fc:
                w = sum(obs2.included[c].n for c, _ in fc)
                band = (sum(obs2.included[c].n * p.p50_days for c, p in fc) / w,
                        sum(obs2.included[c].n * p.p90_days for c, p in fc) / w)
        res["histogram"] = _hist_with_bands(obs2, cfg, band)
        out["cutoffs"][str(B)] = res
    return out
