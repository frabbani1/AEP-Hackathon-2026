# Spec Delta

## Purpose

Defines the structured labor-hour reference derived from the provided workbook and the documented mapping from damage and asset types to labor groups, so later restoration estimates use traceable hours and state every assumption where no direct source exists.

## ADDED Requirements

### Requirement: Workbook converted to a structured labor table
The pipeline SHALL convert the labor workbook into a structured table with one row per labor item, giving the item code or description, labor group, install hours, remove hours, unit basis and source sheet. Hour values MUST keep the precision stored in the workbook.

#### Scenario: Connector rows are preserved
- **WHEN** the connector sheet is converted
- **THEN** every connector code appears once with its install and remove hours unchanged
- **AND** the source sheet is recorded for each row

#### Scenario: Averages by size are preserved
- **WHEN** the size-based sections are converted, such as pole by height range and transformer by kVA
- **THEN** each size row appears with its size key, install hours and remove hours

### Requirement: Missing hours are not treated as zero
A labor item with no hour value in the workbook SHALL be recorded with a missing value and a flag. It MUST NOT be converted to zero hours.

#### Scenario: Item without hours is flagged
- **WHEN** a labor-only item has no install hours in the workbook
- **THEN** its hours are recorded as missing and flagged
- **AND** consumers can distinguish it from an item with a recorded value of zero

### Requirement: Adders carry their unit basis
Labor-only adders SHALL record whether the hours apply per occurrence or per unit quantity, such as per cubic foot.

#### Scenario: Per-quantity adder
- **WHEN** the manual excavation adder is converted
- **THEN** its hours are recorded as 1.5 with unit basis "per cubic foot"

#### Scenario: Per-occurrence adder
- **WHEN** the restricted access adder is converted
- **THEN** its hours are recorded as 0.5 with unit basis "per occurrence"

### Requirement: Indirect labor multiplier is an explicit parameter
The pipeline SHALL expose the indirect labor multiplier from the workbook, 4.5, as a named parameter with its source and a note that it is one combined value covering travel, safety discussion, setup and similar time. It MUST NOT be split into separate travel and non-productive components.

#### Scenario: Multiplier exposed with provenance
- **WHEN** the labor reference is read
- **THEN** it contains one indirect labor multiplier of 4.5 with its source sheet
- **AND** it states that no separate travel or non-productive values were provided

### Requirement: Damage-to-labor mapping is explicit and graded
The pipeline SHALL provide a mapping table from each asset type and damage type found in the damage records to labor groups. Each mapping MUST state its basis as direct, approximate or none, with a rationale. An asset or damage type without a mapping MUST be marked unmapped and MUST NOT receive zero hours.

#### Scenario: Direct mapping
- **WHEN** a damage record has asset type pole
- **THEN** the mapping gives the pole labor group with basis "direct"

#### Scenario: Wire damage has no direct source
- **WHEN** the mapping is generated for damaged wire
- **THEN** its basis is "approximate" or "none" with a rationale stating that the workbook has no conductor repair hours

#### Scenario: Unmapped type is not zero
- **WHEN** a damage record has an asset type with no labor mapping
- **THEN** the record is marked unmapped
- **AND** its labor hours are missing, not zero

### Requirement: Size selection rules are documented
Where the workbook gives hours by size and the asset data lacks the size attribute, the mapping SHALL use a documented default and flag the result as assumed. Pole height SHALL select the size group by the workbook's height ranges.

#### Scenario: Pole height selects size group
- **WHEN** a pole is 40 feet tall
- **THEN** the medium size group hours are selected

#### Scenario: Unknown pole height uses a flagged default
- **WHEN** a pole has unknown height
- **THEN** the documented default size group is used and the result is flagged as assumed

#### Scenario: Transformer size is not available
- **WHEN** a transformer record has no kVA rating
- **THEN** the mapping uses the documented default hours and flags the result as assumed

### Requirement: Access and equipment indicators map to adders
The mapping SHALL relate the damage record access-issue indicator and special-equipment values to labor-only adders through a documented table. Each relation MUST state its basis.

#### Scenario: Access issue maps to an adder
- **WHEN** a damage record has an access issue
- **THEN** the mapping table identifies the access adder to apply and its basis

#### Scenario: Equipment value without an adder
- **WHEN** a special-equipment value has no corresponding adder
- **THEN** it is marked as having no adder and listed in the assumptions register

### Requirement: Assumptions register
The pipeline SHALL produce an assumptions register listing every mapping or default whose basis is approximate, assumed or none. Each entry MUST have a stable identifier, a plain-language description and the affected asset or damage types.

#### Scenario: Register covers all non-direct mappings
- **WHEN** the labor reference is generated
- **THEN** every mapping with a basis other than direct has an entry in the register
- **AND** each entry has a stable identifier that later explanations can cite
