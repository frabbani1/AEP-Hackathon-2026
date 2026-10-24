## Context

We need a "Will I lose power?" answer that a judge can audit. The data supports circuit-level history
well and premise-level outcomes not at all: ARCOS records where damage was reported, not which premises
lost power, and there are only two storms. So the engine scores **circuit-level historical susceptibility**,
adjusted for each premise's position on the circuit, and proves its ranking on a held-out storm.

Inputs (verified against the real files on 2026-10-24):

| Input | Source | Fields used |
| --- | --- | --- |
| Damage records | `arcos_prod_damage_09_28_Clip.dbf` | `Circuit`, `Report_Tim` |
| Premise -> circuit, service type | `Customer_Clip.dbf` | `premise_nu`, `circuit_id`, `kind_of_se` |
| Premise -> device chain | `matched_upstream.txt` | device class and type per hop |

Observed facts that shape the design:
- Storm windows (rule below): S1 = 2024-09-27..10-02 (193 records), S2 = 2025-02-12..02-18 (1,254).
- 110 circuits, 4 to ~3,500 premises each. 81 of 85 ARCOS circuits join the topology.
- Service type: Overhead 64,824, Underground 52,419, Unknown 399.
- Chain: protective devices per premise 0-10 (mode 2); chain length 2-13 (mode 4).

## Goals / Non-Goals

**Goals:**
- Deterministic score, band, confidence, band evidence, top 3 factors, fallback, for any premise token.
- Severity input that visibly moves the band.
- Out-of-sample evidence (S1 -> S2) attached to every band.
- Sub-millisecond runtime: all heavy work happens at build time.

**Non-Goals:**
- Per-premise outage probability or a trained ML classifier.
- Weather ingestion, spatial-proximity or per-device damage models (possible later spec).
- Restoration time, alerts, prep, narration, UI, AWS infrastructure.

## Decisions

### D1. Two-stage design: offline build, pure-function runtime
`scripts/build_risk_artifacts.py` reads the source files once and writes:
- `artifacts/risk/premise_risk.jsonl`: one row per premise token with `R_eff`, `T`, `E`, `P`, `H`, `n_c`, `N_c`,
  `confidence_base`, `fallback_reason` (no raw IDs).
- `artifacts/risk/calibration.json`: thresholds, band evidence table, severity presets, reference premise,
  constants, `config_hash`, backtest report.

Runtime `score(token, severity) -> RiskResult` loads both once and does a lookup plus a few multiplications.
It runs unchanged in Lambda or a local test. Rationale: sliders respond instantly and every number is traceable
to an artifact. Alternative (compute at request time from shapefiles) was rejected as slow and harder to audit.

### D2. Score formula

```
S_p = R_c * (T_p * E_p / K_c) * m
```

| Symbol | Definition | Default |
| --- | --- | --- |
| `D_c` | damage records on circuit c inside calibration storm windows | from data |
| `N_c` | premises on circuit c (Customer_Clip joined to topology) | from data |
| `St` | number of calibration storms | 2 (prod), 1 (backtest) |
| `R_c` | `1000 * (D_c/St + k*rbar) / (N_c + k)` reports per 1k premises per average storm | `k = 0` |
| `rbar` | `sum(D_c) / (St * sum(N_c))` regional rate (used only if `k > 0`) | |
| `T_p` | service-type factor: Overhead 1.0, Underground 0.4, Unknown 1.0 (flagged) | |
| `P_p` | protective devices in chain: Fuse, Recloser (Hydraulic), Recloser (Electronic), Sectionalizer | |
| `H_p` | hops to breaker = chain length - 1 (includes Step transformers) | |
| `E_p` | `1 + a*P_p + b*H_p` | `a = 0.10`, `b = 0.05` |
| `K_c` | premise-weighted mean of `T*E` over circuit c | |
| `m` | severity multiplier | preset, see D4 |

Why divide by `K_c`: a circuit's observed damage rate already reflects its overhead share and layout. Without
normalization an underground-heavy circuit would be discounted twice. With it, `T` and `E` only redistribute
the circuit's rate among its premises, and the circuit's premise-weighted mean score equals `R_c` at `m = 1`.
Score units stay interpretable: expected damage reports per 1,000 premises for this storm, adjusted for position.

