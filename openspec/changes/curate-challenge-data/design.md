# Design

## Context

See proposal.md for motivation and scope. This design covers how the raw files in `/data` become curated artifacts, and defines the artifact contracts that the later risk, restoration, API and UI changes will consume.

Facts from profiling that shape the approach (counts only):

- `/data` is about 397 MB. Shapefile `.dbf` files are tracked with Git LFS and are present locally as real content.
- Layers: customers 117,642; poles 107,013; primary lines 62,788; secondary lines 127,403; transformers 26,959; fuses 7,616; reclosers 331; switches 900; ARCOS damage 1,659; priority facilities 10, 37 and 5.
- Topology: 117,599 premise lines, each an ordered chain `type*objectid*subtype*devicekey` from the nearest transformer to the circuit breaker. The graph has 32,600 nodes, is acyclic, has 110 components and has 60 nodes with more than one parent.
- Customer premises join to topology at 99.96%. Topology transformers join to GIS at 99.6% by object id. Topology fuses join at 98.5% by object id (6,656 of 6,757) and 6,453 of 6,511 distinct location keys match. Reclosers join only by location key (321 of 321).
- Circuit aliases (`CIR60`) and numeric ids (`0000060`) are not one-to-one: 14 of 121 numeric ids map to more than one alias.
- ARCOS points lie on GIS assets (median distance 0 m). Transformer, fuse and recloser damage snaps within 5 m for 96%, 99% and 97% of records.
- Source quirks: ARCOS dates have no time component; damage has no outage id or quantity; pole and line install dates are largely placeholders; priority layers use a projected reference system with a height component.
- Tooling on the dev machine: system Python is 3.14 with no geospatial libraries and no GDAL command line tools.

Project constraints that apply: serverless and low cost, predictions kept separate from generative AI, nothing deployed without review, original data preserved, sensitive data never exposed.

## Goals / Non-Goals

**Goals:**

- One command reproduces every curated artifact from `/data`, and fails loudly on unexpected input.
- Artifacts are small enough (tens of MB) to load into a Lambda function in memory, so the runtime needs no GeoPandas.
- Privacy is enforced by construction (fields dropped at read time) and verified by an automated gate.
- Every link between datasets has a measured coverage rate and an explicit reason for each miss.

**Non-Goals:**

- Zone geometry and map tiles. The map change decides what geometry is safe to publish. This change publishes zone attributes without geometry in `public/`.
- Exposure features, damage rates and any model. The risk change derives them from the `server/` artifacts.
- Restoration arithmetic. This change only supplies the labor table, mapping and assumptions.
- Uploading to S3 or any other cloud step.

## Decisions

### 1. Offline local pipeline, not cloud ETL

The pipeline runs on a developer machine and produces files. Upload to S3 happens in a later, reviewed infrastructure change.

- Why: the raw data is large, the transformation needs GeoPandas and spatial indexes, and it runs rarely. A local run costs nothing and keeps the Lambda package small.
- Alternatives: AWS Glue or a Lambda container image would add cost, deployment risk and review overhead for a one-time conversion. A notebook is not repeatable or testable.

### 2. Layout and tooling

```
pipeline/
  pyproject.toml            Python 3.12, exact-pinned dependencies
  config/
    contracts/*.yaml        one input contract per file or layer
    thresholds.yaml         link coverage minimums, snap tolerance, zone minimum, distance bands
    labor_mapping.yaml      hand-curated mapping and assumptions
  src/aep_pipeline/         stages, one module per concern
  tests/                    unit tests plus a small synthetic fixture dataset
curated/                    outputs, gitignored
  server/                   server-side only artifacts
  public/                   browser-safe artifacts
  reports/                  manifest.json, profile_report.md
```

- Why Python 3.12: matches a supported Lambda runtime and has wheels for the geospatial stack, while the system Python 3.14 does not have the libraries installed.
- Dependencies: GeoPandas, pyogrio, Shapely, pyproj, pandas, PyArrow, NetworkX, openpyxl, PyYAML, with exact versions.
- Outputs stay out of `data/` because that directory is raw input under LFS rules, and the spec requires raw data to stay untouched.

### 3. Artifact formats

GeoParquet with zstd compression for spatial tables, Parquet for tabular data, JSON for small reference data. A trial conversion of all layers (with customer identifying columns dropped) came to about 25 MB against 397 MB raw.

- Alternatives: keeping shapefiles (field names truncated to 10 characters, date-only types, 4 GB limits, no compression); GeoJSON (large and slow to parse); a database (adds a service to operate for a read-mostly dataset).

### 4. Stage flow

```
read+validate contracts --> sanitize at read --> conform (CRS 4326, 2D, placeholders)
   --> link (topology, crosswalk, snap) --> zones --> labor reference
   --> write server/ and public/ --> leak gate --> manifest + report
```

Sensitive columns are dropped when each layer is read, so no later stage can write them by mistake. The leak gate is a second, independent check on the outputs.

CLI contract:

