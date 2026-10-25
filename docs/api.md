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
| `GET /health` | none | status, engine hash, message model, assistant circuit-breaker state (`closed`, `open`, `half_open`, or `null` when no model is configured) |
| `POST /risk` | `token`, `severity?` | risk band, score, confidence, top factors, assumptions, fallback |
| `POST /restoration` | `token`, `severity?`, `crews?`, `access?`, `damage?` or `damage_count?` | restoration range (P50 to P90), step-by-step stages, what-if changes, assumptions |
| `POST /guidance` | `token`, `severity?`, `needs?`, plus restoration inputs | preparation list with safety items first |
| `POST /alerts` | `token`, `severity?`, `needs?`, `channels?`, `prior_typical_hours?`, `hours_until_storm?`, `hours_since_last_update?`, `opt_in?`, plus restoration inputs | alert level (tier), eligibility per message type, timing, channels, escalation recommendations, `rules_fired`, `decision_id` |
| `POST /alerts/enroll` | `token`, `channels` (`[{type, value}]`), `needs?`, `message_types?` | demo sign-up with masked contacts. **Fictional contacts only** |
| `POST /alerts/unenroll` | `token` | deletes the demo sign-up and its mock messages |
| `POST /alerts/simulate` | `token`, `event`, `use_llm?`, `prior_typical_hours?`, `prior_estimate?`, `hours_until_storm?`, plus restoration inputs | records one mock message in the outbox (`not_sent_demo`) or says why nothing was recorded |
| `GET /alerts/audit?token=p_...` | none | latest 50 alert decisions for that token (inputs, rules fired, outputs) |
| `GET /evidence` | none | backtest headline numbers, rule-generated verdicts (`supports`, `mixed`, `does_not_support`, `not_testable`), plot locations, and the six-item limits list. `status` is `unavailable` until a reviewed `backtest.json` is uploaded. Aggregates only, no identifiers |
| `POST /messages` | `token`, `message_type`, `channel?`, `severity?`, `needs?`, `use_llm?`, `prior_estimate?` | one customer message and how it was produced |
| `POST /assistant` | `token`, `kind`, `channel?`, `stage?`, `severity?`, `needs?`, `use_llm?`, `prior_estimate?`, plus restoration inputs | one of eight kinds of plain-language text, with separate sections for confirmed facts, model estimates and customer actions, and its provenance (see "Assistant") |
| `POST /zones` | `severity?` | the outage map: every area's outline, risk band and confidence for one storm scenario, and the smallest group of homes an area may cover. `503` until `zones.json` is published |
| `POST /zones/assess` | `zone_id`, `severity?`, `needs?`, `crews?`, `access?`, `damage?` or `damage_count?`, `use_llm?` (default `false`) | all four answers for one area in the shape the map shows: risk, restoration (P50 to P90, steps, what moves it), preparation, alert level and eligibility, four sample messages, assumptions. See "Outage map" |
| `POST /summary` | same as `/alerts` | risk + restoration + guidance + alerts in one call (one premise-table read, plus one alerts-table read for the sign-up) |

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
- **Alerts** (`concierge/alerts`): a rules table (`rules_table.json`, versioned and hashed) turns risk band, expected
  duration and declared needs into an alert level (`info`, `standard`, `elevated`, `priority`), pre-storm timing, update
  cadence, channel order and escalation recommendations. Every output names the rule that produced it, and every decision
  is written to the audit log. The rules and levels are prototype assumptions, not utility policy. Escalations are
  recommendations for a person, never automatic actions.
- **Messages** (`concierge/messages`): code builds a fact card; the model (Amazon Nova 2 Lite on Bedrock) rewrites it;
  a guard then checks the draft. Any number not in the fact card, link, promise, weather claim, claim about the past,
  or over-length text sends the request back to the fixed template. A Bedrock error does the same. The 911 safety line
  is appended by code after the guard.
- **Assistant** (`concierge/assistant`): one fact card (`factcard-v1`) feeds eight prompts. The model sees no digit and
  no token; it writes `{{range}}` placeholders that code fills in, so one draft can be cached for a whole bucket of
  customers. A stricter guard than the one for messages checks every draft, and any failure, timeout, throttling or an open
  circuit breaker returns the code-built fallback. Safety text is added by code and asserted present. `/messages` is unchanged.

## Assistant (`POST /assistant`)

One route for eight kinds of text. `kind` is one of `explanation`, `prep_before`, `actions_during`, `actions_after`,
`msg_pre_storm`, `msg_outage_confirmed`, `msg_estimate_updated`, `msg_restoration_complete`. `channel` (`sms`, `push`,
`email`, `voice`; default `sms`) matters for the message kinds. `stage` (`before`, `during`, `after`) is read only by
`explanation`; every other kind has a fixed stage. `prior_estimate` (`{low_hours, high_hours}`) is read only by
`msg_estimate_updated`. `use_llm` (default `true`) set to `false` skips the model and the cache. Every field is a closed enum,
a whole-number range, or a true/false value. No field takes free text, and the premise token never reaches a prompt.

Response:

```json
{
  "engine_version": "assistant-v1", "kind": "prep_before", "stage": "before",
  "sections": {
    "confirmed_facts": [],
    "confirmed_note": "No confirmed utility information is connected to this prototype.",
    "model_estimates": [{"id": "range", "text": "Restoration, if you lose power: about 6 to 14 hours.", "label": "estimate, not confirmed"}],
    "customer_actions": [{"id": "downed_lines", "text": "...", "safety": true}, {"id": "charge_devices", "text": "...", "safety": false}]
  },
  "text": "...", "safety": [{"id": "downed_lines", "text": "..."}],
  "source": "model", "model_id": "...", "prompt_version": "prep_before.v1", "prompt_hash": "sha256:...",
  "guard": {"checked": true, "passed": true, "reasons": []},
  "cache": {"hit": false, "key": "sha256:..."},
  "facts_used": {"band": "Medium", "confidence": "Medium", "scenario": "a typical storm", "range": "about 6 to 14 hours"},
  "note": "Times are model estimates, not confirmed by the utility."
}
```