`k` (empirical-Bayes shrinkage toward the regional rate) is a config knob. The probe showed `k = 0` separates
bands best on premise-weighted hit rate, and the `N_c < 50` fallback already removes the unstable tiny circuits.
`a` and `b` are documented assumptions, not fitted: labels are circuit-level, so within-circuit weights cannot be
validated with this data. This is stated in the response's assumptions.

### D3. Bands by fixed percentile thresholds
At build time, compute baseline scores (`m = 1.0`, production rates) for all non-fallback premises. Set
`t_med` = P50 and `t_high` = P85 of those scores (each premise has weight 1). The thresholds are inclusive upper
edges: `Low` if `S <= t_med`, `Medium` if `t_med < S <= t_high`, `High` otherwise. Inclusive edges matter because many
circuits have zero damage, so a percentile can land exactly on 0 and a strict `<` would leave the Low band empty. Thresholds are stored, never recomputed per request.
Rationale: percentiles recomputed after scaling by `m` would never change a band, so the severity slider would do nothing.

### D4. Severity presets (data-anchored scenario multipliers)
`m = storm records / mean storm records`, mean = (193 + 1,254) / 2 = 723.5:

| Preset | m | Note |
| --- | --- | --- |
| `sep-2024-like` | 0.27 | 193 / 723.5 |
| `typical` | 1.00 | average of the two observed storms |
| `feb-2025-like` | 1.73 | 1,254 / 723.5 |
| `beyond-observed` | 2.50 | extrapolated, lowers confidence |

Custom `m` accepted in [0.25, 3.0]; `m > 1.73` sets `extrapolated = true`. Values are recomputed by the build
from the detected windows and stored in `calibration.json`.

### D5. Storm window detection protocol
1. Count ARCOS records per `Report_Tim` day.
2. Candidate days: >= 10 records. Merge candidates separated by <= 2 days.
3. Extend each group to adjacent days with >= 3 records.
4. Keep groups with >= 100 records as storms; order by date (S1, S2).
5. Records outside storm windows (~210, background damage) are excluded from `D_c` and counted in `missing_data`.
6. Records whose `Circuit` has no digits, or whose circuit is not in the topology (CIR12), are excluded and counted.
The build asserts exactly two storms are found; otherwise it fails with the detected windows printed.

### D6. Backtest and band-evidence protocol
1. Calibrate: compute `R_c` from S1 only (`St = 1`), circuit-level scores at `m = 1`, and premise-weighted
   P50/P85 cut points on those scores.
2. Assign each circuit a band from step 1.
3. Outcome on S2: `hit_c = 1` if `1000 * D_c(S2) / N_c >= 2` (at least 2 reports per 1,000 premises).
   Size-normalized on purpose: "any damage" is biased toward big circuits.
4. Per band: `p = sum(N_c * hit_c) / sum(N_c)` (premise-weighted hit rate), `n` = number of circuits in the band.
   Premises on one circuit share an outcome, so circuits, not premises, are the effective sample size.
5. Wilson 95% interval (z = 1.96):
   ```
   center = (p + z^2/(2n)) / (1 + z^2/n)
   half   = (z / (1 + z^2/n)) * sqrt(p(1-p)/n + z^2/(4n^2))
   CI     = [max(0, center - half), min(1, center + half)]
   ```
6. Also report S2 damage density per band (reports per 1k) and the reverse direction (S2 -> S1) as sensitivity.
7. Gate: require `p_Low <= p_Medium <= p_High`. Fail the build otherwise unless `allow_non_monotone` is set
   (recorded in the artifact).
8. Customer-facing evidence sentence per band, e.g. "In the Feb 2025 storm, about 78% of homes in areas we'd rate
   High were on lines with significant damage (95% range 51-88%)."

The live score uses both storms; the evidence table describes how well the same method ranked an unseen storm.
That distinction is stated in assumptions.

Real results from `scripts/build_risk_artifacts.py` (S1 -> S2, `k = 0`, tau = 2 per 1k, 104 circuits with at least
50 premises). The backtest thresholds come from S1 circuit rates alone: `t_med = 0.0` (Low means no S1 damage) and
`t_high = 3.35` reports/1k.

