# AI Outage Concierge

**HackOHI/O 2026 | AEP Challenge**
**Team:** Faiz Rabbani, Parker Miconi

> Predict. Explain. Prepare. Communicate.

An AI-powered assistant that helps customers understand storm-related outage risk, expected restoration time, how to prepare, and whether they qualify for alerts, with honest uncertainty and privacy built in.

The challenge text is in `docs/challenge.md`.

**Status:** code complete for the hackathon submission. The chat page, outage map, risk and restoration engines, alerts, assistant and weather are all built and run end to end on the real data. Two items are deliberately left out and listed in section 8 as unchecked: alert sign-up and audit screens in the web app (the API exists), and a real-data storm summary on the chat page (it shows labeled sample data).

---

## 1. Problem

A major storm is approaching. Customers want clear, personalized answers:

- Will I lose power?
- How long could restoration take?
- What should I do to prepare?
- Do I qualify for outage alerts?

Utilities hold the data to answer these (damage history, circuit maps, network topology, labor assumptions), but it is operational and hard for customers to use. Our assistant turns it into trusted, understandable guidance.

## 2. Our Solution (Summary)

A customer types a city or ZIP code, or picks an area on a map. Areas are groups of at least 10 homes, so no address or premise is ever shown. The app returns:

1. **Outage risk band** (Low / Medium / High) with a confidence level, the reason for that confidence, and the top contributing factors.
2. **Restoration-time range** (P50 to P90 hours) that updates when the judge or customer changes the scenario: storm strength, crews, access, and the number or kinds of repair jobs.
3. **Personalized preparation and safety guidance**, with safety items first and emergencies clearly labeled. Optional self-declared needs (medical equipment, limited mobility, electric heat or cooling) change the advice.
4. **Alert eligibility** with the rules that fired, recommended timing and channels, and escalation.
5. **Proactive messages** for four stages (pre-storm, outage confirmed, estimate updated, restoration complete).
6. **Transparency panel:** confirmed facts vs. model estimates vs. customer actions, plus assumptions, missing data, and a fallback when the model cannot rate an area.
7. **Evidence card:** how the model did on a backtest of two past storms, including where it did not beat a simple baseline.
8. **Plain-language help:** a circled "i" beside every term or number a customer might not know (risk band, confidence, restoration range, storm strength, peak gust, alert level, source labels and more). It opens on hover, keyboard focus or tap, and explains what the item is or how it is worked out. The wording lives in `frontend/src/lib/explain.ts`.

## 3. Customer Journey

1. Customer types a city or ZIP code (chat page) or picks an area on the map.
2. Assistant shows risk band + confidence + "why".
3. Customer adjusts the scenario: storm strength (a past-storm preset, or live weather), crews, access, repair jobs.
4. Assistant shows a restoration range and the factors that could move it.
5. Assistant lists preparation steps and alert eligibility.
6. Assistant shows the message for each stage.

## 4. Architecture

```
 data/ (GIS, damage, topology, labor)
   -> scripts/curate.py               clean tables      artifacts/curated/
   -> scripts/build_risk_artifacts.py risk rows, tree, zones, calibration   artifacts/risk/
        |
        v
 Lambda handler (backend/concierge/api)  <- same code runs locally via scripts/dev_api.py
   risk engine        premise token -> band, confidence, factors
   restoration engine seeded Monte Carlo over a restoration tree (step restoration) -> P50 / P90
   guidance           hard-coded safety text + per-risk preparation items
   alerts             rules table (alerts/rules_table.json) -> tier, timing, channels, audit entry
   messages/assistant templates, optionally phrased by Bedrock, then checked by a guard
   weather            Open-Meteo wind gusts -> storm strength (optional)
        |
        v
 frontend/ (React + Leaflet): chat page, outage map, safety, about
```

**Design principle:** the LLM never invents numbers. Deterministic Python computes every number. The model only phrases text from a whitelisted fact card, and if its output contains a number that is not in the card, the template text is used instead.

A diagram and a longer walkthrough are in `docs/architecture.md` (`docs/architecture.svg`).

The deployed shape (CDK in `infra/`) is S3 + CloudFront for the web app, an API Gateway HTTP API in front of a Python 3.12 Lambda, DynamoDB for the risk table and demo alert sign-ups, and Bedrock for optional phrasing.

## 5. Data

