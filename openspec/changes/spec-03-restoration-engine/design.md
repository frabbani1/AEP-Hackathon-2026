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
Conditional on losing power: if a trial puts no job on the customer's path, the trial is made to have one.
This is stated in assumptions. It keeps the answer "if you lose power", not "whether you do".

> **Amended during implementation (task 3.2).** The first version added one service-level job on the customer's own
> node. That job has the lowest priority (few customers, many hours), so it waits behind every other available job, and
> trials that already had a main-line job on the path finish early. The result is two clusters of outcomes, and the P50
> fell as damage grew for some premises: on 150 real premises, 9 had a lower P50 at `damage_count = 40` than at 5 (by up to
> 9 hours), against the "more damage raises the estimate" requirement. It also made a stronger storm shorten the
> estimate on lines with little damage, because an added service job (about 10 hours of work) costs more than the
> typical drawn repair. The rule now is: if no job is on the path and the trial has jobs, one drawn job (chosen by a
> uniform draw) is moved to the customer's path node of its own level, so the job count never changes (a Damage count run
> still has exactly `D` jobs). If the trial has no jobs, one job is drawn from the historical mix and placed the same
> way. On 300 real premises this gives no violation in any of these checks: `damage_count` 5 to 40 (and each step 5, 10, 20, 40),
> crews 3 to 6, restricted and remote access, and the four severity presets. Customer path mode is unchanged.

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
  The row also gets `pc`, the opaque circuit index, because node indexes are local to a circuit (amended: the artifact
  is keyed by circuit index, so a row needs both to find its place). `ap = 1` marks a premise on an alternate feed.
- Rows without `pn` fall back to the v1 level model and are flagged `model_path = "v1_levels"`. The fallback rebuilds
  the three-level path of v1 (breaker, farthest protective device, nearest protective device, transformer, and the rest
  of the line as a sibling) from `cn`, `nf`, `nl` and `n0` as a small tree, so it uses the same Monte Carlo and supports
  every input. It does not return the v1 analytic numbers.
- Node levels use the rule in `docs/restoration-tree.md`: a distribution transformer is `service`; any other device at
  depth 0 or 1 is `feeder`; deeper devices are `lateral`. A level with no nodes on a circuit uses the next level up.
- Merging prunes whole subtrees in almost every case (a parent serves at least as many premises as each child). A
  surviving node whose resolved parent was merged (possible on alternate feeds) is re-attached to its nearest surviving
  ancestor. A circuit whose breaker serves fewer than 10 premises gets no tree and its premises get no position.
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

> **Amended during implementation (tasks 5.1 to 5.5).**
> - *Streams.* Each trial seeds two generators from `(seed, trial number)`: stream A (labor factor `L`, one uniform for the
>   Poisson count, two uniforms for the on-path rule, then two uniforms per job: one picks the asset type and, from its
>   position inside that type's slice, the placement; one picks the per-job factor from a table of 4,096 lognormal
>   quantiles rescaled to mean 1) and stream B (one uniform per declared crew). Nothing after a variable-length part
>   depends on its length, so a longer job list or more crews only appends draws. The Poisson count is inverted from one
>   uniform (exact up to a mean of 50, normal approximation above), so a higher mean never gives fewer jobs.
> - *Seed.* The seed is the first 8 bytes of SHA-256 over the engine version and the premise token only. Hashing crews,
>   access and damage too gave every slider position fresh noise, so the headline range could rise when a crew was added
>   (at high crew counts one more crew changes the work per crew by about 2%, which sampling noise between seeds can
>   exceed). With one seed per premise, a slider move
>   reuses every draw it did not change, so more crews never raise the P50 and restricted access never lowers it. The
>   output is still a pure function of the request.
> - *Sensitivity.* One pass scores the base crew count and one more or fewer on the same jobs and the same crew draws, over
>   all trials. "Hard-to-reach work sites" and "25% more damage" redraw the jobs on the base seed for
>   `min(200, max(50, 20,000 // jobs))` trials and are reported as the base P50 plus the change measured on the trials both
>   runs share.
> - *Trial rule.* `min(1000, max(200, 60,000 // jobs))`. The 200,000 budget in the first draft took 290 ms on this machine for
>   the heaviest premise (see Verification).
> - *Percentile rounding.* Nearest-rank on the sorted trial values, then round half up to whole hours, minimum 1.
> - *Medium versus High.* Low is at least as wide as High in every checked case. Medium can be one hour narrower than
>   High because a larger shared spread lowers the median of a mean-1 lognormal as well as widening it.

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
- `scenario.damage_count` is the number of jobs per outcome (the Poisson mean, at least 1, in Simulated mode). The
  assumptions also carry a `damage_source: ...` line. `steps` lists a main-line or branch stage only when its median
  finish time is above zero (it is part of the typical outcome); the last step is always the customer's P50.
  Every response is checked against a key whitelist (`restoration/schema.py`) and the identifier scan.

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

