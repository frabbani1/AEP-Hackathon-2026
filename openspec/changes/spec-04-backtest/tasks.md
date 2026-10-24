## 1. Setup and split

- [ ] 1.1 Create `backend/concierge/backtest/` and `backend/tests/backtest/`; add a synthetic fixture (12 circuits, two storms, repair days with a backlog tail, unrepaired rows)
- [ ] 1.2 Implement `split.py` with separate S1 and S2 objects and a guard that raises if forecast code reads S2 damage; test with the random-S2 replacement from the spec
- [ ] 1.3 Load `damage_fact`, `circuit_dim`, `device_dim`, and spec-02 circuit rates; test row counts against `docs/data_findings.md` (1,659 rows, S1 193, S2 1,254)

## 2. Risk evaluation

- [ ] 2.1 Re-run the spec-02 forward backtest through shared functions; test that it equals `artifacts/risk/calibration.json`
- [ ] 2.2 Implement base rate (premise-weighted and per circuit), lift, difference, and the "beats base rate" rule per band; unit tests per rule
- [ ] 2.3 Implement the circuit-size baseline with the same group sizes
- [ ] 2.4 Implement the seeded permutation test (10,000 shuffles) and the bootstrap AUC and Spearman; test determinism and known values on a tiny hand-computed example
- [ ] 2.5 Implement sensitivity runs (tau 1/2/5, reverse, drop 3 largest, min size 100); test that the stored structure matches the primary one

## 3. Restoration evaluation

- [ ] 3.1 Implement observed per-circuit statistics (median, P90) with backlog cutoff `B`, censoring of unrepaired rows, and suppression of circuits with fewer than 10 records; test each exclusion counter
- [ ] 3.2 Implement circuit-level calls into the spec-03 engine returning the pooled job-completion distribution; test on the fixture tree against hand values
- [ ] 3.3 Implement the oracle-damage and forecast-damage runs
- [ ] 3.4 Implement the S1-only fit of `k` with the identifiability rule and the "uncalibrated" fallback; test sparse-data and rich-data fixtures; test that permuting S2 leaves `k` unchanged
- [ ] 3.5 Implement coverage (P50, P90, with backlog as misses), MAE, within-one-day, and the three baselines with the paired bootstrap; test the tie case reports "does not beat"
- [ ] 3.6 Run at `B = 3` and `B = 7` and record excluded shares for S1 and S2

## 4. Verdicts and plots

- [ ] 4.1 Implement `verdicts.py` with the D6 rule table; one test per rule and per level
- [ ] 4.2 Implement the stdlib SVG writer and the three plots; test that files parse as XML and contain no forbidden identifiers
- [ ] 4.3 Implement `report.py` writing `backtest.json` and `docs/backtest.md`, including the fixed limits list, `config_hash`, input hashes, and seed
- [ ] 4.4 Test that a losing-model fixture renders "does not beat" and no false `supports`

## 5. Script and serving

- [ ] 5.1 Add `scripts/run_backtest.py` (`--seed`, `--cutoff-days`, `--sample`); ignore raw per-circuit outputs in `.gitignore`, keep the aggregate JSON and `docs/backtest.md` reviewable
- [ ] 5.2 Add `GET /evidence` to `api/handler.py` and `api/service.py` reading `backtest.json`; test the identifier scan and the limits list
- [ ] 5.3 Document the route in `docs/api.md`

## 6. Verification

- [ ] 6.1 Run on real data. Record in `design.md`: base rate, lift per band, permutation p, AUC with interval, fitted `k` or "not identifiable", coverage P50 and P90 for both runs, MAE against each baseline
- [ ] 6.2 Read the generated verdicts with the team. If the model does not beat a baseline, keep that sentence in the demo script and the README
- [ ] 6.3 Upload `backtest.json` and plots to the clean bucket `serving/` prefix only after review