All sources come from the organizer package and sit in `data/` (the `.dbf` files are Git LFS objects; run `git lfs pull` on a fresh clone). Row counts and match rates are in `docs/data_findings.md`; column rules are in `docs/data_dictionary.md` and `docs/data-cleaning.md`.

| Source | Contents | Use |
| --- | --- | --- |
| `Data1/` | Shapefiles: customers (117,642), poles, primary lines, fuse banks, ARCOS damage assessments (1,659 rows), ambulance, fire stations, hospitals | Damage labels, features, map, priority sites |
| `Data2/` | Shapefiles: secondary lines, transformers, switches, recloser banks | Network detail, protective devices |
| `matched_upstream/` | Premise -> upstream device chain (117,599 premises, 110 circuits) | Dependency graph, step restoration |
| `Install_Labor_HackOHIO.xlsx` | Labor hours per unit, access adders, indirect multiplier | Repair effort baseline |
| `docs/structure_drawings.pdf` | Structure diagrams (images only) | Reference for mapping damage types to parts |

Facts that shaped the design:

- The damage data has two storms: 2024-09-27 to 10-02 (193 rows) and 2025-02-12 to 02-18 (1,254 rows). There is no damage-type or quantity field.
- The data covers the **Roanoke Valley, Virginia** (about 37.0 to 37.5 N, 79.6 to 80.3 W). The app says so and answers "not covered" for other places. For a US place outside it, `POST /outlook` gives a forecast-only outlook with no risk band, restoration time or alerts.
- ARCOS circuit aliases (`CIR60`) and topology circuit ids (`0000060`) do not map one-to-one, so damage attribution to circuits has known error (see `CLAUDE.md`, "Verified data facts").

Raw data is never modified. Derived output goes to `artifacts/` (gitignored except the backtest report).

## 6. Methodology

### 6.1 Storm severity
No weather data is provided with the challenge, so severity is a **scenario input** by default: `sep-2024-like`, `typical`, `feb-2025-like`, `beyond-observed`, or a number from 0.25 to 3.0, calibrated from the two storms in the damage data. The map can also take severity from real weather (Open-Meteo wind gusts), live or replayed for a past date; see `docs/api.md`, "Weather".

### 6.2 Outage risk model
- Not a trained classifier. It is a transparent score, `S = R_eff x T x E x m` (`backend/concierge/risk/`): the circuit's historical damage rate (reports per 1,000 premises per storm), adjusted for service type `T` (overhead 1.0, underground 0.4), exposure `E` (1 + 0.10 per protective device upstream + 0.05 per hop to the breaker) and the storm multiplier `m`. `T` and `E` are stated assumptions, not fitted values.
- Band cut points are the 50th and 85th percentiles of the baseline scores across all premises, so Low is roughly the bottom half, Medium the next 35% and High the top 15% (`artifacts/risk/calibration.json`). The `top factors` come from the score's own terms.
- The label in the code says what it is: "circuit-level historical susceptibility, not a per-premise forecast". It gives a band, not a probability. The backtest calibrates on Sep 2024 and tests on Feb 2025.
- Each map area is rated through one typical home in it (the median-risk premise). Other homes in the area can differ, and the app says so.
- Output: risk band + confidence (based on the number of damage records behind the rating). When data is thin the engine returns a fallback instead of a band.

### 6.3 Restoration-time estimate
- Effort = labor hours by asset type from the labor workbook, plus access adders and the indirect multiplier (travel and nonproductive time).
- A seeded Monte Carlo simulation over a restoration tree (devices between the substation and the customer) with crews as one pool working down a priority list. Reports P50 and P90 and the steps in which service returns.
- Spreads (labor hours, crew presence, working hours per day) are **stated assumptions**, not fitted values.
- Labeled everywhere as a **model estimate, not a confirmed utility ETA**.

### 6.4 Repair prioritization
Priority = (customers restored + critical-facility bonus) / estimated repair hours. Hospitals, fire stations and ambulance services count toward the bonus but are never shown by name or location; an area only gets a "critical facility nearby" note.

### 6.5 Alerts
Rules are data, in `backend/concierge/alerts/rules_table.json` (version `2026-10-24.1`). They are prototype assumptions, not utility policy.

| Risk band | Expected outage under 8 h | 8 to 24 h | 24 to 72 h | 72 h or more |
| --- | --- | --- | --- | --- |
| Low | info | info | standard | standard |
| Medium | standard | standard | elevated | elevated |
| High | standard | elevated | elevated | priority |
| Unrated | standard (cautious general level) | | | |

