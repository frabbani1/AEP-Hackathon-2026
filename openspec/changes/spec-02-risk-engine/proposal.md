## Why

The first customer question is "Will I lose power?". The challenge requires a risk band, a confidence
indicator, the top contributing factors, and a fallback when the model can't predict reliably. We have
only two storms of damage records, no record of where damage did not happen, and no weather data, so a
trained classifier would be hard to defend. A transparent, deterministic score calibrated against a real
out-of-sample storm is more credible and fully explainable.

## What Changes

- Add a deterministic risk engine (pure Python, no LLM) that scores each premise:
  circuit storm-damage rate per 1,000 premises x service-type factor x exposure factor x severity multiplier.
- Assign Low / Medium / High bands from fixed percentile thresholds on the baseline score distribution, so
  the band responds when the judge changes storm severity.
- Report, per band, the real hit rate from a Sep 2024 -> Feb 2025 backtest with a Wilson 95% interval.
- Derive a confidence level from the circuit's damage-record count and size; below threshold return an
  "insufficient data" fallback instead of a band.
- Return the top 3 contributing factors in plain words, computed by log-contribution against a reference premise.
- Always label output as "circuit-level historical susceptibility, not a per-premise forecast".
- Emit a whitelisted risk section of the fact card; no IDs, addresses, or life-support data.

Out of scope: restoration time, alerts, prep guidance, LLM narration, UI, AWS deployment, live weather,
and per-device or spatial-proximity damage models. Those belong to other specs.

## Capabilities

### New Capabilities
- `outage-risk`: premise-level risk band, confidence, band evidence (hit rate + Wilson interval), top factors,
  labeling, and insufficient-data fallback, driven by a storm-severity scenario input.

### Modified Capabilities
None. No existing specs.

## Impact

- New Python package `backend/concierge/risk/`, an offline build script `scripts/build_risk_artifacts.py`,
  and derived artifacts in `artifacts/risk/` (not committed if they contain raw premise IDs).
- Depends on the data-foundation spec (spec-01) for the premise feature table and the circuit key map.
  Until spec-01 lands, this spec is built and tested against small synthetic fixtures with the same schema.
- Consumers: the restoration, alerts, and narration specs read the risk fact-card section; the UI calls
  `score(premise_token, severity)`.
- New pinned dev dependencies: pytest, hypothesis.
