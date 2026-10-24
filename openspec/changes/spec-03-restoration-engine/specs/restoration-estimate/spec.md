## ADDED Requirements

### Requirement: Job pricing from the labor sheet
The engine SHALL price each repair job as `(sum of install and removal hours of the hardware replaced + access adder) x indirect labor multiplier`,
using `labor_standards.json`. The multiplier SHALL be read from the sheet (4.5), not hard-coded. The adder SHALL be
`0` for `normal`, `access_constraint_adder` for `restricted`, and `remote_location_adder` for `remote`.

#### Scenario: Restricted access adds hours to every job
- **WHEN** access changes from `normal` to `restricted` with all other inputs equal
- **THEN** each job's hours rise by `access_constraint_adder x multiplier` and the P50 does not decrease

#### Scenario: Missing labor unit is an error
- **WHEN** an asset maps to a labor category that the sheet does not contain
- **THEN** the engine raises a restoration error and returns no estimate

### Requirement: Damage inputs
The engine SHALL accept `severity`, `crews`, `access`, and either `damage_count` (1 to 400) or a `damage` list of
`{asset_type, qty}` (qty 1 to 20, up to 10 items), never both. With neither, damage SHALL be simulated from the
premise's circuit damage rate and the severity multiplier. `crews` SHALL be a whole number from 1 to 100.

#### Scenario: Damage count replaces the severity-based count
- **WHEN** `damage_count = 12` is supplied
- **THEN** every trial uses exactly 12 jobs, and the response lists `damage_source = "you_provided"` in assumptions

#### Scenario: Both damage inputs rejected
- **WHEN** a request contains both `damage_count` and `damage`
- **THEN** the API returns 400 with a plain message and no estimate

#### Scenario: Out-of-range crews rejected
- **WHEN** `crews = 0` or `crews = 101`
- **THEN** the API returns 400 and no estimate

### Requirement: Step restoration by upstream dependency and customers restored per hour
The engine SHALL schedule jobs so that a node's jobs are available only after every strict ancestor node has no
unfinished job, and SHALL pick among available jobs by highest `n_served / job hours`, breaking ties by shallower
depth then node index. A customer's restoration time SHALL be the cumulative scheduled hours up to the last job on
their path, divided by crews, scaled from working hours to clock hours.

#### Scenario: Upstream job blocks a downstream job with a better ratio
- **WHEN** a lateral job has a higher customers-per-hour ratio than the main-line job above it
- **THEN** the lateral job is not scheduled until the main-line job is complete

#### Scenario: Higher ratio goes first among available jobs
- **WHEN** two sibling jobs are both available and one restores more customers per hour
- **THEN** that job is scheduled first

#### Scenario: Scheduling stops at the customer's last path job
- **WHEN** the customer's last path job completes
- **THEN** jobs scheduled later do not add to that customer's time

### Requirement: Range from a seeded Monte Carlo
The engine SHALL simulate labor-hour uncertainty (a shared factor and a per-job factor) and crew-size uncertainty
(each declared crew present with a fixed probability, at least one crew), and SHALL report the 50th and 90th
percentile of customer restoration time in whole hours as `p50_hours` and `p90_hours`, with `p50_hours <= p90_hours`.
The same inputs and configuration MUST always produce the same output. The response SHALL report the trial count.

#### Scenario: Deterministic output
- **WHEN** `estimate()` is called twice with identical inputs
- **THEN** both results are identical, including the trial count

#### Scenario: More crews never raise the P50
- **WHEN** `crews` increases and all other inputs are equal
- **THEN** `p50_hours` is equal or lower

#### Scenario: Range ordering
- **WHEN** any estimate is returned with status `ok`
- **THEN** `p90_hours >= p50_hours >= 1`

#### Scenario: Lower confidence widens the range
- **WHEN** the same inputs are run with Low confidence instead of High
- **THEN** `p90_hours - p50_hours` is equal or larger

### Requirement: Estimate responds to scenario changes
Changing severity, damage count, crews, or access SHALL recompute the estimate in the same call, with no cached
result reused across different inputs. A call SHALL complete in under 300 ms at the 95th percentile on the deployed
Lambda for any premise.

#### Scenario: More damage raises the estimate
- **WHEN** `damage_count` rises from 5 to 40 on the same premise and crews
- **THEN** `p50_hours` is at least as high, within a one-hour tolerance for simulation noise

#### Scenario: Sensitivity list names what moves the answer
- **WHEN** a response has status `ok` and `crews > 1`
- **THEN** `sensitivity` includes "One more crew" and "One fewer crew" with their P50 values

### Requirement: Estimate is labeled as a model output
Every response with status `ok` SHALL include the label "estimate, not confirmed", the note that this is a model
estimate and not confirmed utility information, the assumptions (crews, working hours, multiplier, hardware
mapping, uniform damage placement, clock starts when crews start), missing-data notes, and conditions that could
change the answer. The range text SHALL describe the low end as likely, not guaranteed.

#### Scenario: Label and assumptions present
- **WHEN** any premise returns status `ok`
- **THEN** the response has the label, the model note, at least four assumptions, and at least one "could change" item

#### Scenario: Crews appear in assumptions
- **WHEN** `crews = 6` is requested
- **THEN** an assumption states that 6 crews are assumed to work 16 hours out of every 24

### Requirement: Insufficient-data fallback
The engine SHALL return `status = insufficient_data` with the risk reason code, the plain-language message, and no
estimate when the premise cannot be rated or has no network context.

#### Scenario: Unknown premise
- **WHEN** a token not in the allow-list is requested
- **THEN** the response has `status = insufficient_data`, no `estimate`, and the message says restoration cannot be estimated

### Requirement: Privacy-safe output
The restoration output SHALL NOT contain node numbers, customer counts per device, circuit or device identifiers,
addresses, coordinates, or the life-support flag. Device-tree nodes serving fewer than 10 premises MUST be merged into
their parent before the artifact is written.

#### Scenario: No small groups in the tree artifact
- **WHEN** the tree artifact is built
- **THEN** every node has `served >= 10`

#### Scenario: Response scan
- **WHEN** a restoration response is serialized
- **THEN** the identifier scan passes and no key outside the contract is present

### Requirement: Backward-compatible fields for one release
The response SHALL include `low_hours`, `typical_hours`, and `high_hours` set to `p50_hours`, `p50_hours`, and
`p90_hours` until alerts, guidance, and messages read the new fields.

#### Scenario: Shim values match
- **WHEN** a response has status `ok`
- **THEN** `typical_hours = p50_hours` and `high_hours = p90_hours`