| Band | Circuits | Premises | Premise-weighted hit rate | Wilson 95% | S2 reports/1k |
| --- | --- | --- | --- | --- | --- |
| Low | 61 | 65,525 | 0.28 | 0.18-0.40 | 5.5 |
| Medium | 26 | 35,193 | 0.64 | 0.45-0.80 | 11.8 |
| High | 17 | 16,829 | 0.77 | 0.53-0.91 | 26.8 |

Sensitivity (all monotone, stored in `calibration.json`):

| Variant | Low | Medium | High |
| --- | --- | --- | --- |
| tau = 1 per 1k | 0.44 | 0.82 | 0.88 |
| tau = 5 per 1k | 0.15 | 0.42 | 0.64 |
| Reverse (calibrate Feb 2025, test Sep 2024), tau = 2 | 0.08 | 0.28 | 0.34 |

The ranking holds in every variant and the intervals are wide. The reverse direction has much lower absolute hit
rates because Sep 2024 was a small storm (193 reports vs 1,254), so few circuits reach 2 reports per 1,000 homes. The
UI should show the forward table and may disclose that absolute hit rates depend on storm size.

### D7. Confidence protocol
Using production `n_c` (records in both storm windows) and `N_c`:

| Condition (first match wins) | Result |
| --- | --- |
| token not in allow-list | fallback `unknown_premise` |
| premise has no circuit / circuit not in topology | fallback `no_circuit` |
| circuit has no ARCOS key mapping | fallback `circuit_unmapped` |
| `N_c < 50` | fallback `circuit_too_small` |
| `n_c = 0` and `N_c < 500` | fallback `no_history_small_circuit` |
| `n_c >= 20` | High |
| `5 <= n_c < 20` | Medium |
| otherwise (`1-4` records, or `0` records on a circuit with `>= 500` premises) | Low |

Then: `extrapolated` severity lowers one level (High -> Medium -> Low). `kind_of_se = Unknown` lowers one level and
adds a missing-data note. Real build (117,646 premises): 17,715 High, 39,555 Medium, 57,782 Low confidence; 2,594 premises fall back
(52 `circuit_too_small`, 47 `no_circuit`, 2,495 `no_history_small_circuit`). All constants live in `config.py`.

Notes on the fallback rows:
- `no_circuit` covers premises that appear in the customer file but have no upstream chain in the topology.
- `circuit_unmapped` is supported through an `unmapped_circuits` input. The current data needs none, because
  ARCOS `CIR60` and topology `0000060` both normalize directly to 60.

