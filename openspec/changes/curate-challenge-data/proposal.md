# Proposal

## Why

The challenge data arrives as raw Esri shapefiles, a delimited topology text file, an Excel workbook and image-only PDFs. None of it can be used directly for risk scoring, restoration estimates or a customer-facing map. Exploration showed that the pieces are linkable (premise to upstream device chain, ARCOS damage to GIS asset, circuit and substation aliases), but only with explicit, tested rules. It also showed that the raw customer layer carries sensitive fields (a life-support flag, exact service coordinates, premise numbers, road names) that must never reach the API, the map or the language model. Every later change depends on one trustworthy, privacy-safe, linked data foundation, so it comes first.

## What Changes

- Add a reproducible, offline curation pipeline that reads the original files in `/data` (read-only) and writes curated artifacts to a separate location.
- Add schema contracts for every input layer and file, with row-count and join-coverage checks that fail loudly when the data does not match what was profiled.
- Add a sanitization step that drops, tokenizes or generalizes sensitive fields before any artifact is produced for serving.
- Add network linking: a circuit and substation crosswalk, a spatial snap of ARCOS damage records to GIS assets, and parsed premise-to-breaker chains with per-device downstream premise counts.
- Add a structured labor reference built from the STORMS workbook (install and remove hours, access adders, the 4.5 indirect labor multiplier) plus a documented mapping from ARCOS asset and damage types to labor groups, with gaps recorded as assumptions.
- Add a counts-only data profiling report that documents coverage, known gaps and limitations for later design and responsible-use disclosures.
- Mark curated outputs as ignored by Git and excluded from any future hosting build.

Scope exclusions:

- No outage-risk model, restoration calculator or scenario logic.
- No API, frontend, map tiles or Bedrock integration.
- No AWS resources, CDK code or deployment.
- No OCR or structured extraction of the structure-diagram PDF.
- No external weather or storm data.
- No modification of any file in `/data`.

Customer-visible outcome: none directly. This change makes later outcomes possible and safe: a customer's anonymized location can be traced to its upstream devices and circuit, damage scenarios can be tied to real assets, and the demo can show only generalized, privacy-safe context. It also fixes the rule that real premise identity and life-support status are never shown.

## Capabilities

### New Capabilities

- `data-curation-pipeline`: Reproducible conversion of the raw `/data` inputs into curated artifacts, including schema contracts, validation checks, preservation of original files and deterministic reruns.
- `data-privacy-sanitization`: Rules for removing, tokenizing or generalizing sensitive and internal-only fields before any artifact is served, including customer identity and location, life-support indicators, internal control identifiers and priority-facility details.
- `network-data-linking`: Linking of customers, topology chains, GIS devices and ARCOS damage records through a circuit crosswalk, spatial snapping and the upstream chain, with reported coverage for each link.
- `labor-reference-data`: Structured labor-hour reference derived from the workbook, with the mapping from damage and asset types to labor groups and the documented assumptions where no direct source exists.

### Modified Capabilities

None. There are no existing specs in `openspec/specs/`.

## Impact

- New code: an offline Python pipeline and its tests, pinned to Python 3.12, using GeoPandas, NetworkX and PyArrow. Location and layout are decided in design.
- New outputs: curated GeoParquet, Parquet and JSON artifacts (roughly 25 MB measured in exploration) plus a profiling report, kept out of Git.
- Repository config: `.gitignore` updated for curated outputs. `/data` and its Git LFS setup are untouched.
- Downstream: the risk, restoration, API and UI changes consume these artifacts and their documented limitations. A later Lambda runtime must not depend on GeoPandas.
- Known data limitations carried forward: no weather data, ARCOS dates have no time component, ARCOS has no outage identifier or damage quantity, and topology does not include switches.
