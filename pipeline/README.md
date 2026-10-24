# AEP data curation pipeline

Offline pipeline that turns the raw challenge files in `data/` into small, linked,
privacy-safe artifacts for the outage risk and restoration work that follows. It reads the
raw files and never changes them. It runs on a developer machine; nothing here uses AWS.

Design and requirements: `openspec/changes/curate-challenge-data/`.

## Requirements

- Python 3.12 (the system Python 3.14 does not have the geospatial libraries)
- [uv](https://docs.astral.sh/uv/) is used below, but any virtual environment works
- The raw data under `data/`. The `.dbf` files are tracked with Git LFS, so run `git lfs pull` first.
  If a file is only an LFS pointer, the pipeline stops with exit code 1 and says so.

## Setup

From the repository root:

```bash
uv venv pipeline/.venv --python 3.12
uv pip install --python pipeline/.venv/bin/python -e "pipeline[dev]"
```

All dependencies are pinned to exact versions in `pipeline/pyproject.toml`.

## Run on the real data

The tokenization key is a secret you choose. It is read only from the environment, is never
written to a file, log, manifest or artifact, and must be at least 16 characters. Keep it
outside the repository. The same key always produces the same tokens, and a different key
produces different tokens, so keep it if you want to reproduce an earlier run.

```bash
export AEP_TOKEN_KEY='choose-a-long-random-secret'
cd pipeline
.venv/bin/python -m aep_pipeline run --raw ../data --out ../curated
```

The run takes well under a minute. Outputs go to `curated/` at the repository root, which is
ignored by Git and must not be part of any hosting build. A new run replaces the previous
`server/`, `public/` and `reports/` folders, but only after every check has passed.

Options: `--config <dir>` points at another configuration directory (default `pipeline/config`).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | An input does not match its contract (missing field, wrong type, too few records, Git LFS pointer, topology cycle) |
| 2 | A link coverage rate is below its minimum |
| 3 | The leak gate found a forbidden field or raw identifier in an output |
| 4 | Missing or short key, missing configuration, or an unsafe path (for example an output folder inside `data/`) |

A failed run writes no serving artifacts. A missing key stops the run before any raw file is opened.

## What it does

```
read + validate contracts -> sanitize at read time -> conform (EPSG:4326, 2D, placeholder dates)
  -> link (topology chains, devices, circuit crosswalk, damage snap) -> check coverage
  -> protection zones + facility bands -> labor reference -> write to a staging folder
  -> leak gate -> manifest -> leak gate again -> publish
```

- **Original files are preserved.** SHA-256 checksums of every file under `data/` are taken before
  the run and checked after it. Operating-system `.DS_Store` files are ignored.
- **Sensitive fields never enter a table.** Each input field is classed in
  `config/contracts/*.yaml` as `public` (may be written), `internal` (read to link or tokenize,
  never written raw), `drop` or `undocumented` (never read).
- **Identifiers are tokens.** Premise, device, circuit, asset and damage identifiers become keyed
  HMAC-SHA256 tokens (128 bits, hex).
- **Locations are zones.** A premise is placed in the first upstream device whose downstream
  premise count reaches `zone_minimum_premises` (10). A zone means that device's whole downstream
  set, so every published zone covers at least 10 premises. Premises in circuits too small to
  reach the minimum are placed at circuit level and are not published.
- **Facilities are bands.** Hospitals, fire stations and ambulance services appear only as a
  coarse distance band per zone (`under_2_km`, `2_to_5_km`, `5_to_10_km`, `over_10_km`).
  Names, addresses, identifiers and exact distances are never written.
- **Nothing is guessed.** A premise with no topology entry has `has_chain` false and no chain.
  A damage record outside the 5 m snap tolerance, or whose asset type has no layer, stays
  unlinked with a reason. A device that is not in the topology has an unknown downstream count
  (null, never zero).

## Output layout

```
curated/
  server/    server-side only; never send to a browser
  public/    browser-safe zone and circuit attributes (no geometry, no coordinates)
  reports/   manifest.json (checksums, counts, coverage, no timestamps) and profile_report.md
```

### `server/`

| File | Key | Main columns |
| --- | --- | --- |
| `premises.parquet` | `premise_token` | `in_customer_layer`, `has_chain`, `circuit_token`, `service_kind`, `customer_count`, `zone_token`, `zone_level`, `zone_meets_minimum` |
| `premise_chains.parquet` | `premise_token`, `position` | `device_token`, `device_type`, `device_subtype` (position 0 is nearest the premise) |
| `topology_edges.parquet` | `child_token`, `parent_token` | `child_has_multiple_parents` |
| `devices.parquet` (GeoParquet) | `device_token` | `device_type`, `device_subtype`, `circuit_token`, `circuit_alias`, `substation_alias`, `in_topology`, `downstream_premises` (null when unknown), `matched_by`, `match_note`, `gis_layer`, `nominal_voltage`, `mounting`, `structure`, `normal_state`, and other GIS attributes |
| `assets_poles.parquet`, `assets_primary.parquet`, `assets_secondary.parquet` (GeoParquet) | `asset_token` | attributes, `install_date` and `install_date_is_placeholder` (placeholder dates become missing and flagged) |
| `damage_records.parquet` (GeoParquet) | `damage_token` | `report_date`, `repaired`, `repaired_date`, `asset_type`, `damage_type`, `circuit_alias`, `substation_alias`, `access_issue`, `special_equipment`, `media_count`, `linked_asset_token`, `link_status`, `link_reason`, `snap_distance_m`, `downstream_premises`, `downstream_impact_known`, `labor_group`, `labor_basis`, `labor_assumption_ids` |
| `circuit_crosswalk.parquet` | `circuit_token`, aliases | `circuit_alias`, `substation_alias`, `record_count`, `ambiguous` |
| `zones.parquet` | `zone_token` | `zone_level`, `circuit_token`, `premise_count`, `meets_minimum`, band columns |
| `labor_items.parquet` | `item_id` | `group_code`, `install_hours`, `remove_hours`, missing flags, `unit_basis`, `source_sheet` |
| `labor_mapping.json`, `assumptions_register.json`, `labor_parameters.json` | | damage-to-labor mapping with its review status, assumptions with stable ids, and the indirect labor multiplier |

Cautions for `server/` files:

- `assets_secondary.parquet` geometry ends at service points, so it can reveal approximate customer
  locations. Never serve it to a browser. The map work must generalize or omit it.
- Circuit and substation aliases are passed through as the organizers provided them. Some values do
  not follow the anonymized `CIR<n>` / `STATION<n>` form and may be real names; the profile report
  gives the count. Aliases never appear in `public/`.

### `public/`

| File | Key | Columns |
| --- | --- | --- |
| `zones.parquet` | `zone_token` | `circuit_token`, `zone_level`, `premise_count`, `hospital_band`, `fire_band`, `ambulance_band` |
| `circuits.parquet` | `circuit_token` | `zone_count`, `premise_count` |

### Labor reference notes

- Hours keep the precision stored in the workbook. An item with no hours is missing, never zero.
- The indirect labor multiplier (4.5 in the real workbook) is one combined value. The workbook
  gives no separate travel or non-productive values.
- `config/labor_mapping.yaml` is a hand-curated mapping. Its `review.status` is `proposed` until the
  team reviews the approximate rows and the special-equipment-to-adder rows. Wire damage has no
  labor hours (assumption `A-WIRE-01`); the restoration step must supply a labeled assumption.

## Configuration

| File | Purpose |
| --- | --- |
| `config/contracts/*.yaml` | Expected fields, types, geometry, reference system, minimum record counts and sensitivity class of every input |
| `config/thresholds.yaml` | Zone minimum, snap tolerance, distance bands, link coverage minimums (`null` means measured but not enforced) |
| `config/privacy.yaml` | Forbidden column names and fragments, raw identifier formats, extra rules for `public/` |
| `config/labor_mapping.yaml` | Damage-to-labor mapping and assumptions |

## Tests

```bash
cd pipeline
.venv/bin/python -m pytest -m "not real_data"   # fast, synthetic fixtures only
.venv/bin/python -m pytest                       # also checks the real files under data/ (skipped when absent)
```

Fixtures are generated at test time from the contracts. The repository tracks `*.dbf` files with Git
LFS, so no binary fixtures are committed. To run the whole pipeline on the synthetic data by hand:

```bash
cd pipeline
.venv/bin/python -m tests.fixtures /tmp/aep-fixture
AEP_TOKEN_KEY='fixture-key-0123456789' .venv/bin/python -m aep_pipeline run \
  --raw /tmp/aep-fixture/data --out /tmp/aep-fixture/curated --config /tmp/aep-fixture/config
```

## Measured on the real data

Figures from a full run on the real inputs (macOS, Python 3.12). They will vary a little by machine.

- Run time: about 25 seconds.
- Output size: 29.2 MB across 17 artifacts (about 28 MB on disk), against 397 MB of raw data.
- Memory to load the artifacts with pandas and pyarrow only (no GeoPandas), peak resident size:
  - runtime subset (tables and JSON, no geometry, no pole or line assets): about 361 MB, of which
    about 94 MB is the interpreter and libraries
  - everything, including geometry: about 525 MB
  A Lambda function that reads the runtime subset needs more than 512 MB; 1024 MB leaves headroom.
- Zone levels (premises assigned): transformer 51,197; fuse 46,564; recloser 12,348; circuit breaker 6,116;
  step transformer 1,223; sectionalizer 118; circuit level 80. 6,088 zones are published, each with at least 10 premises.

## Known limits

No weather data, date-only damage timestamps, no outage identifiers or damage quantities in the
damage layer, and switches absent from the topology file. `reports/profile_report.md` lists the
current figures and gaps for every run.
