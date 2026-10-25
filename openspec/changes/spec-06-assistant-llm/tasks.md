## 1. Fact card

- [x] 1.1 Create `backend/concierge/assistant/` and `backend/tests/assistant/`; add a recording fake Bedrock client and a card fixture for each band and for insufficient data
- [x] 1.2 Implement `factcard.py` building `factcard-v1` from the risk, restoration, guidance, and alert outputs; digit-free factor sentences keyed by (id, direction)
- [x] 1.3 Implement `to_prompt()` projection: `{{range}}` and `{{prior_range}}` placeholders, no strings with digits, digit-free confidence and assumption replacements, needs reduced to a boolean; test no digit and no token
- [x] 1.4 Implement `sanitize_card()` (safe characters, length cap, no braces or newlines, instruction-like deny-list); tests for the injection and brace cases

## 2. Safety text and libraries

- [x] 2.1 Implement `safety.py` constants and the placement table; assert-present check that raises when a required string is missing
- [x] 2.2 Add `UTILITY_OUTAGE_CONTACT` handling with generic wording when unset; test no digits or links in the default text
- [ ] 2.3 Write `guidance/during.py` and `guidance/after.py` item libraries (no numbers, no unverified food-safety timings, reviewed wording); have the team review the text before merge
  - Libraries are written and tested for no digits, no safety-topic words and plain sentence length. The team review of the wording is still open. One existing item (`shelter_plan` in `guidance/prepare.py`) was split into two sentences so the model can rephrase it within the guard's sentence-length limit.
- [x] 2.4 Move need-specific items so code appends them verbatim after model items; test they are never in a prompt

## 3. Prompts

- [x] 3.1 Write the shared system prompt and the eight `*.v1.txt` prompts with fixed JSON shapes; record prompt hashes
- [x] 3.2 Implement prompt assembly that places the system prompt and the delimited card in separate Converse fields; test that the safety text and the token are absent
- [x] 3.3 Test the prompt scan over 200 random real premises (needs the artifacts): no row value, circuit id, home count, or hex cell

## 4. Guard

- [x] 4.1 Implement `guard.py` rules 1 to 9 from design D3; one test per rule, including digits, spelled-out numbers, placeholders, estimate wording, conditional pre-storm times, weather, promises, links, meta phrases, and sentence length
- [x] 4.2 Implement strict JSON parsing, key whitelist, length caps, markup stripping
- [x] 4.3 Implement item-id checks for the three action kinds (exact set, no repeats)
- [x] 4.4 Implement placeholder fill from the full card; test the same draft gives each customer their own range
- [x] 4.5 Property test (hypothesis): for random drafts, if the guard passes then the final text contains no digit except those from the card's range fields and the fixed safety text

## 5. Fallbacks and orchestration

- [x] 5.1 Implement fallbacks for all eight kinds (explanation composer, library items with headline, spec-05 templates); test each passes the guard and safety checks
- [x] 5.2 Implement `compose.py`: card -> sanitize -> cache -> model -> guard -> fill -> safety -> response with provenance and the three sections
- [x] 5.3 Implement the Bedrock call in `phrase.py` with Converse, temperature 0.2, per-kind token cap, 8 s read timeout, one retry on throttling
- [x] 5.4 Implement the circuit breaker (3 failures in 60 s opens for 60 s, one trial after); test with a failing fake and a clock stub
- [x] 5.5 Test a burst of 50 requests with a throttling fake: all return valid fallbacks

## 6. Cache

- [x] 6.1 Implement the cache key (design D4) and an LRU with 24-hour expiry; test that each extra key field changes the key
- [x] 6.2 Store only guard-passed drafts in placeholder form; test failed drafts are not cached and `use_llm = false` bypasses the cache
- [x] 6.3 Property test: two cards with equal keys give identical template output apart from filled placeholders
  - Covers the seven kinds other than `explanation`. The explanation fallback quotes the engine's confidence reason, which states a record count that the cache key does not hold, so two equal-key cards can differ there. The cached draft itself never carries a count (the model sees a digit-free reason).

## 7. API and validation

- [x] 7.1 Add `POST /assistant` to `api/handler.py` and `api/service.py`; closed enums, unknown-field rejection, 8 KB limit; keep `/messages` and `/guidance` shapes
- [x] 7.2 Enforce token regex plus allow-list before any model call; unknown tokens return the insufficient-data fallback without a model call
- [x] 7.3 Build the injection corpus file and the test that runs it through every field and through card strings; assert 400 or a template and no corpus string in the recorder
- [x] 7.4 Log only kind, source, reasons, cache hit, and latency; log-capture test for tokens, drafts, and final text
- [x] 7.5 Run the identifier scan on all assistant responses for 200 random real premises
- [x] 7.6 Update `docs/api.md` (route, kinds, sections, provenance, what is never returned)

## 8. Verification

- [x] 8.1 Golden test: old four-message fallback text equals the new fallback for the same cards (or differences are listed and approved)
- [x] 8.2 With the real model, run each kind for the three bands and the insufficient-data case; record guard acceptance rate and the most common rejection reasons in `design.md`; tune prompts, not the guard
  - Run 2026-10-24 against Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`, us-east-2), 112 calls with the cache off. First run 85%, after prompt tuning 97%. Details in `design.md` ("Real-model check").
- [ ] 8.3 Manual burst test of 30 concurrent calls against the deployed API with the model on; record throttling behavior, fallback rate, and p95 latency (needs credentials and review before running)
- [ ] 8.4 Demo check: show model vs template (`use_llm` on and off), a rejected draft falling back, an injected token rejected, and the three response sections
- [ ] 8.5 Retire the old guard in `messages/compose.py` once 8.1 passes
  - 8.1 passes (the new fallback text equals the old text for all 128 combinations). Left open on purpose: the design's migration plan waits a week of parity before `/messages` moves onto the new flow, and `messages/compose.py` is still the fallback source and guard for `/messages` and `/alerts/simulate`.
