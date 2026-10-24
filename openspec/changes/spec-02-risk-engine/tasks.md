## 1. Setup

- [x] 1.1 Create `backend/concierge/risk/` package and `backend/tests/risk/`; add pinned pytest and hypothesis to `pyproject.toml` (dev extras)
- [x] 1.2 Add `config.py` with all constants from design D2, D4, D6, D7 (a, b, k, type factors, tau, P50/P85, record and size thresholds, severity bounds)
- [x] 1.3 Build a synthetic fixture (3-4 circuits, ~60 premises, two storm windows) matching the spec-01 schema, including an unknown premise, a tiny circuit, and a zero-history circuit

## 2. Loading and storms

- [x] 2.1 Implement whitelisted loaders in `load.py`; test that `critical_c` and address columns are never returned
- [x] 2.2 Implement topology parser returning `P_p` (Fuse, Recloser H/E, Sectionalizer) and `H_p` (chain length - 1); test on sample lines from `matched_upstream.txt`
- [x] 2.3 Implement circuit key normalization (`CIR60` and `0000060` -> 60; no digits -> None), reusing spec-01's map if available
- [x] 2.4 Implement storm window detection (D5); test that the real data yields 2024-09-27..10-02 and 2025-02-12..02-18

## 3. Features and score

- [x] 3.1 Implement `R_c`, `T_p`, `E_p`, `K_c` in `features.py`
- [x] 3.2 Implement `score()` core in `engine.py` with severity presets and range validation
- [x] 3.3 Property tests: score is non-decreasing in `m`, `R_c`, `P`, `H`; Overhead/Underground ratio is 2.5 on the same chain; circuit premise-weighted mean equals `R_c` at m = 1; same input gives same output

## 4. Calibration and evidence

- [x] 4.1 Implement fixed P50/P85 thresholds and band assignment (D3); test that severity changes bands and thresholds stay fixed
- [x] 4.2 Implement Wilson interval; property test bounds in [0, 1], containing p, narrowing as n grows; check against known values (p=0.62, n=32 -> ~[0.45, 0.77])
- [x] 4.3 Implement S1 -> S2 backtest (D6) with size-normalized hit, premise-weighted rate, circuit-count n, density, reverse direction, and tau 1/5 sensitivity
- [x] 4.4 Implement monotonicity gate with recorded override; test both pass and fail paths

## 5. Confidence, fallback, explanation

- [x] 5.1 Implement D7 rule table in `confidence.py`, including extrapolation and Unknown-service downgrades; one test per row
- [x] 5.2 Implement fallback responses with reason codes and plain messages
- [x] 5.3 Implement log-contribution attribution with 2-player Shapley split (D8); test contributions sum to `ln S - ln S_ref`
- [x] 5.4 Implement factor templates; test top 3 ordering, direction, and that text has no identifiers (regex for CIR, STATION, digit runs of 6+)

## 6. Output and privacy

- [x] 6.1 Implement `RiskResult` schema and whitelist serializer (D9); test unknown keys raise
- [x] 6.2 Implement HMAC premise tokens with salt from environment; test raw `premise_nu` never appears in artifacts or output
- [x] 6.3 Add label, assumptions, missing_data, could_change to every ok response; test presence
- [x] 6.4 Add audit log line per call with no raw IDs

## 7. Build script and integration

- [x] 7.1 Implement `scripts/build_risk_artifacts.py` writing `premise_risk.jsonl` and `calibration.json` with `config_hash`; add `artifacts/` raw outputs to `.gitignore`
- [x] 7.2 Integration test on real data: 2 storms found, 110 circuits, ~117.6k premises scored or fallen back, monotone backtest, no forbidden fields in artifacts
- [x] 7.3 Record the real backtest table and confidence counts in `design.md` (replace the probe numbers); also report reverse and tau sensitivity
- [x] 7.4 Demo check: score one premise per band, switch severity `typical` -> `feb-2025-like`, show band and factors change; score an unknown token and show fallback
