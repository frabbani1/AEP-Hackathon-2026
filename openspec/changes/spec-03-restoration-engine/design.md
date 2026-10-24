## Context

The estimate has to answer "how long if I lose power?" with a range a judge can audit and a slider can move.
The data supports this only in part, so the design is explicit about what is measured and what is assumed.

Inputs (checked against the repo on 2026-10-24):

| Input | Source | Fields used |
| --- | --- | --- |
| Labor per unit and adders | `artifacts/curated/labor_standards.json` (30 units) | `install_hours`, `remove_hours`; adders `access_constraint_adder` 0.5 h, `remote_location_adder` 1.312 h (both "per job"); `indirect_labor_multiplier` 4.5 |
| Device tree | `device_dim` (32,600 devices) | `parent_key`, `depth`, `is_protective`, `n_premises_served`, `small_group`, `circuit` |
| Premise path | `premise_dim` | `transformer_key`, `circuit`, `hops`, `protective_count` |
| Damage rate | risk rows (`R` reports per 1,000 homes per average storm, `cn` homes on the line) | from spec-02 |
| Historical asset mix | `damage_fact` (1,644 mapped rows) | `asset_type` |

Facts that shape the design:
- ARCOS has one row per damaged asset and no quantity field. We treat one row as one repair job.
- `n_premises_served` is the customer count under a device. It is the "customers restored" figure for ordering.
- 60 of 32,600 devices have more than one parent (alternate feeds). We use the first parent and flag it.
- The labor sheet gives point values only. There is no spread, so the uncertainty below is an assumption, not a fit.
  Spec-04 checks the result against observed repair days.
- Observed repair time in ARCOS is report-to-repair days per damaged asset. It is not customer outage length.
- `restoration-v1` already exists in `model.py` (three fixed levels, analytic, confidence-scaled spread).

## Goals / Non-Goals

**Goals:**
- Price each repair job from the labor sheet, including the access adder and the 4.5 multiplier.
- Order jobs upstream-first, then by customers restored per hour, using the device tree.
- Return P50 to P90 from a seeded Monte Carlo over labor-hour and crew-size uncertainty.
- Recompute on any input change in well under a second and return the same answer for the same inputs.
- State assumptions and label the result "estimate, not confirmed".

**Non-Goals:**
- Live crew locations, travel routing, weather, or road conditions.
- Predicting which homes lose power. The estimate is conditional on the customer losing power.
- Calibration of the uncertainty parameters (spec-04 reports how well they hold up).
- Alert timing and wording.

## Decisions

### D1. Job list
A job is one damaged asset at one tree node.

```
job.hours = (unit_hours(asset) + access_adder(access)) x M          M = 4.5
unit_hours(asset) = sum over hardware in ASSET_LABOR[asset] of (install_hours + remove_hours)
```

`ASSET_LABOR` and `ASSET_LEVEL` stay as in v1 (for example `pole` -> Medium pole, `transformer` -> 50 kVA,
`primary_line` -> crossarm + insulator + pin + dead end). They are engineering assumptions and are listed in the
response. The adder applies once per job, before the multiplier, matching the sheet's "per job" unit.
The multiplier covers travel and non-productive time, so we add no separate travel term.

### D2. Where damage comes from
Three sources, in this order of precedence. Supplying more than one of `damage_count` and `damage` is a 400.

| Mode | Trigger | Job count | Asset types | Placement |
| --- | --- | --- | --- | --- |
| Simulated | neither given | `Poisson(E_c)` per trial, `E_c = R x cn / 1000 x m` | drawn from the historical mix | uniform over circuit nodes of the matching level |
| Damage count | `damage_count = D` (1 to 400) | exactly `D` | historical mix | same as above |
| Customer path | `damage = [{asset_type, qty}]` | the list | as given | on the customer's own path, nothing elsewhere (v1 meaning) |

