# CLAUDE.md

Guidance for AI coding agents working in this repo.

## Project

AI Outage Concierge, HackOHI/O 2026 AEP challenge (team: Faiz Rabbani, Parker Miconi).
A customer picks an anonymized location before a storm and asks: Will I lose power? How long could
restoration take? What should I do? Do I qualify for alerts? Challenge text: `docs/challenge.md`.

Status: `curate-challenge-data` (offline data pipeline, 40/41 tasks; task 8.5, the labor-mapping
review, is open), `spec-02-risk-engine` and `spec-03-restoration-engine` (7.4, removing the one-release `low/typical/high_hours`
shim, waits for spec-05 and spec-06) are implemented. The frontend has two pages (chat home at `/`, outage
map at `/map`, see `frontend/`). The map page runs on the real API (`POST /zones`, `POST /zones/assess`, `GET /evidence`,
through `src/api/httpClient.ts`). The chat also uses the real API: a typed city or ZIP goes to `POST /zones/find`
(`backend/concierge/zones/places.py`, public place and ZIP tables for the Roanoke Valley only), and the answers come from
`POST /zones/assess`. Areas are named for nearby places ("Salem"), not "Area 7"; `scripts/relabel_zones.py` renames an older `zones.json`.
Anything outside that territory (an Ohio ZIP, say) answers "not covered". Only the chat page's storm summary is still a sample.
Spec-05 (alerts) and spec-06 (assistant) are implemented; open items are listed in their `tasks.md` (team reviews, deploy-time checks).
The two backends do not talk to each other yet (see "Circuit keys" and `pipeline/README.md`). Work is driven by
OpenSpec changes in `openspec/changes/`.

## Core architecture rule

A deterministic Python engine computes every number. The LLM (Amazon Bedrock) only explains and writes
messages from a whitelisted "fact card". If LLM output contains a number not in the fact card, fall
back to a template. Never let the LLM compute, round, or invent a number.

Target stack (from `openspec/config.yaml`): React + TypeScript + Vite on Amplify, API Gateway HTTP API,
Python Lambda, Bedrock, S3, CDK (TypeScript). Do not deploy cloud resources without review.

## Repo map

