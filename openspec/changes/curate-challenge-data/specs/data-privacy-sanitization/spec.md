# Spec Delta

## Purpose

Defines which sensitive and internal-only data is removed, tokenized or generalized before any curated artifact exists, so later API, map and language-model components cannot expose customer identity, exact locations, life-support status or internal operational fields.

## ADDED Requirements

### Requirement: Sensitive customer fields are excluded
Curated artifacts SHALL NOT contain street address, address number, road name, street code, city, meter identifier, free-text annotation, service-point coordinates or the raw premise number from the customer layer.

#### Scenario: Customer artifact has no direct identifiers
- **WHEN** the customer-derived curated artifact is inspected
- **THEN** none of the excluded fields appear as columns
- **AND** no value matching the raw premise number format appears in any column

### Requirement: Life-support indicator is never carried forward
No curated artifact, report or manifest SHALL contain the life-support indicator or any count, flag or derived value computed from it.

#### Scenario: Life-support status absent from outputs
- **WHEN** all curated artifacts and reports are scanned
- **THEN** no field, value or aggregate derived from the life-support indicator is found

### Requirement: Identifiers are replaced by keyed tokens
Premise and device identifiers in curated artifacts SHALL be replaced by tokens derived with a secret key. A token MUST be stable for the same identifier and key, differ under a different key, and not be reversible without the key. The key MUST NOT be stored in the repository, any artifact or any report.

#### Scenario: Same key gives stable tokens
- **WHEN** the pipeline tokenizes the same identifier twice with the same key
- **THEN** the two tokens are identical

#### Scenario: Different key gives different tokens
- **WHEN** the same identifier is tokenized with two different keys
- **THEN** the resulting tokens differ

#### Scenario: Key is required
- **WHEN** the pipeline starts without a tokenization key supplied from the environment
- **THEN** the run stops before reading any raw data and states that a key is required

### Requirement: Customer location is generalized to protection zones
Serving artifacts SHALL locate a premise only by a protection zone derived from its upstream device chain, never by exact coordinates. Each zone presented for customer-facing use MUST cover at least a configured minimum number of premises, 10 by default. Smaller zones SHALL be merged into the next upstream zone until the minimum is met.

#### Scenario: Small transformer zone is merged upstream
- **WHEN** a transformer serves fewer premises than the minimum
- **THEN** the premise is assigned to the nearest upstream device zone that meets the minimum

#### Scenario: Circuit is too small to satisfy the minimum
- **WHEN** no upstream zone on a premise's chain meets the minimum
- **THEN** the premise is assigned at circuit level and the zone is marked as circuit-level

### Requirement: Priority facility detail is withheld
Serving artifacts SHALL describe priority facilities (hospitals, fire stations, ambulance services) only by category and a coarse distance band from a zone. They MUST NOT include facility names, street addresses, source identifiers or precise coordinates.

#### Scenario: Facility proximity is banded
- **WHEN** a zone's proximity to priority facilities is published
- **THEN** each category is expressed as a distance band
- **AND** no facility name, address or coordinate appears

### Requirement: Internal operational identifiers are excluded
Curated artifacts SHALL NOT contain device control-system identifiers, source-system global identifiers, crew or task identifiers, or other fields marked internal in the input contracts.

#### Scenario: Control identifiers removed from device data
- **WHEN** the device-derived curated artifacts are inspected
- **THEN** control-system identifiers and source-system global identifiers are absent

### Requirement: Source file metadata is not propagated
Descriptive metadata embedded in source files, such as author names, internal tool names and file-system paths, SHALL NOT be copied into any curated artifact, report or manifest.

#### Scenario: Document metadata stays out of outputs
- **WHEN** reports and manifests are scanned
- **THEN** they contain no author, internal tool name or source file-system path taken from file metadata

### Requirement: Fields of unknown meaning are excluded
Any input field whose meaning is not documented in the input contract SHALL be excluded from curated artifacts until it is documented.

#### Scenario: Undocumented flag is dropped
- **WHEN** the customer layer contains a flag field with no documented meaning
- **THEN** the field does not appear in any curated artifact

### Requirement: Automated leak check gates every run
Before finishing, the pipeline SHALL scan every serving artifact against the forbidden field list and the raw identifier formats, and MUST fail the run and remove the offending artifact outputs if a leak is detected.

#### Scenario: Forbidden column is detected
- **WHEN** a serving artifact contains a forbidden column
- **THEN** the run fails with a message naming the artifact and the column
- **AND** no serving artifact from that run remains in the output location

#### Scenario: Clean run passes the check
- **WHEN** no serving artifact contains a forbidden field or raw identifier
- **THEN** the leak check passes and is recorded in the manifest