Placement by level uses `ASSET_LEVEL`: each asset type maps to `feeder`, `lateral` or `service`, and a job lands on a
uniformly chosen node of that level. The build assigns each node a level from its `device_class`, `is_protective` and
`depth` (task 2.1 fixes the exact rule after inspecting `device_dim`; it is not decided here). Uniform placement is an
assumption: line length would be a better weight, but per-node length is not in the curated tables.
Conditional on losing power: if a trial puts no job on the customer's path, one service-level job is added on it.
This is stated in assumptions. It keeps the answer "if you lose power", not "whether you do".

### D3. Step restoration order
Per trial, jobs are scheduled on one pooled resource of `c_eff` crews (see D5).

1. A node's jobs are **available** when no strict ancestor node still has an unfinished job. This is the
   upstream dependency: a lateral cannot restore anyone while the main line above it is still down.
2. Among available jobs, pick the highest **priority = n_served(node) / job.hours** (customers restored per
   crew-hour). Ties break by shallower depth, then node index.
3. Elapsed clock hours for a customer = cumulative `job.hours` scheduled up to and including the last job on
   their path, divided by `c_eff`, times `CLOCK_FACTOR = 24 / 16 = 1.5` (crews work 16 of 24 hours).
4. Stop scheduling once the customer's last path job is done.

`n_served(node)` is an upper bound on customers restored by that job, because other jobs in the subtree may still
block them. The index is a priority heuristic, not a promise of optimal dispatch. Stated in assumptions.

This is a fluid model: the pool works down one priority list. It matches "labor hours / crews" and it ignores the
limit that two crews cannot work the same job. It underestimates time when few jobs are available at once.
Listed under limitations. A discrete-crew simulation is a possible later change.

### D4. Tree artifact
`scripts/build_risk_artifacts.py` (or a new `build_restoration_tree`) writes `serving/risk/restoration_tree.json`:

```json
{"version": "tree-v1", "circuits": {"<circuit_idx>": {"parent": [-1,0,0,1], "depth": [0,1,1,2],
  "served": [3100,1800,900,40], "level": ["feeder","feeder","lateral","service"]}}}
```

- Arrays only. No device keys, circuit labels, or coordinates. `circuit_idx` is an opaque integer, not the circuit id.
- Nodes serving fewer than 10 premises are merged into their parent (project rule: suppress groups under 10).
- Each premise row gets `pn`, the node index of its transformer. The customer's path is the parent chain from `pn`.
- Rows without `pn` fall back to the v1 level model and are flagged `model_path = "v1_levels"`.
- The file lives in the clean bucket under a prefix CloudFront does not serve (same rule as `premise_risk.jsonl`).

### D5. Monte Carlo
Each trial draws, in this fixed order (so the stream is the same across scenarios):

| Draw | Distribution | Default | Why |
| --- | --- | --- | --- |
| Shared labor factor `L` | lognormal, mean 1, sigma by confidence: High 0.25, Medium 0.30, Low 0.40 | | crew productivity and sheet error hit every job together |
| Per-job factor `l_j` | lognormal, mean 1, sigma 0.25 | | individual jobs run long or short |
| Crew availability | each of `crews` crews is present with probability 0.85; `c_eff = max(1, present)` | | crews declared are not always working |
| Damage count (Simulated mode only) | `Poisson(E_c)` | | expected damage is a mean, not a fixed number |

Job hours in a trial are `job.hours x L x l_j`. `c_eff` uses the first `crews` uniform draws, so more crews never
lowers `c_eff` on the same stream (common random numbers). The same coupling is used for the sensitivity runs.

Percentiles: nearest-rank on sorted trial results, rounded to whole hours, minimum 1. `p50_hours <= p90_hours`.
Trials: `min(1000, max(200, 200_000 // n_jobs))`. The response reports `trials`.
Seed: first 8 bytes of SHA-256 over `(engine_version, token, multiplier, crews, access, damage inputs)`.
The same inputs give the same output. A changed input gives a new seed, which is why sensitivity runs reuse the
base seed instead.

All sigma, probability, and trial values are assumptions in `restoration/config.py` and are echoed in the response.
They are not fitted. Spec-04 reports the observed coverage of P50 and P90 so the claim can be checked.

### D6. Output contract (restoration section of the fact card)

