## ADDED Requirements

### Requirement: Rules table as data
The engine SHALL derive every alert tier, timing, channel order, and escalation from a versioned rules table
(`rules_table.json`) keyed by risk band (Low, Medium, High, Unrated), expected duration bucket (short, medium, long,
extended, unknown), and declared-needs flags. The loader SHALL reject a table with a missing cell, an unknown tier, or
a missing plain-language reason, and SHALL return a hash of the table. Evaluator code MUST NOT branch on band or
duration outside the table.

#### Scenario: Every cell exists
- **WHEN** the table is loaded
- **THEN** each band x duration cell for Low, Medium, and High, and the Unrated x unknown cell, has a tier and a reason

#### Scenario: Incomplete table is rejected
- **WHEN** a cell is removed from the table
- **THEN** loading fails with the name of the missing cell

#### Scenario: Response names the table
- **WHEN** any evaluation is returned
- **THEN** it includes `rules_table_version` and `rules_table_hash`

### Requirement: Alert tier from risk, duration, and declared needs
The engine SHALL compute a tier (`info`, `standard`, `elevated`, `priority`) from the base table, then apply rule N1
(any declared need raises the tier one step, capped at `priority`), N2 (a declared medical-equipment need sets the tier
to at least `elevated`), and N3 (a declared medical-equipment need with `p50_hours >= 4`, or an Unrated premise,
sets the tier to `priority`). The response SHALL list each rule applied in `rules_fired` and give a plain-language
`tier_reason`.

#### Scenario: Higher risk gives an equal or higher tier
- **WHEN** only the band changes from Low to High with the same duration and no needs
- **THEN** the tier is the same or higher

#### Scenario: Declared need raises the tier
- **WHEN** `mobility_limited = true` is added to a Medium-risk, long-duration case
- **THEN** the tier rises from `elevated` to `priority` and `rules_fired` includes `N1`

#### Scenario: Medical need floor
- **WHEN** `medical_equipment = true` on a Low-risk, short-duration case with `p50_hours = 2`
- **THEN** the tier is at least `elevated` and `rules_fired` includes `N2`

#### Scenario: Needs are not inferred
- **WHEN** no needs are declared
- **THEN** no need rule appears in `rules_fired`, regardless of band, duration, or location

### Requirement: Eligibility per message type
The engine SHALL return, for pre-storm, outage-confirmed, estimate-updated, and restoration-complete messages,
whether the customer is eligible, the rule id, and a plain reason. Pre-storm is eligible at tiers `standard` and above;
at tier `info` it is sent only if the customer opted in. Outage-confirmed and restoration-complete are eligible for every
enrolled customer. Estimate-updated is eligible only when the typical estimate changed by at least
`max(2, 0.25 x prior)` hours and the tier's minimum gap since the last update has passed. When no earlier estimate is
supplied there is nothing to compare, so the customer is eligible to receive updates and the reason says the change
threshold applies once there is an earlier estimate. `qualifies` SHALL be true when pre-storm alerts are eligible; outage and
restoration alerts are available to every enrolled customer, and the response SHALL say so.

#### Scenario: Low-risk customer without needs does not get pre-storm alerts
- **WHEN** the band is Low, the duration is short, and no needs are declared
- **THEN** pre-storm is not eligible, the reason says the customer can opt in, and outage-confirmed is still eligible

#### Scenario: Small estimate change is not worth a message
- **WHEN** the typical estimate moves from 10 hours to 11 hours
- **THEN** estimate-updated is not eligible and the reason says the change is too small

#### Scenario: Qualifies means storm alerts
- **WHEN** the customer is Low risk with no needs and has not opted in
- **THEN** `qualifies` is false and the response says outage and restoration alerts are still available to enrolled customers

#### Scenario: Opting in qualifies a basic-level customer
- **WHEN** the same customer sets `opt_in = true`
- **THEN** pre-storm is eligible with rule `K-pre-optin` and `qualifies` is true

#### Scenario: No earlier estimate
- **WHEN** no prior estimate is supplied
- **THEN** estimate-updated is eligible and the reason says updates are sent when the estimate changes by a meaningful amount

#### Scenario: Unrated premise still gets a general alert
- **WHEN** the premise cannot be rated
- **THEN** pre-storm is eligible at tier `standard` or higher with a reason that says the risk could not be rated

