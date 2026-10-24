"""Restoration-time estimate. Deterministic: every number here comes from this module, never from the LLM.

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

from ..risk.confidence import downgrade

ENGINE_VERSION = "restoration-v1"
LEVELS = ("feeder", "lateral", "service")
STAGE_LABEL = {
    "feeder": "Main line repairs finish",
    "lateral": "Branch line repairs finish",
    "service": "Repairs near your home finish and your power returns",
}

DEFAULT_CREWS = 3
MAX_CREWS = 100
MAX_JOBS_PER_ASSET = 20
ACCESS_LEVELS = ("normal", "restricted", "remote")
ACCESS_ADDER_KEY = {"normal": None, "restricted": "access_constraint_adder", "remote": "remote_location_adder"}

WORKING_HOURS_PER_DAY = 16.0            # crews rest 8 of every 24 hours
CLOCK_FACTOR = 24.0 / WORKING_HOURS_PER_DAY
MIN_EXPECTED_EVENTS = 0.05              # a line with no damage history is treated as having a tiny chance of damage

# Range around the typical estimate by confidence (low factor, high factor). Skewed up: past repairs had a long tail.
SPREAD = {"High": (0.75, 1.4), "Medium": (0.65, 1.6), "Low": (0.5, 2.0)}

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
_TOTAL = sum(ASSET_COUNTS.values())


class RestorationError(ValueError):
    """Bad scenario input or a labor sheet that cannot price a repair."""


def _unit_hours(labor: Mapping, spec) -> float:
    cat, key = (spec, None) if isinstance(spec, str) else spec
    for u in labor["units"]:
        if u["category"] == cat and (key is None or key in (u.get("size_group"), u.get("size_kva"))):
            return (u["install_hours"] or 0.0) + (u["remove_hours"] or 0.0)
    raise RestorationError(f"labor sheet has no unit for {spec!r}")


def _adder_hours(labor: Mapping, access: str) -> float:
    key = ACCESS_ADDER_KEY[access]
    if key is None:
        return 0.0
    for a in labor["adders"]:
        if a["key"] == key and a["hours"] is not None:
            return float(a["hours"])
    raise RestorationError(f"labor sheet has no adder {key!r}")


def job_hours(asset: str, labor: Mapping, access: str) -> float:
    """Crew-hours for one repair job, including the indirect labor multiplier."""
    direct = sum(_unit_hours(labor, s) for s in ASSET_LABOR[asset]) + _adder_hours(labor, access)
    return direct * float(labor["indirect_labor_multiplier"])


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


def display(lo: int, hi: int) -> str:
    """Plain-language range. Switches to days for long outages."""
    if hi < 36:
        if lo == hi:
            return f"about {hi} hour{'s' if hi != 1 else ''}"
        return f"about {lo} to {hi} hours"
    lo_d, hi_d = max(1, round(lo / 24)), max(1, round(hi / 24))
    if lo_d == hi_d:
        return f"about {hi_d} day{'s' if hi_d != 1 else ''}"
    return f"about {lo_d} to {hi_d} days"


def estimate(*, token: str | None, row: Mapping | None, severity: Mapping, fallback: Mapping | None,
             base_confidence: str | None, labor: Mapping, crews: int = DEFAULT_CREWS, access: str = "normal",
             damage: Sequence[tuple[str, int]] | None = None) -> dict:
    """Restoration estimate for one premise. `row` is the premise record (risk fields plus cn, n0, nl, nf)."""
    if not (1 <= crews <= MAX_CREWS):
        raise RestorationError(f"crews must be between 1 and {MAX_CREWS}")
    if access not in ACCESS_LEVELS:
        raise RestorationError(f"access must be one of {list(ACCESS_LEVELS)}")
    for asset, qty in damage or ():
        if asset not in ASSET_LEVEL or not (1 <= qty <= MAX_JOBS_PER_ASSET):
            raise RestorationError("damage items need a known asset_type and a quantity from 1 to "
                                   f"{MAX_JOBS_PER_ASSET}")

    out = {"engine_version": ENGINE_VERSION, "premise_token": token, "severity": dict(severity),
           "scenario": {"crews": crews, "access": access,
                        "damage_source": "you_provided" if damage else "simulated"},
           "estimate": None, "steps": [], "sensitivity": [], "confidence": None, "assumptions": [],
           "missing_data": [], "could_change": [], "model_note": None, "fallback": None}

    if row is None or row.get("st") != "ok" or not all(k in row for k in ("cn", "n0", "nl", "nf", "R")):
        fb = dict(fallback or {"reason_code": "no_network_context",
                               "message": "We don't have the line details needed for this location."})
        fb["message"] = fb["message"] + " We also can't estimate how long restoration would take."
        out["status"] = "insufficient_data"
        out["fallback"] = fb
        return out

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


def downgrade_level(level: str) -> str:
    return downgrade(level)
