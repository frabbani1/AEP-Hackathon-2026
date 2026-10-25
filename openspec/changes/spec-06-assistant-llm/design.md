## Context

The engines produce numbers and decisions. The assistant turns them into words a customer can use, without ever
touching the numbers or the safety advice. The architecture rule from `CLAUDE.md` governs everything here: Python
computes, the model phrases a fact card, and any unsafe draft falls back to a template.

What exists today (read on 2026-10-24):
- `messages/compose.py`: fact card for the four messages, a Bedrock phraser (Converse API), a guard (numbers copied from
  the card are allowed, weather and promise words are blocked, length limits), templates, and a safety line added after the guard.
- `guidance/prepare.py`: rule-based preparation items. A comment says the model never writes them.
- `api/service.py`: strict request parsing, unknown-field rejection, 8 KB body limit, identifier scan on every response.
- `api/handler.py`: builds a Bedrock client from `BEDROCK_MODEL_ID`. Lambda is 512 MB, 20 s.

Gaps this design closes:
- The guard allows model-copied numbers, so cached text cannot be shared across customers.
- No explanation prompt, and no during-outage or after-outage content.
- No stated protection against prompt injection, and no cache.
- Bedrock throttling is handled only by a per-call fallback. It has not been exercised under load.

## Goals / Non-Goals

**Goals:**
- One fact card schema; eight prompts; strict, parseable outputs.
- The model writes no numbers and no safety text.
- Every prompt has a deterministic template fallback that is itself safe.
- Cache by bucket without ever serving a wrong number.
- A clear defense against injection through any request field.

**Non-Goals:**
- Chat, memory, retrieval, or tools for the model.
- Translating or voicing messages.
- Any model-made decision (who qualifies, tier, number, band).

## Decisions

### D1. Fact card schema (`factcard-v1`)
Built by code from the risk, restoration, guidance, and alert outputs. It is the only content the model sees.

```json
{
  "schema_version": "factcard-v1",
  "stage": "before",
  "status": "ok",
  "band": "Medium",
  "band_word": "moderate",
  "confidence": {"level": "Medium", "reason": "12 damage reports on your line in 2 past storms"},
  "scenario": {"preset": "typical", "words": "a typical storm", "extrapolated": false},
  "range": {"display": "about 6 to 14 hours", "bucket": "medium", "label": "estimate, not confirmed"},
  "prior_range": {"display": "about 4 to 9 hours"},
  "top_factors": [{"id": "hist", "direction": "raises", "text": "Your line had more storm damage than most lines in past storms."}],
  "assumptions": ["Storm strength is a scenario you choose, not a weather forecast."],
  "missing_data": ["No weather data"],
  "could_change": ["A stronger storm than any we've seen"],
  "label": "circuit-level historical susceptibility, not a per-premise forecast",
  "has_declared_need": true
}
```

Rules:
- `stage` is `before`, `during`, or `after`. The four message types map to a stage and a purpose.
- `range` and `prior_range` appear only for stages and message types that may state a time (spec-05 D5 rules:
  not in restoration-complete).
- The card holds no token, premise number, circuit, device, address, coordinate, or count of premises. The
  `top_factors[].text` strings are digit-free variants of the risk D8 sentences, kept in `factcard.py` and keyed by
  (factor id, direction). The risk engine's own sentences state numbers (reports per 1,000 homes, device counts), so
  they are not used. The full card keeps `range.display` and `prior_range.display` for code to fill placeholders. The
  card sent to the model is a projection (`to_prompt()`) where those two fields are replaced by the literal strings
  `{{range}}` and `{{prior_range}}`, and every other string that contains a digit (for example the confidence reason
  "12 damage reports on your line in 2 past storms" or the "16 hours out of every 24" assumption) is left out and
  replaced by a digit-free fixed sentence for its confidence level or assumption id. The model never sees a digit.
  Code-built fallback text may still use the full strings.
