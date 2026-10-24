# CLAUDE.md

Guidance for AI coding agents working in this repo.

## Project

AI Outage Concierge, HackOHI/O 2026 AEP challenge (team: Faiz Rabbani, Parker Miconi).
A customer picks an anonymized location before a storm and asks: Will I lose power? How long could
restoration take? What should I do? Do I qualify for alerts? Challenge text: `docs/challenge.md`.

Status: `spec-02-risk-engine` is implemented (backend only). Work is driven by OpenSpec changes in `openspec/changes/`.

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
| `backend/concierge/risk/` | Risk engine: loaders, storms, features, calibration, confidence, explanation, engine, build |
| `backend/tests/risk/` | Unit, property (hypothesis), synthetic-fixture, integration, and real-data tests |
| `scripts/build_risk_artifacts.py` | Builds `artifacts/risk/` (gitignored) from `data/` |
| `data/Install_Labor_HackOHIO.xlsx` | Labor hours per unit, access adders, indirect multiplier (4.5) |

Never modify files under `data/`. Write derived outputs elsewhere (e.g. `artifacts/`).

## Verified data facts (checked 2026-10-24)

- `.dbf` files are Git LFS objects. They are pulled in this checkout; on a fresh clone run `git lfs pull`.
- ARCOS damage: 1,659 rows. `Event` is "Default" everywhere. Storms come from `Report_Tim` clusters:
  2024-09-26..10-02 (~194 rows) and 2025-02-12..02-18 (1,254 rows). ~210 rows fall outside both storms.
- ARCOS `Asset_Type`: primary 704, Pole 355, Fuse Bank 280, secondary 156, Transformer 74,
  Recloser Bank 58, Switch 19, Capacitor Bank 6. No damage-type or quantity field.
- Circuit keys: ARCOS `Circuit` = "CIR60", topology/customer = "0000060". Normalize both to int.
  85 ARCOS circuits; 81 join the topology (CIR12 and blank/non-numeric values do not).
- Topology: 117,599 premises, 110 circuits. Each line is `premise<TAB>class*id*type*name...`, ending at
  `eo_circuit*...*Circuit Breaker*<circuit>`. Protective types: Fuse, Recloser (Hydraulic/Electronic),
  Sectionalizer. `Step` is a step transformer (not protective). Chain length 2 to 13.
- Customer_Clip: 117,642 rows; `premise_nu` joins 117,595 topology premises. `kind_of_se`:
  Overhead 64,824, Underground 52,419, Unknown 399. Circuit sizes range from 4 to ~3,500 premises.
- Facility layers (hospital, fire, ambulance) are EPSG:3857; everything else WGS84.

## Privacy rules (non-negotiable)

Never send these to the UI, logs, or the LLM: `critical_c` (life-support flag), `address`, `address_nu`,
`anno_strin`, `road_name`, `city`, service coordinates, raw `premise_nu`, meter/location IDs, device IDs,
substation/circuit labels, hospital names/addresses, ARCOS crew/NEAR_FID fields. Use salted premise
tokens. Suppress any group under 10 premises. Never infer sensitive needs; only use self-declared ones.
Load only whitelisted columns from source files.

## Commands

```bash
.venv/bin/python -m pip install -e '.[dev]'     # or: pip install pytest==9.1.1 hypothesis==6.170.9
.venv/bin/python -m pytest                       # all tests, ~2 s (includes the real-data build)
CONCIERGE_TOKEN_SALT=<secret> .venv/bin/python scripts/build_risk_artifacts.py --sample
```

Risk API: `RiskEngine.from_artifacts("artifacts/risk").score(token, "typical" | "feb-2025-like" | 1.4).to_dict()`.

## Environment

- Python 3.11 venv in `.venv/` (pytest and hypothesis installed). The risk engine itself uses only the standard library. Pin exact dependency versions.
- `CONCIERGE_TOKEN_SALT` must be set to build artifacts. Never commit it.
- `.venv/` currently also holds empty `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env`.
  These look misplaced; real project files belong at the repo root.
- OpenSpec CLI is not installed (`npm i -g @fission-ai/openspec` to get `openspec validate`).
