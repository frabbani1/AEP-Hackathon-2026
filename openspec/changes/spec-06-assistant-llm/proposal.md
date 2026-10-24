## Why

Customers need the numbers explained in plain words, and need preparation, during-outage, and after-outage guidance
that fits their situation. The challenge also asks for four proactive messages, privacy, safety messages that are
clearly labeled, and a fallback when the model cannot be relied on. The project rule is firm: Python computes every
number, and the language model only phrases a whitelisted fact card.

Today only the four messages go through the model (`messages/compose.py`). There is no explanation prompt, and
the preparation list is rule-based only. The current guard lets the model copy numbers from the card, which makes
caching by bucket unsafe (two customers in one bucket have different numbers). There is also no stated defense
against prompt injection, and no protection against a customer-supplied value reaching a prompt.

## What Changes

- Define a versioned **fact card schema**: band, confidence, range, top factors, assumptions, label, plus stage,
  scenario, and a declared-needs flag. This is the only data the model ever sees.
- Add **eight prompts**: explanation, preparation (before), during-outage actions, after-outage actions, and the
  four messages. Each has a version and a strict output format.
- The model **never writes a number**. It writes placeholders (`{{range}}`, `{{prior_range}}`) that code fills in.
  A number guard rejects any digit or spelled-out number in a draft, so the same draft is safe to reuse.
- **Hard-coded safety text** (downed lines, generator carbon monoxide, 911, utility outage line) is added by code
  and cannot be changed or dropped by the model. Drafts that touch those topics are rejected.
- Add a **template fallback** for every prompt, and a short circuit breaker for Bedrock errors and throttling.
- Add a **cache** keyed by (band, range bucket, needs flag, stage), plus the extra fields that change the wording.
- Add **input validation and prompt-injection defense**: no data rows in the prompt, location tokens checked against
  an allow-list and never sent to the model, no free-text field reaches a prompt, and model output is parsed strictly.
- Keep **confirmed facts, model estimates, and customer actions** in separate response sections.

The model may rephrase preparation, during-outage, and after-outage items that **code selected**. It cannot add,
remove, or reorder them. This relaxes the current rule that guidance is never touched by the model, so it is called out
in the design for confirmation.

Out of scope: any number or decision made by the model, a free-text chat, retrieval over data, voice synthesis, a
translation layer, real message delivery (spec-05), and the risk and restoration engines (spec-02, spec-03).

## Capabilities

### New Capabilities
- `assistant-narration`: fact card, prompts, placeholder-based phrasing, number guard, template fallback, cache,
  section separation.
- `assistant-input-safety`: request validation, injection defense, hard-coded safety text, output validation.

### Modified Capabilities
None. There are no main specs yet. The four-message flow in `messages/compose.py` is brought under this spec.

## Impact

- New package `backend/concierge/assistant/` (fact card, prompts, guard, cache, safety, library, orchestration).
  `messages/compose.py` becomes a thin wrapper and `POST /messages` stays as an alias.
- New route `POST /assistant {token, kind, ...}`. `POST /messages` and `POST /guidance` keep their shapes.
- New item libraries for during-outage and after-outage actions (`guidance/during.py`, `guidance/after.py`), reviewed text only.
- Reads spec-03 `p50_hours`/`p90_hours` and spec-05 templates (the fallback texts for the four messages).
- Bedrock usage: same model setting (`BEDROCK_MODEL_ID`), shorter prompts, cached outputs, timeout and breaker.
  Cost goes down with the cache. No new AWS service. Optional Bedrock Guardrails are noted but not required.
- No new runtime dependencies.
