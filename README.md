# AEP-Hackathon-2026
# AI Outage Concierge

**HackOHI/O 2026 | AEP Challenge**
**Team:** Faiz Rabbani, Parker Miconi

> Predict. Explain. Prepare. Communicate.

An AI-powered assistant that helps customers understand storm-related outage risk, expected restoration time, how to prepare, and whether they qualify for alerts, with honest uncertainty and privacy built in.

---

## 1. Problem

A major storm is approaching. Customers want clear, personalized answers:

- Will I lose power?
- How long could restoration take?
- What should I do to prepare?
- Do I qualify for outage alerts?

Utilities hold the data to answer these (damage history, circuit maps, network topology, labor assumptions), but it is operational and hard for customers to use. Our assistant turns it into trusted, understandable guidance.

## 2. Our Solution (Summary)

A customer selects an anonymized location on a map. The assistant returns:

1. **Outage risk band** (Low / Medium / High) with a confidence level and top contributing factors.
2. **Restoration-time range** that updates when the judge or customer changes the scenario (storm severity, crews, damage).
3. **Personalized preparation and safety guidance**, with emergencies clearly labeled.
4. **Alert eligibility** and recommended timing, channel, and escalation.
5. **Proactive messages** (pre-storm, outage confirmed, estimate updated, restored).
6. **Transparency panel:** confirmed facts vs. model estimates vs. customer actions, plus assumptions, missing data, and fallback.

## 3. Customer Journey

1. Customer picks a location, premise, or circuit.
2. Assistant shows risk band + confidence + "why".
3. Customer adjusts the scenario (storm severity slider, crews available, damage type).
4. Assistant shows a restoration range and the factors that could move it.
5. Assistant lists preparation steps and alert eligibility.
6. Assistant generates the appropriate message for the current stage.

## 4. Architecture

```
 Data (GIS, damage, topology, labor)  ->  Feature table  ->  Risk model
                                                         ->  Restoration estimator
 Topology graph (matched_upstream)    ->  Customers-affected + step restoration
 Rules engine (alerts, safety)        ->  Eligibility + escalation
 LLM layer (explain only)             ->  Plain-language answers and messages
 Web UI (map + chat + sliders)        ->  Judge-facing demo
```

**Design principle:** the LLM never invents numbers. It only explains outputs from the model and rules.

## 5. Data

| Source | Contents | Use |
| --- | --- | --- |
| Data1.zip | Shapefiles: customers, poles, primary lines, fuse banks, damage assessments, ambulance, fire stations, hospitals | Core features, damage labels, priority sites, map |
| Data2.zip | Shapefiles: secondary lines, transformers, switches, recloser banks | Network detail, step restoration |
| matched_upstream.txt | Premise -> upstream equipment relationships | Dependency graph, customers affected |
| STORMS labor-hour reference (Excel) | Labor hours by damage type | Repair effort baseline |
| Indirect labor assumptions (Excel) | Travel and nonproductive-time multipliers | Realistic durations |
| Structure diagrams (PDF) | Sample structures incl. fuse drawing | Map damage type -> parts/materials |

> TODO: after inspecting the files, document actual layers, columns, row counts, event names, and data quality issues here.

## 6. Methodology

### 6.1 Storm severity
No weather data is provided, so severity is a **scenario input**:
- A slider / named scenarios (e.g., moderate storm, derecho, ice storm).
- Calibrated from historical events in the damage data (e.g., damage points per mile of line per event).
- Optional: add NOAA historical wind/ice data if time allows.

### 6.2 Outage risk model
- One row per asset / line segment / circuit; label = damaged in past events.
- Candidate features: asset type, primary vs. secondary, line length/exposure, nearby historical damage density, substation/district, downstream customer count, storm severity.
- Model: logistic regression or gradient boosting; feature importance powers the "top factors".
- **Customer risk is roughly the chance that any device in their upstream chain fails.**
- Output: risk band + confidence (based on data volume and model agreement).

### 6.3 Restoration-time estimate
- Effort = labor hours by damage type (STORMS) x quantity.
- Adjust with travel and nonproductive-time multipliers.
- Divide by crews available; report a range (optimistic to pessimistic).
- Use topology for step restoration: upstream repairs restore many customers first.
- Clearly labeled as a **model estimate, not a confirmed utility ETA**.

