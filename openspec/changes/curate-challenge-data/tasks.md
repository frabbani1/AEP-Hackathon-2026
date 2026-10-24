# Tasks

## 1. Project scaffold

- [x] 1.1 Create `pipeline/` with `pyproject.toml` (Python 3.12, exact-pinned GeoPandas, pyogrio, Shapely, pyproj, pandas, PyArrow, NetworkX, openpyxl, PyYAML, pytest), a `src/aep_pipeline/` package and empty `tests/`. Verify: a fresh Python 3.12 virtual environment installs the package and `python -c "import aep_pipeline"` succeeds.
- [x] 1.2 Add `curated/` to `.gitignore`. Verify: `git check-ignore -v curated/server/example.parquet` reports the rule, and `/data` is not listed as changed in `git status`.
- [x] 1.3 Add the command line entry `python -m aep_pipeline run --raw data --out curated` with named exit codes (0 success, 1 contract, 2 coverage, 3 leak, 4 missing key or configuration). Verify: `--help` prints usage, and a run without `AEP_TOKEN_KEY` exits 4 before opening any raw file (test with a raw path that does not exist).

## 2. Input contracts and raw preservation

- [x] 2.1 Build a small synthetic fixture set under `pipeline/tests/fixtures/` (tiny shapefiles with the real field names and types for every layer, a short topology file with CRLF endings, a mini workbook). It must contain no real data. Verify: a test loads every fixture file and checks it matches the real layer's field list.
- [x] 2.2 Write one contract YAML per input in `pipeline/config/contracts/` (fields, types, geometry type, reference system, minimum records, and per-field `sensitivity: public | internal | drop | undocumented`) from the profiled schemas. Verify: a test loads all contracts and confirms every field of every real layer is classified (run against real file metadata only, no row reads).
- [x] 2.3 Implement the contract validator, including detection of Git LFS pointer files. Verify: tests cover a missing field, a wrong geometry type, a row count below the minimum and an LFS pointer; each fails with a message naming the file and expectation, and exit code 1.
- [x] 2.4 Implement the raw-data guard (checksum before and after, refuse any output path inside the raw directory). Verify: a test that points `--out` inside the raw directory is refused, and a test confirms checksums are identical after both a successful and a failed run on fixtures.

## 3. Sanitization and tokenization

- [x] 3.1 Implement keyed tokenization (HMAC-SHA256, 128-bit hex) with the key read from `AEP_TOKEN_KEY`. Verify: tests show the same input and key give the same token, a different key gives a different token, and the key never appears in any returned or logged value.
- [x] 3.2 Apply the per-field sensitivity classes at read time so dropped and undocumented fields (address fields, road name, street code, city, meter id, annotation text, service-point coordinates, life-support indicator, the unexplained customer flag, control-system ids, source global ids, facility names and addresses, crew and task fields) never enter a table. Verify: a test over the fixtures asserts none of those columns exist after reading, and that no column name matches the forbidden list.
- [x] 3.3 Implement the leak gate: scan every file under `server/` and `public/` for forbidden column names and for values matching the raw premise number format; on a hit, delete that run's outputs and exit 3. Verify: tests inject a forbidden column and a raw 9-digit premise value into a fixture artifact and confirm exit 3 with the artifact and column named, and that a clean fixture run passes and records the result.
- [x] 3.4 Ensure source file metadata (PDF author and title, shapefile metadata paths) is never read into reports or manifests. Verify: a test builds reports from fixtures that carry author and path metadata and asserts those strings are absent.

## 4. Conforming and deterministic output

- [x] 4.1 Implement layer loading that converts every layer to EPSG:4326 with 2D geometry, including the priority layers' projected system with height. Verify: a test loads fixtures with mixed reference systems and asserts one reference system and no Z values in the output.
- [x] 4.2 Implement placeholder date normalization (`0000/00/00` and 1900-01-01 become missing with a boolean flag). Verify: tests cover both placeholder forms and a genuine date that passes through unchanged.
- [x] 4.3 Implement the Parquet and GeoParquet writers with fixed compression and sorted rows so output is deterministic. Verify: a test writes the fixture outputs twice and the file checksums match.

## 5. Topology and graph

- [x] 5.1 Implement the streaming topology parser producing premise chains and counting rejected lines. Verify: tests cover a valid four-device line, a CRLF line, and a malformed device entry that is rejected and counted.
- [x] 5.2 Build the edge list with multi-parent flags and run graph checks (acyclic, component count). Verify: a test with two chains that share a device but continue to different parents keeps both edges and flags the device; a cycle in a fixture raises a clear error.
- [x] 5.3 Compute downstream premise counts per device. Verify: tests assert each breaker's count equals the number of chains ending there and a single-parent device's parent count is at least its own.
- [x] 5.4 Run the parser on the real topology file (read-only) and record the results. Verify: 117,599 chains, 32,600 nodes, 110 components and 60 multi-parent nodes, or any difference is explained in the profiling notes.

## 6. Linking

