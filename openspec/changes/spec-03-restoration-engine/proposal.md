## Why

The second customer question is "How long could restoration take?". The challenge wants a range, not a promise,
built from damage type and quantity, upstream dependencies, step restoration, labor hours, travel and
non-productive time. It must update when damage, crews, access, or network assumptions change, and it must say
that the output is a model estimate, not confirmed utility information.

A first version (`restoration-v1`, `backend/concierge/restoration/model.py`) already exists but is not under a
spec. It is analytic: it splits expected damage into three levels (main line, branch, near-home), prices each
level with the labor sheet, and widens the range by a fixed factor per confidence level. That has three gaps
against the brief:

- The range is a fixed multiplier, not a simulation, so it does not respond to labor or crew uncertainty.
- Step restoration is a fixed three-level order. It does not use the topology to pick the next job by
  customers restored per hour.
- The judge cannot set a damage count directly. Only a per-asset list is accepted.

## What Changes

- Add `restoration-v2`: build a job list (damaged assets), price each job as
  `(labor hours per unit + access adder) x 4.5 indirect multiplier`, divide by crews, and schedule jobs
  upstream-first by customers restored per hour using the device tree.
- Replace the fixed spread with a seeded Monte Carlo over labor-hour and crew-size uncertainty. Report P50 to P90.
- Inputs: severity, damage count, crews, access toggle. Any change recomputes immediately.
- Show the assumptions (crews, multiplier, working hours, hardware mapping) in every response and label the
  output "estimate, not confirmed".
- Return what moves the answer: a sensitivity list (one more crew, one fewer, hard access, more damage).
- Keep the insufficient-data fallback from the risk engine.

Out of scope: live crew positions, road or weather data, calibration against observed repairs (spec-04),
alert timing (spec-05), message wording (spec-06), any UI, and any claim that a time is confirmed.

## Capabilities

### New Capabilities
- `restoration-estimate`: job pricing, topology-ordered step restoration, Monte Carlo range (P50 to P90),
  scenario inputs, assumptions, labeling, and fallback.

### Modified Capabilities
None. There are no main specs yet. `restoration-v1` code exists and is brought under this spec.

## Impact

- Rewrites `backend/concierge/restoration/model.py` and adds `backend/concierge/restoration/{jobs,schedule,montecarlo}.py`.
- Adds a build step that writes a compact per-circuit device tree (`restoration_tree`) to the serving artifacts.
  It contains no device IDs, only integer node numbers, parent numbers, depth and customer counts.
- Response contract changes: `estimate` gains `p50_hours` and `p90_hours`. `low_hours`, `typical_hours`, `high_hours`
  stay for one release so alerts, guidance and messages keep working, then are removed in spec-05 and spec-06 work.
- New request field `damage_count` on `/restoration`, `/guidance`, `/alerts`, `/messages`, `/summary`.
- Depends on spec-01 curated tables (`device_dim`, `premise_dim`, `labor_standards.json`) and spec-02 risk rows.
- Runtime stays pure Python (no numpy) inside the existing 512 MB / 20 s Lambda. Target under 300 ms per call.
