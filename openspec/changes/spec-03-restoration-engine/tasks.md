## 1. Setup and baseline

- [ ] 1.1 Add `restoration/config.py` with every constant in design D1 to D6 (multiplier source, 16-hour day, sigmas by confidence, per-job sigma 0.25, crew probability 0.85, trial rule, caps)
- [ ] 1.2 Freeze v1 behavior: add golden tests for the current `estimate()` output on 3 synthetic rows so regressions in unchanged paths (fallback, validation, display) are visible
- [ ] 1.3 Build a synthetic tree fixture (1 circuit, 8 nodes, depths 0 to 3) and 4 premise rows with `pn`

## 2. Tree artifact

- [ ] 2.1 Inspect `device_class`, `device_type`, `depth` and `is_protective` values in `device_dim`, write the node-level rule (feeder, lateral, service) in `docs/`, then add the `restoration_tree` builder: arrays of parent, depth, served, level per circuit; first parent for multi-parent devices
- [ ] 2.2 Merge nodes with `served < 10` into the parent; test that no node below 10 survives and totals are preserved
- [ ] 2.3 Add `pn` to each premise row in `premise_risk.jsonl` and DynamoDB load; test that every `in_topology` premise has a valid `pn`
- [ ] 2.4 Write `restoration_tree.json` to `serving/risk/` and exclude it from the CloudFront-served prefixes; test that no device key or circuit label appears in the file

## 3. Jobs and pricing

- [ ] 3.1 Implement `jobs.py` (`job_hours`, asset and level maps) reading the multiplier from the labor sheet; test restricted and remote adders against `labor_standards.json` values
- [ ] 3.2 Implement job generation for Simulated, Damage count, and Customer path modes with fixed-order draws; test counts, level placement, and the forced on-path service job
- [ ] 3.3 Reject both `damage_count` and `damage`; validate ranges; test each 400 message is plain and contains no input echo beyond the field name

## 4. Scheduler

- [ ] 4.1 Implement availability (no unfinished strict ancestor) and the priority heap; test the blocked-lateral and higher-ratio scenarios from the spec
- [ ] 4.2 Implement early stop at the customer's last path job and per-level `done_by_hours`; test on the fixture tree by hand-computed values
- [ ] 4.3 Property tests (hypothesis): elapsed time is non-decreasing in job hours and non-increasing in crews; a customer with no jobs on the path gets the forced job

## 5. Monte Carlo

- [ ] 5.1 Implement seeded draws (shared labor factor, per-job factor, crew availability, Poisson damage) with the fixed draw order from D5
- [ ] 5.2 Implement nearest-rank P50 and P90, trial-count rule, and the response `simulation` block; test determinism and `p50 <= p90`
- [ ] 5.3 Implement sensitivity runs with common random numbers; test that "One more crew" never raises P50
- [ ] 5.4 Test that Low confidence gives a range at least as wide as High on the same seed
- [ ] 5.5 Profile on the largest real circuit with `beyond-observed` severity; record p95 latency; adjust the trial rule until under 300 ms

## 6. Output and integration

- [ ] 6.1 Assemble the D6 contract in `model.py`, including label, meaning text, assumptions, missing data, could-change, and the v1 shim fields
- [ ] 6.2 Add `damage_count` to request parsing in `api/service.py` and `docs/api.md`; unknown fields stay rejected
- [ ] 6.3 Gate by `RESTORATION_ENGINE` (default `v1` until 7.3 passes); keep fallback behavior identical to v1
- [ ] 6.4 Run the identifier scan and key whitelist over restoration responses for 200 random real premises; zero findings

## 7. Verification

- [ ] 7.1 Integration test on real artifacts: score one premise per risk band, change crews 3 -> 6, access normal -> restricted, `damage_count` 5 -> 40, and show the range moves in the expected direction
- [ ] 7.2 Record in `design.md` the real P50 and P90 for those cases, the p95 latency, and the trial counts
- [ ] 7.3 Flip the default to `v2` after 7.1 and 7.2 pass; update `README.md` limits section
- [ ] 7.4 After spec-05 and spec-06 read `p50_hours` and `p90_hours`, remove the shim fields and their test
