AGENT COUNCIL: AEP AI OUTAGE CONCIERGE (HackOHI/O 2026) - BEST MVP
===================================================================
Local council, 4 members: Devil's Advocate, Simplicity Champion, Security Auditor, Scalability Architect.
All four are Claude playing different roles, not different AI vendors. When they agree, treat it as a
starting point to test, not as independent confirmation. Every member read the repo data directly.

-------------------------------------------------------------------
0. STEP ZERO: THE ATTRIBUTE DATA IS NOT IN THE CHECKOUT
-------------------------------------------------------------------
- Every .dbf file under data/Data1 and data/Data2 is a ~130-byte Git LFS pointer, not a real table.
  .gitattributes routes *.dbf through LFS, and git-lfs is not installed on this machine.
- What that means: the shapefiles have geometry but no attributes. There are no circuit IDs, no premise IDs,
  no damage types and no repair dates until someone runs:
      brew install git-lfs && git lfs install && git lfs pull
  The real files total about 330 MB (Secondary 106 MB, Customer 79 MB, Pole 55 MB, Primary 50 MB, ARCOS 7.4 MB).
- The Simplicity and Security members fetched read-only copies into the session scratchpad and checked the
  facts below against them.
- Backup plan: get the original zip from the organizers, OR commit one small CSV extract that the whole
  team works from.

-------------------------------------------------------------------
1. VERIFIED DATA FACTS
-------------------------------------------------------------------
ARCOS damage (1,659 points; Roanoke VA / APCO region)
- The Event field is "Default" for every row, but Report_Tim falls into TWO storms:
  Sep 2024 (175 rows, Helene) and Feb 2025 (1,254 rows).
- Asset_Type: primary 704, Pole 355, Fuse Bank 280, secondary 156, Transformer 74, Recloser 58.
- Access_Iss = Yes on 535 rows. Special_Eq is filled on ~286 (Backyard Bucket, Track Hoe, ...).
- There is NO explicit damage-type or quantity field. Asset_Type is what has to map to STORMS labor units.
- Repair time is recorded in whole days (Repaired_D - Report_Tim):
  0d=500, 1d=423, 2d=230, 3d=69, then a tail to about 20 days. Treat the tail as backlog, not outage length.

Topology: matched_upstream.txt
- 117,599 premises across 110 circuits and ~25.4k distribution transformers. Every premise has a full chain:
  xfmr -> fuse / recloser / sectionalizer -> breaker.
- Chain depth is 2 to 13 hops (mode 4). A median device serves 6 customers; the largest serves 2,811.

Customer_Clip (117,642 points)
- premise_nu joins 117,595 of the topology premises. That leaves 43+ unmatched premises, which make a good
  "can't predict" fallback demo.
- Other fields: kind_of_se (Overhead/Underground), circuit_id, and critical_c, which has 113 "LS Life Support" rows.

Circuit key between ARCOS and topology
- ARCOS uses names like "CIR60"; the topology uses "0000060". The aep_circui and circuit_id fields in
  Transformer_Clip map 82 of the 85 damage circuits.

Install_Labor_HackOHIO.xlsx
- Hours per unit: pole 1.9-3.4 h, transformer ~1.2-1.4, recloser 0.57, switch 1.76, fuse cutout 0.26, crossarm 0.37.
- Access adders: 0.5-1.3 h (rock hole 4 h).
- Indirect Labor Multiplier (ILM) = 4.5.

Critical-facility layers
- Hospitals 5, Fire 37, Ambulance 10. They are in Web Mercator (EPSG:3857) while everything else is WGS84,
  so reproject them before doing any distance math.
- The Hospitals layer includes names and addresses.

structure_drawings.pdf
- Drawings only, no text layer.
- Use: turn a damaged asset into its bill of materials and labor units, e.g. a transformer pole =
  pole + crossarm + cutout + arrester + transformer.

-------------------------------------------------------------------
2. CORE PROBLEM
-------------------------------------------------------------------
Turn one or two storms of damage records, the network topology and the labor standards into honest,
personalized answers for one customer:
- "Will I lose power?"
- "How long?"
- "What do I do?"
- "Do I get alerts?"

