## ADDED Requirements

### Requirement: Versioned fact card is the only model input
The system SHALL build a fact card (`factcard-v1`) from the engine outputs containing: status, band, band word, confidence
level and reason, scenario words, range display and bucket with the label "estimate, not confirmed", optional prior range,
up to three top factors, assumptions, missing data, could-change items, the risk label, the stage, and a declared-needs
boolean. The prompt sent to the model SHALL be a projection of the card that contains no digit and no premise token,
circuit or device value, address, coordinate, or count of premises. Need-specific details SHALL NOT be sent.

#### Scenario: Prompt projection has no digits
- **WHEN** a card with range "about 6 to 14 hours" and a reason mentioning "12 damage reports" is projected for the model
- **THEN** the projection contains `{{range}}` and no digit character

#### Scenario: Only a needs boolean is sent
- **WHEN** a customer declared a medical-equipment need
- **THEN** the projection holds `has_declared_need = true` and no need name

#### Scenario: Restoration-complete card has no range
- **WHEN** the message type is restoration-complete
- **THEN** the card has no `range` and no `prior_range`

### Requirement: Eight prompts with strict outputs
The system SHALL provide versioned prompts for explanation, preparation (before), during-outage actions, after-outage actions,
and the pre-storm, outage-confirmed, estimate-updated, and restoration-complete messages. Each prompt SHALL ask for JSON in a
fixed shape, and the response SHALL record the prompt version.

#### Scenario: Every kind has a prompt and a fallback
- **WHEN** each of the eight kinds is requested with the model disabled
- **THEN** each returns non-empty template text with `source = "template"`

#### Scenario: Prompt version reported
- **WHEN** the model path succeeds for `explanation`
- **THEN** the response has `prompt_version = "explanation.v1"`

### Requirement: The model writes no numbers
The model SHALL use placeholders `{{range}}` and `{{prior_range}}` for any time, and the system SHALL fill them from the card after
the guard passes. The guard SHALL reject a draft that contains a digit or a spelled-out number (other than inside an allowed
placeholder), a placeholder not allowed for the kind, or a missing required placeholder.

#### Scenario: Digit in draft falls back
- **WHEN** a draft says "restoration in 6 hours"
- **THEN** the guard fails and the response uses the template with `source = "template"`

#### Scenario: Spelled-out number falls back
- **WHEN** a draft says "about six hours"
- **THEN** the guard fails and the response uses the template

#### Scenario: Placeholder filled from the card
- **WHEN** a draft says "If you lose power, restoration could take {{range}}, which is an estimate."
- **THEN** the final text contains "about 6 to 14 hours" copied from the card and no other number

#### Scenario: Required placeholder missing
- **WHEN** an outage-confirmed draft states no time
- **THEN** the guard fails because `{{range}}` is required when the card has a range

### Requirement: Estimate, scenario, and tone wording
For kinds that state a time, the guard SHALL require the word "estimate" or "estimated", and pre-storm times SHALL be conditional
("if you lose power"). The guard SHALL reject promise words, weather claims, claims about crews or causes or outage duration,
links, email addresses, phone numbers, internal terms, and instruction-like or meta phrases. Average sentence length SHALL be at
most 20 words.

#### Scenario: Promise rejected
- **WHEN** a draft says "we guarantee power by tonight"
- **THEN** the guard fails

#### Scenario: Weather claim rejected
- **WHEN** a draft mentions "strong winds"
- **THEN** the guard fails because no weather data is used

#### Scenario: Pre-storm time must be conditional
- **WHEN** a pre-storm draft says "restoration will take {{range}}" with no "if"
- **THEN** the guard fails

### Requirement: Model may rephrase only code-selected items
For preparation, during-outage, and after-outage kinds, code SHALL select the items. The model MAY rephrase them but SHALL NOT add,
remove, merge, or reorder them. The guard SHALL reject output whose item ids are not exactly the selected ids, with each used once.

