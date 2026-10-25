## Context

Alerts should be explainable at a glance: why this customer qualifies, when they would hear from us, on what channel,
and when a person should step in. The rules are prototype assumptions, not AEP policy, and the output says so.

Inputs available (from the repo, 2026-10-24):

| Input | From | Notes |
| --- | --- | --- |
| Risk band, or `insufficient_data` | spec-02 | Low, Medium, High, or unrated |
| Restoration `p50_hours`, `p90_hours` | spec-03 | until it ships: `typical_hours`, `high_hours` |
| Declared needs | request | `medical_equipment`, `mobility_limited`, `electric_heat_or_cooling`, booleans, self-declared only |
| Channels the customer has | request or enrollment | `sms`, `push`, `email`, `voice` |
| Prior typical estimate | request | for estimate-updated |
| Hours until storm | request (new, optional) | 0 to 168 |

Existing behavior (`alerts-v1`) has rules R1 to R10 in code: eligibility (R1 to R6), channel order (voice first for a
medical need), pre-storm lead times by band, and escalations R7 to R10. The new table keeps that intent and makes it data.
Constraints: needs are never inferred; escalations are recommendations to a person, not automatic actions; no real
personal data in the demo; no real messages are sent.

## Goals / Non-Goals

**Goals:**
- One table that anyone can read: band x duration x needs -> tier, timing, channel, escalation.
- Every output names the rule that produced it.
- A sign-up flow a judge can click through without risking real data or real messages.
- An audit trail for every decision.
- Four templates, four channels each, safe to send without an LLM.

**Non-Goals:**
- Real delivery, real contacts, reply handling, quiet hours by local time, accounts, authentication.
- Changing risk or restoration numbers.
- Wording by the LLM (spec-06).

## Decisions

### D1. Buckets
Duration bucket from `p50_hours` (same cutoffs as the guidance module): `short` < 8, `medium` 8 to 23, `long` 24 to 71,
`extended` >= 72, `unknown` when there is no estimate. Band is `Low`, `Medium`, `High`, or `Unrated`
(insufficient data). Needs flags: `medical`, `any_need`.

### D2. Rules table (data)
`alerts/rules_table.json` holds the tables below with a `version`. A loader validates that every cell exists, that
tiers are valid, and returns a `table_hash`. Code never branches on band or duration directly.

Base tier (rule ids `T-<band>-<duration>`):

| Band \ Duration | short | medium | long | extended | unknown |
| --- | --- | --- | --- | --- | --- |
| Low | info | info | standard | standard | info |
| Medium | standard | standard | elevated | elevated | standard |
| High | standard | elevated | elevated | priority | elevated |
| Unrated | n/a | n/a | n/a | n/a | standard |

Needs adjustment (applied in this order):

| Rule | Condition | Effect |
| --- | --- | --- |
| N1 | `any_need` | raise tier one step (info -> standard -> elevated -> priority, capped) |
| N2 | `medical` | tier at least `elevated` |
| N3 | `medical` and (`p50_hours >= 4` or Unrated) | tier `priority` |

Tier behavior (rule ids `B-<tier>`):

| Tier | Pre-storm lead hours | Outage-confirmed within | Min gap between estimate updates | Channel order (only enrolled channels) |
| --- | --- | --- | --- | --- |
| info | none (opt-in only) | 15 min | 6 h | push, email |
| standard | 24, 6 | 10 min | 4 h | sms, push, email |
| elevated | 48, 24, 6 | 5 min | 2 h | sms, push, voice, email |
| priority | 72, 48, 24, 6 | 2 min | 1 h | voice, sms, push, email |

Restoration-complete is sent at once for every enrolled customer (rule `K-restored`).
A channel the customer has not enrolled is never used. If none is enrolled the message appears in the app only, and
the response says so. If the customer declared a medical need and has no voice channel, the response recommends
adding one. It does not add one.

Eligibility per message type (rule ids `K-*`):

| Type | Eligible when | Rule |
| --- | --- | --- |
| pre_storm | tier is standard, elevated, or priority | `K-pre` |
| pre_storm | tier is info | not sent unless the customer opted in (`K-pre-optin`) |
| outage_confirmed | always for enrolled customers | `K-out` |
| estimate_updated | `abs(new_typical - prior_typical) >= max(2, 0.25 x prior_typical)` and the tier's minimum gap has passed | `K-upd` |
| restoration_complete | always for enrolled customers | `K-restored` |

Escalation triggers (rule ids `E*`). Each is a recommendation for a person. Nothing happens automatically.

| Rule | Condition | Recommendation |
| --- | --- | --- |
| E1 | `p50_hours >= 24` | customer care follow-up |
| E2 | `medical` and (`p50_hours >= 4` or Unrated) | priority outreach |
| E3 | `p90_hours >= 72` | share shelter and community resource information |
| E4 | `p90_hours / p50_hours > 4` and `p90_hours - p50_hours >= 24` | human review of the estimate before it is shared |
| E5 | `any_need` and `p50_hours >= 24` | resource outreach for the declared need |

