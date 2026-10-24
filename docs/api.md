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
| `POST /restoration` | `token`, `severity?`, `crews?`, `access?`, `damage?` | restoration range, step-by-step stages, what-if changes, assumptions |
| `POST /guidance` | `token`, `severity?`, `needs?`, plus restoration inputs | preparation list with safety items first |
| `POST /alerts` | `token`, `severity?`, `needs?`, `channels?`, `prior_typical_hours?`, `hours_until_storm?`, `hours_since_last_update?`, `opt_in?`, plus restoration inputs | alert level (tier), eligibility per message type, timing, channels, escalation recommendations, `rules_fired`, `decision_id` |
| `POST /alerts/enroll` | `token`, `channels` (`[{type, value}]`), `needs?`, `message_types?` | demo sign-up with masked contacts. **Fictional contacts only** |
| `POST /alerts/unenroll` | `token` | deletes the demo sign-up and its mock messages |
| `POST /alerts/simulate` | `token`, `event`, `use_llm?`, `prior_typical_hours?`, `prior_estimate?`, `hours_until_storm?`, plus restoration inputs | records one mock message in the outbox (`not_sent_demo`) or says why nothing was recorded |
| `GET /alerts/audit?token=p_...` | none | latest 50 alert decisions for that token (inputs, rules fired, outputs) |
| `GET /evidence` | none | backtest headline numbers, rule-generated verdicts (`supports`, `mixed`, `does_not_support`, `not_testable`), plot locations, and the six-item limits list. `status` is `unavailable` until a reviewed `backtest.json` is uploaded. Aggregates only, no identifiers |
| `POST /messages` | `token`, `message_type`, `channel?`, `severity?`, `needs?`, `use_llm?`, `prior_estimate?` | one customer message and how it was produced |
| `POST /summary` | same as `/alerts` | risk + restoration + guidance + alerts in one call (one premise-table read, plus one alerts-table read for the sign-up) |

Inputs

- `token`: `p_` plus 16 hex characters (an opaque premise token from the clean tables).
- `severity`: `sep-2024-like`, `typical` (default), `feb-2025-like`, `beyond-observed`, or a number from 0.25 to 3.0.
- `crews`: whole number 1 to 100 (default 3). `access`: `normal` (default), `restricted`, `remote`.
- `damage`: optional list of `{"asset_type", "qty"}` (qty 1 to 20, up to 10 items). `asset_type` is one of
  `primary_line`, `secondary_line`, `pole`, `fuse_bank`, `transformer`, `recloser`, `switch`, `capacitor_bank`,
  `light`, `step_transformer`. When given, only that damage is counted.
- `needs`: `{"medical_equipment", "mobility_limited", "electric_heat_or_cooling"}`, each true or false. Declared by
  the customer, never inferred.
- `message_type`: `pre_storm`, `outage_confirmed`, `estimate_updated`, `restoration_complete`.
  `channel`: `sms` (default), `push`, `email`, `voice`.
- `prior_estimate`: `{"low_hours", "high_hours"}` for `estimate_updated`.
- `hours_until_storm`: whole number 0 to 168. Each pre-storm lead time becomes `scheduled`, `send_now` (one catch-up alert)
  or `skipped`. `hours_since_last_update`: whole number 0 to 168, checked against the level's minimum gap between updates.
  `opt_in`: true to opt in to storm alerts at the basic level (an enrolled customer who kept `pre_storm` counts as opted in).
- `event` (simulate): one of the four `message_type` values.
- `channels` on `/alerts/enroll`: 1 to 4 items, one per type (`sms`, `voice`, `push`, `email`).
  Phones must be fictional (`555-0100` to `555-0199`), emails must be at `example.com`, `.org`, `.net`, or end in `.test` or
  `.invalid`, and `push` takes the value `app`. Anything else returns 400 with a demo-only message.

Example

```bash
curl -s -X POST "$API/summary" -H 'content-type: application/json' \
  -d '{"token":"p_0123456789abcdef","severity":"feb-2025-like","crews":6,"needs":{"medical_equipment":true}}'
```

## How the numbers are produced

Every number comes from deterministic Python. The language model only phrases `/messages` output.

- **Risk** (`concierge/risk`): circuit-level historical damage per 1,000 homes, adjusted for service type and position on
  the line, scaled by the storm scenario. It is a susceptibility rating, not a forecast, and says so in `label`.
- **Restoration** (`concierge/restoration/model.py`): expected damage on the customer's line, split into main line,
  branch and near-home repairs using the historical asset mix. Each repair costs the STORMS install + removal hours
  (plus an access adder), times the 4.5 indirect-labor multiplier. Crews work 16 of every 24 hours, upstream first, so
  `steps` shows when each stage finishes. The range widens when the evidence is thinner. The asset-to-hardware
  mapping and the 16-hour working day are assumptions that need utility review. The response always says it is a model
  estimate, not confirmed utility information.
- **Guidance** (`concierge/guidance`): fixed rule library. Safety items are always first and marked `safety: true`.
- **Alerts** (`concierge/alerts`): a rules table (`rules_table.json`, versioned and hashed) turns risk band, expected
  duration and declared needs into an alert level (`info`, `standard`, `elevated`, `priority`), pre-storm timing, update
  cadence, channel order and escalation recommendations. Every output names the rule that produced it, and every decision
  is written to the audit log. The rules and levels are prototype assumptions, not utility policy. Escalations are
  recommendations for a person, never automatic actions.
- **Messages** (`concierge/messages`): code builds a fact card; the model (Amazon Nova 2 Lite on Bedrock) rewrites it;
  a guard then checks the draft. Any number not in the fact card, link, promise, weather claim, claim about the past,
  or over-length text sends the request back to the fixed template. A Bedrock error does the same. The 911 safety line
  is appended by code after the guard.

## Demo-only sign-up, mock delivery and audit

- **Nothing is ever sent.** There is no SMS, voice, push or email integration, and the Lambda role has no permission to
  send. `POST /alerts/simulate` writes a mock message to an outbox with `status: "not_sent_demo"`.
- **No real personal data.** Sign-up accepts only fictional contacts. Names, addresses and free text are rejected.
  Contact values are stored for 24 hours, returned masked (`***-0123`), and never logged.
- **Audit log.** Each evaluation, simulation, sign-up and removal writes one entry (decision id, time, route, token,
  rules-table hash, inputs, rules fired, outputs) kept for 7 days. Entries never hold contacts or message text. Declared
  needs appear only as two true/false values (any need, medical equipment).
- **No authentication.** The sign-up, simulate and audit routes have none. Anyone who holds an opaque premise token can read
  that token's audit entries. This is acceptable for a demo only. Put an authorizer in front before any real use.
- Storage is the `aep-demo-alerts` DynamoDB table (key `token` + `sk`, TTL attribute `ttl`) when `ENROLL_TABLE` is set,
  and process memory otherwise (local runs, tests). A failed audit write is logged and does not block the answer.

## What is never returned

Raw premise numbers, addresses, coordinates, circuit or device identifiers, life-support flags, and the exposure
weights the restoration model uses (`cn`, `n0`, `nl`, `nf`). Responses are scanned for identifier-like text before
they leave the Lambda.

## Limits

- Restoration damage is simulated from only two past storms. Damage reports show repairs, not which homes lost power.
- Premises on very small lines, or with no damage history, return `status: insufficient_data` with a plain-language
  fallback instead of a number.
- No weather data, live crew locations or road conditions are used.
- Alert timing has no quiet hours because no time zone is known. Estimate-updated rules need the caller to supply the
  earlier estimate (`prior_typical_hours`).
