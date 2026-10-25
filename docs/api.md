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
| `POST /zones` | `severity?` (also `weather`), `as_of?` | the outage map: every area's outline, risk band and confidence for one storm scenario, and the smallest group of homes an area may cover. With `severity: "weather"` each area uses the weather at its own location (see "Weather"). `503` until `zones.json` is published |
| `POST /zones/find` | `query` (a city or 5-digit ZIP code) | `status: "found"` with the area (`zone_id`, `zone_name`, `match`, `exact`, `nearby`), or `status: "not_covered"` with `covered_places`. Street addresses are rejected with `400`. The text is compared with public place and ZIP tables, then dropped: it is not stored, logged or echoed. See "Outage map" |
| `POST /zones/assess` | `zone_id`, `severity?` (also `weather`), `as_of?`, `needs?`, `crews?`, `access?`, `damage?` or `damage_count?`, `use_llm?` (default `false`) | all four answers for one area in the shape the map shows: risk, restoration (P50 to P90, steps, what moves it), preparation, alert level and eligibility, four sample messages, assumptions. See "Outage map" |
| `POST /outlook` | `query` (a US city or 5-digit ZIP code) | a **forecast-only outlook** for a place with no outage data: `status: "ok"` with `label: "Prediction only"`, a four-step weather `outlook.level` (`quiet`, `watch`, `elevated`, `high`), forecast facts, hazards, general preparation, and `not_available` (outage risk rating, restoration time, alert eligibility). `status: "not_found"` when nothing matches; `400` for a street address; `503` if the place lookup or weather is down. See "Forecast-only outlook" |
| `POST /weather` | `zone_id`, `as_of?` | current conditions, the last 24 hours and the next 48 hours for one area (or 48 hours from a past date), hazards, and the storm strength they imply. `503` if Open-Meteo does not answer. See "Weather" |
| `POST /summary` | same as `/alerts` | risk + restoration + guidance + alerts in one call (one premise-table read, plus one alerts-table read for the sign-up) |

Inputs

- `token`: `p_` plus 16 hex characters (an opaque premise token from the clean tables).
- `severity`: `sep-2024-like`, `typical` (default), `feb-2025-like`, `beyond-observed`, or a number from 0.25 to 3.0. On `/zones` and `/zones/assess` it can also be `weather`: storm strength then comes from real weather at the area (see "Weather"). Token routes (`/risk`, `/summary` and the rest) answer `400` to `weather`, because a token carries no location.
- `as_of`: a past date such as `2025-02-16`, only with `severity: "weather"` (and on `/weather`). It replays 48 hours of that date's weather instead of using the live forecast.
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
  under the token salt) and is not a location. Names come from public place names near the area's outline (`concierge/zones/places.py`,
  OpenStreetMap place coordinates), for example `Salem`, `Roanoke` or `Northeast Roanoke`, and are unique. No customer field is used.
- A zone is **rated through one representative premise**: the one whose typical-storm risk score is the middle one among the
  zone's rated premises (ties broken by token). Its answers describe a typical home in the area, not the whole area. The
  response says so in `transparency.assumptions`. A zone with no rated premise returns `status: insufficient_data`, no
  risk or restoration, and the most common fallback reason among its premises.
- `/zones/find` turns a typed city or ZIP code into an area: the ZIP's centre (US Census ZCTA table) or the place's coordinates
  (`places.py`) is matched to the area whose outline contains it, else the nearest area within 8 km. Anything else
  (a place or ZIP outside the data, "Salem, OH") answers `not_covered`. Coverage is only as wide as the data: the service data
  covers the Roanoke Valley and nearby counties, so the table holds places and ZIPs there and nowhere else.
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

## Forecast-only outlook (`POST /outlook`)

For a city or ZIP code outside the outage data (`/zones/find` answered `not_covered`). The risk and restoration engines are
fitted to one service territory, so for anywhere else they say nothing. The outlook gives only what a weather forecast can
support, and is built to be impossible to confuse with the outage report.

- **What it is.** A prediction from the next 48 hours of forecast: the peak wind gust, plus ice, heavy snow, thunderstorms and
  heavy rain when forecast. It maps to a four-step level (`quiet` at or below 25 mph with no hazards, `watch` above 25 mph or with
  thunderstorms or heavy rain, `elevated` from 46 mph or with ice or heavy snow, `high` from 58 mph). The cut-offs reuse the
  prototype's wind thresholds. They are display assumptions, not a fitted outage model, and the response says so (`outlook.method`).
- **What it is not.** No risk band, confidence, restoration range, alert level or sample messages, and no model-written text.
  `not_available` lists the three things it does not give. Preparation is the guidance engine's general list for the level.
- **How it is labeled.** `label` is `"Prediction only"` and `notice` says there are no outage records for the place. The web app
  draws it in purple (`--forecast`), a color used for nothing else, with dashed borders, a "Prediction only" tag, a four-bar
  meter and the words "no outage data". The map legend carries the same key.
