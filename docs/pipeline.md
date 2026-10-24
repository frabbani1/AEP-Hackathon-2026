# Data pipeline (two S3 buckets)

Raw originals go into a private raw-sensitive bucket. The ETL cleans them with the rules in
[data-cleaning.md](data-cleaning.md) and publishes only sanitized output to a private clean-public-safe bucket.
The API reads the clean bucket only.

```
data/ + docs/structure_drawings.pdf
   │  make ingest   (aws s3 sync, count + byte check)
   ▼
s3://aep-hack-raw-464604123481/          raw-sensitive · SSE-S3 · versioned · all public access blocked
   raw/<dataset>/<date>/                 originals, untouched (Data1, Data2, matched_upstream, labor, drawings)
   staging/<run_id>/                     full build incl. internal/ crosswalk + quality report · deleted after 7 days
   │  make etl      (scripts/etl.py)
   ▼
s3://aep-hack-clean-464604123481/        clean-public-safe · SSE-S3 · versioned · all public access blocked
   curated/tables/*.parquet              premise_dim, device_dim, damage_fact, circuit_dim, hex_context
   curated/labor_standards.json, drawings/, manifest.json
   serving/risk/premise_risk.jsonl, calibration.json
   _meta/etl_run_*.json, validation_*.md|json
   │  make validate (scripts/validate.py)  → docs/validation_<date>.md
   ▼
data/curated/, data/serving/, data/_meta/   local mirror (gitignored)
```

| Resource | Name | Notes |
| --- | --- | --- |
| Region | `us-east-2` | |
| Raw bucket | `aep-hack-raw-464604123481` | lifecycle: `staging/` expires after 7 days (old versions after 1); `raw/` old versions after 30 |
| Clean bucket | `aep-hack-clean-464604123481` | lifecycle: old versions after 30 days |
| Token salt | SSM `/aep-hack/token-salt` (SecureString, standard tier) | same salt every run, so tokens are stable |
| ETL role | `aep-hack-etl-role` | read `raw/`, read/write `staging/`, read/write clean `curated/ serving/ _meta/`, read the salt. Cannot write or delete `raw/` |
| API role | `aep-hack-api-role` | Lambda trust. `GetObject`/`ListBucket` on clean `curated/` and `serving/` only. Explicit deny on the raw bucket (role policy and bucket policy), on all writes, and on SSM |

## Prerequisites

```bash
.venv/bin/python -m pip install -e '.[dev]'     # pyarrow, pyshp, pyproj, openpyxl, h3
brew install awscli poppler                      # poppler renders the drawings (optional)
git lfs pull                                     # only if .dbf files are ~130-byte pointer stubs

# credentials: SSO profile, or the temporary keys from the sandbox portal
aws sso login --profile myisb_IsbUsersPS-464604123481 && export AWS_PROFILE=myisb_IsbUsersPS-464604123481
export AWS_REGION=us-east-2
aws sts get-caller-identity
```

Optional: run as the least-privilege ETL role instead of your SSO role.

```bash
read -r AK SK ST < <(aws sts assume-role --role-arn arn:aws:iam::464604123481:role/aep-hack-etl-role \
  --role-session-name etl --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_SESSION_TOKEN="$ST"; unset AWS_PROFILE
```

## Steps

| Command | What it does | Fails when |
| --- | --- | --- |
| `make ingest` | `scripts/ingest.sh`: refuses LFS pointer stubs, `aws s3 sync` each dataset to `raw/<dataset>/<today>/`, compares file count and bytes with local. `DRYRUN=1` previews; `INGEST_DATE=YYYY-MM-DD` pins the folder | a stub `.dbf`, or any count/byte mismatch |
| `make etl` | `scripts/etl.py`: download latest `raw/*/<date>/` (sizes checked), run the curate + risk build, upload the full build to `staging/<run_id>/`, sanitize, sync to the clean bucket (`--delete`), mirror to `data/`. `--ingest-date`, `--no-upload` available | missing inputs, crosswalk not 1:1, missing labor categories, banned column in a table |
| `make validate` | `scripts/validate.py`: checks the clean bucket (see below) and writes `docs/validation_<date>.md` + `_meta/validation_<date>.md` | any check fails (exit 1) |
| `make docs` | `scripts/make_docs.py`: regenerates `docs/data_dictionary.md` and `docs/data_findings.md` from `data/` | a published column has no dictionary entry |
| `make iam` | `scripts/setup_iam.sh`: create/update both roles and both bucket policies (idempotent) | |
| `make all` | ingest → etl → validate → docs | |

Bucket names default to the ones above. Override with `make etl RAW_BUCKET=... CLEAN_BUCKET=...`.

### What the ETL changes

- **Circuit keys.** Numeric ids are normalized (`0000060` → 60). ARCOS `CIRn` labels go through the device-layer
  crosswalk (CIR2 → 16) and are never parsed for digits. This is a deliberate exception to "CIR60 → 60":
  the digits are wrong for 12 labels.