- [x] 6.1 Join customers to chains on the raw premise number, then tokenize, and set `has_chain` (without inferring chains). Verify: a test shows a premise missing from topology gets `has_chain` false and an empty chain.
- [x] 6.2 Match topology devices to GIS devices by object id then location key, recording `matched_by`. Verify: tests cover an id match, a recloser matched only by location key and an unmatched device that stays in the graph without GIS attributes.
- [x] 6.3 Build the circuit and substation crosswalk with ambiguity flags. Verify: a test where one numeric id carries two aliases lists both with counts and flags the circuit; on real metadata, 14 ambiguous circuit ids are reported (or the difference is explained).
- [x] 6.4 Implement damage snapping in a metric projection (UTM zone 17N) with the 5 m tolerance and unlinked reasons ("no asset within tolerance", "no matching layer"). Verify: tests cover a record at 3 m (linked), 50 m (unlinked) and a capacitor bank (no matching layer).
- [x] 6.5 Attach downstream premise counts to linked damage records, using null for devices absent from the topology. Verify: tests show a count of 3 for a damaged transformer with three premises and null (not 0) for a device not in the topology.
- [x] 6.6 Implement `thresholds.yaml` and the coverage check with exit code 2. Verify: a test lowers a fixture match rate below its threshold and the run fails naming the link, rate and threshold; initial values match the design table.

## 7. Protection zones and proximity

- [x] 7.1 Implement zone assignment (first chain device with at least 10 downstream premises, else circuit level) and a zone-level distribution summary. Verify: tests cover a 3-premise transformer merged to its fuse zone, a circuit with too few premises falling back to circuit level, and a configurable minimum.
- [x] 7.2 Implement facility proximity bands per zone (median premise distance, banded, three categories) without writing exact distances. Verify: a test asserts each zone has three bands and no numeric distance column exists in any output.

## 8. Labor reference

- [x] 8.1 Implement the workbook reader (values only) producing `labor_items` with group code, install and remove hours, unit basis, source sheet and a missing-value flag. Verify: tests against the real workbook assert `CNC-HTA-10` is 0.096 install and 0.016 remove, hand dig is 1.5 per cubic foot, the restricted access adder is 0.5 per occurrence, and junction pole work has missing (not zero) hours.
- [x] 8.2 Expose the indirect labor multiplier (4.5) as a named parameter with source and the no-split note. Verify: a test reads the value 4.5 from the output and checks the note text is present.
- [x] 8.3 Write `labor_mapping.yaml` from the design table and a loader that checks every asset type and damage type found in the damage layer has an entry. Verify: a test fails if a type in the real ARCOS metadata has no entry, and unmapped types produce missing (not zero) hours.
- [x] 8.4 Implement size selection (pole height ranges, unknown height defaults to medium and flagged, transformer average when kVA is unavailable). Verify: tests cover 40 ft (medium), unknown height (medium, assumed) and a transformer without kVA (average, assumed).
- [ ] 8.5 Generate the assumptions register with stable ids and have the team review the special-equipment-to-adder rows. Verify: a test confirms every non-direct mapping has a register entry with an id and description, and review sign-off is noted in the mapping file header.

## 9. Outputs, manifest and report

- [x] 9.1 Write the `server/` and `public/` artifacts defined in the design with the listed minimum columns. Verify: schema tests check each artifact's file name, key and required columns on the fixture run.
- [x] 9.2 Write `manifest.json` (input checksums, record counts, artifact list, coverage, thresholds, configuration, pipeline version, key fingerprint, leak check result). Verify: a test confirms every field is present and the key itself is not.
- [x] 9.3 Generate `profile_report.md` (counts, null rates, safe categorical summaries, coverage, known gaps, unprocessed PDF input). Verify: a test asserts the four required gaps are stated and that no sensitive value from the fixtures appears.
- [x] 9.4 Write `pipeline/README.md` covering environment setup, the key variable, the command, exit codes and the artifact contract. Verify: following the README on a clean checkout runs the fixture pipeline successfully.

## 10. Integration checks on real data

- [x] 10.1 Run the full pipeline on `/data` with a generated key. Verify: exit 0, all link coverage rates meet the thresholds, and the manifest and report exist. Set the pole and line snap thresholds from this run, then rerun and confirm they pass.
- [x] 10.2 Run it a second time with the same key. Verify: all artifact checksums match the first run.
- [x] 10.3 Confirm originals are untouched. Verify: `git status --short data` is empty and checksums of the raw files match the manifest.
- [x] 10.4 Scan real outputs for leaks. Verify: the leak gate passed, and an independent check finds none of a sample of raw premise numbers, no life-support flag and no facility names in any artifact or report.
- [x] 10.5 Review output size and zone levels. Verify: total curated size is in the tens of MB (design estimate about 25 MB), the zone-level distribution is recorded, and the artifacts load in plain Python within a memory budget suitable for a Lambda function (record the measured figure).
- [x] 10.6 Validate the change. Verify: `openspec validate curate-challenge-data --strict` passes.

## Workflow follow-up

- Archive the change after review requirements are satisfied.
- Verify the archived result.
