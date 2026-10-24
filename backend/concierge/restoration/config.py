"""Constants for the restoration engine (spec-03, design D1 to D6).

Everything here is an assumption or a cap, not a fitted value. The labor hours themselves are NOT here: they come
from the labor sheet (`labor_standards.json`), including the indirect labor multiplier (read at run time, never
hard-coded). Spec-04 reports how well these assumptions hold up against observed repairs.
"""
from __future__ import annotations

import os

ENGINE_VERSION = "restoration-v2"
V1_ENGINE_VERSION = "restoration-v1"
TREE_VERSION = "tree-v1"

# ---- engine selection (task 6.3) -----------------------------------------------------------------------------------
ENGINE_ENV = "RESTORATION_ENGINE"
ENGINES = ("v1", "v2")
DEFAULT_ENGINE = "v2"           # flipped from "v1" after the integration checks in design.md passed (task 7.3)


def engine_from_env(environ=None) -> str:
    """The configured engine. An unknown value is an error, so a typo cannot silently pick the other engine."""
    value = (environ if environ is not None else os.environ).get(ENGINE_ENV, DEFAULT_ENGINE)
    if value not in ENGINES:
        raise ValueError(f"{ENGINE_ENV} must be one of {list(ENGINES)}")
    return value


# ---- D1: pricing ---------------------------------------------------------------------------------------------------
# The multiplier (4.5) is read from the labor sheet at this key. It covers travel and non-productive time, so the
# model adds no separate travel term.
MULTIPLIER_KEY = "indirect_labor_multiplier"
ACCESS_LEVELS = ("normal", "restricted", "remote")
ACCESS_ADDER_KEY = {"normal": None, "restricted": "access_constraint_adder", "remote": "remote_location_adder"}

# ---- D3: working day -----------------------------------------------------------------------------------------------
WORKING_HOURS_PER_DAY = 16.0            # crews rest 8 of every 24 hours
CLOCK_FACTOR = 24.0 / WORKING_HOURS_PER_DAY

# ---- D5: uncertainty (assumptions, not fitted) -----------------------------------------------------------------------
# Shared labor factor L: lognormal with mean 1. It hits every job together (crew productivity, sheet error).
SHARED_SIGMA = {"High": 0.25, "Medium": 0.30, "Low": 0.40}
# Per-job factor: lognormal with mean 1. Individual jobs run long or short.
JOB_SIGMA = 0.25
# Each declared crew is present with this probability; at least one crew always works.
CREW_PRESENT_PROBABILITY = 0.85

# Trials = min(TRIALS_MAX, max(TRIALS_MIN, TRIAL_BUDGET // n_jobs)). Design D5 started at a budget of 200,000; task 5.5
# lowered it to 60,000 after profiling the largest circuit under the strongest storm (see design.md, "Verification").
TRIALS_MAX = 1000
TRIALS_MIN = 200
TRIAL_BUDGET = 60_000
# The what-if runs that change the jobs (hard access, more damage) use min(SENSITIVITY_TRIALS, budget // n_jobs) trials,
# at least SENSITIVITY_MIN_TRIALS. They are measured on matched trials, so a smaller sample is enough.
SENSITIVITY_TRIALS = 200
SENSITIVITY_BUDGET = 20_000
SENSITIVITY_MIN_TRIALS = 50
SENSITIVITY_DAMAGE_FACTOR = 1.25

PERCENTILES = (50, 90)

# ---- inputs and caps -----------------------------------------------------------------------------------------------
DEFAULT_CREWS = 3
MAX_CREWS = 100
DAMAGE_COUNT_MIN = 1
DAMAGE_COUNT_MAX = 400
MAX_JOBS_PER_ASSET = 20
MAX_DAMAGE_ITEMS = 10
# Simulated damage is capped at the same number as a user-entered count, so a very exposed line cannot make one call slow.
SIMULATED_JOBS_CAP = DAMAGE_COUNT_MAX
MIN_EXPECTED_EVENTS = 0.05              # a line with no damage history is treated as having a tiny chance of damage

# Poisson draws use exact inversion up to this mean and a normal approximation above it (see montecarlo.py).
POISSON_EXACT_MAX = 50.0

# ---- D4: tree ------------------------------------------------------------------------------------------------------
LEVELS = ("feeder", "lateral", "service")
MIN_SERVED = 10                         # project rule: suppress groups under 10 premises
FEEDER_MAX_DEPTH = 1                    # tree-v1 node-level rule, see docs/restoration-tree.md

LABEL = "estimate, not confirmed"
MODEL_NOTE = "This is a model estimate, not a restoration time confirmed by the utility."
