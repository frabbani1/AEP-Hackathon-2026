## 1. Rules table

- [ ] 1.1 Write `alerts/rules_table.json` from design D2 (base tier grid, N1 to N3, tier behavior, eligibility rules, E1 to E5), each with a plain reason and a `version`
- [ ] 1.2 Implement `table.py` loader and validator (all cells, valid tiers, reasons present) returning `table_hash`; tests for the missing-cell and bad-tier failures
- [ ] 1.3 Freeze v1 behavior with golden tests on `alerts/rules.py` outputs for 6 representative cases, to compare against the new evaluator

## 2. Evaluator

- [ ] 2.1 Implement duration and band buckets, read `p50_hours`/`p90_hours` with fallback to `typical_hours`/`high_hours`
- [ ] 2.2 Implement tier selection with N1 to N3 and `rules_fired`; one test per grid cell and per needs rule
- [ ] 2.3 Implement property tests: tier is non-decreasing in band, in duration, and when adding a need; no need rule fires without a declared need
- [ ] 2.4 Implement eligibility per message type including the estimate-change threshold and tier minimum gap; tests for each row
- [ ] 2.5 Implement timing with `hours_until_storm` (scheduled, send_now, skipped); tests for h = 0, 20, 60 and no value
- [ ] 2.6 Implement channel selection from enrolled channels, medical voice recommendation, and unknown-channel 400
- [ ] 2.7 Implement escalation E1 to E5 as advisory items; test each trigger and the advisory statement
- [ ] 2.8 Assemble the D4 response with `decision_id`, `qualifies`, `tier_reason`, assumptions; keep `/alerts` and `/summary` backward compatible; update `docs/api.md`

## 3. Templates

- [ ] 3.1 Move template text from `messages/compose.py` to `messages/templates.py` and extend to 4 types x 4 channels
- [ ] 3.2 Test all sixteen: length limits including the safety line, no digits in restoration-complete, "estimate" wording, no links or names or phone numbers, Unrated pre-storm text
- [ ] 3.3 Run every template through the existing message guard; keep `compose.py` working against the new module

## 4. Enrollment and mock delivery

- [ ] 4.1 Implement fictional-contact validation (555-0100 to 555-0199 phones, reserved email domains, `app` for push, field and length limits); tests for accept and reject lists, including near misses
- [ ] 4.2 Implement `EnrollmentStore`, `MemoryStore`, masking, upsert, expiry, unenroll; tests including masked output and 24-hour expiry
- [ ] 4.3 Implement `MockDelivery` and the outbox; implement `POST /alerts/simulate`; test eligible and ineligible paths
- [ ] 4.4 Add routes `POST /alerts/enroll`, `POST /alerts/unenroll`, `POST /alerts/simulate`, `GET /alerts/audit` to `api/handler.py` and `api/service.py`; keep unknown-field rejection and the 8 KB limit
- [ ] 4.5 Add a test that scans `scripts/setup_iam.sh` and `infra/lib/stack.ts` for `sns:Publish`, `ses:Send`, `sms-voice`, `mobiletargeting`, and that imports contain no messaging client
- [ ] 4.6 Add a log-capture test proving contact values never appear in logs or errors

## 5. Audit log

- [ ] 5.1 Implement audit entry builder and writer (store with 7-day TTL plus a structured log line); no contacts, no message text
- [ ] 5.2 Write an audit entry from `/alerts`, `/alerts/simulate`, enroll, and unenroll; test the entry matches the response `decision_id`
- [ ] 5.3 Implement `GET /alerts/audit` limited to 50 entries; test ordering and the limit
- [ ] 5.4 Test replay: feeding an entry's inputs back through the evaluator reproduces its outputs for the same table hash

## 6. Infrastructure and docs (review before applying)

- [ ] 6.1 Add the `aep-demo-alerts` DynamoDB table (pay-per-request, TTL attribute) to `infra/lib/stack.ts`; show the `cdk diff` to the team before any deploy
- [ ] 6.2 Add `ENROLL_TABLE` environment handling: `DynamoStore` when set, `MemoryStore` otherwise
- [ ] 6.3 After spec-03 ships, check E4's ratio against real P90/P50 values and record the result in `design.md`
- [ ] 6.4 Update `docs/api.md` with the new routes, the demo-only notice, and the no-authentication warning for enrollment and audit

## 7. Verification

- [ ] 7.1 Demo check: one customer per tier; add a declared need and show the tier rise; move `hours_until_storm` and show timing change; enroll with a fictional phone; simulate all four events; show the audit entries
- [ ] 7.2 Try to enroll a real-looking phone and email and show the rejection
- [ ] 7.3 Remove `alerts/rules.py` once nothing imports it
