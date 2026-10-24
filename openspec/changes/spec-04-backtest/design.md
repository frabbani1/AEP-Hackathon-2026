## Context

We want a leak-free check of two claims: (1) risk bands order circuits by how much damage a later storm caused,
and (2) the restoration range matches how long repairs took. Both are tested at circuit level, because the labels are
circuit-level. There are no premise-level outage labels, so no premise-level accuracy can be reported.

Facts from the repo (read on 2026-10-24):

| Fact | Value | Source |
| --- | --- | --- |
| Storms | S1 Sep 2024 (193 rows), S2 Feb 2025 (1,254 rows), 212 background rows | `docs/data_findings.md` |
| S1 repairs | 149 repaired in 7+ days (backlog), 44 repaired in under 7 days | `damage_fact` |
| S2 repairs | 1,205 repaired under 7 days, 4 repaired in 7+ days, 45 not repaired | `damage_fact` |
| S2 non-backlog median repair | 1 day | `damage_fact` |
| Repair days resolution | whole calendar days, 0 to 6 then 12+ | `damage_fact.repair_days` |
| Unrepaired rows | 76 carry a placeholder date; `repair_days` is null | data cleaning rules |
| Spec-02 forward table (tau = 2 per 1k, 104 circuits) | Low 0.385, Medium 0.707, High 0.744 premise-weighted hit rate | `artifacts/risk/calibration.json` |
| Base rate computed from that table | 0.536 premise-weighted; 47 of 104 circuits = 0.452 | arithmetic on the table; the build must recompute it |

Reading of the table: Low's interval (0.27 to 0.51) sits below 0.536. High's interval (0.51 to 0.89) reaches below
0.536, so High does not clearly beat the base rate. Medium and High overlap. This design makes the report say
that, instead of leaving it for a judge to find. The numbers above are context only; the build produces the
real ones.

The curated `is_backlog` flag uses 7 days, which matches the gap in the data (nothing between 6 and 12 days).
The brief says "about 3 days". See D4 and Open Questions.

## Goals / Non-Goals

**Goals:**
- Leak-free calibrate-on-S1, test-on-S2 evaluation for risk and restoration.
- Per-band hit rate against a base rate and simple baselines, with uncertainty.
- Predicted vs observed restoration with plots, coverage, and baseline error.
- Verdict text produced by rules. Honest when the model does not win.

**Non-Goals:**
- Improving the models, or tuning parameters to raise the scores.
- Premise-level accuracy, weather skill, or live monitoring.
- Treating repair days as customer outage duration.

## Decisions

### D1. Leak-free protocol
- Inputs to any S2 prediction: S1 damage records, static network tables, the chosen S2 severity multiplier, and the
  labor sheet. Nothing from S2 damage rows may reach a prediction.
- S2 severity is given the way the UI gives it: the `feb-2025-like` preset (m = 1.73 relative to the two-storm mean).
  This uses the regional S2 storm size, not any circuit's S2 outcome. The report says so.
- Enforcement: the evaluator receives S1 and S2 tables through separate objects. A test replaces every S2 damage
  row with random data and asserts predictions are unchanged.
- The risk part re-runs the spec-02 forward backtest through the same functions and asserts it equals the stored
  `calibration.json` table. A mismatch fails the run.

### D2. Risk evaluation (circuit level)
Unit: circuits with at least 50 premises (104 circuits). Outcome: `hit_c = 1` if S2 reports per 1,000 premises is
at least tau (2). Bands come from the S1-only thresholds (spec-02 D6).

