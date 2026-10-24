"""Restoration estimate, version 2 (spec-03), and the switch between v1 and v2.

Every number comes from this package, never from the LLM. The v2 estimate answers "if this customer loses power,
how long could it take?" with a range:

1. Damage is a list of repair jobs, each priced from the labor sheet: (install + removal hours + access adder) x the
   indirect labor multiplier (`jobs.py`). Jobs come from the circuit's damage rate (Simulated), from a count the
   caller enters (Damage count), or from a damage list that sits on the customer's own path (Customer path).
2. Jobs land on the circuit's device tree (`tree.py`) and are scheduled upstream-first, then by customers restored
   per crew-hour (`schedule.py`). The customer's time is the work done until their last path repair, spread over the
   crews at work and scaled from working hours to clock hours.
3. A seeded Monte Carlo varies labor hours and crews at work (`montecarlo.py`). The response reports the 50th and
   90th percentile as `p50_hours` and `p90_hours`, the assumptions behind them, and what would move the answer.

`RESTORATION_ENGINE=v1` keeps the frozen first version (`v1.py`). The v2 spreads and probabilities are assumptions
(`config.py`), not fitted values; spec-04 reports how well they hold up.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

from ..risk.confidence import downgrade
from . import config, v1
from .common import RestorationError, display
from .config import (ACCESS_LEVELS, DEFAULT_CREWS, ENGINE_VERSION, LEVELS, MAX_CREWS, MAX_JOBS_PER_ASSET,
                     WORKING_HOURS_PER_DAY)
from .jobs import ASSET_LEVEL, job_hours, make_plan, multiplier
from .montecarlo import (derive_seed, paired_p50, percentiles, run_trials, run_trials_multi, summarize,
                         sensitivity_trial_count, trial_count, whole_hours)
from .schema import validate_output
from .tree import RestorationTree, levels_tree
from .v1 import STAGE_LABEL

__all__ = ["ACCESS_LEVELS", "ASSET_LEVEL", "DEFAULT_CREWS", "ENGINE_VERSION", "LEVELS", "MAX_CREWS",
           "MAX_JOBS_PER_ASSET", "RestorationError", "display", "downgrade_level", "estimate", "validate"]


def validate(crews, access, damage, damage_count) -> None:
    """Input checks. Messages name the field and the allowed range and never repeat what the caller sent."""
    v1.validate(crews, access, damage)
    if damage_count is not None:
        if damage:
            raise RestorationError("send either damage_count or damage, not both")
        if (isinstance(damage_count, bool) or not isinstance(damage_count, int)
                or not (config.DAMAGE_COUNT_MIN <= damage_count <= config.DAMAGE_COUNT_MAX)):
            raise RestorationError(f"damage_count must be a whole number from {config.DAMAGE_COUNT_MIN} "
                                   f"to {config.DAMAGE_COUNT_MAX}")


def estimate(*, token: str | None, row: Mapping | None, severity: Mapping, fallback: Mapping | None,
             base_confidence: str | None, labor: Mapping, crews: int = DEFAULT_CREWS, access: str = "normal",
             damage: Sequence[tuple[str, int]] | None = None, damage_count: int | None = None,
             tree: RestorationTree | None = None, engine: str | None = None) -> dict:
    """Restoration estimate for one premise. `row` is the premise record (risk fields plus line context)."""
    engine = engine or config.engine_from_env()
    if engine == "v1":
        if damage_count is not None:
            raise RestorationError("damage_count is not available with the v1 restoration engine")
        return v1.estimate(token=token, row=row, severity=severity, fallback=fallback,
                           base_confidence=base_confidence, labor=labor, crews=crews, access=access, damage=damage)
    if engine != "v2":
        raise RestorationError("unknown restoration engine")
    return _estimate_v2(token=token, row=row, severity=severity, fallback=fallback, base_confidence=base_confidence,
                        labor=labor, crews=crews, access=access, damage=damage or None, damage_count=damage_count,
                        tree=tree)


# ---- v2 ------------------------------------------------------------------------------------------------------------
def _hours_text(h: int) -> str:
    if h < 36:
        return f"{h} hour{'s' if h != 1 else ''}"
    d = max(1, round(h / 24))
    return f"{d} day{'s' if d != 1 else ''}"


def _meaning(p50: int, p90: int) -> str:
    a, b = _hours_text(p50), _hours_text(p90)
    lead = f"Likely about {a}, and it could take up to {b}." if p50 != p90 else f"Likely about {a}."
    return (f"{lead} The low end is a likely time, not a guarantee. "
            f"Half of our simulated outcomes finish within {a}. Nine in ten finish within {b}.")


def _more_damage(plan, source: str):
    """The plan with 25% more damage, or None when the scenario has no room for it."""
    if source == "simulated":
        return replace(plan, mean_jobs=plan.mean_jobs * config.SENSITIVITY_DAMAGE_FACTOR)
    if source == "count" and plan.count < config.DAMAGE_COUNT_MAX:
        more = max(plan.count + 1, math.ceil(plan.count * config.SENSITIVITY_DAMAGE_FACTOR))
        return replace(plan, count=min(config.DAMAGE_COUNT_MAX, more))
    return None


def _assumptions(*, crews, access, labor, level, source, count, model_path, row) -> list[str]:
    s_shared = round(config.SHARED_SIGMA[level] * 100)
    s_job = round(config.JOB_SIGMA * 100)
    out = [
        f"{crews} crew{'s are' if crews != 1 else ' is'} assumed to work {int(WORKING_HOURS_PER_DAY)} hours out of "
        "every 24.",
        f"Each crew is assumed to be present in {round(config.CREW_PRESENT_PROBABILITY * 100)}% of simulated "
        "outcomes, and at least one crew always works.",
        "Crews fix upstream damage first. Among repairs that are ready, the one that restores the most customers "
        "per crew-hour goes first (step restoration).",
        f"Labor hours come from the STORMS reference sheet times a {multiplier(labor):g}x multiplier for travel and "
        "non-productive time.",
        "Which hardware a repair replaces is an engineering assumption that has not been reviewed by the utility.",
        f"Repair time varies between simulated outcomes: all repairs share one spread of about {s_shared}% (wider "
        f"when less evidence backs your line) and each repair adds its own spread of about {s_job}%. These spreads "
        "are assumptions, not fitted to observed repairs.",
        "Damage is spread evenly across the sections of your line that match the type of damage, because the length "
        "of each section is not known.",
        "The clock starts when crews start work. Damage assessment and mobilization time beyond the multiplier are "
        "not included.",
    ]
    if access != "normal":
        out.append(f"Work sites are {access} to reach, which adds time to every repair.")
    if source == "path":
        out.append("damage_source: you_provided. Only the damage you entered is counted, and it is placed on your "
                   "own path with no other repairs ahead of yours.")
    elif source == "count":
        out.append(f"damage_source: you_provided. Every simulated outcome has {count} repair jobs, drawn from the mix "
                   "of past damage. Your outage is assumed to need at least one repair on your own path.")
    else:
        out.append("damage_source: simulated. Damage is simulated from past storms on your line and the storm "
                   "scenario you chose, and your outage is assumed to need at least one repair on your own path.")
    if row.get("ap"):
        out.append("Your home can also be fed from another line. The estimate uses the usual feed.")
    if model_path == "v1_levels":
        out.append("Detailed line data is not available for this location, so your line is modeled as a main line, "
                   "a branch and the equipment near your home.")
    return out


def _estimate_v2(*, token, row, severity, fallback, base_confidence, labor, crews, access, damage, damage_count,
                 tree) -> dict:
    validate(crews, access, damage, damage_count)
    source = "path" if damage else "count" if damage_count is not None else "simulated"
    out = {"engine_version": ENGINE_VERSION, "premise_token": token, "status": None, "label": None,
           "model_note": None, "severity": dict(severity),
           "scenario": {"crews": crews, "access": access,
                        "damage_source": "simulated" if source == "simulated" else "you_provided",
                        "damage_count": None},
           "estimate": None, "steps": [], "sensitivity": [], "confidence": None, "assumptions": [],
           "missing_data": [], "could_change": [], "simulation": None, "fallback": None}

    if not v1.has_line_context(row):
        return validate_output(v1.insufficient_data(out, fallback))

    located = tree.locate(row) if tree is not None else None
    model_path = "tree" if located else "v1_levels"
    circuit, pn = located or levels_tree(row)

    sev_mult = float(severity["multiplier"])
    floor_used, mean_jobs = False, 0.0
    if source == "simulated":
        mean_jobs = float(row["R"]) * float(row["cn"]) / 1000.0 * sev_mult
        if mean_jobs < config.MIN_EXPECTED_EVENTS:
            mean_jobs, floor_used = config.MIN_EXPECTED_EVENTS, True
    plan = make_plan(circuit=circuit, pn=pn, labor=labor, access=access, source=source, damage=damage,
                     count=damage_count or 0, mean_jobs=mean_jobs)

    level = "Low" if floor_used else (base_confidence or "Low")
    sigma = config.SHARED_SIGMA[level]
    seed = derive_seed(engine_version=ENGINE_VERSION, token=token)
    trials = trial_count(plan.expected_jobs)
    # One pass scores the base crew count and one more / one fewer on the same jobs and the same crew draws, so the
    # what-ifs for crews are measured on every trial and can only move in the expected direction.
    crew_counts = [crews] + ([crews + 1] if crews < MAX_CREWS else []) + ([crews - 1] if crews > 1 else [])
    by_crews = run_trials_multi(plan, crews=crew_counts, seed=seed, trials=trials, sigma_shared=sigma)
    base = by_crews[crews]
    summary = summarize(base)
    p50, p90 = summary["p50_hours"], summary["p90_hours"]

    def p50_of(results) -> int:
        return whole_hours(percentiles(results.total)[0])

    # The other what-ifs change the jobs, so they run again on the base seed for fewer trials and are measured on
    # the trials they share with the base run (montecarlo.paired_p50).
    n_sens = sensitivity_trial_count(plan.expected_jobs, trials)
    sensitivity = []
    if crews < MAX_CREWS:
        sensitivity.append({"change": "One more crew", "p50_hours": p50_of(by_crews[crews + 1])})
    if crews > 1:
        sensitivity.append({"change": "One fewer crew", "p50_hours": p50_of(by_crews[crews - 1])})
    variants = []
    if access == "normal":
        variants.append(("Hard-to-reach work sites",
                         replace(plan, prices=tuple(job_hours(a, labor, "restricted") for a in plan.assets))))
    more = _more_damage(plan, source)
    if more is not None:
        variants.append(("25% more damage", more))
    for label, vplan in variants:
        res = run_trials(vplan, crews=crews, seed=seed, trials=n_sens, sigma_shared=sigma)
        sensitivity.append({"change": label, "p50_hours": paired_p50(base, res, summary["p50_raw"])})

    steps = []
    for lv, median in (("feeder", summary["feeder_median"]), ("lateral", summary["lateral_median"])):
        if median > 0:          # a stage appears when it is part of the typical outcome
            steps.append({"stage": lv, "label": STAGE_LABEL[lv], "done_by_hours": whole_hours(median)})
    steps.append({"stage": "service", "label": STAGE_LABEL["service"], "done_by_hours": p50})

    out.update(
        status="ok", label=config.LABEL, model_note=config.MODEL_NOTE,
        estimate={"p50_hours": p50, "p90_hours": p90,
                  # one-release shim for alerts, guidance and messages (task 7.4 removes it)
                  "low_hours": p50, "typical_hours": p50, "high_hours": p90,
                  "display": display(p50, p90), "meaning": _meaning(p50, p90)},
        steps=steps, sensitivity=sensitivity,
        confidence={"level": level,
                    "reason": "Based on past damage reports on your line." if source == "simulated"
                    else "Based on the damage you entered, not on past reports."},
        simulation={"trials": trials, "seed_policy": "derived from inputs", "model_path": model_path},
    )
    out["scenario"]["damage_count"] = max(1, int(round(plan.expected_jobs)))     # an outage needs a repair
    out["assumptions"] = _assumptions(crews=crews, access=access, labor=labor, level=level, source=source,
                                      count=damage_count, model_path=model_path, row=row)
    out["missing_data"] = [
        "No live crew locations, road conditions, or weather are used.",
        "Damage reports show repairs, not which homes lost power.",
        "Size of the damaged equipment is not known, so typical sizes are used.",
        "No time for damage assessment or crew mobilization is included.",
        "The length of each line section is not known, so damage is spread evenly across sections.",
    ]
    if floor_used:
        out["missing_data"].append("Your line has no damage history, so the estimate is a rough placeholder.")
    out["could_change"] = [
        "How many crews are sent, and how soon they can start.",
        "Whether trees, flooding or blocked roads slow the work.",
        "Damage that is found only after crews arrive.",
        "Where on your line the damage actually lands.",
    ]
    return validate_output(out)


def downgrade_level(level: str) -> str:
    return downgrade(level)