## Verification (tasks 5.5, 7.1, 7.2)

Run on 2026-10-24 with artifacts built from `data/` by `scripts/curate.py` and `scripts/build_risk_artifacts.py` (local
salt, so the premises below are described, not named). Machine: Apple M4 laptop, CPython 3.12.10, one process.
`scripts/profile_restoration.py` reproduces the latency numbers and `--cases --severity typical` the table.

**Artifacts.** 105 circuits and 6,581 nodes in `restoration_tree.json` (121 KB), 115,052 scored premises, every one with a
valid `pc` and `pn`. 5 circuits (33 premises) are below the 10-premise rule and have no tree.

**Latency (task 5.5).** One `estimate()` call includes its sensitivity runs. Severity `beyond-observed` (x2.5) unless noted.

| Case | Jobs per outcome | Trials | p50 | p95 | max |
| --- | --- | --- | --- | --- | --- |
| 400 random premises, simulated | up to 280 | 214 to 1,000 | 22 ms | 52 ms | 60 ms |
| 20 heaviest premises, simulated (largest circuit, 3,527 homes) | up to 280 | 214 | 49 ms | 56 ms | 56 ms |
| same, 1 crew / 100 crews | up to 280 | 214 | 49 / 50 ms | 58 / 57 ms | 58 / 57 ms |
| largest circuit, `damage_count = 400` | 400 | 200 | 39 ms | 39 ms | 39 ms |
| largest circuit, `damage_count = 200` | 200 | 300 | 45 ms | 46 ms | 46 ms |
| 100 random premises, `damage_count = 40` | 40 | 1,000 | 43 ms | 47 ms | 50 ms |

The first draft (budget 200,000, no one-pass crews runs, a normal draw per job) took 293 ms at p50 and 321 ms at p95 for the
heaviest premises. Three changes brought it down: a table lookup per job factor, one pass for the crew what-ifs, and the
60,000 trial budget. **Not measured:** the deployed Lambda. At 512 MB a Lambda gets a fraction of a core, so the
300 ms p95 target on Lambda is not confirmed by these numbers; if it is missed, the lever is the function's memory size
(an infrastructure change that needs review), not the model.

**Scenario table (task 7.1).** Typical storm, Medium-confidence premise with the median expected damage in each risk
band. Each cell is P50 to P90 hours, 1,000 trials.

| Band | Expected jobs | Base (3 crews) | 6 crews | Restricted | Remote | `damage_count` 5 | `damage_count` 40 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Low | 4 | 3 to 13 | 2 to 7 | 5 to 16 | 7 to 21 | 3 to 17 | 9 to 57 |
| Medium | 6 | 3 to 19 | 2 to 9 | 5 to 27 | 9 to 36 | 4 to 20 | 20 to 90 |
| High | 9 | 3 to 9 | 1 to 4 | 4 to 11 | 7 to 15 | 3 to 10 | 4 to 11 |

The ranges move the right way: more crews lower both ends, harder access raises both, and more damage raises the P90
(the P50 rises where the customer is not near the top of the line). The High-band premise sits at the top of its line, so
extra damage elsewhere barely reaches it. Wide upper ends (a P90 of 90 hours for 40 jobs) are the model saying that a customer
low on a long line can wait behind many repairs.

`backend/tests/restoration/test_realdata.py` repeats the direction checks for one premise per band and for 40 random
premises, and the identifier scan plus key whitelist over 200 random premises (zero findings). A separate run on 300 random
premises found no violation of: `damage_count` 5 < 10 < 20 < 40 (P50, one-hour tolerance), crews 3 to 6, restricted
and remote access, and the four severity presets.

**Trial counts.** 1,000 up to 60 jobs, 983 at 61, 300 at 200 jobs, 200 from 300 jobs. The "25% more damage" and "hard-to-reach"
runs use 200 trials up to 100 jobs, 100 at 200 jobs and 50 from 400 jobs.

## Open Questions

- Should a fixed assessment delay be added before crews start (for example 2 hours), or stay out until real data exists?
- Is `p = 0.85` crew availability acceptable, or should the judge see a crew-reliability control?
- Should the customer-facing range be P50 to P90 (per the brief) or P25 to P90, which avoids a low end that is half-likely to be beaten?
