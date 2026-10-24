# API reference

Base URL: the `ApiUrl` output of `make deploy` (also in `infra/cdk-outputs.json`). Every route is also served under
`/api/...`, so a frontend on the CloudFront domain can call same-origin paths such as `POST /api/summary`.

**No authentication.** This is a demo API on the public internet. It only accepts opaque premise tokens and returns
whitelisted fields, and a light throttle (50 requests/s, burst 100) is set. Put an authorizer in front of it before
any real use.

All routes take JSON. Unknown fields are rejected with `400`. Bodies over 8 KB are rejected with `413`.
Errors look like `{"error": {"code": "bad_request", "message": "..."}}`.

| Route | Body | Returns |
| --- | --- | --- |
| `GET /health` | none | status, engine hash, message model |
| `POST /risk` | `token`, `severity?` | risk band, score, confidence, top factors, assumptions, fallback |
| `POST /restoration` | `token`, `severity?`, `crews?`, `access?`, `damage?` or `damage_count?` | restoration range (P50 to P90), step-by-step stages, what-if changes, assumptions |
| `POST /guidance` | `token`, `severity?`, `needs?`, plus restoration inputs | preparation list with safety items first |
| `POST /alerts` | `token`, `severity?`, `needs?`, `channels?`, `prior_typical_hours?`, plus restoration inputs | eligibility per message type, timing, channels, escalation recommendations |
| `POST /messages` | `token`, `message_type`, `channel?`, `severity?`, `needs?`, `use_llm?`, `prior_estimate?` | one customer message and how it was produced |
| `POST /summary` | same as `/alerts` | risk + restoration + guidance + alerts in one call (one DynamoDB read) |

Inputs

- `token`: `p_` plus 16 hex characters (an opaque premise token from the clean tables).
- `severity`: `sep-2024-like`, `typical` (default), `feb-2025-like`, `beyond-observed`, or a number from 0.25 to 3.0.
- `crews`: whole number 1 to 100 (default 3). `access`: `normal` (default), `restricted`, `remote`.
- `damage`: optional list of `{"asset_type", "qty"}` (qty 1 to 20, up to 10 items). `asset_type` is one of
  `primary_line`, `secondary_line`, `pole`, `fuse_bank`, `transformer`, `recloser`, `switch`, `capacitor_bank`,
  `light`, `step_transformer`. When given, only that damage is counted, and it sits on the customer's own path.
- `damage_count`: optional whole number 1 to 400. Every simulated outcome has exactly that many repair jobs, drawn
  from the mix of past damage and spread over the customer's line. Send `damage_count` or `damage`, never both
  (`400`). With neither, damage is simulated from the line's damage rate and the storm scenario.
- `needs`: `{"medical_equipment", "mobility_limited", "electric_heat_or_cooling"}`, each true or false. Declared by
  the customer, never inferred.
- `message_type`: `pre_storm`, `outage_confirmed`, `estimate_updated`, `restoration_complete`.
  `channel`: `sms` (default), `push`, `email`, `voice`.
- `prior_estimate`: `{"low_hours", "high_hours"}` for `estimate_updated`.

Example

```bash
curl -s -X POST "$API/summary" -H 'content-type: application/json' \
  -d '{"token":"p_0123456789abcdef","severity":"feb-2025-like","crews":6,"needs":{"medical_equipment":true}}'
```

## How the numbers are produced

Every number comes from deterministic Python. The language model only phrases `/messages` output.

- **Risk** (`concierge/risk`): circuit-level historical damage per 1,000 homes, adjusted for service type and position on
  the line, scaled by the storm scenario. It is a susceptibility rating, not a forecast, and says so in `label`.
- **Restoration** (`concierge/restoration`, engine `restoration-v2`): a list of repair jobs, each priced from the
  STORMS labor sheet as (install + removal hours + access adder) x the 4.5 indirect-labor multiplier. Jobs come from
  the line's damage rate, from `damage_count`, or from `damage`. They are placed on the customer's device tree and
  scheduled upstream first, then by customers restored per crew-hour (step restoration). A seeded Monte Carlo varies
  labor hours and crews at work, and the response reports the 50th and 90th percentile as `p50_hours` and `p90_hours`
  (`display` reads "about 6 to 14 hours"; the low end is a likely time, not a floor). Crews work 16 of every 24
  hours. `sensitivity` lists what moves the answer (one more or fewer crew, hard-to-reach sites, 25% more damage).
  `simulation` reports the trial count and whether the circuit tree or the three-level fallback path was used.
  The asset-to-hardware mapping, the spreads, the 85% crew presence and the 16-hour day are assumptions that need
  utility review, and every response lists them. The response always says it is a model estimate, not confirmed
  utility information (`label`: "estimate, not confirmed"). `estimate.low_hours`, `typical_hours` and `high_hours`
  repeat `p50_hours`, `p50_hours` and `p90_hours` for alerts, guidance and messages until they read the new fields.
  `RESTORATION_ENGINE=v1` selects the first, analytic version (no `damage_count`).
- **Guidance** (`concierge/guidance`): fixed rule library. Safety items are always first and marked `safety: true`.
- **Alerts** (`concierge/alerts`): documented rules R1 to R10 with the rule id and reason in every decision.
  Escalations are recommendations for a person, never automatic actions.
- **Messages** (`concierge/messages`): code builds a fact card; the model (Amazon Nova 2 Lite on Bedrock) rewrites it;
  a guard then checks the draft. Any number not in the fact card, link, promise, weather claim, claim about the past,
  or over-length text sends the request back to the fixed template. A Bedrock error does the same. The 911 safety line
  is appended by code after the guard.

## What is never returned

Raw premise numbers, addresses, coordinates, circuit or device identifiers, life-support flags, and the exposure
weights and tree position the restoration model uses (`cn`, `n0`, `nl`, `nf`, `pc`, `pn`, `ap`, and everything in
`restoration_tree.json`). Responses are scanned for identifier-like text before
they leave the Lambda.

## Limits

- Restoration damage is simulated from only two past storms. Damage reports show repairs, not which homes lost power.
- The restoration spreads (labor, crew presence) are assumptions, not fitted. Damage is placed evenly across the line
  sections of the matching type, because section length is not in the data. Crews are modeled as one pool working down
  a priority list, which ignores the limit that two crews cannot work the same job, so short outages may be
  underestimated. No assessment or mobilization delay is included: the clock starts when crews start.
- Premises on very small lines, or with no damage history, return `status: insufficient_data` with a plain-language
  fallback instead of a number.
- No weather data, live crew locations or road conditions are used.
