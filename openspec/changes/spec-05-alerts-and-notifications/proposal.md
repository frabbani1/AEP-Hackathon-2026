## Why

The fourth customer question is "Do I qualify for outage alerts?". The challenge wants eligibility by documented
rules, timing, channel, escalation triggers, and messages for four moments: pre-storm, outage confirmed, estimate
updated, and restoration complete. It also wants privacy, safety, and clear limits.

A first version (`alerts-v1`, `backend/concierge/alerts/rules.py`) exists but is not under a spec. It has rules R1 to
R10 written as code branches, but:

- There is no tier. A customer sees eligibility, but not a single "what level of alerting is this" answer.
- Timing and channel do not depend on a documented risk x duration x needs table. They are scattered `if` statements.
- There is no sign-up flow, so a judge cannot see what "enrolling" would look like.
- Nothing records why a decision was made. A judge cannot audit it.
- Template text sits inside the LLM module (`messages/compose.py`), and only handles two channel shapes.

## What Changes

- Replace the `if` branches with a **rules table as data**: risk band x expected duration x declared needs gives a
  tier, pre-storm timing, update cadence, channel order, and escalation triggers. Every cell has a rule id and a
  plain reason.
- Add a **demo-only sign-up flow**. It accepts only fictional contact details (555-01xx phone numbers, example.com
  emails), stores them in a mock store with an expiry, and never sends anything. Delivery is a mock that writes to an
  outbox.
- Add **four message templates** (pre-storm, outage-confirmed, estimate-updated, restored), each in four channel
  variants, as the fixed fallback text. Spec-06 may rephrase them; this spec owns the canonical text.
- Add an **audit log** of every alert decision: inputs, rules fired, outputs, rules-table version.
- Add an optional `hours_until_storm` input so timing changes when a judge moves it.

Out of scope: real SMS, voice, push, or email delivery; real customer data; opt-out by reply; local-time quiet hours
(no time zone is known); LLM wording and the number guard (spec-06); the restoration model (spec-03); accounts or
authentication; any claim that these rules are utility policy.

## Capabilities

### New Capabilities
- `outage-alerts`: rules table, tier, timing, channels, escalation, eligibility per message type, and the four templates.
- `alert-enrollment`: demo sign-up, mock store, mock delivery, audit log.

### Modified Capabilities
None. There are no main specs yet. `alerts-v1` code exists and is brought under this spec.

## Impact

- Replaces `backend/concierge/alerts/rules.py` with `alerts/{table.py,evaluate.py,templates.py,enroll.py,delivery.py,audit.py}`
  and a rules file `alerts/rules_table.json`.
- Moves template text out of `messages/compose.py` into `messages/templates.py`; spec-06 imports it.
- New routes: `POST /alerts/enroll`, `POST /alerts/unenroll`, `POST /alerts/simulate`, `GET /alerts/audit`.
  `POST /alerts` and `POST /summary` keep their shape and add `tier`, `decision_id`, and `qualifies`.
- New DynamoDB table `aep-demo-alerts` with a TTL (CDK change, applied only after review). Local runs and tests use an
  in-memory store.
- Reads `p50_hours` and `p90_hours` from spec-03 (falls back to the shim fields until then).
- No new runtime dependencies. The Lambda role gets no SNS, SES, or Pinpoint permission, and a test enforces that.