- **Facilities.** EPSG:3857 → WGS84, then reduced to hospital/fire/ambulance yes/no flags per hex cell.
- **Storms.** Clustered from `Report_Tim`: S1 Sep 27–Oct 2 2024, S2 Feb 12–18 2025.
- **Join.** Customers are joined to the topology. Unmatched premises are kept and flagged (`in_topology`, `in_customer_file`).
- **Removed.** Life-support flag, house numbers, road/city, service coordinates, premise/meter ids (replaced by salted
  HMAC tokens), facility names/addresses, scada/station/owner fields. Loaders refuse to read them, and a second
  column filter runs before anything reaches the clean bucket.
- **Suppressed.** `circuit_dim` rows with fewer than 10 premises (5 circuits, 33 premises).

### What validation checks

1. Every Parquet file reads back in full. Every JSON/JSONL file parses. Every file matches `manifest.json` (bytes + sha256).
2. No banned column or JSON key, no raw premise number as a value, and no `internal/` folder in the clean bucket.
3. Row counts: raw ARCOS records = staging `damage_fact`; customers ∪ topology = staging `premise_dim`;
   staging = clean except rows the ETL reports as suppressed; no table shrinks by more than 1% against the last passing run.
4. Every published group has at least 10 premises: `hex_context` rows, `premise_dim` hex groups, `circuit_dim` rows.
   Device-level counts are reported, not failed, because device keys are salted and carry no location.
5. Both buckets block all public access, are not public by policy, and are encrypted and versioned.

## Git and the public repo

`data/`, `raw/`, `council_out/`, `.etl_work/`, `*.parquet`, `*.jsonl` and `*.zip` are gitignored. **However**, the 98
raw files under `data/` (including `Customer_Clip.dbf` and the hospital layer, via Git LFS) were committed before
this rule existed, and `.gitignore` does not untrack them. To remove them from the repo you need
`git rm -r --cached data/`, plus a history rewrite (`git filter-repo`) and a force push if they must be gone from
history. That is destructive, so it has not been done.

`.etl_work/` holds a local copy of the raw files for the current run (needed by `make validate`). Each ETL run deletes
the previous run's folder. Remove it with `rm -rf .etl_work` when you are done.

## Teardown

See [aws-setup.md](aws-setup.md#teardown-destructive-read-before-running). Add
`aws iam delete-role-policy --role-name aep-hack-etl-role --policy-name etl-data-access && aws iam delete-role --role-name aep-hack-etl-role`.

## API layer (DynamoDB, Lambda, HTTP API, CloudFront)

```
clean bucket serving/risk/premise_risk.jsonl ──make load-table──▶ DynamoDB aep-premise-risk (token → risk row + line context)
clean bucket serving/risk/calibration.json, curated/labor_standards.json ──▶ read by Lambda at cold start
                                                      │
client ──▶ CloudFront ──/api/*──▶ API Gateway HTTP API ──▶ Lambda aep-concierge-api (role: aep-hack-api-role)
              │                                                    └──▶ Bedrock (Amazon Nova 2 Lite) for message wording only
              ├── /curated/drawings/*, /curated/labor_standards.json, /serving/risk/calibration.json ──▶ clean bucket (OAC)
              └── default ──▶ web bucket (empty until the frontend is built)
```

| Command | What it does |
| --- | --- |
| `make load-table` | Loads `data/serving/risk/premise_risk.jsonl` (written by the last `make etl`) into DynamoDB. Re-run after every ETL: tokens and rows come from that run's salt and data. About 4 minutes for 117,646 items. |
| `make deploy` | `cdk deploy` of `infra/` (pinned CDK 2.274.0), then `scripts/setup_iam.sh` to attach the CloudFront read statement to the clean bucket. First time only: `cd infra && npx cdk bootstrap aws://<account>/us-east-2`. |
| `make smoke` | 17 checks against the deployed API and CloudFront, including that private paths return 403. |

Order for a fresh account: `make iam` → `make ingest etl validate` → `make deploy` → `make load-table` → `make smoke`.

CloudFront serves only three clean-bucket paths (drawings, labor standards, calibration). The premise table, Parquet
files and `_meta/` reports are not reachable from the internet. Endpoint contracts are in [api.md](api.md).

Bedrock: Anthropic models are blocked in the current sandbox because they need AWS Marketplace permissions the sandbox
role lacks (a Haiku 4.5 call worked once, then failed). The API uses Amazon Nova 2 Lite
(`us.amazon.nova-2-lite-v1:0`). To switch models, run `cdk deploy -c bedrockModelId=<profile id>` and
`BEDROCK_PROFILE=<profile id> make iam`. If Bedrock fails for any reason, messages fall back to fixed templates.

Teardown: `cd infra && npx cdk destroy` removes the table, Lambda, API, CloudFront and web bucket. The buckets, roles,
salt and budget from the earlier steps are separate.
