## ADDED Requirements

### Requirement: Demo-only sign-up accepts fictional contacts only
`POST /alerts/enroll` SHALL accept channels `sms`, `voice`, `push`, and `email`. Phone numbers SHALL be accepted only in
the range `555-0100` to `555-0199`. Email addresses SHALL be accepted only at `example.com`, `example.org`, `example.net`, or a
domain ending in `.test` or `.invalid`. `push` SHALL take only the value `app`. Anything else SHALL return 400 with the message
"Demo only: use a fictional number like 555-0123 or an email at example.com." The endpoint MUST NOT accept a name, address, or
free text.

#### Scenario: Fictional phone accepted
- **WHEN** an `sms` channel has value `+1 614 555 0123`
- **THEN** the enrollment is stored and the response shows the number masked

#### Scenario: Real-looking phone rejected
- **WHEN** an `sms` channel has value `+1 614 867 5309`
- **THEN** the response is 400 with the demo-only message and nothing is stored

#### Scenario: Real email domain rejected
- **WHEN** an `email` channel has value `someone@gmail.com`
- **THEN** the response is 400 with the demo-only message and nothing is stored

#### Scenario: Extra fields rejected
- **WHEN** the body includes `name` or `address`
- **THEN** the response is 400 listing the unknown field

### Requirement: Mock store with expiry
Enrollments SHALL be stored in a mock store keyed by premise token, one per token, with channel types, validated values, optional
needs flags, opted-in message types, `created_at`, and `expires_at` set 24 hours ahead. Responses SHALL show contact values masked and
SHALL NOT return them in full. Contact values MUST NOT be written to logs, audit entries, or error messages. The store SHALL have an
in-memory implementation for tests and local runs and a DynamoDB implementation with a TTL for the deployed API.

#### Scenario: Upsert by token
- **WHEN** the same token enrolls twice
- **THEN** one record exists and it holds the second request's channels

#### Scenario: Masked on read
- **WHEN** an enrollment is returned
- **THEN** the phone shows only its last four digits and the email shows only its first character and domain

#### Scenario: Expiry set
- **WHEN** an enrollment is created
- **THEN** `expires_at` is 24 hours after `created_at`

#### Scenario: Contacts never logged
- **WHEN** enrollment and simulation requests run with log capture on
- **THEN** no log line contains a full phone number or email address

### Requirement: Unenroll
`POST /alerts/unenroll` SHALL delete the enrollment and its outbox items for the token and confirm in plain language.

#### Scenario: Unenroll removes data
- **WHEN** a token with an enrollment is unenrolled
- **THEN** reading its enrollment returns none and its outbox is empty

### Requirement: Mock delivery only
The system SHALL deliver messages only through a mock that records an outbox item with `status = "not_sent_demo"`. The code
MUST NOT import or call any SMS, voice, push, or email client. The Lambda role MUST NOT include `sns:Publish`, `ses:Send*`,
`sms-voice:*`, or `mobiletargeting:*`.

#### Scenario: Simulated event writes to the outbox
- **WHEN** `POST /alerts/simulate` runs for an eligible event
- **THEN** the response contains the channel, the message text, and `status = "not_sent_demo"`

#### Scenario: Ineligible event sends nothing
- **WHEN** the event is not eligible for the customer
- **THEN** the response says why, the outbox is unchanged, and the audit log records the decision

#### Scenario: No real delivery permission
- **WHEN** the IAM script and CDK stack are scanned
- **THEN** none of the forbidden actions appear

### Requirement: Audit log of every alert decision
The system SHALL write one audit entry per evaluation, simulation, enrollment, and unenrollment. Each entry SHALL contain a decision id,
timestamp, route, premise token, engine version, rules-table hash, the inputs used (band, duration bucket, estimate hours, need
flags as booleans, enrolled channel types, prior estimate, hours until storm), the rule ids fired, and the outputs. Entries MUST NOT
contain contact values, message text, raw premise numbers, addresses, or circuit or device identifiers. Entries SHALL expire after 7 days.
`GET /alerts/audit` SHALL return up to 50 latest entries for a token.

#### Scenario: Every evaluation is logged
- **WHEN** `POST /alerts` is called
- **THEN** one audit entry exists with the same `decision_id` as the response and the `rules_fired` list

#### Scenario: Inputs reproduce the decision
- **WHEN** an entry's inputs are fed back to the evaluator with the same rules-table hash
- **THEN** the tier, channels, eligibility, and escalation equal the entry's outputs

#### Scenario: No contacts in the audit
- **WHEN** audit entries for an enrolled token are scanned
- **THEN** no phone number, email address, or message text is present

#### Scenario: Audit read is limited
- **WHEN** 80 decisions exist for a token
- **THEN** `GET /alerts/audit` returns the 50 latest

### Requirement: No real personal data and clear demo labeling
Every enrollment response SHALL state that this is a demo, that nothing is sent, and that only fictional contact details are
accepted. The API documentation SHALL state that the audit and enrollment routes have no authentication and are for demonstration only.

#### Scenario: Demo notice present
- **WHEN** an enrollment succeeds
- **THEN** the response includes the demo notice
