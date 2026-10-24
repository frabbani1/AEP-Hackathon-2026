## ADDED Requirements

### Requirement: Leak-free calibrate-on-S1, test-on-S2 protocol
The backtest SHALL calibrate only on the Sep 2024 storm (S1) and evaluate on the Feb 2025 storm (S2) at circuit level.
Predictions for S2 MUST use only S1 damage records, static network and labor tables, and the S2 severity preset.
No S2 damage-row value (repair days, counts, or asset types, except in the explicitly labeled oracle-damage run) MAY
influence a forecast prediction.

#### Scenario: Altering S2 rows does not change forecast predictions
- **WHEN** every S2 damage row is replaced with random values
- **THEN** the forecast-damage predictions are identical

#### Scenario: Risk part matches the stored spec-02 table
- **WHEN** the backtest re-runs the spec-02 forward evaluation
- **THEN** its per-band hit rates equal the values in `calibration.json`, otherwise the run fails

### Requirement: Risk bands compared with a base rate
For each band the backtest SHALL report the premise-weighted hit rate, a Wilson 95% interval using circuits as n, the
base rate over all evaluated circuits, the lift (`p / b`), and the difference (`p - b`). The base rate SHALL be
recomputed on every run and reported both premise-weighted and per circuit.

#### Scenario: Base rate present
- **WHEN** the backtest finishes
- **THEN** `backtest.json` contains the base rate, and each band has hit rate, interval, lift, and difference

#### Scenario: High band only beats the base rate when its interval clears it
- **WHEN** the High band's Wilson lower bound is at or below the base rate
- **THEN** the verdict for High is `mixed`, not `supports`

### Requirement: Baselines and significance for risk bands
The backtest SHALL report a circuit-size baseline, a seeded permutation test on the High-minus-Low hit-rate gap
(10,000 shuffles), the AUC of the S1 score against S2 hits with a bootstrap interval, and the Spearman correlation of
S1 and S2 damage rates.

#### Scenario: Permutation test is reproducible
- **WHEN** the backtest runs twice with the same seed
- **THEN** the p-value is identical

#### Scenario: Model that does not beat the size baseline is reported as such
- **WHEN** the band spread does not exceed the size-baseline spread by more than both intervals
- **THEN** the `risk_vs_size` verdict level is `mixed` or `does_not_support` and the report text says so

### Requirement: Sensitivity analysis
The backtest SHALL report risk results for tau of 1, 2, and 5 reports per 1,000 premises, the reverse direction
(S2 to S1), the removal of the three largest circuits, and a minimum circuit size of 100. Results SHALL be stored
next to the primary result.

#### Scenario: Reverse direction reported
- **WHEN** the backtest finishes
- **THEN** `backtest.json` has a reverse-direction table with the same fields as the forward table

### Requirement: Restoration range compared with observed repair days
The backtest SHALL compare the predicted P50 and P90 repair time per S2 circuit with the observed median and P90 of
`repair_days`, for two runs: oracle damage (actual S2 asset counts) and forecast damage (S1 rate scaled to S2
severity). It SHALL report P50 and P90 coverage, circuit-level mean absolute error in days, and the share of circuits
within one day.

#### Scenario: Both runs reported
- **WHEN** the backtest finishes
- **THEN** each restoration metric appears once for the oracle run and once for the forecast run

#### Scenario: Small circuits suppressed
- **WHEN** a circuit has fewer than 10 repaired non-backlog S2 records
- **THEN** it is excluded from the restoration metrics and counted in `excluded_small`

### Requirement: Crews treated as a free parameter fitted on S1 only
The backtest SHALL fit one effective-crews value on S1 non-backlog records and keep it fixed for S2. If fewer than
30 records or fewer than 8 circuits with at least 3 records are available, the fit SHALL be declared not identifiable,
the default `crews = 3` SHALL be used, and the restoration result SHALL be labeled "uncalibrated". An in-sample S2
fit MAY be shown only as a ceiling labeled "not a test".

#### Scenario: Sparse S1 data
- **WHEN** S1 provides 44 non-backlog records spread over fewer than 8 qualifying circuits
- **THEN** the fit is `not_identifiable`, the run uses `crews = 3`, and the report says "uncalibrated"

#### Scenario: Fitted value is not tuned on S2
- **WHEN** the S2 data is permuted
- **THEN** the fitted crews value does not change

### Requirement: Backlog is excluded from scoring and reported
The backtest SHALL treat repairs taking more than the cutoff `B` days as backlog (primary `B = 3`, sensitivity
`B = 7`), SHALL exclude them from range scoring, and SHALL report the share excluded for each storm and cutoff.
It SHALL also report P90 coverage with backlog counted as misses. Unrepaired rows SHALL be excluded as censored and counted.

#### Scenario: Backlog share reported for both cutoffs
- **WHEN** the backtest finishes
- **THEN** the report lists the excluded share for S1 and S2 at `B = 3` and `B = 7`

#### Scenario: Unrepaired rows are not treated as zero days
- **WHEN** a row has `repaired = false`
- **THEN** it is excluded from observed repair statistics and counted under `censored`

### Requirement: Baselines and a bootstrap test for restoration
The backtest SHALL compare circuit-level error with a constant baseline (S1 global median), an S1 circuit-median
baseline, and a constant "1 day" baseline. It SHALL state that the model beats a baseline only when the paired
circuit bootstrap 95% interval of the error difference lies entirely in the model's favor.

#### Scenario: Tie with the constant baseline is reported
- **WHEN** the bootstrap interval of the MAE difference includes 0
- **THEN** the `rest_vs_baseline` verdict says the model does not beat the baseline

### Requirement: Predicted vs observed plots
The backtest SHALL write SVG plots of hit rate per band with the base-rate line, of predicted P50 against observed
median repair days per circuit (identity line, +/- 1 day band), and of the observed repair-day histogram with the
predicted P50 to P90 band and the backlog region. The plots SHALL use anonymous points and no circuit labels.

#### Scenario: Plot files exist and carry no identifiers
- **WHEN** the backtest finishes
- **THEN** the three SVG files exist and contain no circuit id, circuit label, or device key

### Requirement: Honest, rule-generated verdicts
Verdict sentences SHALL be produced by fixed rules from the computed metrics. A `supports` verdict SHALL be emitted only
when its rule fires. The report SHALL include a fixed "What this does not show" list covering: two storms only,
S1 mostly backlog, crews not observed, repair time is not outage length, whole-day resolution, and circuit-level only.

#### Scenario: Losing model is reported plainly
- **WHEN** the report is rendered from a fixture where the model does not beat the baseline
- **THEN** the text contains "does not beat" and no `supports` verdict for that comparison

#### Scenario: Limits list always present
- **WHEN** `docs/backtest.md` is generated
- **THEN** it contains each of the six limits

### Requirement: Privacy-safe outputs
Backtest outputs SHALL contain only aggregate values. They MUST NOT contain circuit ids or labels, device keys,
premise tokens, hex cells, or counts for groups of fewer than 10.

#### Scenario: Output scan
- **WHEN** `backtest.json`, the SVGs, and `docs/backtest.md` are scanned
- **THEN** no forbidden identifier pattern is found

### Requirement: Evidence route
The API SHALL expose `GET /evidence` returning the aggregate verdicts and plot locations, with the note that results
describe two past storms and are not a guarantee.

#### Scenario: Evidence has no identifiers
- **WHEN** `GET /evidence` is called
- **THEN** the response passes the identifier scan and includes the limits list
