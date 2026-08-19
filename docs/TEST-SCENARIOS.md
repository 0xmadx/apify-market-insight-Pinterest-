# TEST SCENARIOS — what "done" means, for the agent that writes the code

> Acceptance scenarios for the graph layer and the four actors described in
> [ARCHITECTURE.md](ARCHITECTURE.md). Every expected value here was either
> **measured on the wire** (probe run 2026-08-18, raw responses in
> `../probes/results/`) or comes from a measured claim in
> [07-API-REFERENCE.md](wire/07-API-REFERENCE.md). Nothing is invented.
>
> Two kinds of test, and both are required:
> **[offline]** — runs against fixtures (the probe JSONs) with no network; fast,
> runs in CI, proves the parsers and the logic.
> **[live]** — one small real call through the vault; proves Pinterest still
> behaves as documented. Live tests are cheap by design (1–2 requests each) and
> skipped automatically when `src.status` reports no usable profile.
>
> House style: follow `tests/test_incremental.py` — plain check() functions,
> a throwaway Redis namespace, real assertions on real shapes. Write the test
> that proves a bug FIRST when fixing one.

---

## GROUP A — Transport (both call styles)

**A1. Style A envelope unwrap [offline]**
- Given the fixture `probes/results/3.2-topics_featured.json`
- When passed through the Style-A unwrapper
- Then the payload is exactly `resource_response.data` (a 5-item list) and
  `client_context` (which carries account PII) is dropped and never logged or
  stored.

**A2. Style A error surface [offline]**
- Given a synthetic envelope with `resource_response.error.message_detail = "X"`
- Then the transport raises/returns a typed API error carrying "X" — not `None`,
  not `{}`.

**A3. The mandatory header [live, 2 requests]**
- When calling any Style-A endpoint WITH `X-Pinterest-PWS-Handler: trends/index.js`
  → 200. WITHOUT it → 403 whose body contains `Invalid Resource Request`, and
  `classify()` returns **`malformed`** — NOT `auth_expired`, NOT `blocked`.
- And a `malformed` response must never trigger `mark_blocked` on the profile.
  (This is the guard that keeps a code bug from evicting the only session.)

**A4. Bootstrap date [live, 1 request]**
- `GET /latest_available_date/` returns `{"date": "<YYYY-MM-DD>"}`, the date
  parses, and is ≤ today. Every subsequent builder must be shown (by unit test)
  to inject exactly this value as `end_date`/`endDate` — a builder that defaults
  to `today()` is a failing test.

**A5. Backoff without headers [offline]**
- Given a mocked sequence 429, 429, 200 from `/metrics/`
- Then the client retries with exponential backoff and succeeds, and asserts the
  response carries NO `x-ratelimit-*`/`retry-after` header to read (backoff is
  blind by necessity — encode that as an assertion so a future "optimisation"
  reading those headers fails loudly).

---

## GROUP B — Parsers (one named parser per endpoint; never index raw keys inline)

**B1. The double-spelling trap ⭐ [offline]**
- Given `probes/results/3.13-metrics.json` (which contains BOTH `has_prediction`
  AND `hasPrediction`, same value — verified live)
- Then the metrics parser emits exactly one canonical field.
- And given the same record with `hasPrediction` deleted → same output.
- And with `has_prediction` deleted → same output.
- And with both deleted → `None` (absent, not `False`). *This scenario exists
  because reading one spelling silently breaks the day Pinterest drops the
  duplicate — the exact failure class that emptied every table in the parent
  project.*

**B2. Key coverage diff [offline, one test per parser]**
- For each fixture in `probes/results/`, assert: every key the parser reads
  exists in the fixture, and print (not fail on) fixture keys the parser
  ignores. A parser reading a key no response contains is the #1 historical bug;
  this test makes it impossible to reintroduce quietly.

**B3. Absent is not zero [offline]**
- Given a `top_trends_filtered` row with `yoy_change` missing/null (the 3.12
  fixture has `affinity: null` in 10/10 rows and null `yoy_change` occurs live)
- Then the parsed record carries `null`, never `0`, and `affinity` is dropped
  entirely (never populated — measured).

**B4. Parallel arrays zip [offline]**
- Given `probes/results/3.3-moment_available.json` (13 moments as parallel
  arrays: `moments`, `phase_labels`, `peaks`, `historical_peaks`,
  `moment_next_occurrence_timestamps`)