- `python -m aep_pipeline run --raw data --out curated`, with the key supplied in the environment variable `AEP_TOKEN_KEY`.
- Exit codes: 0 success; 1 contract violation; 2 link coverage below threshold; 3 leak detected; 4 missing key or configuration.
- A `.dbf` that is a Git LFS pointer (not real content) is reported as a contract violation with a hint to fetch LFS files.

### 5. Tokenization

Premise, device, damage-record and circuit identifiers become tokens using HMAC-SHA256 with the secret key, truncated to 128 bits, in hexadecimal.

- Why keyed: premise numbers are 9-digit values, so an unkeyed hash can be reversed by trying every number. A key held outside the repository prevents that.
- Alternatives: a random mapping table (the table itself becomes a sensitive, reversible artifact that has to be stored); unkeyed hashes (brute-forceable); keeping raw ids (violates the privacy spec).
- Consequence: a new key produces new tokens, so anything cached downstream must be regenerated. The manifest records a key fingerprint (a short hash of a derived value, not the key) so mismatches are detectable.
- Circuit and substation aliases (`CIR60`, `STATION2`) were anonymized by the organizers and stay in `server/` artifacts. `public/` artifacts use tokens only.

### 6. Protection zones

For each premise, walk its chain from the nearest transformer toward the breaker and pick the first device whose downstream premise count is at least the minimum (10 by default). If none qualifies, the premise is assigned at circuit level. The zone identifier is the device token.

- Why: in the data the median transformer serves 3 premises and the median fuse serves 6, so transformer-level or exact views could identify a household. Using the existing chain gives zones that already match how restoration works (a fuse or recloser operating).
- Alternatives: a fixed grid (breaks the link to protective devices and mixes circuits); clustering of coordinates (needs exact coordinates in outputs and does not match topology).

### 7. Topology parsing and graph

Stream the file line by line (it is CRLF terminated and about 28 MB), split on tab, then on `*`. Reject lines whose device entries lack four parts. Build chains, edges and per-device downstream counts with plain dictionaries. Use NetworkX only for validation checks (acyclic, component count, multi-parent detection) and for later runtime use. Devices with several parents keep every edge and carry a flag.

### 8. Linking rules

- Customers to topology: join on the raw premise number before it is tokenized, then tokenize.
- Topology devices to GIS: object id first, then location key as fallback. Record which one matched.
- Circuit crosswalk: built from device layers. A numeric id with several aliases keeps all of them and is flagged ambiguous. ARCOS circuit aliases are not used to attach damage to customers; the spatial snap is.
- ARCOS snap: nearest asset of the matching layer, computed in a metric projection (UTM zone 17N) with a 5 m tolerance. Asset types without a layer (capacitor bank, step transformer, light, unknown) are reported as unlinked with the reason "no matching layer".
- Downstream impact on damage: attach the premise count if the device is in the topology. Otherwise record unknown, not zero.
- Facility proximity: distance from each premise to the nearest facility per category, then the median per zone is mapped to a band (under 2 km, 2 to 5, 5 to 10, over 10; configurable). Exact distances are never written.

### 9. Coverage thresholds

Thresholds live in `thresholds.yaml`, set just below the profiled values so a changed or damaged input fails the run:

| Link | Observed | Initial minimum |
|---|---|---|
| Customer to topology | 99.96% | 99.5% |
| Topology transformer to GIS | 99.6% | 99% |
| Topology fuse to GIS (id or location key) | at least 98.5% | 97% |
| Topology recloser to GIS | 100% | 99% |
| ARCOS device damage to asset within 5 m | 96% to 99% | 94% |
| ARCOS pole, primary and secondary damage to asset within 5 m | 99.15%, 99.29%, 99.36% (first full run) | 98% |
| ARCOS switch damage to asset within 5 m | 84.2% (16 of 19, small sample) | 80% |

The pole, line and switch thresholds were not measured during profiling. They were set from the first full run, just below the observed rates, and then fixed.

### 10. Labor reference and mapping

The workbook is read as values only with openpyxl. Sheet sections become rows with group code, install hours, remove hours, unit basis and source. Items with no hours (such as junction pole work) are stored as missing and flagged.

The damage-to-labor mapping is a hand-curated YAML file, not derived automatically. Initial proposal, to be reviewed by the team:

| ARCOS asset type | Labor group | Basis |
|---|---|---|
| Pole | Pole by height range | direct |
| Switch | Switch | direct |
| Recloser Bank | Recloser | direct |
| Fuse Bank | Fuse cutout | approximate (group description refers to transformer protection) |
| Transformer | Transformer by kVA | approximate (no kVA field, so average across sizes) |
| primary, secondary wire | none | none (workbook has no conductor repair hours) |
| Capacitor Bank, Step Transformer, Light | none | none |

Unknown pole height uses the medium group and is flagged assumed. Access issue and special equipment values map to labor-only adders through a second table in the same file, each row with a basis. All entries other than direct are copied to the assumptions register with stable ids such as `A-WIRE-01`, so later explanations can cite them.