### Requirement: Timing
The engine SHALL return pre-storm lead hours by tier (`info` none, `standard` 24 and 6, `elevated` 48, 24 and 6,
`priority` 72, 48, 24 and 6), a maximum delay for outage-confirmed, and a minimum gap for estimate updates, all from
the table. When `hours_until_storm` is given, each lead time SHALL be `scheduled` if still ahead, `send_now` for the
single smallest lead time already reached, and `skipped` for the others; at `hours_until_storm = 0` all are `skipped`.

#### Scenario: Catch-up sends one message
- **WHEN** the tier is `elevated` and `hours_until_storm = 20`
- **THEN** the 48 and 24 lead times are `skipped` except that 24 is `send_now`, and 6 is `scheduled`

#### Scenario: Timing responds to input
- **WHEN** `hours_until_storm` changes from 60 to 10
- **THEN** the lead-time statuses change

### Requirement: Channel selection
The engine SHALL order channels by tier from the table and SHALL use only channels the customer has enrolled or
supplied. If the customer declared a medical-equipment need and has no voice channel, the response SHALL recommend adding
one without adding it. If an empty channel list is supplied, the response SHALL say messages appear in the app only. If no channel
list is supplied and the customer has no enrollment, the response SHALL suggest the top two channels for the tier and say they are
suggestions. Unknown channels SHALL be rejected with a 400.

#### Scenario: Voice first at priority
- **WHEN** the tier is `priority` and the customer has `sms`, `voice`, and `email`
- **THEN** `recommended_channels` is `["voice", "sms", "email"]`

#### Scenario: Missing channel is not invented
- **WHEN** the customer has only `email`
- **THEN** `recommended_channels` is `["email"]`

### Requirement: Escalation triggers
The engine SHALL return escalation recommendations from the table: customer care follow-up when `p50_hours >= 24`,
priority outreach for a declared medical-equipment need with `p50_hours >= 4` or an Unrated premise, community resource
information when `p90_hours >= 72`, human review when `p90_hours / p50_hours > 4` and `p90_hours - p50_hours >= 24`, and resource outreach for any declared need
when `p50_hours >= 24`. Each item SHALL be worded as a recommendation for a person and MUST NOT trigger any automatic action.

#### Scenario: Long outage with a medical need
- **WHEN** `medical_equipment = true` and `p50_hours = 30`
- **THEN** escalation includes E1 and E2 and each has `action` and a plain `reason`

#### Scenario: Wide range needs review
- **WHEN** `p50_hours = 3` and `p90_hours = 30`
- **THEN** escalation includes E4

#### Scenario: A range that is wide only by rounding is not escalated
- **WHEN** `p50_hours = 2` and `p90_hours = 9`, or `p50_hours = 4` and `p90_hours = 14`
- **THEN** escalation does not include E4, because E4 needs both a P90/P50 ratio above 4 and a gap of at least 24 hours

#### Scenario: Escalation is advisory
- **WHEN** an escalation is returned
- **THEN** the response states that escalations are recommendations for a person to act on

### Requirement: Four message templates in four channels
The system SHALL provide fixed template text for pre-storm, outage-confirmed, estimate-updated, and restoration-complete
messages in `sms`, `push`, `email`, and `voice` variants, each within its channel length limit (480, 200, 1000, 500).
Any restoration time SHALL be called an estimate. A pre-storm time SHALL be conditional on losing power. A restoration-complete
message SHALL contain no numbers. Templates MUST NOT contain links, names, phone numbers, or internal terms. When the premise is
Unrated, pre-storm text SHALL say to prepare as if an outage is possible.

#### Scenario: All sixteen templates render within limits
- **WHEN** each type and channel is rendered with a sample fact card
- **THEN** every text is non-empty, within the channel limit after the safety line is added, and passes the message guard

#### Scenario: Restoration-complete has no numbers
- **WHEN** the restoration-complete template is rendered
- **THEN** the text contains no digits

#### Scenario: Estimate wording
- **WHEN** an outage-confirmed template includes a time
- **THEN** the text contains the word "estimate" and does not contain "guarantee" or "promise"

### Requirement: Labeling and assumptions
Every evaluation SHALL state that the alert rules are prototype assumptions and not utility policy, that needs are only those
the customer declared, and that escalations are recommendations. It SHALL label the tier as a prototype alert level.

#### Scenario: Assumptions present
- **WHEN** any evaluation is returned
- **THEN** all three statements appear in `assumptions`
