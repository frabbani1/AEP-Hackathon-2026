## ADDED Requirements

### Requirement: Premise risk score
The engine SHALL compute a deterministic risk score for a premise token and a storm-severity input as
`S = R_c x (T_p x E_p / K_c) x m`, where `R_c` is the circuit's storm-damage rate per 1,000 premises per
average storm, `T_p` is the service-type factor, `E_p = 1 + a*P_p + b*H_p` is the exposure factor,
`K_c` is the premise-weighted mean of `T x E` on the circuit, and `m` is the severity multiplier.
The same inputs and configuration MUST always produce the same output.

#### Scenario: Overhead premise scores higher than underground on the same circuit
- **WHEN** two premises share a circuit and identical device chains, one Overhead and one Underground
- **THEN** the Overhead premise's score is 2.5x the Underground premise's score (1.0 / 0.4)

#### Scenario: Normalization preserves circuit history
- **WHEN** scores are computed for every premise on a circuit at m = 1.0
- **THEN** the premise-weighted mean score on that circuit equals `R_c`

#### Scenario: Deterministic output
- **WHEN** `score(token, severity)` is called twice with the same artifacts
- **THEN** both results are identical, including `config_hash`

### Requirement: Severity scenario input
The engine SHALL accept a severity preset or a numeric multiplier in [0.25, 3.0]. Presets SHALL include
`sep-2024-like`, `typical` (1.0), `feb-2025-like`, and `beyond-observed`. Multipliers above the largest
observed storm MUST be flagged as extrapolated. Severity MUST be labeled as a scenario assumption, not a forecast.

#### Scenario: Raising severity can raise the band
- **WHEN** a Medium premise is rescored with a higher multiplier
- **THEN** the score increases and the band is Medium or High, never Low

#### Scenario: Out-of-range multiplier rejected
- **WHEN** a multiplier of 10 is requested
- **THEN** the engine returns a validation error and no score

### Requirement: Risk bands with fixed thresholds
The engine SHALL assign Low, Medium, or High by comparing the score to two inclusive upper-edge thresholds
fixed at build time from the P50 and P85 of baseline (m = 1.0) premise scores. Thresholds MUST NOT be recomputed per request.

#### Scenario: Band follows thresholds
- **WHEN** a score is at or below the P50 threshold
- **THEN** the band is Low

#### Scenario: Severity moves premises across fixed thresholds
- **WHEN** severity changes from `typical` to `feb-2025-like`
- **THEN** the share of premises in High increases

### Requirement: Band evidence from an out-of-sample backtest
The build SHALL calibrate scores on the Sep 2024 storm only and evaluate on the Feb 2025 storm. For each band it
SHALL report the premise-weighted hit rate, where a hit is a circuit with at least 2 Feb 2025 damage reports per
1,000 premises, and a Wilson 95% interval using the number of circuits in the band as n. Every response SHALL
include the evidence for its band, with the hit definition in plain words.

#### Scenario: Evidence attached to response
- **WHEN** a premise is scored High
- **THEN** the response includes the High band's hit rate, interval bounds, circuit count, and backtest label

#### Scenario: Monotone bands pass the build
- **WHEN** backtest hit rates satisfy Low <= Medium <= High
- **THEN** the build writes artifacts and records the result

#### Scenario: Non-monotone bands are flagged
- **WHEN** backtest hit rates are not ordered Low <= Medium <= High
- **THEN** the build fails unless an explicit override is set, and the override is recorded in the artifact

### Requirement: Confidence level and insufficient-data fallback
The engine SHALL derive confidence from the circuit's calibration damage-record count `n_c` and premise count `N_c`:
High when `n_c >= 20`; Medium when `5 <= n_c < 20`; Low when `1 <= n_c < 5`, or `n_c = 0` and `N_c >= 500`.
The engine SHALL return `status = insufficient_data` with no band when the premise token is unknown, the premise
has no circuit, the circuit has no damage-key mapping, `N_c < 50`, or `n_c = 0` and `N_c < 500`.
Extrapolated severity MUST lower confidence by one level (High to Medium, Medium to Low).

#### Scenario: Unknown premise falls back
- **WHEN** a token not in the allow-list is scored
- **THEN** the response has `status = insufficient_data`, a reason code, a plain-language message, and no band

#### Scenario: Small zero-history circuit falls back
- **WHEN** a premise is on a circuit with 0 records and 300 premises
- **THEN** the response has `status = insufficient_data` and reason `no_history_small_circuit`

#### Scenario: Extrapolated severity lowers confidence
- **WHEN** a High-confidence premise is scored with `beyond-observed`
- **THEN** confidence is Medium and the extrapolation is listed in assumptions

### Requirement: Top contributing factors in plain words
The engine SHALL return the top 3 factors ranked by absolute log-contribution against a reference premise, each
with a direction (`raises` or `lowers`) and a plain-language sentence. Factors SHALL be drawn from: circuit damage
history, service type, protective devices upstream, distance down the line (hops), and storm severity.

#### Scenario: Underground lowers risk
- **WHEN** an Underground premise is scored
- **THEN** if service type is in the top 3 it has direction `lowers`

#### Scenario: Factor text contains no identifiers
- **WHEN** factor sentences are generated
- **THEN** they contain no circuit name, device ID, substation name, or premise number

### Requirement: Honest labeling
Every non-fallback response SHALL include the label "circuit-level historical susceptibility, not a per-premise
forecast", the list of assumptions, missing-data notes, and conditions that could change the answer.

#### Scenario: Label present
- **WHEN** any premise is scored with status `ok`
- **THEN** the response contains the label, at least one assumption, and at least one "could change" item

### Requirement: Privacy-safe output
The risk output SHALL contain only whitelisted fields. It MUST NOT contain raw premise numbers, circuit or substation
labels, device IDs, addresses, coordinates, or the life-support flag. The engine MUST NOT read `critical_c` or any
address field. Circuits with fewer than 10 premises MUST never produce a band.

#### Scenario: Output whitelist enforced
- **WHEN** a response is serialized
- **THEN** any key outside the whitelist raises an error before the response leaves the engine

#### Scenario: Life-support flag is not loaded
- **WHEN** the build loads the customer table
- **THEN** only `premise_nu`, `circuit_id`, and `kind_of_se` are read