Each answer needs visible uncertainty, adjustable scenarios and no leaking of sensitive data. Judges reward a
credible, explainable prototype over breadth. The challenge text says so explicitly.

-------------------------------------------------------------------
3. OPTIONS THE COUNCIL CONSIDERED
-------------------------------------------------------------------
A. ML outage classifier + LLM chatbot + full GIS map.
   Rejected by 3 of 4 members. There is no record of where damage did NOT happen, the labels are spatially
   leaky (they track where the wind went), and there is effectively 1-2 events. A judge asking "validated
   against what?" would sink it.
B. Topology-exposure risk score + labor-hour restoration simulator + LLM that only narrates numbers.
   CHOSEN. Every member's own pick is a variant of this.
C. Pure lookup + templates, no LLM.
   Simplest option. Too plain for "AI-powered", but it is the fallback path inside B.
D. RAG / agent framework / vector DB.
   Cut. No unstructured corpus exists that would justify it.

-------------------------------------------------------------------
4. THE SOLUTION: "STORM REPLAY CONCIERGE"
-------------------------------------------------------------------
A deterministic engine computes every number; the LLM never produces a number.

(1) OFFLINE PRECOMPUTE (Python + pyshp, run once)
- Output premise_card.json, one row per premise:
  circuit, OH/UG, hop count, upstream device chain, downstream customer count per device,
  circuit damage rate per 1k premises, and nearest-damage distance.
- Output device_tree.json for the restoration simulation.
- Show premises to users only as a hashed token or a hex cell of at least ~0.5 km. Never an address or a pin.

(2) RISK BAND (Low / Med / High + confidence)
- Score = circuit historical damage density, weighted by:
  - overhead (1.0) vs underground (~0.4)
  - exposure along the upstream chain (number of fuses/reclosers, hops to the breaker)
  - a storm-severity slider
- Calibrate the bands using the real hit rate per band from the storm data.
- Confidence comes from how many damage records the circuit has. Too few records means "Insufficient data"
  (the required fallback).
- Show the top 3 factors in plain words.
- Be honest about what it is: "circuit-level historical susceptibility", NOT a per-premise forecast.

(3) RESTORATION RANGE (runs in the browser, so sliders respond instantly)
- Damaged assets -> bill of materials (from the structure drawings) -> STORMS hours per unit
  -> + access adders -> x ILM 4.5 -> / crews.
- Step restoration: sequence jobs upstream-first, ordered by customers restored per hour. A premise behind a
  repaired recloser comes back before one behind a damaged fuse.
- Report P50-P90 as a range, never a single point.
- Sliders: storm severity, damage count, crews, access-issue toggle.
- State the crew-size assumption (e.g. 2-4 people per crew). Without it, estimates can be off by 2-4x.

(4) THE WINNING MOMENT: A BACKTEST PANEL
- Fit or calibrate on Helene (Sep 2024), predict Feb 2025 at circuit level, and show accuracy honestly.
- Plot the restoration formula against the observed Report -> Repaired days (0-3 days for most records).
- Very likely no other team will have validated estimates.

(5) PREP, ALERTS AND MESSAGES
- Write the rules table down on screen: risk band x expected duration x SELF-DECLARED needs
  (a "medical / life-support" checkbox escalates the alert tier and channel).
- The app never infers sensitive attributes from utility records.
- LLM (Claude) input is a whitelisted "fact card" JSON only: band, range, top factors, assumptions, and an
  "estimate, not confirmed" label.
- LLM output:
  - an explanation
  - prep and safety steps (safety items labeled)
  - four messages: pre-storm, outage-confirmed, estimate-updated, restored.
- Number guard: if the LLM text contains a number that does not match the engine output, fall back to a template.
- Cache the LLM text by (band, range bucket, needs flag, stage), about 240 variants, so the demo works offline.

(6) UI
- One page: premise / hex picker -> risk card -> restoration sliders -> prep and alerts -> message previews.
- Add a "How we protect you" panel: what is hidden, the assumptions, and the confidence.
- Map: one Leaflet layer of hexes colored by band, with one circuit drawn at a time.
  Never render all ~190k line segments.

