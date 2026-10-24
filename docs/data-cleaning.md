# Data cleaning guide

How the raw files in `data/` and `docs/` become the clean tables the app uses. Everything here is reproducible with
one command and runs in about 5 seconds.

```bash
.venv/bin/python -m pip install -e '.[dev]'            # pinned dependencies (pyarrow, pyshp, pyproj, openpyxl, h3)
export CONCIERGE_TOKEN_SALT="$(openssl rand -hex 32)"    # keep this value; see "The salt" below
.venv/bin/python scripts/curate.py                       # -> artifacts/curated/
.venv/bin/python scripts/build_risk_artifacts.py         # -> artifacts/risk/   (spec-02 risk engine)
.venv/bin/python -m pytest                               # 146 tests
```

Raw files are never modified. If `.dbf` files are tiny text stubs, run `git lfs pull` first.

## What comes out

`artifacts/curated/` (3.6 MB from about 750 MB raw):

| File | Rows | What it is |
| --- | --- | --- |
| `tables/premise_dim.parquet` | 117,646 | One row per premise: salted token, circuit, service type, protective-device count, hops, hex cell |
| `tables/device_dim.parquet` | 32,600 | The device tree: transformers, fuses, reclosers, breakers, with parent, depth, premises served |
| `tables/damage_fact.parquet` | 1,659 | Cleaned ARCOS damage: storm, repair days, asset type, damage type, access issue, hex cell |
| `tables/circuit_dim.parquet` | 110 | Per circuit: premises, service mix, devices, line miles, damage by storm |
| `tables/hex_context.parquet` | 1,140 | Per hex cell: premise count, damage by storm, hospital/fire/ambulance nearby flags |
| `labor_standards.json` | 30 units, 11 adders | Labor hours by unit, access adders, indirect labor multiplier (4.5) |
| `drawings/page-1..9.png` | 9 | The structure-drawing PDF, one image per page |
| `manifest.json` | | Every published file with size and sha256 |
| `internal/` | | Circuit crosswalk and quality report. **Never published.** |

## Cleaning rules, and why

### 1. ARCOS circuit labels are not circuit numbers

This one matters most. ARCOS labels circuits `CIR2`, `CIR10` and so on, but the label number is **not** the circuit
id used by customers and topology. In the device layers, `CIR2` is circuit 16, `CIR6` is circuit 5, `CIR10` is circuit 9.

- Source of truth: the numeric `circuit_id` matches the customer file on 117,615 of 117,615 joined rows.
- The four device layers (fuse, transformer, switch, recloser) give a perfect 1:1 label to id map across 117 labels.
- Check: ARCOS damage points sit next to the right circuit's transformers 1,560 times out of 1,645 using the crosswalk,
  against 999 using the digits in the label. For the 12 shifted labels, the digits were right 0 times.
- The crosswalk is built by `load_crosswalk()`. If layers ever disagree it raises, so a bad map cannot slip through.
- Of 1,659 ARCOS rows, 1,645 map to a topology circuit. 14 do not: `TINKER` (6), `PENN FOREST` (7) and `SERVICE CENTER` (1). These are facility-style names, not circuits.

The primary and secondary line layers use a different label scheme, so never use their `aep_circui` column.

### 2. Storms are found from dates

`Event` is "Default" on every row. Storm windows come from `Report_Tim` clusters:
Sep 27 to Oct 2, 2024 (193 rows) and Feb 12 to 18, 2025 (1,254 rows). 212 rows are background damage outside both.

### 3. Repair time

`Repaired_D` minus `Report_Tim`, in whole days. Two cleaning rules:
- 76 rows say `Repaired = No` but still carry a placeholder repair date (Oct 2025, Dec 2025). Their duration is
  **unknown**, so `repair_days` is empty, not zero.
- Real repairs take 0 to 6 days, then there is a gap, then a 12 to 25+ day tail. Rows at 7 days or more are flagged
  `is_backlog` so models do not treat backlog as outage length.