Declared-need rules: any declared need raises the tier one step; declared medical equipment sets at least `elevated`, and `priority` when the P50 is 4 hours or more. Each tier sets pre-storm lead times (for example `standard`: 24 h and 6 h before; `elevated`: 48, 24 and 6 h), the time allowed to confirm an outage, the minimum gap between estimate updates, and channel order. Every decision is written to an audit entry with its inputs and the rules that fired (`GET /alerts/audit`, kept 7 days).

### 6.6 Role of the structure / fuse drawing
Fuse banks and other protective devices define protection zones in the restoration tree: a fault at a fuse affects only the customers downstream. The PDF itself is image-only and is used as a reference for the asset-type-to-labor mapping (`docs/data-cleaning.md`), not parsed.

## 7. Responsible AI, Privacy, Safety

- **Uncertainty:** ranges and confidence levels, never false precision. The restoration range is P50 to P90.
- **Fact separation:** the response has separate `confirmed`, model-estimate and customer-action sections. `confirmed` states that no utility data is connected.
- **Privacy:** salted opaque tokens only. Map areas cover at least 10 homes. No addresses, raw premise numbers, meter or device ids, circuit labels, or facility names and locations leave the server. The city or ZIP a person types is matched against public tables and dropped, and street addresses are rejected. Declared needs are used for that request only.
- **Safety:** hard-coded safety messages (downed lines, generator carbon monoxide, 911 and the utility emergency line) that the LLM cannot override. A check raises if a required string is missing.
- **Fallback:** when the model cannot rate an area, the app says so and tells the customer to prepare as if an outage is possible.
- **Accessibility:** native controls with labels, live regions on value changes, large touch targets and screen-reader text. The "i" help bubbles work with mouse, keyboard (focus opens, Escape closes), touch and screen readers, and never carry color alone. Full validation would need manual testing with assistive technologies.
- **Controls:** input validation (unknown fields rejected, 8 KB body limit), confidence thresholds, an output guard on every model draft, a circuit breaker on the model, and an audit log of alert decisions.
- **Demo only:** alert sign-ups accept fictional contacts only and nothing is ever sent. The API has **no authentication**; do not expose it beyond a demo.

## 8. Features

- [x] Map-based location selection (Leaflet), plus city / ZIP lookup
- [x] Risk band + confidence + explanation
- [x] Scenario controls: storm strength (past storms or live weather), crews, access, repair jobs and damage kinds
- [x] Restoration range + "what would change this"
- [x] Preparation and safety guidance, with declared-needs tailoring
- [x] Alert eligibility + four message templates (optionally phrased by a model, with template fallback)
- [x] Priority-location awareness (critical facility nearby note and alert escalation; sites are never named)
- [x] Fallback mode (unrated areas, places outside the data)
- [x] Audit log of alert decisions (API: `GET /alerts/audit`)
- [x] Backtest and evidence card (`docs/backtest.md`, `GET /evidence`)
- [x] Plain-language "i" help bubbles on confusing terms and metrics (chat page and map)
- [ ] Alert sign-up and audit log screens in the web app (API only today)
- [ ] Chat page storm summary on real data (still a sample)

## 9. Tech Stack

| Layer | Choice |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, Leaflet. No outside links in the app |
| API | Python 3.12 Lambda behind an API Gateway HTTP API, one handler (`backend/concierge/api/handler.py`) |
| Engines | Standard-library Python for risk, restoration, alerts and guidance |
| Data prep | pyarrow, pyshp, pyproj, openpyxl, h3 (`pyproject.toml`); GeoPandas pipeline in `pipeline/` |
| LLM | Amazon Bedrock Converse (optional), behind a number guard and a template fallback |
| Weather | Open-Meteo (wind gusts) |
| Storage | S3 (raw and clean buckets), DynamoDB (risk rows, demo sign-ups) |
| Infra | AWS CDK in TypeScript (`infra/`), CloudFront for the web app |
| Tests | pytest + hypothesis (1,400+ backend tests), `tsc` for the frontend |

API reference: `docs/api.md`. Design history: `openspec/changes/`.

## 10. Setup and Run

Run the app locally (no AWS needed; the built artifacts must exist under `artifacts/`):

