"""Restoration-time estimate, version 1 (frozen). Kept selectable with RESTORATION_ENGINE=v1 until v2 is the default.

Deterministic: every number here comes from this module, never from the LLM. Golden tests pin its output
(backend/tests/restoration/test_v1_golden.py), so change nothing here except to fix a bug.

How the estimate works (each assumption is also returned to the caller):

1. Expected damage on the customer's line = (damage reports in the past storms / number of storms) x severity.
   In the risk artifacts that is  R x N / 1000 x m  (R = reports per 1,000 homes per average storm, N = homes on the line).
2. Damage is split into three levels along the path from the home to the substation, using the mix of asset types in
   the historical ARCOS damage reports:  main line ("feeder"), branch ("lateral") and near-home ("service") repairs.
3. Only damage on the customer's own path matters to them. The share of a level's damage that lands on their path is
   the share of homes their device at that level serves (nf / N, nl / N, n0 / N).
4. Crews restore upstream first (step restoration): all main-line repairs on the line come before branch repairs,
   which come before near-home repairs. So the main-line repairs elsewhere on the line are a queue ahead of the customer.
5. Labor: each repair replaces the hardware listed in ASSET_LABOR. Hours = (install + removal hours from the STORMS
   labor sheet + any access adder) x the indirect labor multiplier (travel and nonproductive time).
6. Elapsed time = labor hours / crews, then scaled from working hours to clock hours (crews work WORKING_HOURS_PER_DAY
   of every 24).
7. The range widens when the evidence behind the estimate is thinner (confidence level).

The asset -> hardware mapping and the level mapping are engineering assumptions that need review by a utility engineer.
Counts of homes served (n0, nl) are used only inside the calculation; groups under 10 homes are never returned.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .common import RestorationError, display
from .config import (ACCESS_LEVELS, CLOCK_FACTOR, DEFAULT_CREWS, LEVELS, MAX_CREWS, MAX_JOBS_PER_ASSET,
                     MIN_EXPECTED_EVENTS, V1_ENGINE_VERSION, WORKING_HOURS_PER_DAY)
from .jobs import ASSET_COUNTS, ASSET_LEVEL, job_hours

ENGINE_VERSION = V1_ENGINE_VERSION
STAGE_LABEL = {
    "feeder": "Main line repairs finish",
    "lateral": "Branch line repairs finish",
    "service": "Repairs near your home finish and your power returns",
}

# Range around the typical estimate by confidence (low factor, high factor). Skewed up: past repairs had a long tail.
SPREAD = {"High": (0.75, 1.4), "Medium": (0.65, 1.6), "Low": (0.5, 2.0)}

_TOTAL = sum(ASSET_COUNTS.values())


def level_profile(labor: Mapping, access: str) -> dict[str, dict[str, float]]:
    """Share of historical damage and mean job hours per level."""
    out = {}
    for lv in LEVELS:
        assets = [a for a, l in ASSET_LEVEL.items() if l == lv and ASSET_COUNTS.get(a)]
        w = sum(ASSET_COUNTS[a] for a in assets)
        out[lv] = {"share": w / _TOTAL,
                   "hours": sum(ASSET_COUNTS[a] * job_hours(a, labor, access) for a in assets) / w}
    return out


def _core(row: Mapping, multiplier: float, labor: Mapping, crews: int, access: str,
          damage: Sequence[tuple[str, int]] | None) -> dict:
    """Workload by level and cumulative clock hours."""
    floor_used = False
    if damage:
        events = {lv: 0.0 for lv in LEVELS}
        work = {lv: 0.0 for lv in LEVELS}
        for asset, qty in damage:
            lv = ASSET_LEVEL[asset]
            events[lv] += qty
            work[lv] += qty * job_hours(asset, labor, access)
    else:
        n_line = float(row["cn"])
        exposure = {"feeder": min(1.0, row["nf"] / n_line), "lateral": min(1.0, row["nl"] / n_line),
                    "service": min(1.0, row["n0"] / n_line)}
        prof = level_profile(labor, access)
        e_line = float(row["R"]) * n_line / 1000.0 * multiplier
        if e_line < MIN_EXPECTED_EVENTS:
            e_line, floor_used = MIN_EXPECTED_EVENTS, True
        own = {lv: e_line * prof[lv]["share"] * exposure[lv] for lv in LEVELS}
        total_own = sum(own.values())
        scale = max(1.0, 1.0 / total_own) if total_own > 0 else 1.0     # an outage needs at least one repair
        own = {lv: v * scale for lv, v in own.items()}
        queue = e_line * prof["feeder"]["share"] * (1.0 - exposure["feeder"])
        events = dict(own)
        events["feeder"] += queue
        work = {lv: events[lv] * prof[lv]["hours"] for lv in LEVELS}

    stages, cum = [], 0.0
    for lv in LEVELS:
        if work[lv] <= 0:
            continue
        cum += work[lv] / crews * CLOCK_FACTOR
        stages.append((lv, cum))
    return {"events": events, "work": work, "stages": stages, "clock_hours": cum, "floor_used": floor_used}


def _hours(x: float) -> int:
    return max(1, int(round(x)))


def has_line_context(row: Mapping | None) -> bool:
    """A scored premise with the exposure counts both engines need."""
    return row is not None and row.get("st") == "ok" and all(k in row for k in ("cn", "n0", "nl", "nf", "R"))


def insufficient_data(out: dict, fallback: Mapping | None) -> dict:
    """The fallback answer, shared by both engines so the wording and shape stay identical."""
    fb = dict(fallback or {"reason_code": "no_network_context",
                           "message": "We don't have the line details needed for this location."})
    fb["message"] = fb["message"] + " We also can't estimate how long restoration would take."
    out["status"] = "insufficient_data"
    out["fallback"] = fb
    return out


def validate(crews: int, access: str, damage: Sequence[tuple[str, int]] | None) -> None:
    if not (1 <= crews <= MAX_CREWS):
        raise RestorationError(f"crews must be between 1 and {MAX_CREWS}")
    if access not in ACCESS_LEVELS:
        raise RestorationError(f"access must be one of {list(ACCESS_LEVELS)}")
    for asset, qty in damage or ():
        if asset not in ASSET_LEVEL or not (1 <= qty <= MAX_JOBS_PER_ASSET):
            raise RestorationError("damage items need a known asset_type and a quantity from 1 to "
                                   f"{MAX_JOBS_PER_ASSET}")


def estimate(*, token: str | None, row: Mapping | None, severity: Mapping, fallback: Mapping | None,
             base_confidence: str | None, labor: Mapping, crews: int = DEFAULT_CREWS, access: str = "normal",
             damage: Sequence[tuple[str, int]] | None = None) -> dict:
    """Restoration estimate for one premise. `row` is the premise record (risk fields plus cn, n0, nl, nf)."""
    validate(crews, access, damage)

    out = {"engine_version": ENGINE_VERSION, "premise_token": token, "severity": dict(severity),
           "scenario": {"crews": crews, "access": access,
                        "damage_source": "you_provided" if damage else "simulated"},
           "estimate": None, "steps": [], "sensitivity": [], "confidence": None, "assumptions": [],
           "missing_data": [], "could_change": [], "model_note": None, "fallback": None}

    if not has_line_context(row):
        return insufficient_data(out, fallback)

    core = _core(row, float(severity["multiplier"]), labor, crews, access, damage)
    typical = core["clock_hours"]
    level = base_confidence or "Low"
    if core["floor_used"]:
        level = "Low"
    lo_f, hi_f = SPREAD[level]
    lo, mid, hi = _hours(typical * lo_f), _hours(typical), _hours(typical * hi_f)
    lo, hi = min(lo, mid), max(hi, mid)

    sens = [("One more crew", crews + 1), ("One fewer crew", crews - 1)]
    out["sensitivity"] = [
        {"change": label, "typical_hours": _hours(_core(row, float(severity["multiplier"]), labor, c, access,
                                                        damage)["clock_hours"])}
        for label, c in sens if 1 <= c <= MAX_CREWS]
    if access == "normal":
        out["sensitivity"].append({
            "change": "Hard-to-reach work sites",
            "typical_hours": _hours(_core(row, float(severity["multiplier"]), labor, crews, "restricted",
                                          damage)["clock_hours"])})

    out["status"] = "ok"
    out["estimate"] = {"low_hours": lo, "typical_hours": mid, "high_hours": hi, "display": display(lo, hi)}
    out["steps"] = [{"stage": lv, "label": STAGE_LABEL[lv], "done_by_hours": _hours(cum)}
                    for lv, cum in core["stages"]]
    out["confidence"] = {"level": level,
                         "reason": "Based on past damage reports on your line." if not damage
                         else "Based on the damage you entered, not on past reports."}
    out["assumptions"] = [
        f"{crews} repair crews work on your line, {int(WORKING_HOURS_PER_DAY)} hours out of every 24.",
        "Crews fix main line damage first, then branch lines, then repairs near homes (step restoration).",
        f"Labor hours come from the STORMS reference sheet times a {labor['indirect_labor_multiplier']:g}x multiplier "
        "for travel and non-productive time.",
        "Which hardware a repair replaces is an engineering assumption that has not been reviewed by the utility.",
    ]
    if access != "normal":
        out["assumptions"].append(f"Work sites are {access} to reach, which adds time to every repair.")
    if damage:
        out["assumptions"].append("Only the damage you entered is counted, and no other repairs are ahead of yours.")
    else:
        out["assumptions"].append("Damage is simulated from past storms on your line and the storm scenario you chose, "
                                  "and your outage is assumed to need at least one repair.")
    out["missing_data"] = [
        "No live crew locations, road conditions, or weather are used.",
        "Damage reports show repairs, not which homes lost power.",
        "Size of the damaged equipment is not known, so typical sizes are used.",
    ]
    if core["floor_used"]:
        out["missing_data"].append("Your line has no damage history, so the estimate is a rough placeholder.")
    out["could_change"] = [
        "How many crews are sent, and how soon they can start.",
        "Whether trees, flooding or blocked roads slow the work.",
        "Damage that is found only after crews arrive.",
    ]
    out["model_note"] = "This is a model estimate, not a restoration time confirmed by the utility."
    return out