All thresholds are named constants in the JSON with a `reason` string in plain language. They are assumptions.
The E4 rule was first set for the v1 range (ratio above 3). Task 6.3 re-checked it against restoration-v2 and replaced it with a ratio above 4 plus a 24-hour gap (see "E4 check against restoration-v2" below).
Mapping from v1: R1 to R3 -> `T-*` plus `K-pre`; R4b -> `K-out`; R5 -> `K-upd`; R6 -> `K-restored`; R7 -> E1;
R8 -> E2; R9 -> E3; R10 -> E4.

### D3. Timing with `hours_until_storm`
When `hours_until_storm = h` (0 to 168) is given, each lead time `L` in the tier's list gets a status:
- `scheduled` if `L < h` (that moment is still ahead).
- `send_now` for the smallest `L` with `L >= h`, when `h > 0` (one catch-up message, so a customer is not sent a pile
  of missed alerts at once).
- `skipped` for every other `L >= h`, with the reason "that time has already passed".
- If `h = 0` (the storm has started) every lead time is `skipped`.

If `h` is omitted, the full lead list is returned with no status. Moving `h` changes the output, which a judge can see.

### D4. Evaluate output contract (`POST /alerts`)

```json
{
  "engine_version": "alerts-v2", "rules_table_version": "2026-10-24.1", "rules_table_hash": "sha256:…",
  "decision_id": "d_8f3a…", "qualifies": true, "tier": "elevated",
  "tier_reason": "Medium risk and a long expected outage; you declared a need, which raises the level.",
  "eligibility": {"pre_storm": {"eligible": true, "rule": "K-pre", "reason": "…"}},
  "timing": {"pre_storm": [{"hours_before_storm": 24, "status": "scheduled"}],
             "outage_confirmed": {"within_minutes": 5}, "estimate_updated": {"min_gap_hours": 2},
             "restoration_complete": {"when": "as soon as restoration is confirmed"}},
  "recommended_channels": ["sms", "push"], "channel_note": "…",
  "escalation": [{"rule": "E1", "action": "customer_care_follow_up", "reason": "…"}],
  "rules_fired": ["T-Medium-long", "N1", "B-elevated", "K-pre", "E1"],
  "assumptions": ["These alert rules are prototype assumptions, not utility policy.", "…"]
}
```

`qualifies` is true when pre-storm alerts are eligible (tier `standard` or above, or the customer opted in). Outage-confirmed and
restoration-complete are available to every enrolled customer, so counting them would make `qualifies` always true; the response
carries a `qualifies_note` that says this. `rules_fired` is the list used by the audit log. The response also carries
`enrolled` and `decision_id`.

### D5. Templates
Four message types x four channels. Canonical text is in `messages/templates.py`, built from placeholders
`{risk_level}`, `{scenario}`, `{estimate}`, `{prior_estimate}` that only the fact card can fill.

| Type | Content rule |
| --- | --- |
| pre_storm | Says the risk level for the scenario. Any restoration time is conditional ("If you lose power ...") and called an estimate. If unrated: "prepare as if an outage is possible". |
| outage_confirmed | Says an outage is confirmed. Gives the estimate if there is one, called an estimate, not confirmed. Says an update will follow. |
| estimate_updated | Gives the new estimate and the earlier one if known. Says it is still an estimate. |
| restoration_complete | Says power is restored and asks the customer to report if it is still out. Tells the customer to stay away from downed lines, with no number. No digits anywhere in the text. |

Channel limits: sms 480, push 200, email 1000, voice 500 characters. Voice text avoids symbols and reads numbers as ranges
("6 to 14 hours"). The safety line (downed lines, call 911) is appended to pre_storm and outage_confirmed by code
(owned by spec-06). It is not appended to restoration_complete, which must stay free of digits, so that template carries its
own stay-away sentence. Templates never contain links, names, or phone numbers. Ownership: this spec owns template text and
tests; spec-06 owns LLM phrasing and the guard. Both use the same module so the fallback is identical to what the guard
tests.

### D6. Sign-up (demo only)
`POST /alerts/enroll {token, channels: [{type, value}], needs?, message_types?}`.

Validation (a rejection returns 400 with "Demo only: use a fictional number like 555-0123 or an email at example.com."):
- `type` in `sms`, `voice`, `push`, `email`. `push` takes a fixed `value` of `"app"` (no device token is collected).
- Phone (`sms`, `voice`): North American format whose exchange and line are `555-0100` to `555-0199` (reserved for fiction).
- Email: domain is `example.com`, `example.org`, `example.net`, or ends in `.test` or `.invalid` (reserved by RFC 2606 and 6761).
- At most 4 channels, one per type. Values at most 64 characters. No name, address, or free text is accepted.
- `needs` uses the same booleans as the other routes. They are optional and only stored if the customer supplies them.