| Output | Definition |
| --- | --- |
| Base rate `b` | premise-weighted share of hit circuits over all evaluated circuits; also the unweighted circuit share |
| Band hit rate `p_k` | spec-02 premise-weighted rate, Wilson 95% interval with n = circuits in band |
| Lift | `p_k / b` and difference `p_k - b` |
| Beats base rate | High: Wilson lower bound `> b`. Low: Wilson upper bound `< b`. Medium: interval excludes `b` in either direction |
| Size baseline | rank circuits by premise count, cut into the same three group sizes, same hit rate and interval |
| Permutation test | shuffle band labels across circuits 10,000 times (seeded); p-value for the High-minus-Low hit-rate gap |
| Rank metric | AUC of the S1 score against `hit_c`, with a 2,000-resample circuit bootstrap interval; also Spearman rank correlation of S1 rate vs S2 rate |
| Density | S2 reports per 1,000 premises per band (spec-02 already stores it) |
| Sensitivity | tau 1 / 2 / 5; reverse direction (S2 -> S1); drop the 3 largest circuits; minimum circuit size 100 |

The report states separately whether the model beats the base rate, beats the size baseline, and separates
Medium from High.

### D3. Restoration evaluation (circuit level)
Two predictions per S2 circuit, so error can be attributed:

| Run | S2 damage used | Tests |
| --- | --- | --- |
| Oracle damage | actual S2 asset counts for the circuit (asset types and counts, no repair times) | job pricing, scheduling, crews |
| Forecast damage | S1 circuit rate scaled to S2 severity | the full chain, including the damage forecast |

For each run the spec-03 engine is called at circuit level with the circuit's own device tree and
`crews = k`. The predicted distribution is the pooled completion time of all jobs on the circuit across Monte
Carlo trials, reported as P50 and P90 in days.

Free parameter `k` (effective crews working the circuit). It is not observed. It is fitted once, on S1 only:
choose the `k` in 1 to 20 that minimizes mean absolute error between predicted P50 and each circuit's observed median
repair days (S1 non-backlog records). The fitted `k` is then fixed for S2. Rules:
- S1 has only 44 non-backlog records. If fewer than 30 records or fewer than 8 circuits have enough data
  (at least 3 records per circuit), the fit is declared **not identifiable**. The run then uses the default
  `crews = 3` and labels the restoration result "uncalibrated".
- Report the fitted `k` with a bootstrap interval, and the in-sample S2 best `k` as a ceiling, clearly labeled "not a test".

Observed per circuit: median and P90 of `repair_days` over S2 repaired, non-backlog records. A circuit is included
only if it has at least 10 such records (small groups are suppressed; the count of excluded circuits is reported).
Unrepaired rows are excluded as censored and counted.

### D4. Backlog handling
Backlog cutoff `B` days. Primary run: `B = 3`, as the brief says ("the tail beyond about 3 days is backlog").
Sensitivity run: `B = 7`, the curated `is_backlog` rule. For each cutoff the report gives the share of rows
excluded for S1 and S2. Backlog rows are shown in the histogram as a shaded region and are never used to score the
range. The report says the model does not try to predict backlog, and that excluding it favors the model
(a survivorship effect), so coverage is also shown with backlog counted as "beyond P90".

### D5. Restoration metrics and baselines

| Metric | Definition |
| --- | --- |
| Coverage P50 | share of S2 non-backlog records with `repair_days <=` the circuit's predicted P50 days (target about 50%) |
| Coverage P90 | same with P90 (target about 90%) |
| Day resolution | predicted days compared on whole days; the report states the +/- 1 day resolution limit |
| Circuit MAE | mean absolute error of predicted P50 vs observed median, in days |
| Within one day | share of circuits with `abs(pred - obs) <= 1` |
| Baselines | (a) constant: S1 global median repair days; (b) S1 circuit median where the circuit has data, else global; (c) naive "1 day" |
| Model beats baseline | paired circuit bootstrap (2,000) of MAE difference; only "yes" if the 95% interval is entirely below 0 |

If the S2 distribution is mostly "0 or 1 day", the constant baseline may tie the model. The report then says
"the model does not beat a constant" and shows both. It does not hide the tie.

