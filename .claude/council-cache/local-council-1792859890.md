# Local Council — AEP Outage Concierge MVP

**Question:** Review our repository files and README. Which of our proposed solutions is the absolute best one for a hackathon-winning MVP, and why?
**Roles:** Devil's Advocate, Simplicity Champion, Security Auditor, Scalability Architect

> **Local council** — these perspectives all come from Claude playing different roles, not from different AI vendors. Treat agreement as a shared starting point to pressure-test, not as independent confirmation.

Premise correction sent to members: README describes ONE solution, not several, so the question was framed as "which cut of this plan makes the best MVP."

## Devil's Advocate
Invert README emphasis. Hero = deterministic slider-driven restoration engine, calibrated on observed Report_Tim -> Repaired_D durations if populated. Risk = per-circuit empirical damage rate (damage pts / mile primary on 9/28) x stated severity multiplier, not a trained classifier — 1 event, 1,659 points, label confounded by crew routing. Topology via joins (Customer.circuit_id/aep_xfmr_l, Fuse_Bank.upstream_l/aep_custom), not networkx. LLM optional w/ cached template fallback. Cut priority mode, audit UI, structure PDF mapping, NOAA, React. Risks: Customer_Clip has address/premise/critical_c PII; severity uncalibratable from one event; "AI" optics risk. Confidence: medium.

## Simplicity Champion
One Streamlit file. Risk = damage density near location x severity slider, terciles -> band, confidence from data volume (~30 lines pandas). Restoration = P25/P50/P90 repair durations by asset type from damage data (or STORMS lookup if supplied) / crews, x access toggle. Drop trained model, topology graph, prioritizer, LLM (f-string templates; optional "explain" button). st_folium, precomputed parquet. Risks: LFS pull blocks all; simple model may look basic (defend with brief's wording + visible fallback); spatial leak — say it out loud. Confidence: medium.

## Security Auditor
GitHub repo is PUBLIC (coordinator verified via GitHub API) and serves real LFS .dbf attribute tables, incl. Customer_Clip (~79 MB) with address, premise_nu, macss_mete, critical_c; damage layer has crew/reporter fields + exact lat/long. Violates brief's privacy requirement. Make private now, purge history (LFS objects need repo recreate or GitHub Support), gitignore data/ .env .venv/, check data terms. MVP: zone-level aggregation (fuse zone/grid, k>=10 suppression), column allowlist in prepare_data.py, critical_c never used (declared toggle only), LLM sees only allowlisted JSON, visible privacy-filter panel. Confidence: high.

## Scalability Architect
Counts from .shx: ~117.6k customers, 107k poles, 127.4k secondary, 62.8k primary, 27k transformers, 7.6k fuse banks, 1,659 damage pts; real .dbf ~380 MB. Precompute offline per fuse zone into parquet; runtime = arithmetic only (<50 ms slider). Logistic regression w/ severity offset. pydeck/deck.gl, never render poles/customers/secondary. LLM out of slider path; cache messages for rehearsed locations. Missing matched_upstream -> spatial-join fallback. Production pitch: per-zone CDN cache, stateless API, async recompute of downstream zones. Confidence: medium.

## Synthesis

**Shared starting points (common prior, stress-test it):** all four cut the README to a precomputed Streamlit app: transparent risk score (rates/density or LR, not GBM), deterministic restoration formula driven by sliders, rules + templated messages, LLM only explaining. All flag LFS pull as hour-0 blocker and topology graph as cuttable. Same-model agreement — what all may miss: judges may reward visible "AI concierge" interaction more than all four assume.

**Genuine tensions:** (1) LLM — Simplicity drops it; others keep explain-only. Challenge is titled "AI Concierge": keep one LLM explain/message button with template fallback. (2) Unit — premise pick vs zone aggregation; Security + Scalability argue zones (privacy + speed + matches fuse-zone logic). Zones win. (3) Risk method — density/rate vs LR w/ offset; with one event, both are honest only if labeled relative; LR adds little.

**Blind spots:** Public repo w/ PII is the urgent issue none of the modelling lenses foresaw. Nobody verified the damage layer has damage-type/quantity fields needed for STORMS formula. Nobody addressed pitch/demo narrative or judging rubric (brief publishes none).

**Suggested direction:** Restoration-scenario engine as hero, zone-level privacy-first Streamlit app, empirical risk rate + severity multiplier, observed-duration-calibrated restoration range, rules + templates, optional LLM explain button, visible privacy/transparency panel. Step 0: secure repo; step 1: git lfs pull + inspect Event/Repaired_D/damage-type fields — that result decides risk method and restoration calibration.