#### Scenario: Invented item rejected
- **WHEN** the model returns an item id that code did not select
- **THEN** the guard fails and the fixed item texts are used

#### Scenario: Dropped item rejected
- **WHEN** the model omits a selected item
- **THEN** the guard fails and the fixed item texts are used

#### Scenario: Need-specific items appended by code
- **WHEN** the customer declared a mobility need
- **THEN** the mobility items appear verbatim after the model's items and were not part of the prompt

### Requirement: Template fallback for every kind
Every kind SHALL have a deterministic fallback built from code and fixed text, and every fallback SHALL pass the same guard and safety
checks. A model error, timeout, throttling, empty draft, or guard failure SHALL return the fallback with the reasons recorded. A
fallback that fails its own guard SHALL raise an error rather than reach a customer.

#### Scenario: Bedrock error
- **WHEN** the model client raises an error
- **THEN** the response uses the fallback, has `source = "template"`, and reasons include "model unavailable"

#### Scenario: Circuit breaker after repeated failures
- **WHEN** three model calls fail within 60 seconds
- **THEN** the next calls skip the model for 60 seconds and return the fallback, and then one trial call is made

#### Scenario: Throttling uses the fallback
- **WHEN** a fake client raises a throttling error on every call during a burst of 50 requests
- **THEN** all 50 responses are valid fallbacks and no request fails

### Requirement: Cache by bucket without wrong numbers
The system SHALL cache guard-passed drafts in placeholder form keyed by band, range bucket, declared-needs flag, and stage, plus the
fields that change the wording (confidence level, scenario preset, status, channel, factor ids and directions, selected item ids,
prompt version, model id, schema version, library version). A cache hit SHALL fill placeholders from the current card. The response
SHALL report whether it was a hit. `use_llm = false` SHALL bypass the cache.

#### Scenario: Same bucket, different numbers
- **WHEN** two customers share a cache key but have ranges "about 6 to 14 hours" and "about 7 to 16 hours"
- **THEN** the second response is a cache hit and shows its own range, not the first customer's

#### Scenario: Different channel is a different key
- **WHEN** the same card is requested for sms and then email
- **THEN** the second request is a cache miss

#### Scenario: Failed drafts are not cached
- **WHEN** a draft fails the guard
- **THEN** nothing is stored in the cache

### Requirement: Separation of confirmed facts, estimates, and customer actions
The response SHALL return `confirmed_facts`, `model_estimates`, and `customer_actions` as separate sections. In this prototype
`confirmed_facts` SHALL be empty and a note SHALL say no confirmed utility information is connected. Model estimates SHALL carry the
label "estimate, not confirmed". Estimates and actions MUST NOT share a list.

#### Scenario: Sections present
- **WHEN** `prep_before` is returned for a rated premise
- **THEN** the response has all three sections, an empty `confirmed_facts`, a labeled range under `model_estimates`, and the actions under `customer_actions`

#### Scenario: Insufficient data
- **WHEN** the premise cannot be rated
- **THEN** the response says the risk cannot be rated, tells the customer to prepare as if an outage is possible, and has no `model_estimates`

### Requirement: Plain-language explanation of the result
The `explanation` kind SHALL explain the band, the confidence, the main factors, the assumptions, the missing data, and what could
change the answer, in plain language, using only the card. Its fallback SHALL be the band sentence, the factor sentences, the
confidence reason, and the could-change items in that order.

#### Scenario: Explanation covers required parts
- **WHEN** the explanation fallback is produced for a Medium-band premise
- **THEN** it names the band, at least one factor, the confidence, and at least one thing that could change the answer

### Requirement: Provenance in every response
Each response SHALL report `source` (`model`, `cache`, or `template`), the model id when used, the prompt version, the guard result with reasons,
the cache hit flag, and the facts used. The model note "Times are model estimates, not confirmed by the utility" SHALL be present whenever a time is stated.

#### Scenario: Template source reported
- **WHEN** the guard rejects a draft
- **THEN** `source = "template"` and `guard.reasons` is non-empty