```bash
git clone <repo> && cd AEP-Hackathon-2026
git lfs pull                                   # the shapefile .dbf files

# Use your own Python 3.12 venv outside the repo (the committed .venv is not usable on other machines)
python3.12 -m venv ~/aep-venv
~/aep-venv/bin/pip install pyarrow==26.0.0 pyshp==3.1.6 pyproj==3.7.2 openpyxl==3.1.5 h3==4.5.0 certifi

# Build the artifacts once (the same salt must be used for both steps; keep it secret)
export CONCIERGE_TOKEN_SALT='choose-a-long-random-secret'
~/aep-venv/bin/python scripts/curate.py --no-drawings
~/aep-venv/bin/python scripts/build_risk_artifacts.py

# Start the local API on :8787 and the web app on http://localhost:5173
make dev PY=$HOME/aep-venv/bin/python
```

`make dev-api` runs only the API (`scripts/dev_api.py`, bound to 127.0.0.1); then run `npm run dev` in `frontend/` yourself.

Tests:

```bash
~/aep-venv/bin/pip install pytest==9.1.1 hypothesis==6.170.9
~/aep-venv/bin/python -m pytest                # about 2 minutes; real-data tests skip without artifacts/
cd frontend && npm run build                    # typecheck + production build
```

AWS (needs credentials and review; see `docs/aws-setup.md`): `make ingest etl validate`, `make load-table`, `make deploy`, `make publish-web`, `make smoke`.

## 11. Demo Script (for judges)

1. Type "Salem" or a Roanoke Valley ZIP, or click an area on the map -> show risk band, confidence, top factors.
2. Move the storm-strength slider -> the band and range update. Hover the circled "i" beside Confidence, the restoration range or Storm strength to show the plain-language explanations.
3. Change crews available, or the number of repair jobs -> the restoration range updates.
4. Show preparation guidance and tick a declared need; show alert eligibility and the rules that fired.
5. Show the four stage messages (pre-storm, outage confirmed, estimate updated, restored).
6. Show the transparency panel; type a place outside the data (for example an Ohio ZIP) to show "not covered" and the forecast-only outlook.
7. Show the backtest (`docs/backtest.md`, `GET /evidence`). Say plainly: Low-risk areas had clearly less damage than average, but High and Medium cannot be told apart, High does not clearly beat the base rate, and the risk bands do not beat a simple circuit-size ranking by more than the uncertainty. The restoration range does not beat a flat one-day baseline either. About 82% of repairs finished within the predicted P90, close to the 90% a P90 should reach.

## 12. Limitations and Assumptions

- Storm severity is a scenario by default. With `severity: "weather"` it comes from Open-Meteo wind gusts placed on a two-point scale built from the two storms in the data. That scale is an assumption, not a fitted model, and ignores ice, snow load and rain.
- Labor and travel multipliers are representative averages.
- Estimates are not confirmed utility restoration times. The restoration range (P50 to P90) comes from a seeded simulation whose spreads (labor hours, crew presence) are stated assumptions, not values fitted to observed repairs; the backtest checks how well they hold up.
- The restoration model spreads damage evenly over the sections of a line (section length is not in the data), treats crews as one pool working down a priority list, and starts the clock when crews start (no assessment delay).
- Backtest (Sep 2024 -> Feb 2025, circuit level, `docs/backtest.md`): the risk bands separate Low from the rest, but the model does not clearly beat the base rate for High or a circuit-size baseline. The restoration range does not beat a flat one-day guess against observed repair days, and it runs much longer than them (the model times a customer's wait, ARCOS records how long each damaged asset took). Only two storms exist, Sep 2024 is mostly repair backlog, crews were never recorded, and repair time is not outage length.
- Coverage is the Roanoke Valley only. Damage is attributed to circuits through alias digits, which are wrong for roughly 18% of in-storm records (see `CLAUDE.md`).
- The deployed Lambda account quota is 10 concurrent executions. In a 30-call burst test, 20 calls returned API Gateway's 503. Callers should retry after a short wait.
- Prototype scope: focused and explainable, not a production outage-management system.

## 13. Future Work

- Fit the weather-to-severity scale on more storms, and use ice and snow load. Crew-tracking integration.
- Survival-style duration models.
- Alert sign-up and audit screens in the web app.
- Multi-language and SMS/voice channels.
- Real customer-profile integration with proper consent.

## 14. Team

Faiz Rabbani, Parker Miconi

## 15. Acknowledgments

Data and challenge provided by AEP via HackOHI/O 2026. Map background: OpenStreetMap contributors. Weather: Open-Meteo.com (CC BY 4.0).
