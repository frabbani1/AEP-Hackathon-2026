## Why

The challenge asks for explainability and responsible use: confidence, assumptions, and honest limits. We have
numbers for risk bands and restoration ranges, but no one has checked them against what actually happened. Spec-02
already stores a Sep 2024 -> Feb 2025 hit-rate table per band. It does not compare the bands to a base rate, it does not
test whether the result could be chance, and nothing checks the restoration range against observed repair days.

Before the demo, a judge should be able to ask "did this work?" and get a straight answer, including "partly" or
"no". The data makes some answers weak, and the report must say so:

- Only two storms exist. Sep 2024 has 193 damage rows; Feb 2025 has 1,254.
- In Sep 2024, 149 of 193 rows are repairs that took 7 days or more (backlog). Only 44 are normal repairs.
  In Feb 2025 only 4 of 1,254 are that slow.
- Crew counts were not recorded, so "crews" is a free parameter, not an observation.
- ARCOS records repairs of damaged assets, not customer outages. Repair time is not outage length.
- Repair time is a whole number of days, so nothing finer than about one day can be claimed.

## What Changes

- Add an offline backtest that calibrates on Sep 2024 and predicts Feb 2025 at circuit level.
- Risk: report hit rate per band against the base rate, with lift, Wilson intervals, a permutation test, a
  circuit-size baseline, and a rank metric (AUC). Reuse the spec-02 hit definition and thresholds.
- Restoration: compare predicted P50 and P90 repair time with observed repair days per circuit, with a scatter
  plot, coverage of P50 and P90, and error against simple baselines. Treat repairs beyond about 3 days as backlog
  and report them separately.
- Generate the verdict text from fixed rules, so the report cannot claim more than the numbers show. It must say
  so when the model does not beat the baseline.
- Publish aggregate results only (JSON, SVG plots, `docs/backtest.md`). No circuit ids.

Out of scope: changing the risk or restoration models to improve the scores, training any new model, weather data,
premise-level validation (not possible: no outage labels), live monitoring, and UI work beyond serving the summary.

## Capabilities

### New Capabilities
- `model-backtest`: leak-free Sep 2024 -> Feb 2025 evaluation of risk bands and restoration ranges, baselines,
  uncertainty, plots, and rule-generated honest verdicts.

### Modified Capabilities
None. Spec-02 and spec-03 outputs are read, not changed.

## Impact

- New package `backend/concierge/backtest/` and script `scripts/run_backtest.py`.
- New outputs: `artifacts/backtest/backtest.json`, `artifacts/backtest/*.svg`, generated `docs/backtest.md`.
- Optional read-only route `GET /evidence` that returns the aggregate summary so the UI can show it.
- Depends on spec-02 artifacts (`calibration.json`, circuit rates), spec-03 engine (`restoration-v2`), and the
  curated `damage_fact`, `circuit_dim`, `device_dim` tables.
- No new runtime dependencies. Plots are written as SVG by a small stdlib writer.