`message_types` is the customer's choice of which of the four messages they want (default all four). For the `info` tier, keeping
`pre_storm` in that list is the opt-in for storm alerts. A customer who enrolls with the default list is therefore opted in.

Storage (`AlertStore`): key `token`, with channel type, validated value, `needs` flags, message types opted in,
`created_at`, and `expires_at` (24 hours; DynamoDB TTL). One enrollment per token (upsert). Responses show contact values
masked (`***-0123`, `e***@example.com`). Contact values are never logged and never returned in full.
Implementations: `MemoryStore` (local and tests), `DynamoStore` (cloud, single table, TTL). The cloud table is created
by CDK after review.

`POST /alerts/unenroll {token}` deletes the record and its outbox.

### D7. Mock delivery
`Delivery` is an interface with one implementation, `MockDelivery`. `POST /alerts/simulate {token, event, ...scenario}`
evaluates eligibility for the event, builds the message from the template (or from spec-06 when `use_llm` is true),
writes an outbox item `{channel, text, status: "not_sent_demo"}`, and returns it. It requires an enrollment, a chosen message
type, and an eligible event; otherwise it records nothing and returns a reason code (`not_enrolled`, `message_type_not_chosen`,
`not_eligible`), with an audit entry either way. `use_llm` defaults to false here. No network call is made. The code
imports no SMS or email client. The Lambda role has no `sns:Publish`, `ses:Send*`, `sms-voice:*`, or `mobiletargeting:*`;
a test scans the IAM script and CDK stack for those actions.

### D8. Audit log
One entry per decision, written for `POST /alerts`, `/alerts/simulate`, and enrollment changes:

```json
{"decision_id": "d_8f3a…", "ts": "2026-10-24T20:44:57Z", "route": "alerts", "token": "p_9f2c…",
 "engine_version": "alerts-v2", "rules_table_hash": "sha256:…",
 "inputs": {"band": "Medium", "duration_bucket": "long", "p50_hours": 30, "p90_hours": 55, "any_need": true, "medical": false,
            "channels_enrolled": ["sms", "push"], "prior_typical_hours": null, "hours_until_storm": 36},
 "rules_fired": ["T-Medium-long", "N1", "B-elevated", "K-pre", "E1"],
 "outputs": {"tier": "elevated", "channels": ["sms", "push"], "eligible": {"pre_storm": true}, "escalation": ["E1"]}}
```

- Stored in the table with a 7-day TTL and emitted as a structured log line. No contact values, no text of messages.
- Needs appear only as two booleans. They are sensitive because they are self-declared medical information, so the TTL
  is short and the log is never part of any response except `GET /alerts/audit`.
- `GET /alerts/audit?token=p_…` returns up to 50 latest entries for that token. The API has no authentication, so
  anyone holding the opaque token can read its entries. That is acceptable for a demo only and is stated in `docs/api.md`.

### D9. Privacy and safety
- Self-declared needs only; the engine never infers them.
- Escalations are worded as recommendations, with no automatic dispatch.
- Emergency notice ("In an emergency, call 911") is included in the evaluate response via the guidance module.
- Responses pass the existing identifier scan. Contact values are masked before the scan.
- Request bodies stay under 8 KB and unknown fields are rejected, as in the rest of the API.

### D10. Code layout
```
backend/concierge/alerts/
  rules_table.json   tables from D2 with version and plain-language reasons
  table.py           loader, validation, table_hash
  evaluate.py        buckets -> tier -> timing -> channels -> escalation -> rules_fired
  enroll.py          validation (D6), masking, enroll and unenroll
  stores.py          AlertStore interface, MemoryStore, DynamoStore (single table, TTL)
  delivery.py        Delivery interface, MockDelivery, outbox
  audit.py           entry builder, store write, log line
backend/concierge/messages/templates.py   four types x four channels
```

## Risks / Trade-offs

- [Rules are assumptions] -> Labeled in every response and in the docs. The table is data so reviewers can edit it.
- [Fictional-contact check can be bypassed by a user typing real data in an example.com address] -> Only reserved
  ranges are accepted, so a real mailbox or number cannot be stored. The UI says "use fake details".
- [Audit entries hold self-declared need flags] -> Short TTL, booleans only, token not raw ID, no authentication caveat stated.
- [DynamoDB table is new infrastructure] -> Single table with TTL, pay-per-request, applied only after review.
- [Tier names imply a service level] -> Labeled "alert level (prototype)". Not a utility commitment.
- [Quiet hours] -> Not implemented; no time zone is known. Listed as a limitation.

## Migration Plan