Fallback response: `status = insufficient_data`, `reason_code`, plain message ("We don't have enough storm history for
your line to rate it. Prepare as if an outage is possible."), no band, no score.

### D8. Factor attribution protocol
Work in log space, against a reference premise `ref`: `R_eff_ref` = premise-weighted median of `R_eff = R_c / K_c`,
`T_ref = 1.0`, `P_ref` and `H_ref` = premise medians, `m_ref = 1.0`. Since `ln S = ln R_eff + ln T + ln E + ln m`:

```
c_hist = ln(max(R_eff, eps) / R_eff_ref)          eps = 0.1 reports/1k (log floor only)
c_type = ln(T_p / T_ref)
c_dev  = 0.5*[ln E(P_p,H_ref) - ln E(P_ref,H_ref)] + 0.5*[ln E(P_p,H_p) - ln E(P_ref,H_p)]   (Shapley, 2 players)
c_hops = ln(E_p / E_ref) - c_dev
c_sev  = ln(m / m_ref)
```

Rank by `|c|`, drop `|c| < 0.05`, keep the top 3. Direction is `raises` if `c > 0`, else `lowers`. Each factor maps
to a fixed template filled only with fact-card values:

| Factor | Template (raises / lowers) |
| --- | --- |
| hist | "Your line had more / less storm damage than most lines in past storms (about {R} reports per 1,000 homes)." |
| type | "Your service is overhead, which is more exposed to wind and trees." / "Your service is underground, which is better protected." |
| dev | "Power reaches you through {P} protective devices; more devices means more places a fault can cut service." / "...fewer devices than most homes." |
| hops | "You're farther down the line from the source than most homes." / "...closer to the source than most homes." |
| sev | "The storm scenario you picked is stronger / milder than a typical past storm." |

### D9. Output contract (risk section of the fact card)

```json
{
  "engine_version": "risk-v1",
  "config_hash": "sha256:…",
  "premise_token": "p_9f2c…",
  "status": "ok",
  "label": "circuit-level historical susceptibility, not a per-premise forecast",
  "severity": {"preset": "typical", "multiplier": 1.0, "extrapolated": false},
  "band": "Medium",
  "score": 9.4,
  "signals": {"circuit_reports_per_1k": 9.4, "protective_devices": 3},
  "confidence": {"level": "Medium", "records": 12, "reason": "12 damage reports on your line in 2 past storms"},
  "band_evidence": {"backtest": "Sep 2024 -> Feb 2025", "hit_rate": 0.62, "ci95": [0.45, 0.77],
                    "n_circuits": 32, "hit_definition": "line had at least 2 damage reports per 1,000 homes"},
  "top_factors": [{"id": "hist", "direction": "raises", "weight": 0.71, "text": "…"}],
  "assumptions": ["Storm strength is a scenario you choose, not a weather forecast.", "…"],
  "missing_data": ["No weather data", "…"],
  "could_change": ["A stronger storm than any we've seen", "Tree trimming or line upgrades since 2025", "…"],
  "fallback": null
}
```

`premise_token = "p_" + HMAC-SHA256(salt, premise_nu)[:16]`, salt from environment, never committed. A key whitelist is
enforced at serialization. Numbers are rounded once at the engine (score and rates 1 dp, factor weights 2 dp) so the narration number guard
can compare exact strings.

### D10. Privacy protocol
- Column whitelist at load: Customer `premise_nu`, `circuit_id`, `kind_of_se`; ARCOS `Circuit`, `Report_Tim`.
  `critical_c`, address, road, city, coordinates are never read.
- Circuit and device identifiers stay inside the build. Artifacts hold tokens and numeric features only.
- Circuits under 50 premises fall back (stricter than the 10-premise suppression rule).
- Audit log line per call: token, severity, band, confidence, reason codes, config_hash. No raw IDs.

### D11. Code layout
```
backend/concierge/risk/
  config.py      constants (a, b, k, factors, thresholds rules, presets bounds)
  load.py        whitelisted readers (dbf via pyshp from spec-01, topology parser)
  storms.py      window detection (D5)
  features.py    R_c, T, E, P, H, K_c per premise
  calibrate.py   thresholds, backtest, Wilson (D3, D6)
  confidence.py  D7 rules
  explain.py     attribution + templates (D8)
  engine.py      score(token, severity) -> RiskResult
  schema.py      RiskResult dataclass + whitelist serializer + identifier scan
  tokens.py      HMAC premise tokens, salt from environment
  build.py       offline pipeline (also called by the script and by tests)
scripts/build_risk_artifacts.py   thin CLI (--sample, --allow-non-monotone)
backend/tests/risk/  unit, property (hypothesis), fixture, integration tests
```

## Risks / Trade-offs

- [Two storms only] -> Wide intervals are shown, not hidden. Reverse backtest reported as sensitivity.
- [Labels are circuit-level] -> Bands describe the line, and the label says so. `a`, `b`, `T` are stated assumptions.
- [Severity is not validated] -> Presets are anchored to observed storm sizes; beyond-observed lowers confidence.
- [Repairs reflect where wind went, not inherent weakness] -> "could_change" lists storm track; S1 -> S2 overlap
  (36 of 45 S1 circuits damaged again) suggests real susceptibility.
- [Unknown service type, 399 premises] -> Treated as overhead (conservative) and confidence lowered.
- [Threshold choice P50/P85] -> Configurable; the backtest report shows hit rates so the choice is auditable.

## Migration Plan

New code only. Build artifacts locally first; upload to S3 only after review (per project rules).

## Open Questions

- Should `tau = 2/1k` be tuned, or fixed now to avoid overfitting to S2? Proposal: fix it now and report tau = 1 and 5 as sensitivity.
- Does spec-01 own the dbf reader and circuit key map? This implementation ships its own whitelisted stdlib DBF
  reader (`load.py`, no dependencies) so it can run before spec-01 lands. Swap it for spec-01's loader later.
- The live fallback rules (D7) are stricter than the backtest, which only requires 50 premises per circuit. This keeps
  the evidence table based on as many circuits as possible. Revisit if reviewers prefer identical populations.