- Why not compute the mapping: the workbook is organized by compatible unit, not by damage type, and the link between them is engineering judgment that must be visible and reviewable.
- The indirect labor multiplier is exposed as one parameter, 4.5, with a note that no travel or non-productive split exists. Consumers may override it.

### 11. Placeholder dates

Asset dates equal to `0000/00/00` or 1900-01-01 become missing, with a boolean placeholder flag. Without this, the 53,249 poles and 50,512 primary segments with placeholders would look decades old and mislead any age feature.

### 12. Artifact contracts for later changes

These are the stable interfaces the next changes read. Column lists are the minimum.

`curated/server/` (never sent to a browser):

| File | Key | Main columns |
|---|---|---|
| `premises.parquet` | premise_token | zone_token, circuit_token, has_chain, service_kind, customer_count |
| `premise_chains.parquet` | premise_token, position | device_token, device_type, device_subtype |
| `topology_edges.parquet` | child_token, parent_token | multi_parent_flag |
| `devices.parquet` | device_token | device_type, subtype, circuit_token, circuit_alias, substation_alias, downstream_premises, matched_by, GIS attributes (voltage, mounting, structure, normal state) |
| `assets_poles.parquet`, `assets_primary.parquet`, `assets_secondary.parquet` | asset_token | geometry, circuit_alias, attributes, placeholder flags |
| `damage_records.parquet` | damage_token | report_date, repaired flag, repaired_date, asset_type, damage_type, circuit_alias, substation_alias, access_issue, special_equipment, media_count, linked_asset_token, link_status, link_reason, downstream_premises (nullable) |
| `circuit_crosswalk.parquet` | circuit_token | numeric id token, aliases, substation alias, ambiguous flag, record counts |
| `zones.parquet` | zone_token | zone_level, circuit_token, premise_count, meets_minimum (all zones, including those too small to publish), band columns |
| `labor_items.parquet`, `labor_mapping.json`, `assumptions_register.json`, `labor_parameters.json` | stable ids | see capability spec; `labor_parameters.json` holds the indirect labor multiplier and the workbook's group descriptions |

`curated/public/` (browser-safe):

| File | Key | Main columns |
|---|---|---|
| `zones.parquet` | zone_token | circuit_token, zone_level, premise_count, hospital_band, fire_band, ambulance_band |
| `circuits.parquet` | circuit_token | zone_count, premise_count |

`curated/reports/`: `manifest.json` (checksums, counts, coverage, thresholds, key fingerprint, leak check result) and `profile_report.md`.

Both `server/` and `public/` are subject to the leak gate. In the leak gate and in the specs, "serving artifacts" means everything under both directories.

## Risks / Trade-offs

- [Zones too coarse because of the 10-premise minimum] → The minimum is configuration. The tasks report how many premises land at each zone level so the effect is visible before downstream work depends on it.
- [Combining zone, premise count and proximity bands narrows a household down] → Zones always contain at least 10 premises and bands are coarse. The privacy review in the API change re-checks what is combined in a response.
- [Thresholds tuned to one dataset break if organizers ship an updated extract] → That is intended. The run fails with the link name and rate, and a person decides whether to update the threshold.
- [Multi-parent devices (60) make downstream counts depend on the path] → Edges are all preserved and flagged. Counts are per distinct premise, and downstream changes can special-case flagged devices.
- [Hand-curated labor mapping is subjective] → Every non-direct row is in the assumptions register and travels with any estimate that uses it. Wire damage has no source hours, and the restoration change must introduce a labeled planning assumption instead of the pipeline inventing one.
- [Lost or changed tokenization key invalidates tokens] → Artifacts are cheap to regenerate (one command). The key fingerprint in the manifest shows which key built an artifact.
- [ARCOS records have a date but no time] → Dates are kept as dates. No time is invented, and the restoration change must treat durations as day-resolution.
- [The PDF structure drawings stay unused] → Out of scope. The report lists them as an unprocessed input.
- [Some circuit alias values are not in the anonymized `CIR<n>` form (8 distinct values in the real data, for example place-like names)] → Aliases stay in `server/` artifacts, never in `public/`; the profile report gives the count; whether to replace them with generic labels is decided before the API change exposes aliases.
- [Secondary-line geometry ends at service points, so it can reveal approximate customer locations (167 customers lie within 0.1 m of a line vertex; 38.8% within 5 m)] → `assets_secondary.parquet` is server-side only. The map change must generalize or omit it.
- [The raw guard ignores operating-system `.DS_Store` files, which Finder can create at any time] → Every other file under `data/` is checksummed before and after each run.
- [Raw data tracked with Git LFS may exist only as pointer files on another machine] → The contract check detects pointer files and says what to do.

## Open Questions

- The exact mapping of special equipment values (backyard bucket, track hoe and others) to labor-only adders gets team review during the labor task.
- Whether the map change needs coarse zone geometry in `public/` is decided in that change.