- The card also holds no declared-need detail, only `has_declared_need`. Need-specific advice is code-supplied text
  (D6) that is never sent to the model.
- `sanitize_card()` checks every string against a safe pattern (letters, digits, spaces, and `, . ' % ( ) / -`),
  a length cap, no braces or newlines, and a deny-list of instruction-like phrases. A failing card means no model call.

### D2. Eight prompts
Files `assistant/prompts/<kind>.v1.txt`. Each has a version, a hash recorded in the response, and a fixed output format.

| Kind | Purpose | Model output | Fallback (code) |
| --- | --- | --- | --- |
| `explanation` | Why this band and confidence; what could change | `{"text": ...}` up to 700 characters | band sentence + factor sentences + confidence reason + could-change items |
| `prep_before` | Preparation before the storm | `{"intro": ..., "items": [{"id", "text"}]}` | `intro` from guidance headline; library item text |
| `actions_during` | What to do during an outage | same shape | library item text |
| `actions_after` | What to do after power returns | same shape | library item text |
| `msg_pre_storm` | Pre-storm message | `{"text": ...}` | spec-05 template |
| `msg_outage_confirmed` | Outage-confirmed message | `{"text": ...}` | spec-05 template |
| `msg_estimate_updated` | Estimate-updated message | `{"text": ...}` | spec-05 template |
| `msg_restoration_complete` | Restored message | `{"text": ...}` | spec-05 template |

Shared system prompt rules: use only the card; plain language; short sentences; no numbers or spelled-out numbers;
write `{{range}}` or `{{prior_range}}` where a time belongs; call times estimates; say "if" for pre-storm times;
no weather claims (no weather data); no claims about what crews are doing, what caused the outage, or how long it lasted;
no safety advice (code adds it); no links, names, or phone numbers; return JSON only. The card is passed as data
inside delimiters and the prompt says to treat anything inside as data, not instructions.

For `prep_before`, `actions_during`, and `actions_after` the prompt includes the **generic items selected by code**
(id and fixed text) and asks the model to rephrase each in the customer's situation. It may not add, drop, reorder, or merge items.

### D3. Placeholders and the number guard
The model never writes a time. It writes `{{range}}` and `{{prior_range}}`; code replaces them from the card after the guard passes.

Guard (all must pass, else fallback):
1. JSON parses to exactly the expected keys and types. No extra keys.
2. After removing allowed placeholders, the text has **no digit** and no spelled-out number (`two` to `ninety`, `hundred`, `dozen`, `half`,
   `twice`, `double`, `triple`).
3. Placeholders used are a subset of those allowed for the kind, and the required ones are present (estimate messages need
   `{{range}}`; `msg_estimate_updated` with a prior needs `{{prior_range}}` too; `msg_restoration_complete` allows none).
4. Item ids are a subset of the selected ids; each selected id appears at most once; texts are non-empty.
5. No link, email address, phone number, internal term (circuit, transformer, substation), promise word, or weather word.
6. No safety topic words (see D6) because code supplies safety text.
7. Estimate wording: for kinds that state a time, the text says "estimate" (or "estimated"); pre-storm times use "if".
8. Length within the channel limit minus the safety text; average sentence length at most 20 words (plain-language check).
9. No instruction-like or meta phrases ("ignore", "system prompt", "as an AI", "I cannot").

Because drafts have no numbers, the same draft is valid for every customer in the same bucket.

### D4. Cache
Key = SHA-256 of canonical JSON of:
`(kind, band, range bucket, has_declared_need, stage)` plus the fields that change the wording:
`confidence level, scenario preset, status, channel (messages), sorted factor ids with directions, selected item ids,
prompt version, model id, schema version, library version`.