| Path | What it is |
| --- | --- |
| `docs/challenge.md` | Official challenge requirements |
| `docs/structure_drawings.pdf` | Structure diagrams (images only, no text layer) |
| `agent_council.txt` | Prior design discussion; data facts in it must be re-verified |
| `README.md` | Public project overview (has TODO sections) |
| `openspec/config.yaml` | OpenSpec project context and rules |
| `openspec/changes/` | Active changes (proposal, design, specs, tasks) |
| `.kiro/skills/`, `.kiro/prompts/` | OpenSpec workflows (propose, apply, archive, sync) |
| `data/Data1/` | Shapefiles: ARCOS damage, customers, poles, primary, fuse banks, hospitals, fire, ambulance |
| `data/Data2/` | Shapefiles: secondary, transformers, switches, recloser banks |
| `data/matched_upstream/matched_upstream.txt` | Premise -> upstream device chain (tab-separated) |
| `pipeline/` | Offline curation pipeline (Python 3.12, GeoPandas): `data/` -> `curated/`. Own venv, tests, README |
| `curated/` | Pipeline output (gitignored): `server/` (never to a browser), `public/` (zone level, browser-safe), `reports/` |
| `backend/concierge/risk/` | Risk engine: loaders, storms, features, calibration, confidence, explanation, engine, build. Reads `data/` directly, not `curated/` |
| `backend/tests/risk/` | Unit, property (hypothesis), synthetic-fixture, integration, and real-data tests |
| `backend/concierge/restoration/` | Restoration engine v2: `config` (assumptions), `jobs` (pricing, plan), `tree` (runtime tree), `schedule` (step restoration), `montecarlo` (seeded P50/P90), `model` (contract, `RESTORATION_ENGINE` switch), `v1` (frozen first version). Tree builder: `backend/concierge/curate/restoration_tree.py`. Rules in `docs/restoration-tree.md` |
| `backend/concierge/assistant/` | Spec-06 assistant: `factcard` (card, digit-free projection, sanitizer), `guard` (output rules), `compose` (orchestration), `phrase` (Bedrock Converse, breaker), `cache`, `safety` (hard-coded safety text), `fallbacks`, `prompts/` (8 versioned prompts). Route `POST /assistant`. Tests in `backend/tests/assistant/` |
| `backend/concierge/weather/` | Open-Meteo client (`openmeteo`, stdlib only), weather block (`summary`), gust-to-severity scale (`severity`, `anchors.json` from `scripts/calibrate_weather.py`). Used by `severity: "weather"` and `POST /weather`. Never reaches the model. Tests in `backend/tests/weather/` |
| `backend/tests/restoration/` | Golden v1 tests, tree/jobs/scheduler/Monte Carlo/model/service tests, real-data checks (skipped without `artifacts/`) |
| `scripts/profile_restoration.py` | Latency and scenario-table report for the restoration engine on the real artifacts |
| `frontend/` | React + TS + Vite, styled after aepohio.com. Pages: chat home and outage map (Leaflet). `src/api/types.ts` is the UI contract; `src/api/httpClient.ts` calls the API (snake_case to camelCase); `src/api/mockClient.ts` is the storm-summary stub. Every link stays inside the app (no outside sites); pages are `/`, `/map`, `/safety`, `/about`. `VITE_API_URL` (default same-origin `/api`), `VITE_API_PROXY` (dev proxy target, default `http://127.0.0.1:8787`), `VITE_ASSISTANT_LLM=true` (ask Bedrock to phrase map text), `VITE_BASEMAP_URL` (empty = no tiles). `npm run dev` / `npm run build` |
| `backend/concierge/zones/`, `backend/concierge/curate/zones.py` | Outage map zones. Builder (`curate/zones.py`, build time): H3 res-6 cells with 10+ premises, each rated through its median-risk premise, written to `artifacts/risk/zones.json` (server side only, holds tokens). Runtime: `zones/index.py` (load and validate), `zones/view.py` (engine output to the map's shapes). Routes `POST /zones`, `POST /zones/assess` in `api/service.py` |
| `scripts/dev_api.py` | Serves the Lambda handler on `127.0.0.1:8787` from `artifacts/` (no AWS, no auth, one request at a time) for frontend development |
| `backend/concierge/curate/` | Data curation: raw files -> clean Parquet/JSON tables (`artifacts/curated/`) |
| `scripts/curate.py` | Runs the curation pipeline (about 5 s) |
| `scripts/build_risk_artifacts.py` | Builds `artifacts/risk/` (gitignored) from `data/` |
| `scripts/aws_publish.sh`, `scripts/load_dynamodb.py` | Upload to S3 / load DynamoDB. Dry run unless `APPLY=1` / `--apply` |
| `docs/data-cleaning.md`, `docs/aws-setup.md` | Cleaning rules; step-by-step AWS setup |
| `data/Install_Labor_HackOHIO.xlsx` | Labor hours per unit, access adders, indirect multiplier (4.5) |

Never modify files under `data/`. Write derived outputs elsewhere (e.g. `artifacts/`).

## Verified data facts (checked 2026-10-24)

- `.dbf` files are Git LFS objects. They are pulled in this checkout; on a fresh clone run `git lfs pull`.
- ARCOS damage: 1,659 rows. `Event` is "Default" everywhere. Storms come from `Report_Tim` clusters
  (risk design D5): 2024-09-27..10-02 (193 rows; 09-26 has a single row and is not part of it) and
  2025-02-12..02-18 (1,254 rows). 212 rows fall outside both. Report dates span 2022-10 to 2026-08.
- ARCOS `Asset_Type`: primary 704, Pole 355, Fuse Bank 280, secondary 156, Transformer 74,
  Recloser Bank 58, Switch 19, Capacitor Bank 6. No damage-type or quantity field.
- Circuit keys: ARCOS `Circuit` = "CIR60", topology/customer = "0000060". Digit-normalizing both to int is fine for
  joining customers to topology. It is NOT reliable for attributing damage to circuits: aliases and numeric ids are
  not one-to-one (14 of 121 numeric ids carry more than one alias). Measured 2026-10-24: for ARCOS records that snap
  (5 m) to a device or line layer, the alias digits disagree with the asset's circuit for 18% of in-storm records
  (poles have no circuit field; nearest-line attribution differs from alias digits for only 3.7% of them), and the
  topology sides with the snap in all 101 of 101 fuse-bank disagreements. The risk engine still uses alias digits
  (open issue); the pipeline's `damage_records.parquet` carries the snap (`linked_asset_token`).
  85 ARCOS aliases; 82 yield a number (3 aliases have no digits, 14 rows); 81 of those join the topology (CIR12 does not).
- Topology: 117,599 premises, 110 circuits. Each line is `premise<TAB>class*id*type*name...`, ending at
  `eo_circuit*...*Circuit Breaker*<circuit>`. Protective types: Fuse, Recloser (Hydraulic/Electronic),
  Sectionalizer. `Step` is a step transformer (not protective). Chain length 2 to 13.
- Customer_Clip: 117,642 rows; `premise_nu` joins 117,595 topology premises. `kind_of_se`:
  Overhead 64,824, Underground 52,419, Unknown 399. Circuit sizes range from 4 to ~3,500 premises.
- Facility layers (hospital, fire, ambulance) are EPSG:3857; everything else WGS84.
- Geography: shapefile header extents of the customer, pole, primary and ARCOS layers span about 37.0 to 37.5 N and
  79.6 to 80.3 W. That is south of Ohio's southern edge; the basemap labels there read Roanoke, Salem, Blacksburg
  (Virginia). The frontend header and copy say "Ohio Power Company". Decide whether that is intended.

## Privacy rules (non-negotiable)

Never send these to the UI, logs, or the LLM: `critical_c` (life-support flag), `address`, `address_nu`,
`anno_strin`, `road_name`, `city`, service coordinates, raw `premise_nu`, meter/location IDs, device IDs,
substation/circuit labels, hospital names/addresses, ARCOS crew/NEAR_FID fields. Use salted premise
tokens. Suppress any group under 10 premises. Never infer sensitive needs; only use self-declared ones.
Load only whitelisted columns from source files.

## Commands

```bash
# Risk engine (use your own venv, see Environment; <py> = that venv's python)
<py> -m pip install 'pytest==9.1.1' 'hypothesis==6.170.9'
<py> -m pytest                                   # 1,424 tests, ~105 s (real-data tests need artifacts/, see below)
CONCIERGE_TOKEN_SALT=<secret> <py> scripts/curate.py --no-drawings            # artifacts/curated (same salt as the next line)
CONCIERGE_TOKEN_SALT=<secret> <py> scripts/build_risk_artifacts.py --sample   # artifacts/risk, incl. restoration_tree.json and pc/pn on rows
<py> scripts/profile_restoration.py [--cases --severity typical]              # restoration latency / scenario table
<py> scripts/dev_api.py                                                       # local API on :8787 from artifacts/ (build_risk_artifacts.py also writes zones.json)
# then, in frontend/: npm run dev   (the Vite server proxies /api to the local API)

# Data pipeline (Python 3.12 venv at pipeline/.venv; setup in pipeline/README.md)
AEP_TOKEN_KEY=<secret, 16+ chars> pipeline/.venv/bin/python -m aep_pipeline run --raw data --out curated
cd pipeline && .venv/bin/python -m pytest        # 197 tests (185 synthetic + 12 real-data), ~45 s
```

Risk API: `RiskEngine.from_artifacts("artifacts/risk").score(token, "typical" | "feb-2025-like" | 1.4).to_dict()`.
Do not rerun the pipeline into `curated/` with a different `AEP_TOKEN_KEY` unless you mean to: tokens change with the key.

## Environment

- The root `.venv/` is tracked in git (5,427 files), was built on another machine, and has no working interpreter
  on other checkouts, so `.venv/bin/python` fails there. Make your own venv outside the repo. Python 3.12 matches
  the pipeline and the Lambda runtime, and the risk tests pass on it. Untracking `.venv/` is pending a team decision.
- The risk engine itself uses only the standard library. Pin exact dependency versions.
- `CONCIERGE_TOKEN_SALT` (risk engine) and `AEP_TOKEN_KEY` (pipeline) are separate secrets that produce incompatible
  token formats. Never commit either.
- `.venv/` also holds empty, tracked `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env`. They are misplaced
  and should go with the venv.
- OpenSpec CLI 1.15.0 is installed (`openspec validate <change> --strict`). `curate-challenge-data` validates;
  `spec-02-risk-engine` has one warning (an over-long requirement) that fails `--strict`.