- **Privacy.** The typed city or ZIP is sent to Open-Meteo's geocoding service (`concierge/weather/geocode.py`) to find
  coordinates, because the repository has no place table beyond the Roanoke Valley. It is the only thing sent. Street addresses
  (any digits other than a plain ZIP) are refused before anything is sent. The text is not stored, cached, logged or echoed.
  Weather is then requested at the place's coordinates rounded to the 0.05 degree grid, and no coordinate is returned.
- **Failure.** `WEATHER_ENABLED=0` turns the route off (`503`). If the lookup or the weather source does not answer it is a `503`;
  an unknown place is `200` with `status: "not_found"`.
- **Response keys** (snake_case): `status`, `source` (`forecast_only`), `label`, `notice`, `place.name`, `outlook`
  (`level`, `label`, `steps`, `of`, `summary`, `window`, `peak_gust_mph`, `peak_gust_time`, `method`), `hazards`
  (`id`, `level`, `text`), `weather` (`current`, `recent_24h`, `ahead_48h`, `attribution`, `basis`), `preparation`
  (`safety`, `actions`), `not_available`.

## Weather (`severity: "weather"`, `POST /weather`)

Weather is real data from [Open-Meteo](https://open-meteo.com) (CC BY 4.0, attribution is in every weather block). It sets
the storm strength; it does not replace the risk or restoration engines.

- **Where.** An area's weather is looked up at the middle of its outline, rounded to a 0.05 degree grid (about 5 km).
  That rounded coordinate is the only thing sent to Open-Meteo: no token, no premise value, no text from a customer.
- **When.** Live: the current conditions, the last 24 hours and the next 48 hours (`ahead_48h`). The storm strength reads only
  the forecast hours. With `as_of` it reads the 48 hours starting 00:00 UTC on that date, and the block says
  `kind: "historical"`. Live answers are cached for 15 minutes, past dates for the life of the Lambda instance.
- **What counts.** Peak wind gust in the window. It is placed on a straight line through the two storms in our damage data:
  the peak gust of each storm (from the Open-Meteo archive, `backend/concierge/weather/anchors.json`) maps to that storm's own
  multiplier. At or below 25 mph the scale reads its mildest scenario (0.25). Above the stronger storm the line continues and
  is capped at 3.0, and the answer is marked `extrapolated` with its confidence lowered one level.
  `scripts/calibrate_weather.py` regenerates the anchors.
- **What does not count.** Ice, snow load, rain, lightning and tree condition are shown in `hazards` (`modelled: false`) and
  do not move the rating. The two past storms were wind events, so the data cannot say how those other hazards matter.
- **How good is the scale.** It is two points and a line, so it is an assumption, not a fitted outage model. As a check,
  replaying the two storm dates across all map areas gives a median multiplier of 0.56 for 2024-09-27 (the engine's own
  value for that storm is 0.27) and 1.85 for 2025-02-16 (1.73). The check is partly circular: the anchors come from the same
  storms at the middle of the service area. Peak gusts differed by about 13 mph (2024-09-27) and 18 mph (2025-02-16) between areas on the same day, which is why each area is looked up
  on its own.
- **Basis.** Open-Meteo's current and recent values come from weather models fed by observations. They are not a reading
  from one measuring site, and the block says so (`basis`). Forecast values can change. Nothing here is a National Weather
  Service product.
- **Failure.** If Open-Meteo does not answer, `/zones` and `/zones/assess` still return 200 with the default typical-storm
  scenario and a `weather` block with `status: "unavailable"`, `reason` and `notice`. `/weather` returns 503.
- **Response keys** (snake_case): `status`, `source`, `attribution`, `basis`, `kind`, `as_of`, `fetched_at`, `location`
  (`area`, `grid_lat`, `grid_lon`), `units`, `current`, `recent_24h`, `ahead_48h` (or `window_48h` for a past date), `hazards`,
  `hourly`, `severity_window`, and `severity` (`source`, `multiplier`, `peak_gust_mph`, `method`, `anchors`).
  On `/zones` the block is a summary (`areas_total`, `areas_without_weather`, `peak_gust_mph_min`/`max`, `severity`
  with `multiplier_min`/`max`) and each area adds `multiplier` and `peak_gust_mph`.
- **Not in the model's words.** The weather block is built by code. The assistant and message prompts still say the model has
  no weather data, and the guards still reject any draft that mentions wind, rain, snow or ice. Text that names the scenario says
  "a storm of the strength the weather in your area points to".
- **Limits.** The free Open-Meteo tier is for non-commercial use with daily call limits; a commercial deployment needs a paid
  key. The Lambda calls the public internet, so a weather outage adds up to about 8 seconds to a request before it falls back.

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
- Weather sets the storm strength (wind gusts only, on request). Live crew locations and road conditions are not used.
- Alert timing has no quiet hours because no time zone is known. Estimate-updated rules need the caller to supply the
  earlier estimate (`prior_typical_hours`).