The brief's four fields are necessary but not sufficient, because a different channel or factor set needs different text.
Range buckets use the guidance cutoffs: short < 8 hours, medium 8 to 23, long 24 to 71, extended 72 or more, unknown.
Only drafts that **passed** the guard are stored, and only the placeholder form is stored. In-memory LRU (512 entries,
24-hour expiry) per Lambda instance; a cold start empties it. A shared cache (DynamoDB) is a later option.
The response says whether it was a cache hit. `use_llm = false` bypasses the cache and the model.

### D5. Model call, timeout, and breaker
Converse API, temperature 0.2, `maxTokens` by kind, read timeout 8 s, one retry on throttling or timeout, then fallback.
A breaker opens after 3 consecutive model failures in 60 seconds and skips the model for 60 seconds (template only), then
tries once. A template response from an open breaker records `reasons = ["model unavailable"]`.
A throttling test with a fake client must exercise this path; real load is a manual step (task 8.3).

### D6. Hard-coded safety text
`assistant/safety.py` holds verified constants. The model cannot see or alter them.

| Id | Text |
| --- | --- |
| `downed_lines` | Stay far away from downed power lines. Assume they are live, keep others away, and call 911. |
| `generator_co` | Never run a generator, grill or camp stove inside your home or garage. Carbon monoxide can kill. |
| `emergency` | In an emergency, call 911. Utility alerts and this tool do not replace emergency services. |
| `report_outage` | Report your outage through your utility's official outage line or app. |

`report_outage` does not give a phone number. The demo has no verified number, and one must not be invented. A deployment
sets `UTILITY_OUTAGE_CONTACT` to a verified value, and a missing value keeps the generic wording.

Placement by kind (code-added, after the guard, never inside the model text):

| Kind | Safety text |
| --- | --- |
| `explanation` | `emergency` |
| `prep_before` | `downed_lines`, `generator_co`, `emergency` as items marked `safety: true`, listed first |
| `actions_during` | same, plus `report_outage` |
| `actions_after` | `downed_lines`, `emergency` |
| `msg_pre_storm`, `msg_outage_confirmed` | `downed_lines` appended |
| `msg_estimate_updated` | none (matches current behavior) |
| `msg_restoration_complete` | none appended. Spec-05 requires this message to contain no digits, and the downed-line constant contains "911". The fixed template already says to stay away from downed lines, with no number. |

After assembly the code asserts that every required safety string is present verbatim, else it raises (a bug, not a customer error).
Guard rule 6 rejects model drafts that mention downed or live lines, generators, carbon monoxide, 911, or emergencies.

Need-specific items (medical equipment, mobility, electric heat or cooling) stay in the rule library from the guidance
module and are appended by code, verbatim, after the model's items. They are never sent to the model.

### D7. Library for during and after
`guidance/during.py` and `guidance/after.py` hold reviewed item texts with ids and minimum band and duration, in the same
shape as `prepare.py`. They contain no numbers. Food-safety timing is not stated; items point to the local health authority's guidance
instead, to avoid unverified claims. Items are general advice, not professional advice, and say so.

### D8. Response contract (`POST /assistant`)

```json
{
  "engine_version": "assistant-v1", "kind": "prep_before", "stage": "before",
  "sections": {
    "confirmed_facts": [],
    "confirmed_note": "No confirmed utility information is connected to this prototype.",
    "model_estimates": [{"id": "range", "text": "Restoration, if you lose power: about 6 to 14 hours.", "label": "estimate, not confirmed"}],
    "customer_actions": [{"id": "charge_devices", "text": "…", "safety": false}]
  },
  "text": "…", "safety": [{"id": "downed_lines", "text": "…"}],
  "source": "model", "model_id": "…", "prompt_version": "prep_before.v1",
  "guard": {"checked": true, "passed": true, "reasons": []},
  "cache": {"hit": false, "key": "sha256:…"},
  "facts_used": {"band": "Medium", "range": "about 6 to 14 hours"}
}
```

`source` is `model`, `cache`, or `template`. `confirmed_facts` is empty in this prototype and says so. Estimates and actions are never
mixed in the same list. `facts_used` is the fact card subset. `/messages` returns the same fields it does today.

