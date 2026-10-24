"""Rule-generated verdicts (design D6). No free text from a person or a model: each sentence is a fixed template that
is filled with computed numbers. A `supports` level is only built inside the branch where its rule fires."""
from __future__ import annotations

from collections.abc import Mapping

from .config import BacktestConfig

SUPPORTS, MIXED, NO_SUPPORT, NOT_TESTABLE = "supports", "mixed", "does_not_support", "not_testable"
LEVELS = (SUPPORTS, MIXED, NO_SUPPORT, NOT_TESTABLE)

BASELINE_NAMES = {"constant": "the Sep 2024 median", "circuit_median": "the Sep 2024 circuit median",
                  "one_day": "a flat one day"}


def _pct(x: float) -> str:
    return f"{round(100 * x)}%"


def _range(ci) -> str:
    return f"{_pct(ci[0])} to {_pct(ci[1])}"


def _v(code: str, level: str, sentence: str) -> dict:
    assert level in LEVELS
    return {"code": code, "level": level, "sentence": sentence}


def _band(risk: Mapping, name: str) -> Mapping | None:
    b = risk["bands"][name]
    return None if b.get("suppressed") or b["hit_rate"] is None else b


def _not_testable(code: str, what: str) -> dict:
    return _v(code, NOT_TESTABLE, f"{what} could not be tested because too few circuits or records were available.")


def risk_low_vs_base(risk: Mapping) -> dict:
    code, low = "risk_low_vs_base", _band(risk, "Low")
    base = risk["base_rate"]["premise_weighted"]
    if low is None:
        return _not_testable(code, "Whether Low-risk areas had less damage than average")
    text = (f"Low-risk areas: {_pct(low['hit_rate'])} of homes were on lines with significant damage, against "
            f"{_pct(base)} across all areas (95% range {_range(low['ci95'])}).")
    if low["ci95"][1] < base:
        return _v(code, SUPPORTS, text + " This is clearly below the base rate.")
    return _v(code, MIXED, text + " The range reaches the base rate, so the model does not clearly beat the base rate here.")


def risk_high_vs_base(risk: Mapping) -> dict:
    code, high = "risk_high_vs_base", _band(risk, "High")
    base = risk["base_rate"]["premise_weighted"]
    if high is None:
        return _not_testable(code, "Whether High-risk areas had more damage than average")
    text = (f"High-risk areas: {_pct(high['hit_rate'])} of homes were on lines with significant damage, against "
            f"{_pct(base)} across all areas (95% range {_range(high['ci95'])}).")
    if high["ci95"][0] > base:
        return _v(code, SUPPORTS, text + " This is clearly above the base rate.")
    return _v(code, MIXED, text + " The range reaches the base rate, so the model does not clearly beat the base rate here.")


def risk_medium_vs_high(risk: Mapping) -> dict:
    code, med, high = "risk_medium_vs_high", _band(risk, "Medium"), _band(risk, "High")
    if med is None or high is None:
        return _not_testable(code, "Whether High areas differ from Medium areas")
    if high["ci95"][0] > med["ci95"][1]:
        return _v(code, SUPPORTS, "High-risk areas had clearly more damage than Medium-risk areas.")
    if med["ci95"][0] > high["ci95"][1]:
        return _v(code, NO_SUPPORT, "Medium-risk areas had clearly more damage than High-risk areas, the wrong way round.")
    return _v(code, MIXED, f"Medium ({_pct(med['hit_rate'])}) and High ({_pct(high['hit_rate'])}) are not "
                           "distinguishable: their ranges overlap.")


def risk_vs_size(risk: Mapping) -> dict:
    code = "risk_vs_size"
    m, s = risk["spread"], risk["size_baseline"]["spread"]
    if m["value"] is None or s["value"] is None:
        return _not_testable(code, "Whether the bands beat a circuit-size baseline")
    text = (f"The gap between High and Low areas is {round(100 * m['value'])} percentage points for the risk bands and "
            f"{round(100 * s['value'])} when circuits are simply ranked by size.")
    if m["ci95"][0] > s["ci95"][1]:
        return _v(code, SUPPORTS, text + " The risk bands beat the size baseline by more than both ranges.")
    if m["value"] <= s["value"]:
        return _v(code, NO_SUPPORT, text + " The risk bands do not beat the circuit-size baseline.")
    return _v(code, MIXED, text + " The risk bands do not beat the circuit-size baseline by more than the uncertainty.")


