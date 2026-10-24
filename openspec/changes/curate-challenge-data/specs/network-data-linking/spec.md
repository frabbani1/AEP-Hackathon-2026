# Spec Delta

## Purpose

Defines how customers, upstream topology, GIS devices, circuits and damage records are linked so that a location can be traced to its upstream devices and damage can be tied to real assets, with measured coverage and no silent guessing.

## ADDED Requirements

### Requirement: Premise upstream chains
For every premise present in the topology file, the pipeline SHALL produce an ordered chain of upstream devices from the nearest transformer to the circuit breaker, each with its device type and subtype. Lines that cannot be parsed MUST be rejected and counted, not repaired by guessing.

#### Scenario: Valid line becomes an ordered chain
- **WHEN** a topology line lists a premise followed by four devices
- **THEN** the curated chain for that premise has four devices in the order given, nearest to the premise first, ending at the circuit breaker

#### Scenario: Malformed line is rejected
- **WHEN** a topology line has a device entry that does not have the expected number of parts
- **THEN** the line is excluded from the chains and the rejection count in the manifest increases by one

### Requirement: Link coverage is measured and reported
The pipeline SHALL compute and report coverage for each link: customers to topology, topology transformers to GIS transformers, topology fuses to GIS fuses, topology reclosers to GIS reclosers, and damage records to GIS assets. Entries without a link MUST be listed by count with a reason and MUST NOT be dropped silently.

#### Scenario: Coverage figures appear in the manifest
- **WHEN** a run completes
- **THEN** the manifest contains, for each link, the matched count, the unmatched count on each side and the matching rate

#### Scenario: Customer without a chain is flagged
- **WHEN** a customer premise has no topology entry
- **THEN** the premise is marked as having no upstream chain
- **AND** no chain is inferred for it

### Requirement: Minimum link coverage thresholds
Each link SHALL have a declared minimum coverage rate. The run MUST fail when a measured rate falls below its threshold, naming the link, the measured rate and the threshold.

#### Scenario: Coverage falls below threshold
- **WHEN** the customer-to-topology match rate is below its declared threshold
- **THEN** the run fails and reports the link name, measured rate and threshold

### Requirement: Device matching uses available identifiers
The pipeline SHALL match topology devices to GIS device records using the object identifier and, where that identifier does not match, the device location key. The identifier that produced each match MUST be recorded.

#### Scenario: Recloser matched by location key
- **WHEN** a topology recloser has no object identifier match in the GIS recloser layer but its location key matches
- **THEN** the device is linked and the match is recorded as made by location key

#### Scenario: Unmatched topology device is reported
- **WHEN** a topology device matches by neither identifier
- **THEN** it is counted as unmatched in the coverage report
- **AND** it remains in the topology graph without GIS attributes

### Requirement: Circuit and substation crosswalk
The pipeline SHALL produce a crosswalk relating the numeric circuit identifier, the circuit alias and the substation alias used across layers. Where one circuit identifier relates to more than one alias, all aliases MUST be kept and the circuit marked ambiguous. The pipeline MUST NOT choose one alias silently.

#### Scenario: Circuit with two aliases is marked ambiguous
- **WHEN** devices with one numeric circuit identifier carry two different circuit aliases
- **THEN** the crosswalk lists both aliases with their record counts
- **AND** the circuit is flagged ambiguous

### Requirement: Topology graph structure
The pipeline SHALL produce a parent-child edge list from the premise chains. A device with more than one distinct parent MUST keep all its parent edges and be flagged, not collapsed to a single parent.

#### Scenario: Device with two parents is preserved
- **WHEN** two premise chains show the same device followed by different next devices
- **THEN** both edges exist in the edge list and the device is flagged as having multiple parents

### Requirement: Downstream premise counts
For every device in the topology, the pipeline SHALL record the number of distinct premises downstream of it, derived from the chains. The count for a circuit breaker MUST equal the number of premises whose chain ends at that breaker.

#### Scenario: Counts are consistent with chains
- **WHEN** downstream counts are computed
- **THEN** each breaker's count equals the number of chains ending at it
- **AND** for every device with exactly one parent, the parent's count is at least the device's count

### Requirement: Damage records are snapped to assets
Each damage record SHALL be linked to the nearest GIS asset of the matching asset type if the distance is within a configured tolerance, 5 meters by default. A record outside the tolerance MUST remain unlinked with a reason and MUST NOT be forced onto an asset.

#### Scenario: Record within tolerance is linked
- **WHEN** a fuse-bank damage record lies 3 meters from a GIS fuse
- **THEN** the record is linked to that fuse

#### Scenario: Record outside tolerance is not forced
- **WHEN** a damage record has no asset of the matching type within the tolerance
- **THEN** the record is marked unlinked with the reason "no asset within tolerance"

### Requirement: Damaged devices carry downstream impact
For each linked damage record whose asset is a device in the topology, the curated output SHALL include the number of downstream premises. If the device is not in the topology, the record MUST be flagged as having unknown downstream impact, not zero.

#### Scenario: Device in topology
- **WHEN** a damaged transformer is present in the topology with three downstream premises
- **THEN** the damage record shows three downstream premises

#### Scenario: Device missing from topology
- **WHEN** a damaged fuse is not present in the topology
- **THEN** the record shows unknown downstream impact

### Requirement: Priority-facility proximity bands
The pipeline SHALL compute, for each protection zone, the distance band to the nearest hospital, fire station and ambulance service. Bands MUST be coarse and fixed in configuration, and exact distances MUST NOT be published.

#### Scenario: Zone receives a band per category
- **WHEN** proximity is computed for a zone
- **THEN** the zone has one distance band for each of the three facility categories
- **AND** no exact distance value is present