### D6. Verdict generation
`verdicts.py` maps metrics to fixed sentences. No free text from a person or an LLM. Each verdict has a code, a level
(`supports`, `mixed`, `does_not_support`, `not_testable`), and a sentence.

| Code | Rule |
| --- | --- |
| `risk_low_vs_base` | Low upper bound `< b` -> supports; else mixed |
| `risk_high_vs_base` | High lower bound `> b` -> supports; else mixed |
| `risk_medium_vs_high` | intervals overlap -> "not distinguishable" |
| `risk_vs_size` | band spread beats size-baseline spread by more than both intervals -> supports; else mixed |
| `risk_permutation` | p `< 0.05` -> supports; else does_not_support |
| `rest_calibrated` | fit identifiable -> as is; else `not_testable` for the tuned-crew claim |
| `rest_coverage_p90` | coverage within 0.80 to 0.97 -> supports; else does_not_support |
| `rest_vs_baseline` | bootstrap rule in D5 |
| `rest_resolution` | always present: "finest claim is about one day" |

A "supports" verdict never appears unless its rule fired. A test checks that rendering the report from a fixture
with a losing model contains the words "does not beat".

### D7. Outputs and privacy
- `artifacts/backtest/backtest.json`: all metrics, sensitivity tables, verdicts, `config_hash`, git hash, input file hashes.
- `artifacts/backtest/risk_bands.svg`: hit rate per band with intervals and the base-rate line.
- `artifacts/backtest/restoration_scatter.svg`: predicted P50 days (x) vs observed median days (y) per circuit, identity
  line, +/- 1 day band, marker size by record count. Points are anonymous; coordinates rounded to 0.1 day.
- `artifacts/backtest/repair_hist.svg`: observed repair-day histogram, predicted P50 to P90 band, shaded backlog region.
- `docs/backtest.md`: generated; includes a fixed "What this does not show" list.
- No circuit ids, labels, or counts for groups under 10. Circuits are shown as unnamed points.
- Plots use a small stdlib SVG writer, so the Lambda and the dev environment need no plotting package.

### D8. Serving
`GET /evidence` returns the aggregate verdicts and a link to each plot file (served from the clean bucket
`serving/` prefix). It carries the same "model estimate, not confirmed" note and no identifiers.

### D9. Code layout
```
backend/concierge/backtest/
  split.py        S1/S2 separation objects and the leak guard
  risk_eval.py    D2 metrics, permutation, AUC, bootstrap
  rest_eval.py    D3 and D5: circuit-level runs, fit of k, coverage, baselines
  verdicts.py     D6 rules and sentences
  svg.py          stdlib SVG plots
  report.py       backtest.json, docs/backtest.md
scripts/run_backtest.py   CLI (--seed, --cutoff-days, --sample)
backend/tests/backtest/
```

## Risks / Trade-offs

- [Two storms, one mostly backlog] -> The S1 fit is weak; "not identifiable" is a valid outcome and is reported.
- [Repairs are not outages] -> Stated in the report; the claim is about repair time per damaged asset.
- [Free crews parameter] -> Fit on S1 only, never on S2, and the ceiling is labeled.
- [Survivorship from dropping backlog] -> Coverage also reported with backlog counted as misses.
- [Whole-day resolution] -> Nothing finer than about a day is claimed.
- [Few circuits per band] -> Wide intervals are kept in the report and the plots.

## Migration Plan

New code and artifacts only. Run locally, review the report, then upload `backtest.json` and plots to the clean
bucket after review (project rule). The `/evidence` route ships with the next API deploy.

## Open Questions

- Primary backlog cutoff: 3 days (brief) or 7 days (curated `is_backlog`)? The design runs both and uses 3 as the headline.
- Should the report also give an S2 -> S1 restoration check? S1 has 44 usable records, so it is likely not informative.
- Is circuit-pooled job completion the right proxy for "time to restore a customer"? Observed data cannot tell us.