### 6.4 Repair prioritization
Priority = (customers restored + critical-facility bonus) / estimated repair hours.
- Critical facilities: hospitals, fire stations, ambulance services (shown anonymized).
- Customers restored per repair comes from the upstream topology.

### 6.5 Role of the structure / fuse drawing
- Maps damage types (fuse, crossarm, insulator, etc.) to parts that need replacing.
- Fuse banks define protection zones: a fuse fault affects only the customers downstream.

### 6.6 Alerts
Documented rules (challenge assumptions), for example:
- High risk -> pre-storm alert via the customer's preferred channel.
- Critical facility nearby -> earlier alert + escalation.
- Estimate changes materially -> "estimate updated" message.

> TODO: finalize thresholds and list them in a table.

## 7. Responsible AI, Privacy, Safety

- **Uncertainty:** ranges and confidence levels, never false precision.
- **Fact separation:** confirmed vs. model estimate vs. customer action.
- **Privacy:** anonymized IDs only; no raw personal data, employee contacts, or exact sensitive-facility locations (critical sites shown as "critical facility nearby").
- **Safety:** hard-coded safety messages (downed lines, generator CO risk, 911 / utility emergency line) that the LLM cannot override.
- **Fallback:** when data is thin or confidence is low, say so, give general guidance, and hand off to a human.
- **Accessibility:** plain-language toggle, large text, screen-reader-friendly output; declared needs change advice without inferring sensitive attributes.
- **Controls:** input validation, confidence thresholds, audit log of every prediction (inputs, confidence, rules fired).

## 8. Features

- [ ] Map-based location selection
- [ ] Risk band + confidence + explanation
- [ ] Scenario sliders (storm severity, crews, damage)
- [ ] Restoration range + "what would change this"
- [ ] Preparation and safety guidance
- [ ] Alert eligibility + 4 message templates
- [ ] Priority-location mode
- [ ] Fallback mode
- [ ] Audit log

## 9. Tech Stack

> TODO: fill in. Suggested: Python (pandas, geopandas, scikit-learn, networkx), Streamlit or React + Leaflet for UI, an LLM API for explanations.

## 10. Setup and Run

```bash
# TODO: replace with real commands
git clone <repo>
cd ai-outage-concierge
pip install -r requirements.txt
# place data in ./raw/ (Data1.zip, Data2.zip, matched_upstream.zip)
python prepare_data.py
streamlit run app.py
```

## 11. Demo Script (for judges)

1. Select a location -> show risk band, confidence, top factors.
2. Move the storm-severity slider -> risk and range update live.
3. Change crews available -> restoration range updates.
4. Show preparation guidance and alert eligibility.
5. Generate a pre-storm and an "estimate updated" message.
6. Show the privacy/uncertainty panel and trigger the fallback.
7. Show the backtest (`docs/backtest.md`, `GET /evidence`). Say plainly: Low-risk areas had clearly less damage than average,
   but High and Medium cannot be told apart, High does not clearly beat the base rate, and the risk bands do not beat a
   simple circuit-size ranking by more than the uncertainty. The restoration range does not beat a flat one-day baseline either.

## 12. Limitations and Assumptions

- Storm severity is simulated/calibrated, not live weather.
- Labor and travel multipliers are representative averages.
- Estimates are not confirmed utility restoration times. The restoration range (P50 to P90) comes from a seeded
  simulation whose spreads (labor hours, crew presence) are stated assumptions, not values fitted to observed repairs;
  the backtest spec checks how well they hold up.
- The restoration model spreads damage evenly over the sections of a line (section length is not in the data), treats
  crews as one pool working down a priority list, and starts the clock when crews start (no assessment delay).
- Backtest (Sep 2024 -> Feb 2025, circuit level, `docs/backtest.md`): the risk bands separate Low from the rest, but the
  model does not clearly beat the base rate for High or a circuit-size baseline. The restoration range does not beat a flat
  one-day guess against observed repair days, and it runs much longer than them (the model times a customer's wait, ARCOS records
  how long each damaged asset took). Only two storms exist, Sep 2024 is mostly repair backlog, crews were never recorded,
  and repair time is not outage length.
- Prototype scope: focused and explainable, not a production outage-management system.

## 13. Future Work

- Live weather feeds and crew-tracking integration.
- Survival-style duration models.
- Multi-language and SMS/voice channels.
- Real customer-profile integration with proper consent.

## 14. Team

Faiz Rabbani, Parker Miconi

## 15. Acknowledgments

Data and challenge provided by AEP via HackOHI/O 2026.