### D9. Request validation and injection defense
- Route and body: JSON object under 8 KB; unknown fields rejected; types and ranges as in `api/service.py`.
- `token`: must match `^p_[0-9a-f]{16}$` and exist in the premise rows (the allow-list). A well-formed unknown token returns the
  insufficient-data fallback with **no model call**. The token is never placed in any prompt, cache key, or log field that leaves the API.
- `kind`, `channel`, `message_type`, `severity` preset, `access`: closed enums. Numbers: integer ranges. `needs`: three booleans.
  No field accepts free text, so no customer string can reach a prompt.
- The prompt is built only from the sanitized fact card and fixed library text. It contains no premise rows, no counts of homes,
  no circuit or device values. A test builds prompts for many real premises and scans for any row value.
- The system prompt and the card are separate Converse fields. The card sits inside delimiters and is described as data.
- Output is parsed with a strict JSON parser, keys are whitelisted, strings are length-capped, markdown and HTML tags are stripped,
  and the guard in D3 runs. Anything else falls back to the template.
- Defense in depth for the card: `sanitize_card()` rejects instruction-like phrases and unsafe characters, so even an
  upstream bug that puts odd text in a factor sentence cannot reach the model.
- Optional hardening: attach a Bedrock Guardrail through `GUARDRAIL_ID` if the team wants it. Not required for correctness.
- Logs: kind, source, guard reasons, cache hit, latency. No card text, no draft text, no token.

### D10. Code layout
```
backend/concierge/assistant/
  factcard.py     build + sanitize_card (D1)
  prompts/        eight .v1.txt files + shared system prompt
  guard.py        D3 rules, placeholder fill
  cache.py        key (D4), LRU with expiry
  phrase.py       Bedrock Converse call, timeout, breaker (D5)
  safety.py       constants and placement (D6)
  compose.py      orchestration: card -> cache -> model -> guard -> fill -> safety -> response
backend/concierge/guidance/during.py, after.py     item libraries (D7)
backend/concierge/messages/compose.py              thin wrapper kept for /messages
```

## Risks / Trade-offs

- [Model text feels generic without numbers] -> Placeholders put the range in the sentence; cache gives consistent wording.
- [Strict guard rejects good drafts] -> Rejections fall back to templates; reasons are logged so prompts can be tuned. Acceptance rate is measured (task 8.2).
- [Rephrasing guidance introduces a wording error] -> The model can only rephrase code-selected items, safety is code-only, and a reviewer can read the fixed fallbacks.
- [Cache serves text for the wrong situation] -> Key includes every field that changes wording; a property test checks that two cards with the same key give identical template output.
- [Injection through the fact card] -> No free text enters the card; strings are sanitized; token never sent.
- [Breaker hides a persistent outage] -> Response shows `source = template` and the reason; health route reports the breaker state.
- [Sending a declared-need flag to Bedrock] -> Only a boolean is sent. Need details are never sent. Bedrock stays inside the AWS account.

## Migration Plan

1. Land fact card, guard, cache, safety, and libraries with fake-model tests.
2. Route the four messages through the new flow; keep `/messages` identical in shape. Compare old and new fallback text.
3. Add `/assistant` and the three action kinds and the explanation.
4. Retire the old guard after a week of golden-test parity.
5. No cloud change is applied without review (project rule).

## Open Questions

- Confirm the policy change: the model may rephrase code-selected guidance items (it could not before). If not, `prep_before`, `actions_during`, and `actions_after` use fixed text only and the model writes just the intro.
- Which model ID will the demo use? `BEDROCK_MODEL_ID` currently selects it; prompts are model-agnostic but acceptance rates may differ.
- Should a shared cache (DynamoDB) be added, or is an in-memory cache enough for a hackathon demo?
- Is there a verified utility outage number the team can show? If not, the generic sentence stays.

## Implementation notes (2026-10-24)

