# Spec Delta

## Purpose

Defines how the raw challenge datasets are converted into curated artifacts in a repeatable, verifiable way that never alters the original files and fails loudly when the data differs from what was profiled.

## ADDED Requirements

### Requirement: Original datasets are preserved
The pipeline SHALL treat every file under the raw data directory as read-only input. It MUST NOT create, modify, rename or delete any file in that directory or its subdirectories.

#### Scenario: Raw files unchanged after a run
- **WHEN** the pipeline completes a run, successful or failed
- **THEN** the checksum of every raw input file matches its checksum from before the run
- **AND** no new file exists under the raw data directory

### Requirement: Curated outputs are isolated from raw data
The pipeline SHALL write all outputs to a dedicated output location outside the raw data directory. That location MUST be excluded from version control and from any future hosting build.

#### Scenario: Output location is separate and ignored
- **WHEN** the pipeline writes curated artifacts
- **THEN** every artifact path is outside the raw data directory
- **AND** version control reports no tracked or untracked curated artifact files

### Requirement: Declared input contracts
The pipeline SHALL validate each input against a declared contract listing its expected fields, field types, geometry type, coordinate reference system and minimum record count. A violation MUST stop the run with a message naming the file and the violated expectation.

#### Scenario: Expected field is missing
- **WHEN** an input layer lacks a field named in its contract
- **THEN** the run stops before producing any curated artifact
- **AND** the message names the layer and the missing field

#### Scenario: Record count far below expectation
- **WHEN** an input contains fewer records than its declared minimum
- **THEN** the run stops and reports the observed and expected counts

### Requirement: Deterministic reruns
Given identical raw inputs, identical configuration and an identical tokenization key, the pipeline SHALL produce identical curated artifact contents on every run.

#### Scenario: Repeated run yields the same content
- **WHEN** the pipeline is run twice with the same inputs, configuration and key
- **THEN** the content checksums of all curated artifacts are identical across the two runs

### Requirement: Single spatial reference for curated geometry
The pipeline SHALL emit all spatial curated artifacts in one geographic coordinate reference system with two-dimensional geometry, regardless of the source layer's reference system or elevation values.

#### Scenario: Mixed source reference systems are conformed
- **WHEN** inputs use different coordinate reference systems, including projected systems with a height component
- **THEN** every spatial curated artifact declares the same geographic reference system
- **AND** no curated geometry carries an elevation value

### Requirement: Run manifest
Each successful run SHALL write a manifest recording the input file checksums, input and output record counts, the list of curated artifacts, link coverage metrics, the configuration used and the pipeline version. The manifest MUST NOT contain the tokenization key.

#### Scenario: Manifest describes a run
- **WHEN** a run completes successfully
- **THEN** a manifest exists listing every input checksum and every curated artifact with its record count
- **AND** the manifest contains no tokenization key material

### Requirement: Counts-only profiling report
The pipeline SHALL produce a human-readable profiling report containing counts, null rates, value-distribution summaries for non-sensitive categorical fields, link coverage and the list of known data gaps. The report MUST NOT contain row-level values of sensitive fields.

#### Scenario: Report lists known gaps
- **WHEN** the report is generated
- **THEN** it states that no weather data is provided, that damage dates have no time component, that damage records have no outage identifier or quantity, and that switches are absent from the topology file

#### Scenario: Report omits sensitive values
- **WHEN** the report is generated
- **THEN** it contains no customer address, premise number, road name, exact customer coordinate or facility name

### Requirement: Placeholder values are normalized and flagged
The pipeline SHALL convert known placeholder dates in asset data to missing values and set a flag showing the original value was a placeholder. It MUST NOT present placeholder dates as real installation or manufacture dates.

#### Scenario: Zero date becomes missing
- **WHEN** an asset record has the installation date "0000/00/00"
- **THEN** the curated installation date is missing
- **AND** a placeholder flag is set for that record

#### Scenario: Default century date becomes missing
- **WHEN** a line segment has the installation date 1900-01-01
- **THEN** the curated installation date is missing and flagged as a placeholder