1. Land table, evaluator, and tests with `MemoryStore`. Keep `POST /alerts` backward compatible.
2. Switch `POST /alerts` and `/summary` to the new evaluator once golden tests pass.
3. Add enrollment routes locally, then the DynamoDB table in CDK after review.
4. Remove `alerts/rules.py` after nothing imports it.

## Open Questions

- Should `info` tier customers get pre-storm messages if they ticked "opt in"? The design says yes; confirm.
- Is a 24-hour enrollment expiry long enough for the judging window?
- The audit write is best effort: a failed write is logged and does not block the customer's answer. Should it fail closed instead?
- E4 was changed from P90/P50 > 3 to P90/P50 > 4 and P90 - P50 >= 24 hours after the restoration-v2 check below (it fires for
  about 5% of premises in a typical storm). Still open: the team should confirm that choice (option 2 below) and how many reviews a person can handle.

## E4 check against restoration-v2 (task 6.3)

Run on 2026-10-24 with `scripts/check_e4_ratio.py`: 3,000 random scored premises from the real artifacts (every one with a
tree position), default scenario (3 crews, normal access), four storm presets, the real `restoration-v2` engine. The rule under
test was the original rule in `rules_table.json`: E4 fires when `p90_hours / p50_hours > 3.0`.

| Storm preset | Median ratio | 90th percentile of ratio | Highest | E4 fires |
| --- | --- | --- | --- | --- |
| sep-2024-like | 3.67 | 4.00 | 10.4 | 93% |
| typical | 3.67 | 5.00 | 13.1 | 86% |
| feb-2025-like | 3.67 | 5.33 | 12.0 | 80% |
| beyond-observed | 3.67 | 5.50 | 13.3 | 79% |

Under a typical storm E4 fires for 98% of premises whose P50 is under 4 hours, 77% when it is 4 to 23 hours, and 27% when it is a
day or more. By risk band it fires for 97% of Low, 86% of Medium and 55% of High premises. If the ratio were set at 2 / 2.5 / 3 /
4 / 5 / 6, E4 would fire for 99% / 95% / 86% / 23% / 7% / 3% of premises in a typical storm.

**Result: the threshold of 3 no longer works** (the table above is the original rule, kept as the evidence for the change). The v2 range is wide by construction (a shared labor spread, a spread per repair,
crews that are present 85% of the time, and a long upper tail), so a typical P90/P50 is already 3.7. Short outages are the worst
case: with whole-hour rounding a P50 of 2 hours and a P90 of 8 hours already counts as "very wide". In v1 the ratio came from fixed factors (1.9 to 4.0, before
rounding), so 3 was a reasonable cut-off; with v2 it flags nearly everyone, and a "human review"
recommendation for 86% of customers is not a useful escalation.

**Decision (2026-10-24, applied; the team can still overturn it).** Option 2 below with the ratio raised to 4. The rules table now
holds `min_p90_over_p50_ratio = 4.0` and `min_p90_minus_p50_hours = 24`, and E4 fires only when both hold. This changes the rules-table hash,
so audit entries written before the change replay against the old hash only. The spec scenarios and tests use `p50 = 3, p90 = 30` (fires)
and `p50 = 4, p90 = 14` and `p50 = 2, p90 = 9` (do not fire). Options considered:

1. Raise the ratio to 5. E4 then fires for about 7% of premises in a typical storm.
2. Keep a ratio but add a floor, here `p90 - p50 >= 24` hours, so a 2-hour to 8-hour range is not "wide". This fits what
   a reviewer cares about (a wide range in absolute time) and removes the rounding effect. **Chosen, with ratio 4.**
3. Drop E4 and rely on E1 and E3, which already use absolute P50 and P90 thresholds.

**Re-run with the chosen rule** (`scripts/check_e4_ratio.py`, 3,000 random scored premises, 3 crews, normal access, artifacts rebuilt
from `data/` with a throwaway salt):

| Storm preset | E4 fires |
| --- | --- |
| sep-2024-like | 1.7% |
| typical | 4.5% |
| feb-2025-like | 7.1% |
| beyond-observed | 10.1% |

Under a typical storm E4 fires for 0.1% of Low, 4.1% of Medium and 20.8% of High premises, and for 0.1% of premises whose P50 is under
4 hours, 10.5% when it is 4 to 23 hours and 8.8% when it is a day or more. For comparison, in a typical storm a ratio of 4 alone would
fire for 22%, and ratio 4 with a gap floor of 12 / 24 / 36 / 48 hours would fire for 16% / 5% / 2% / 1%.
Whether a person can review 5% of customers in a typical storm (about 10% in the worst preset) is a staffing question for the team.
Caveats: three crews and normal access only, and the spreads in the range are assumptions that the backtest (`docs/backtest.md`) shows
are not fitted to observed repairs.