```json
{
  "engine_version": "restoration-v2",
  "status": "ok",
  "label": "estimate, not confirmed",
  "model_note": "This is a model estimate, not a restoration time confirmed by the utility.",
  "scenario": {"crews": 3, "access": "normal", "damage_source": "simulated", "damage_count": 9},
  "estimate": {"p50_hours": 6, "p90_hours": 14, "low_hours": 6, "typical_hours": 6, "high_hours": 14,
               "display": "about 6 to 14 hours",
               "meaning": "Half of our simulated outcomes finish within 6 hours. Nine in ten finish within 14 hours."},
  "steps": [{"stage": "feeder", "label": "Main line repairs finish", "done_by_hours": 3}],
  "sensitivity": [{"change": "One more crew", "p50_hours": 5}, {"change": "Hard-to-reach work sites", "p50_hours": 8}],
  "confidence": {"level": "Medium", "reason": "Based on past damage reports on your line."},
  "assumptions": ["…"], "missing_data": ["…"], "could_change": ["…"],
  "simulation": {"trials": 1000, "seed_policy": "derived from inputs", "model_path": "tree"},
  "fallback": null
}
```

- `low_hours = p50_hours`, `typical_hours = p50_hours`, `high_hours = p90_hours` are a one-release shim for
  alerts, guidance and messages. The shim and its removal are tracked in tasks.
- `display` rule from v1: hours below 36, otherwise days.
- Customer copy must read the range as "likely about P50, could take up to P90". The low end is not a floor.
- Sensitivity entries: one more crew, one fewer crew (if `crews > 1`), hard access (if access is normal),
  and 25% more damage (if not Customer path mode). Each uses 200 trials on the base seed.
- Fallback: same as v1. If the risk row is not `ok` or has no network fields, return `insufficient_data` with the
  risk fallback message plus "We also can't estimate how long restoration would take."

### D7. Privacy
Counts under 10 are merged at build time and never returned. The response carries hours, stage labels, and
assumptions only. No node numbers, `served` counts, circuit ids, or device identifiers. The existing identifier scan
in `service.py` runs on the result. The tree file is excluded from CloudFront.

### D8. Code layout
```
backend/concierge/restoration/
  config.py       constants (M, working hours, sigmas, crew probability, trial rules, damage caps)
  jobs.py         ASSET_LABOR, ASSET_LEVEL, job_hours(), job generation by mode
  schedule.py     availability + priority scheduler for one trial
  montecarlo.py   draws, seeding, percentiles, sensitivity with common random numbers
  model.py        estimate(): validation, fallback, assembly of the D6 contract
scripts/build_risk_artifacts.py   adds restoration_tree.json and `pn` per premise
```

## Risks / Trade-offs

- [Uncertainty is assumed, not fitted] -> Echoed in every response. Spec-04 reports coverage and says when it fails.
- [Uniform placement of damage] -> Stated. Line length per node is the obvious upgrade.
- [Fluid crew model ignores parallelism limits] -> Listed as a limitation; the range is wider than a point estimate.
- [Clock starts when crews begin] -> No assessment or mobilization delay is included. Listed in `missing_data`.
- [Python Monte Carlo speed] -> Trial cap rule and an early stop in D3. Profiling is a task. Fall back to fewer trials, never to a silent v1 answer.
- [Priority index ignores other blockers] -> Heuristic, stated.
- [Alternate feeds] -> 60 devices have two parents; first parent used. Premises flagged `alternate_path` get a note.

## Migration Plan

1. Ship the tree artifact and `pn` on premise rows. Reload DynamoDB (`make load-table`) after review.
2. Ship v2 behind `RESTORATION_ENGINE=v2`. Keep v1 selectable until the demo check passes.
3. Remove the shim fields once spec-05 and spec-06 read `p50_hours` and `p90_hours`.
4. No cloud change is applied without review (project rule).

## Open Questions

- Should a fixed assessment delay be added before crews start (for example 2 hours), or stay out until real data exists?
- Is `p = 0.85` crew availability acceptable, or should the judge see a crew-reliability control?
- Should the customer-facing range be P50 to P90 (per the brief) or P25 to P90, which avoids a low end that is half-likely to be beaten?