Stack: Python precompute + Streamlit, or a static HTML/JS page + the Claude API. No backend database, no auth.

-------------------------------------------------------------------
5. PRIVACY RULES (judged under "Responsible AI", and cheap to do)
-------------------------------------------------------------------
- These must never reach the UI or the LLM:
  - Customer: critical_c (LS flag), anno_strin (house numbers), road_name/city, service lat/lon, premise_nu,
    meter/location IDs
  - Hospital NAME/ADDRESS
  - Assets: scada / station / owner fields
  - ARCOS: Substation/Circuit labels and NEAR_FID
- Suppress any device or hex group with fewer than 10 premises. Small fuses plus a hex can single out a house.
- Show critical facilities only as an aggregate factor ("critical facilities on your line are prioritized"),
  never as pins.
- Prompt injection: the LLM context holds no data rows, so "list life-support customers on my street" has
  nothing to leak. Validate the premise token against the allow-list.

-------------------------------------------------------------------
6. MEMBER POSITIONS (summary)
-------------------------------------------------------------------
- Devil's Advocate: no ML (one storm, no negatives, spatial leakage). Chain-exposure risk + "Helene Replay"
  simulator + a restoration backtest vs. actual repair times. Cut the free-form chatbot as the main UI
  (use guided cards with a chat side panel).
- Simplicity Champion: lookup + formula on one page; the LLM is optional and only rephrases text. Cut the
  pole/primary/secondary geometry, RAG and agents. One Leaflet dot or hex map is the only cosmetic worth adding.
- Security Auditor: a deterministic engine + an LLM that only narrates a sanitized fact card. The biggest risk is
  re-identifying life-support households by combining fields. The Helene -> Feb 2025 backtest is the
  responsible-AI proof.
- Scalability Architect: precompute per circuit; run the simulation in the browser (~1.7k jobs takes <10 ms);
  cache LLM output by band. Scale pitch: "a 100x storm is a CDN and cache problem, not a compute problem."
  It was the only member open to a light chain-segment model, P(out) = 1 - prod(1 - p_i), validated on
  held-out circuits.

-------------------------------------------------------------------
7. SYNTHESIS
-------------------------------------------------------------------
Where members agreed (a shared prior, so test it)
- No heavy ML. The numbers come from a topology + labor-formula engine; the LLM narrates only. Restoration
  is shown as a range; the LFS pull is the first step.
- Shared-model caveat: all four may be under-valuing a modest learned model. If the circuit-level holdout on
  Feb 2025 beats the rule baseline, show both.

Where members disagreed
- Single vs two storms. Devil's Advocate guessed one storm from the filename. The members who read the real
  data saw Sep 2024 + Feb 2025. Two storms make an honest out-of-sample backtest possible, and that is the
  best credibility lever. Use it.
- How central the LLM is. Simplicity says optional; the others say narrator. Decision: the LLM narrates and
  writes messages (it is an "AI concierge" challenge), with template fallback.
- How much map. Use a hex/circuit map only. A full GIS stack is a time sink.

Blind spots raised by one member
- Repair dates have day granularity, and the long tail is backlog.
- The crew-size assumption.
- The 3 unmapped circuits and the 43 unmatched premises.
- The CRS mismatch on the facility layers.
- Small-device re-identification.

Not covered by any member
- There is no weather or wind input in the data. The "storm severity" slider is a scenario multiplier, not a
  forecast. Label it that way.
- Pitch and demo script (3-minute flow: pick hex -> risk -> drag crews slider -> message -> backtest slide).
- Accessibility of the UI (plain language, contrast, screen reader).

-------------------------------------------------------------------
8. BUILD ORDER (hackathon timeline)
-------------------------------------------------------------------
1. git lfs pull; export a small CSV/JSON extract; build the circuit key map (CIR## <-> 00000##).
2. Precompute script -> premise_card.json + device_tree.json.
3. Risk band + restoration formula + sliders (working end-to-end with templates).
4. Backtest panel (Helene -> Feb 2025, and formula vs. observed repair days).
5. Claude narrator + 4 messages + number guard + cache.
6. Privacy panel, fallback cases, map polish, demo script.