- `source` is `model`, `cache` (a guard-passed draft reused, with this customer's own numbers filled in) or `template`
  (the code-built fallback). `guard.reasons` says why a draft or the model was not used, in fixed phrases that never quote the draft.
- For the three item kinds (`prep_before`, `actions_during`, `actions_after`), `text` is the introduction and the items are in
  `sections.customer_actions`: safety items first (`safety: true`), then the items code selected (the model may rephrase them but
  not add, drop or reorder them), then need-specific items verbatim. `model_estimates` and `customer_actions` never share a list.
- `confirmed_facts` is always empty in this prototype. `model_estimates` is empty when the premise cannot be rated.
- The model never writes a number: it writes `{{range}}` and `{{prior_range}}` and code fills them in after the guard passes.
  Safety text (downed lines, generator and carbon monoxide, 911, reporting an outage) is added by code and checked to be present.
  `UTILITY_OUTAGE_CONTACT` may name a verified outage line or app; unset, the text says "your utility's official outage line or app".
- A well-formed token that is not in the premise table gets the insufficient-data fallback and no model call.
- Logs hold the kind, source, guard reasons, cache hit and latency only. They never hold the token, a draft or the final text.

## Outage map (`POST /zones`, `POST /zones/assess`)

The map shows areas, the engines take premise tokens. `zones.json` (built by `concierge/curate/zones.py`, published to
`serving/risk/`, never served to a browser) links the two.

- A **zone** is an H3 cell at resolution 6 (about 36 km2) holding at least 10 premises. Every published premise cell has one
  resolution-6 ancestor, so zones are coarser than the premise cells. The zone id is `z_` plus 12 letters (an HMAC of the cell
  under the token salt) and is not a location. Names are `Area 1`, `Area 2`, and so on, north to south.
- A zone is **rated through one representative premise**: the one whose typical-storm risk score is the middle one among the
  zone's rated premises (ties broken by token). Its answers describe a typical home in the area, not the whole area. The
  response says so in `transparency.assumptions`. A zone with no rated premise returns `status: insufficient_data`, no
  risk or restoration, and the most common fallback reason among its premises.
- The representative token never leaves the server. `/zones/assess` writes **nothing** to the alerts audit log, because browsing
  the map is not an alert decision, and the sign-up store is not read.
- `/zones` is cached per storm scenario (a layer depends on nothing else). `/zones/assess` runs the risk, restoration, alerts and
  assistant code once per call. With `use_llm` false (the default) all text comes from the code-built templates and the call
  makes no Bedrock request. With `use_llm` true it makes up to eight sequential assistant calls (each may use the draft cache),
  so expect it to be slower.
- `/zones/assess` response keys (snake_case): `source` (`live`), `zone_id`, `zone_name`, `status`, `severity`,
  `critical_facility_nearby`, `confirmed`, `risk`, `restoration`, `preparation`, `alerts`, `messages`, `transparency`,
  `fallback`. `risk.explanation` and each message carry `origin` (`model` or `template`). Preparation comes from the assistant's
  item libraries (`prep_before`, `actions_during`, `actions_after`): safety notices first, then three groups, with items added
  for declared needs marked `tailored_for`. `restoration.steps` are the engine's `steps`, and `restoration.movers` list the
  engine's `sensitivity` changes that move the likely time (a change that leaves P50 unchanged is left out).
- Needs: the map offers `medical_equipment`, `mobility_limited` and `electric_heat_or_cooling`, the same three the engines know.
- Build: `scripts/build_risk_artifacts.py` and `scripts/etl.py` write `zones.json` when the clean tables include `hex_context`.
  Run `make etl` and upload before the map routes work in the cloud. Locally, `scripts/dev_api.py` serves the handler on
  127.0.0.1:8787 and the Vite dev server proxies `/api` to it.

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

Raw premise numbers, addresses, coordinates, circuit or device identifiers, life-support flags, the zone-to-premise map
(`zones.json`; a zone response carries the zone id and outline only), and the exposure
weights and tree position the restoration model uses (`cn`, `n0`, `nl`, `nf`, `pc`, `pn`, `ap`, and everything in
`restoration_tree.json`). Responses are scanned for identifier-like text before
they leave the Lambda.

## Limits

- Restoration damage is simulated from only two past storms. Damage reports show repairs, not which homes lost power.
- The restoration spreads (labor, crew presence) are assumptions, not fitted. Damage is placed evenly across the line
  sections of the matching type, because section length is not in the data. Crews are modeled as one pool working down
  a priority list, which ignores the limit that two crews cannot work the same job, so short outages may be
  underestimated. No assessment or mobilization delay is included: the clock starts when crews start.
- A map area is rated through one typical home in it. Lines differ inside a 36 km2 area, and some of the text the engines write
  says "your line" or "your home" because it was written for one premise.
- Premises on very small lines, or with no damage history, return `status: insufficient_data` with a plain-language
  fallback instead of a number.
- No weather data, live crew locations or road conditions are used.
- Alert timing has no quiet hours because no time zone is known. Estimate-updated rules need the caller to supply the
  earlier estimate (`prior_typical_hours`).