Where the build differs from the text above, or adds to it:

- **Short downed-line sentence in messages.** D6 lists `downed_lines` for pre-storm and outage-confirmed messages. The existing line
  (`messages/templates.SAFETY_LINE`, 58 characters) is kept for messages because push is limited to 200 characters and task 8.1 needs
  the old and new fallback text to match. It is the constant `downed_lines_short` in `assistant/safety.py`. The longer
  `downed_lines` text is used for the three item kinds and the explanation does not carry it.
- **Scenario words.** `messages/templates.SCENARIO_WORDS` names "September 2024" and "February 2025", which are digits. The model gets
  `SCENARIO_PROMPT_WORDS` ("a milder past storm", "a stronger past storm"). Code-built fallbacks keep the old words.
- **Factor sentences.** The digit-free sentences for the overhead and underground service factors say "trees and falling branches"
  instead of "wind and trees", because the weather rule rejects the word "wind" and a model that copied the sentence would be refused.
- **Allow-list for what the model sees.** Besides the sanitizer, `to_prompt()` builds every field from a closed enum or a fixed
  sentence. Assumptions, missing data and could-change items pass only if they are one of the risk engine's own fixed sentences
  (`RISK_SENTENCES`) or one of the swapped digit-bearing ones. A test checks the engine against that set, so a new engine sentence fails
  the test instead of silently disappearing from the model's view.
- **Restoration assumptions.** The restoration engine's assumption list states crew hours and spreads. The card carries two fixed
  digit-free sentences instead.
- **Item kinds.** `text` in the response is the introduction. The items are in `sections.customer_actions`, safety first.
  `actions_during` carries `downed_lines`, `generator_co`, `emergency` and `report_outage`.
- **Token pattern.** `risk/tokens.TOKEN_RE` ended in `$`, which accepts a trailing newline (`"p_0123456789abcdef\n"`). It now ends in `\Z`.
  This was found by the assistant's injection tests and affects every route that validates a token.
- **Identifier scan.** `prompt_hash` and `cache.key` are SHA-256 values that can contain six digits in a row, so they are exempt
  from the scan like `config_hash`. Nothing else changed in the scan.
- **Model client.** `api/handler.py` builds the Bedrock client with an 8 s read timeout, a 3 s connect timeout and one SDK attempt, because the
  assistant makes its own single retry. `/messages` shares the client, so it gets the same timeouts.
- **Open question on rephrasing.** The code follows the proposal: the model may rephrase code-selected items. It cannot add, drop or reorder them,
  and need-specific items are never sent. If the team says no, set the three item kinds to fixed text and keep only the introduction.

## Real-model check (task 8.2)

Run on 2026-10-24 with Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`, us-east-2), cache off, 14 premises from artifacts built from `data/`
with a throwaway salt (4 each of Low, Medium and High, 2 that cannot be rated) x 8 kinds = 112 calls, default scenario, sms channel.

| Run | Accepted | Notes |
| --- | --- | --- |
| First prompts | 95 of 112 (85%) | All four message kinds accepted every time. The explanation was accepted 3 of 14: 11 drafts too long, 8 with sentences over the limit, 1 with a spelled-out number. Item kinds: 6 drafts with long sentences. |
| After prompt tuning (shorter explanation, "under about fifteen words" for sentences and items) | 109 of 112 (97%) | Remaining rejections: 2 `actions_during` drafts with a weather word, 1 pre-storm draft that did not call the time an estimate. |

The guard was not changed. One bug surfaced here and was fixed in code, not prompts: the explanation fallback failed the wording check
for real premises because a fixed factor sentence said "wind" (see above). Latency per call on a laptop: p50 0.7 to 0.9 s, p95 1.5 to 1.9 s, max 3.4 s.
The model often wraps its JSON in a code fence; the parser removes one surrounding fence and nothing else.
Not measured: the burst test against the deployed API (task 8.3), and acceptance on other models.