- Then the parser zips by index into 13 objects; timestamps (epoch-ms **strings**)
  become ints; a length mismatch between arrays raises, never truncates.

**B5. Multi-term response matching [offline]**
- Given a `/metrics/` fixture for terms `["a","b","c"]` returning only `a` and
  `c` (the API silently drops no-data terms — measured: 10 requested → 9 back)
- Then results are matched **by term**, `b` is reported as
  `{term: "b", status: "no_data"}` — not dropped, not misaligned by index.

**B6. Editorial per-region keywords [offline]**
- Given `probes/results/3.5-editorial_content.json`
- Then requesting region `US` yields each item's `keywords["US"]` list; a region
  absent from an item's keywords map yields `null` for that item, not another
  region's list.

---

## GROUP C — Vocabulary and validation (fail before the wire, with the reason)

**C1. One vertical per call 🚨 [offline]**
- When a `top/` request is built with `parent_product_categories` containing 2+
  IDs → the builder REFUSES with a message citing the normalisation rule.
  (Measured: Beauty's #1 = 1.00 alone, 0.01 beside Fashion — combining loses data.)

**C2. L1-only verticals [offline]**
- A `top/` build with an L2/L3 id (e.g. `1311`) → refused, message says "L1
  vertical required; L2/L3 return 200 with 0 rows silently". The 7 working L1
  ids are the allowlist: `1181,1250,1042,1148,1016,1500,1315`.

**C3. Lowercase keywords [offline]**
- Every keyword-pipeline builder lowercases input. `prefix_match` with an
  uppercase stem → refused or lowered (measured: `HALLOW` → 200 + `[]`, a silent
  empty).

**C4. Param ceilings [offline]** — builders refuse, citing the measured limit:
- `numTermsToReturn > 100` · `limit > 522` (top/) · `predicted_days > 91`
- `lookback_days`/`days > 730` · `interest_limit > 24`
- `aggregation != 2` on `/metrics/` · `trendsPreset ∉ {1,2,3,4}`
- `shouldMock=true` → refused always (returns fake 2019 data with HTTP 200)
- moment/metrics `monthly` + `predicted_days` not divisible into months → refused
  (measured 400: "91 predicted days do not evenly divide")

**C5. Region capability matrix [offline]**
- `top_products` or `editorial` with region `DE` → refused/`unsupported` BEFORE
  the wire (only US/CA/GB+IE return data), with the region list in the message.
- Moment slug validation is per-region: slug valid in US but absent from the
  requested region's `moment/available` list → refused (invalid slug → 400 live).

**C6. Two age/gender schemes never cross [offline]**
- `top_trends_filtered` uses numeric codes (`ageBuckets=4` = 25-34, `gender=1` =
  Female); shopping endpoints use enums (`AGE_25_34`, `FEMALE`). One shared
  builder input (e.g. `age="25-34"`) must map to the right scheme per endpoint;
  a test feeds each endpoint and asserts the emitted wire params differ correctly.

**C7. `event` is never mapped by UI label, and audience is always event-stamped [offline]**
- The detail page labels `ENGAGEMENT` as **"All"**; the shopping table labels the
  same value **"Engagement"**. A builder fed the label `"All"` must emit
  `event=ENGAGEMENT`; fed `"Engagement"` from the table context, also
  `ENGAGEMENT`. Any label→enum map that produces a different value for the two
  is a failing test. (Doc #7 §3.9, doc #3 §C4a.)
- Every demographics record carries the `event` it was measured under. Measured:
  category `1408` gives 65+ = **32%** under `OUTBOUND_CLICK` but **19%** under
  `SAVE`. A record stating an age/gender split without its `event` is a failing
  test — "the audience for this category" is not a well-formed fact.
- Conversely `related_search_trends` is identical across all three events (17
  terms, same order — verified). A planner that requests keywords once per event
  is a failing test; it must request them once total.

---

## GROUP D — Decision nodes (the traversal logic)

**D1. Crystal-ball branch [offline]**
- Given metrics fixtures where term X has prediction and term Y does not:
- X's record carries forecast bounds; Y's carries `forecast: null` AND the
  fallback ranking fields (growth_rates, seasonality_score); and the client
  performed NO retry-with-different-params for Y (`has_prediction` is fixed per
  keyword×region — verified inert).

**D2. Moment phase gate [offline]**
- Given the 3.3 fixture, only moments with phase ∈ {rising, approaching} get
  `moment/metrics` + keyword calls in the planned request list; cooled/frozen
  moments appear in output with phase only. Assert on the *planned request
  list*, not just the output.

**D3. Empty-but-200 tri-state [offline]**
- `top_products` with `event=SAVE` → output `unsupported_parameter`, not `[]`.
- `editorial` region FR → `region_unsupported`, not `[]`.
- `top/` genuinely small vertical (measured: 19 rows for a limit-20 ask, because
  the vertical HAS 19) → real data, NOT flagged as truncation.
- Three different causes, three different labels, never a bare empty list.

**D4. Moment demographics workaround [offline]**
- The moment-audience traversal (moments → moment keywords → /demographics/ →
  aggregate) emits `_meta.basis = "derived"` on the aggregated audience; a
  record claiming `measured` for it is a failing test.

**D5. Normalisation scope stamp [offline]**
- Any record carrying a relative count (`normalizedCount`,
  `percent_relative_volume`, …) also carries the scope it is relative to (the
  call/group id). Two records from different responses must have different
  scope ids — that is the machine-checkable form of "never compare across
  responses".

---

## GROUP E — The four actors (one end-to-end scenario each; fixtures first, then one budgeted live run)

**E1. keyword-research: discover→enrich [offline + live≤8 req]**
- Input `{mode: "discover", preset: 3, region: "US", limit: 10, include: all}`
- Then: 1 bootstrap + 1 discovery + batched pipeline calls (metrics ONE call for
  all 10 terms with `normalize_against_group=true` — 10 separate calls is a
  failing test); each record has term, growth trio, seasonality, 🔮 flag,
  demographics, 5 related, ≤9 images, `_meta.end_date`.
- Live budget: assert total request count ≤ 8 for 10 keywords.

**E2. shopping-trends: the mascara traversal [offline + live≤6 req]**
- Input `{region: "US", verticals: [1250], drill_top_n: 1}`
- Then: taxonomy joined (top category has `friendly_name`, not just an id);
  drilled category has demographics + search_queries (~17 terms, from ONE
  demographics call — a second call per event is a failing test: keywords are
  identical across events, measured); top_products present (US) with pin URLs
  built as `https://www.pinterest.com/pin/{pin_id}/`.

**E3. seasonal-moments: daily granularity [offline + live≤4 req]**
- Input `{region: "US", phases: ["rising","approaching"], aggregation: "daily"}`
- Then: only qualifying moments drilled; the curve has daily density (365-day
  lookback → ~456 points daily vs ~66 weekly — assert >300); audience block
  labelled `derived`; JP/IN region input → clean `no_moments_for_region`
  result, not a crash (measured: 0 moments).

**E4. trend-radar: only what changed [offline]**
- Run twice over the same fixtures with the seen-set active: first run emits
  5 spotlight + 6 editorial records; second run emits 0; then mutate one trend's
  `pct_growth_mom` in the fixture → third run emits exactly that 1 record.
  (This rides the already-tested state layer; the scenario proves the actor
  wires `fields=` correctly.)

---

## GROUP F — Shared-vault product behavior (customers on the operator's sessions)

**F1. Polite queue, loud failure [offline]**
- With an empty test-namespace vault, an actor run fails within `WAIT_TIMEOUT`
  with the customer-readable session message — never hangs, never emits an
  empty-but-"successful" dataset. (`VaultEmpty` path exists; test the actor
  surfaces it as a failed run status.)

**F2. Lease excludes concurrent identity sharing [offline]**
- Two simulated runs against a one-profile test vault: the second either waits
  or fails — it never holds the same lease. (Extends the existing vault tests to
  the actor entrypoint.)

**F3. Cache is shared and honest [offline]**
- Two runs, same taxonomy need, inside TTL → one wire call (assert via cache
  hit counters). A 403/empty response is never served from cache (already
  proven in `test_incremental.py` — keep it green).

**F4. Budget cap [offline]**
- `maxRecords`/`drill_top_n` caps the *planned request list* before execution;
  a plan exceeding the per-run budget is trimmed with a note in `_meta`, not
  executed and rate-limited.

---

## GROUP G — Live doc-drift canary (the probe, kept runnable)

**G1.** `probes/probe_endpoints.py` stays green: 16/16 OK. Run before any
release; any endpoint dropping to FAIL/EMPTY is a stop-ship until the doc and
parsers are reconciled with the new wire truth.

**G2.** Shape-drift check: after each probe run, re-run the B2 key-coverage
tests against the FRESH probe JSONs (not the committed fixtures). New keys are
logged as opportunities; vanished keys that a parser reads are failures.

---

## H, I, J — scenarios added by the pre-ship audit

These came from measuring, not from planning, and each one names a bug that
shipped:

| Id | Scenario | Expected (measured) |
|---|---|---|
| H1 | history caps differ per endpoint | shopping ~257d, keywords ~365d, moments ~730d. Past the cap: **HTTP 200 + empty list**, refused before the wire |
| H2 | date provenance | only discovery echoes the date it used; shopping and moment/metrics echo none, so `end_date_basis` is `requested` there |
| H3 | forecast from a past date | `predicted_days > 0` with a past `endDate` is HTTP 500 on keyword and shopping charts. Forecast dropped, history kept, `forecast_suppressed` explains |
| I1 | the Date-range control | `past_3_months/6_months/1_year/2_years` → 90/180/365/730, reaching the wire on all three surfaces |
| I2 | precedence | an explicit `days`/`chartDays`/`lookbackDays` **overrides** `dateRange` |
| I3 | moment regions | 17 of 32 have moments; the other 15 are refused, not silently empty |
| I4 | phase labels | wire `cooldown`→UI "Cooling", `off_season`/`ended`→"Frozen"; unknown phases pass through |
| J1 | crawl depth | depth 1 follows the entry page's keyword links; depth 2 reaches depth 2 (it silently did not) |
| J2 | crawl cost | breadth-first and batched — 74 nodes for ~17 requests, not ~975 |
| J3 | crawl budget | caps what is **followed**; entry page is a reported floor. Budget must count REAL requests — it once read an attribute only the test client had, and counted zero in production |
| J4 | crawl summary | terminal record; `truncated: true` means the dataset is partial |
| J5 | crawl provenance | every node carries `crawl_path`; ids namespaced by kind |

---

## COVERAGE LEDGER (updated 2026-08-19 — the build is done)

335 checks across five suites, every one tagged with its scenario id:

| Suite | Checks | Covers |
|---|---|---|
| `tests/test_incremental.py` | 20 | freshness layer (cache/seen-set/watermark — F3's cache honesty) |
| `tests/test_shopping_api.py` | 54 | B1–B5, C1–C5, C7, C2b (vertical-name guard), F (event/demographics) |
| `tests/test_shopping_traversal.py` | 33 | E2 end-to-end incl. budget, A4, D3–D5 |
| `tests/test_full_project.py` | 95 | B1–B6, C4, D1–D5, D4/D4b (§3.18 + interest matrix), E1–E4, E2b (§3.19 commerce), F1/F3/F6 (incl. cache wiring), H2/H3 |
| `tests/test_dispatch.py` | 133 | the customer-facing path: schema↔code↔docs drift (all four directions), C6, H (history caps + date provenance), I (the Date-range control, moment regions, phase labels), J (the crawl) |

**Covered live instead of offline:** A3 (the PWS-handler 403 → `malformed`) and
A5's no-rate-limit-headers fact are exercised by `probes/probe_endpoints.py` and
`probes/param_matrix.py` (37 cases) rather than unit tests — G1 runs them
before any release.

**Deliberately not yet implemented:**
- **A1/A2 as standalone units** — the envelope unwrap and error surface are
  exercised through every traversal test and every probe; standalone units add
  little until transport changes.
- **F1/F2/F4 (actor-level queue/lease/budget)** and **E-scenario live budget
  asserts** — Phase 4 (deployment) work: they test the actor under Apify
  conditions, not the traversals.
- **G2** shape-drift automation — run manually today (`probe_endpoints` then
  `inventory`); wire into CI at deploy time.

Scenario text below is kept as written — it is the contract the ledger is
audited against.

---

### Priorities for the coding agent

Build order that keeps every commit shippable:
1. GROUP A + B (transport + parsers) — pure functions over existing fixtures.
2. GROUP C (vocab/validation) — no network needed.
3. GROUP D (decision nodes) — the graph logic.
4. E1 (flagship actor), then E2, E3, E4.
5. F (product behavior) — mostly wiring existing layers.
G runs throughout.