### 4. Damage type exists

The council notes said there was no damage-type field. There is: `Inform_Wor` holds Damaged Wire (861), Damaged Switch
(357), Damaged Pole (355), Damaged Equipment (81), Damaged Light (5). It becomes `damage_type`, next to `asset_type`.
Quantity still does not exist; each row is one damaged asset.

### 5. Device tree and alternate paths

`matched_upstream.txt` repeats the same chain for many premises. It is turned into a tree: each device has one
`parent_key`, a `depth` (0 at the breaker) and `n_premises_served`. That is exactly what step restoration needs.

Only 82 of 32,600 devices appear under more than one parent (alternate feed paths). The tree keeps the majority parent.
The 210 premises whose own chain disagrees are flagged `alternate_path`. Depth is derived from the final tree, so a
parent and its depth can never disagree.

### 6. Premise join gaps

- 117,599 premises have a chain; 117,595 of them join the customer file; 47 customers have no chain.
- Those 47 stay in `premise_dim` with `in_topology = false`. They are the demo cases for "we can't rate this".
- 7 premises list a different circuit in the customer file than in the topology. The topology wins.

### 7. Labor workbook

The workbook is a set of loosely laid-out blocks, not a table. `parse_labor()` reads them into tidy rows and refuses to
continue if a required category (pole, transformer, recloser, switch, fuse cutout, crossarm, connector) or the 4.5
multiplier is missing. It keeps the anonymized adder names, not the "Original Intent" column.

### 8. Facility layers

Hospitals, fire stations and ambulance services are in Web Mercator (EPSG:3857). They are reprojected to WGS84 and
reduced to three yes/no flags per hex cell ("a hospital is in or next to this cell"). Names, addresses and exact points
are never read.

### 9. Drawings

The PDF is images only, no text layer, so it is rendered to 9 PNGs. Page 1 is a transformer pole with callouts for
cutout, bracket, fuse link, arrester, transformer, grounding and secondary cable. Turning pages into a parts list per
damage type is an engineering mapping for the restoration spec, and needs someone to review it.

## Privacy rules built into the code

| Rule | How it is enforced |
| --- | --- |
| Sensitive columns are never read | `FORBIDDEN_COLUMNS` in `curate/shapes.py` raises if a loader asks for `critical_c`, address fields, names, outage ids, substation labels, exact lat/lon. `premise_nu` needs an explicit `allow` and is tokenised immediately |
| No raw ids in outputs | Premises become `p_` + 16 hex (HMAC with the salt). Devices become `d_` + 12 hex. A test searches every table's bytes for real premise numbers |
| No exact locations | Coordinates become H3 hex cells (resolution 8, about 0.7 km2) |
| Small groups are suppressed | A cell is published only if at least 10 premises share it, else the next coarser cell, else none. 129 households get no cell. A test checks every published cell has 10 or more premises |
| No banned columns in tables | `write_table` and a post-write scan both fail the build on names like `latitude`, `address`, `premise_nu`, `critical_c` |
| Internal files stay internal | The crosswalk and quality report sit in `internal/`, outside the manifest and the publish script |

## The salt

Tokens are salted HMACs. The same salt must be used for every build, or tokens change between runs and the risk
artifacts will not line up with the curated tables. Store it once (see `docs/aws-setup.md` step 7) and never commit it.

## Reading the quality report

`artifacts/curated/internal/quality_report.json` records every drop and count: columns dropped per source, rows per
table, storm windows, circuit status of each damage row, hex resolution counts, topology conflicts. If a number changes
between runs, start there.

## Known limits

- Damage rows are repairs, not outages. They show where crews worked, not which homes lost power.
- Two storms only. The Sep 2024 storm is about 6.5 times smaller than Feb 2025.
- Device-layer attributes such as transformer kVA are not joined to the topology yet, so transformer labor uses the
  size-independent hours until that join is built.
- Poles have no circuit column, so they are not in `circuit_dim`.
