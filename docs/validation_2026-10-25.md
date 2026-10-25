# Validation report 2026-10-25

- Result: **PASS** (45/45 checks passed)
- ETL run: `20261025T012024Z`
- Validated at: 2026-10-25T01:43:40+00:00
- Clean bucket: `s3://aep-hack-clean-464604123481/`

| Section | Check | Result | Detail |
| --- | --- | --- | --- |
| buckets | aep-hack-raw-464604123481 public access block | PASS | {"BlockPublicAcls": true, "IgnorePublicAcls": true, "BlockPublicPolicy": true, "RestrictPublicBuckets": true} |
| buckets | aep-hack-raw-464604123481 not public by policy | PASS | IsPublic=False |
| buckets | aep-hack-raw-464604123481 default encryption | PASS | AES256 |
| buckets | aep-hack-raw-464604123481 versioning | PASS | Enabled |
| buckets | aep-hack-clean-464604123481 public access block | PASS | {"BlockPublicAcls": true, "IgnorePublicAcls": true, "BlockPublicPolicy": true, "RestrictPublicBuckets": true} |
| buckets | aep-hack-clean-464604123481 not public by policy | PASS | IsPublic=False |
| buckets | aep-hack-clean-464604123481 default encryption | PASS | AES256 |
| buckets | aep-hack-clean-464604123481 versioning | PASS | Enabled |
| readback | curated/labor_standards.json | PASS | parses |
| readback | curated/manifest.json | PASS | parses |
| readback | curated/tables/circuit_dim.parquet | PASS | 105 rows |
| readback | curated/tables/damage_fact.parquet | PASS | 1659 rows |
| readback | curated/tables/device_dim.parquet | PASS | 32600 rows |
| readback | curated/tables/hex_context.parquet | PASS | 1140 rows |
| readback | curated/tables/premise_dim.parquet | PASS | 117646 rows |
| readback | serving/risk/calibration.json | PASS | parses |
| readback | serving/risk/premise_risk.jsonl | PASS | 117646 lines |
| readback | serving/risk/restoration_tree.json | PASS | parses |
| readback | serving/risk/zones.json | PASS | parses |
| readback | manifest bytes + sha256 | PASS | 10 files |
| privacy | no banned columns or keys | PASS | 0 found |
| privacy | no internal/ files | PASS |  |
| privacy | no raw premise numbers in values | PASS | checked against 117,646 raw ids |
| rows | raw ARCOS records -> staging damage_fact | PASS | 1659 -> 1659 |
| rows | raw customers UNION topology -> staging premise_dim | PASS | 117646 -> 117646 (customers 117642, topology 117599) |
| rows | staging premise_dim == premise_risk | PASS | 117646 vs 117646 |
| rows | staging -> clean premise_dim | PASS | 117646 -> 117646 |
| rows | staging -> clean device_dim | PASS | 32600 -> 32600 |
| rows | staging -> clean damage_fact | PASS | 1659 -> 1659 |
| rows | staging -> clean circuit_dim | PASS | 110 -> 105 (5 suppressed by rule) |
| rows | staging -> clean hex_context | PASS | 1140 -> 1140 |
| rows | vs previous run premise_dim | PASS | 117646 -> 117646 |
| rows | vs previous run device_dim | PASS | 32600 -> 32600 |
| rows | vs previous run damage_fact | PASS | 1659 -> 1659 |
| rows | vs previous run circuit_dim | PASS | 105 -> 105 |
| rows | vs previous run hex_context | PASS | 1140 -> 1140 |
| rows | vs previous run premise_risk | PASS | 117646 -> 117646 |
| groups | hex_context rows >= 10 premises | PASS | 1140 cells, min 10 |
| groups | premise_dim hex groups >= 10 | PASS | 1140 groups, min 10, 133 premises without a cell |
| groups | circuit_dim rows >= 10 premises | PASS | 105 circuits, min 19 |
| groups | zones.json areas >= 10 premises | PASS | 57 areas, min 12 |
| groups | zones.json ids unique | PASS | 57 ids |
| groups | zones.json each area has a typical home or a reason | PASS | 0 bad |
| groups | zones.json no home represents two areas | PASS | 57 typical homes |
| groups | zones.json homes do not exceed homes with a cell | PASS | 117,513 in areas, 117,513 with a cell |

## Notes

- device_dim: 26,019 of 32,600 devices serve fewer than 10 premises (flagged small_group; device keys are salted and carry no location, so not counted as a published group)