def risk_permutation(risk: Mapping, cfg: BacktestConfig) -> dict:
    code, p = "risk_permutation", risk["permutation"]["p_value"]
    if p is None:
        return _not_testable(code, "Whether the High-minus-Low gap could be chance")
    if p < cfg.permutation_alpha:
        return _v(code, SUPPORTS, f"The High-minus-Low gap is unlikely to be chance (permutation p = {p:.3f}).")
    return _v(code, NO_SUPPORT, f"The High-minus-Low gap could be chance (permutation p = {p:.3f}).")


def rest_calibrated(rest: Mapping | None) -> dict:
    code = "rest_calibrated"
    fit = (rest or {}).get("fit")
    if fit and fit["status"] == "fitted":
        return _v(code, SUPPORTS, f"Crews were fitted on Sep 2024 only ({fit['crews']} crews) and then fixed for Feb 2025.")
    if fit and fit["status"] == "not_identifiable":
        return _v(code, NOT_TESTABLE, f"Sep 2024 has {fit['records']} usable repair records on {fit['circuits_with_enough_records']} "
                  "circuits, too few to fit the number of crews. The run uses a default of "
                  f"{fit['crews']} crews and is uncalibrated.")
    return _v(code, NOT_TESTABLE, "The number of crews could not be fitted because the restoration engine was not run.")


def rest_coverage_p90(rest: Mapping | None, cfg: BacktestConfig) -> dict:
    code, run = "rest_coverage_p90", ((rest or {}).get("runs") or {}).get("forecast")
    if not run or run["coverage_p90"] is None:
        return _v(code, NOT_TESTABLE, "How often repairs finished within the predicted P90 was not tested.")
    c = run["coverage_p90"]
    text = (f"{_pct(c)} of repairs finished within the predicted P90 ({_pct(run['coverage_p90_backlog_as_miss'])} if "
            "backlog repairs count as misses).")
    if cfg.coverage_p90_low <= c <= cfg.coverage_p90_high:
        return _v(code, SUPPORTS, text + " That is close to the 90% a P90 should reach.")
    return _v(code, NO_SUPPORT, text + " That is not close to the 90% a P90 should reach.")


def rest_vs_baseline(rest: Mapping | None) -> dict:
    code, run = "rest_vs_baseline", ((rest or {}).get("runs") or {}).get("forecast")
    rows = {k: v.get("model_vs_baseline") for k, v in (run or {}).get("baselines", {}).items()}
    rows = {k: v for k, v in rows.items() if v}
    if not rows:
        return _v(code, NOT_TESTABLE, "Whether the restoration range beats simple baselines was not tested.")
    beaten = [k for k, v in rows.items() if v["beats"]]
    lost = [k for k in rows if k not in beaten]
    names = lambda ks: ", ".join(BASELINE_NAMES[k] for k in ks)  # noqa: E731
    if not lost:
        return _v(code, SUPPORTS, f"The restoration range has lower error than every baseline ({names(beaten)}), "
                                  "and the 95% range of each difference is below zero.")
    if not beaten:
        return _v(code, NO_SUPPORT, f"The restoration range does not beat any simple baseline ({names(lost)}). "
                                    "The 95% range of each error difference includes zero or favors the baseline.")
    return _v(code, MIXED, f"The restoration range beats {names(beaten)} but does not beat {names(lost)}.")


def rest_resolution() -> dict:
    return _v("rest_resolution", NOT_TESTABLE,
              "Repair times are recorded in whole days, so the finest claim is about one day.")


def build_verdicts(risk: Mapping, restoration: Mapping | None, cfg: BacktestConfig) -> list[dict]:
    """`risk` is the redacted primary result; `restoration` is run_restoration() output."""
    primary_b = str(cfg.cutoffs()[0])
    rest = ((restoration or {}).get("cutoffs") or {}).get(primary_b)
    return [
        risk_low_vs_base(risk), risk_high_vs_base(risk), risk_medium_vs_high(risk), risk_vs_size(risk),
        risk_permutation(risk, cfg), rest_calibrated(rest), rest_coverage_p90(rest, cfg), rest_vs_baseline(rest),
        rest_resolution(),
    ]
