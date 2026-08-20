# CLAUDE.md — working notes for pinterest-apify

A family of **Apify actors selling enriched Pinterest Trends data**, built on a
session the operator's own Chrome holds. The actor never logs in, never stores a
credential, and never runs a browser. The defining failure mode to guard against,
inherited from this project's parent: **a plausible wrong number, not a crash**.

## Orientation

| Question | Read |
|---|---|
| What do I build first, and in what order | `docs/BUILD-PLAN.md` — **Phase 0 (run + mine all endpoints) precedes any code** |
| How do the 20 endpoints link, what is the product | `docs/ARCHITECTURE.md` |
| Exact params, limits, measured traps | `docs/wire/07-API-REFERENCE.md` |
| Call chains + validation checklist | `docs/wire/08-BUILD-GUIDE.md` |
| What "done" means for any code | `docs/TEST-SCENARIOS.md` (scenario ids A1…G2) |
| Real response shapes | `probes/results/*.json` + `probes/RESULTS.md` |
| Which spec drafts are superseded and why | `docs/README.md` |

**Before writing any code, invoke the `pinterest-trends-coder` skill** — it
carries the enforced rules (parser discipline, normalisation scopes,
one-vertical-per-call, the double-spelled `has_prediction`). It is not advisory.

## Running things

```bash
.venv/Scripts/python.exe -m src.status              # vault health — ALWAYS first
.venv/Scripts/python.exe -m probes.probe_endpoints  # 16 live endpoint probes (needs vault)
.venv/Scripts/python.exe -m probes.coverage         # fields the parsers DON'T surface
.venv/Scripts/python.exe -m tests.test_incremental        # 20
.venv/Scripts/python.exe -m tests.test_shopping_api       # 54
.venv/Scripts/python.exe -m tests.test_shopping_traversal # 33
.venv/Scripts/python.exe -m tests.test_full_project       # 95
.venv/Scripts/python.exe -m tests.test_dispatch           # 44 — zero-input + input plumbing
```

Everything is a module run from the repo root. The venv is local to this repo.

## Sessions — the part that must not be broken

- Cookies come from a **Redis vault** filled by the operator's Chrome extension
  (via the Go cookie server in the *parent* Etsy repo). This repo only READS the
  vault. `src/vault.py`, `src/session.py`: use, never extend, without the
  operator saying so.
- **No Playwright, no headless browsers, ever.** Transport is `curl_cffi`
  impersonating Chrome (TLS/JA3). The real browser earns the session; the code
  replays it.
- The extension only beacons **while a Pinterest tab is open**. Profiles go
  stale after 15 min without one — `src.status` reporting 0/2 usable usually
  means "no tab open", not an outage.
- `classify()` verdicts: `malformed` = our request is wrong (missing
  PWS-handler header — never evict a profile over it) · `auth_expired` = fix is
  in Chrome · `rate_limited` = back off blindly (this API has NO rate-limit
  headers) · `blocked` = bot check fired.
- One leased profile per run (SET NX + TTL). Operator sessions serve every
  customer → concurrency = number of live profiles; an empty vault must fail
  loudly within `WAIT_TIMEOUT`, never hang, never emit an empty "successful"
  dataset.

## Hard-won wire facts (each cost a probe to learn)

- `/latest_available_date/` first; its date feeds every `end_date`. `today()`
  → 400. Data lags ~4 days.
- Header `X-Pinterest-PWS-Handler: trends/index.js` exact-match on every call.
- `/metrics/` returns **both** `has_prediction` and `hasPrediction`; parsers
  normalise to one name, both-absent → `None` never `False`.
- All counts are peak-normalised **within their response** — no absolute
  volumes exist anywhere. One vertical per `top/` call; group-normalise
  multi-term metrics; compare across scopes only via percentage fields.
- Silent empties: uppercase keyword → `[]` · L2/L3 id to `top/` → 0 rows ·
  `top_products` event≠OUTBOUND_CLICK → `[]` · editorial outside US/CA/GB+IE →
  0 items. Each has a distinct cause label in output; never a bare `[]`.
- `shouldMock=true` returns fake 2019 data with HTTP 200. Always false.
- Ceilings (400 past them): terms 100 · top/ limit 522 · predicted_days 91 ·
  days 730 · interest_limit 24 · aggregation=2 only (except moment/metrics:
  the API's ONLY daily-granularity endpoint).
- Multi-term responses silently drop no-data terms — match by term, not index.
- **History reaches ~365 days and no further** — measured, and it corrects doc
  #7 §3.12's "far-past" claim. Past that, discovery returns **200 + an empty
  list**, not a 400: a silent empty that reads as "nothing was trending".
  `vocab.history_date()` refuses it with the reason.
- **Pinterest snaps `endDate` to its own week boundary** and reports the snapped
  value: asked 2026-02-15 → answered 2026-02-13. Records carry `_meta.end_date`
  (what Pinterest used) and `_meta.end_date_requested` (what was asked).
- **32 regions**, not the 10 on the first line of §4.1's wrapped code block.
  `top_products`/`editorial` = US/CA/GB+IE only; **JP and IN have 0 moments**.
- **Moment slugs are region-specific** (25 globally, 13 US) and must be
  lowercase with apostrophes stripped — `Father's Day` → `fathers day`, wrong
  form is a 400. `vocab.moment_slug()` does it; never pass a raw name.
- **`ageBuckets` 18-24 maps to TWO codes (`2,3`)** — 7 UI options send 8 codes.
  Sending only `2` narrows the band silently, with no error.
- `lookbackWindow` and `rankingMethod` are INERT **on keyword discovery** — never
  sent there. `ranking_method` on shopping `top/` is a REAL param (GROWTH /
  HIGH_VOLUME / VIRAL); the UI only ever sends GROWTH.
- Shopping `top/`+`metrics/` take `age_bucket`/`gender` in the **enum** form
  (`AGE_25_34`/`FEMALE`) while keyword discovery takes numeric codes for the
  same bands — one customer input, two wire schemes (scenario C6).
- **7 verticals carry trend data, not 3** — settled on the wire 2026-08-19, all
  seven counts exact (19/9/6/3/2/2/1). Doc #5's table says 3 and is WRONG; #7
  §4.3 is right. The four extra (DIY, Arts & entertainment, Wedding, Media) are
  hidden from Pinterest's UI — exactly what a UI-reading competitor cannot see.
- Moment Age/Gender IS reachable (§3.18, captured): a persisted GraphQL POST
  with `queryHash` + `X-Pinterest-GraphQL-Name`, handler `trends/moments/
  [momentId].js` — page-specific, NOT the global `trends/index.js`. The hash
  rotates on Pinterest deploys → `StaleQueryHash`, then re-capture. Same query
  does moment × interest via `terms:"<moment>:<id>"` + `category:
  "MOMENT_INTEREST"` — both move together or you get a silent `items:[]`.
- Price/outbound URL live only on `www.pinterest.com` (§3.19 `PinResource`,
  `field_set_key: auth_web_main_pin`, handler `www/pin/[id].js`). 1 request per
  pin, no batch form.
- Still not reproducible: merchant endpoints (need a catalog),
  `publish_state=DRAFT` (permission-gated).

## State of the build

**Built + tested:** session layer (vault lease, curl_cffi identity, classify) ·
freshness layer (response cache per-kind TTL, seen-set with content
fingerprints, mark-after-push ordering) · actor skeleton (`src/main.py`) ·
endpoint probe harness (16/16 OK on 2026-08-18).

## Shipping a change

Deployment goes **one direction only** — this repo is the source of truth, the
deployed actor is a snapshot of it, and there is no editing on Apify:

```
edit locally  →  release gate  →  apify push  →  smoke the cloud run
```

`.actor/actor.json` carries `buildTag: latest`, so **a push immediately changes
what existing customers get**. Additive changes (new field, new optional input,
bug fix) are safe on `latest`; a renamed field, changed default or removed
operation is a `version` bump first. The test: *could a customer's existing code
break if this landed silently tonight?*

The gate is `docs/DEPLOY.md` § Before every release — and run it in that order,
because `probe_endpoints` rewrites the fixtures the suites then read. Full
reasoning in `docs/DEPLOY.md` §5.

---

**Built + verified live:** the graph layer (`transport.py`, `vocab.py`,
`parsers.py`) and five traversals as one actor with an `operation` input —
`shopping`, `keywords`, `moments`, `radar`, and `crawl`, which follows the
links between them instead of stopping at one page. Both of Pinterest's time
controls are wired: `endDate` (which date) and `dateRange` (how much history).
465 checks, 0 unread response fields (`probes/coverage.py`).

**Both browser captures landed 2026-08-19:**
- §3.18 moment Age/Gender via the persisted GraphQL query — audience is now
  `measured`, not derived. The SAME query answers **moment × interest**, and
  Pinterest's ~7-per-moment dropdown is UI curation, not a data limit.
- §3.19 `PinResource` on `www.pinterest.com` — price + outbound merchant URL.
  ✅ Verified live: Mascaras → Amazon $14.85 / Thrive $46.80 / Target $8.00
  with outbound URLs. `enrichTopN` caps it; unenriched products keep
  `price: None` ("not fetched"), never 0.

**Verified end-to-end 2026-08-19** via `./run_local.sh` — the real Apify SDK
on Linux 3.12 against a NON-localhost Redis (the Upstash shape), exit 0,
records on disk. Nothing in the project is unverified any more.

**Remaining:** only the Apify cloud itself, which needs a network-reachable
Redis (`docs/DEPLOY.md`). The operator is running local for now.

## Working style that has paid off

- Probe the wire before theorising; one live call beats three plausible theories.
- Diff response keys against the keys the code reads — the single check that
  would have caught the parent project's biggest bug at any point.
- When a doc and the wire disagree, the wire wins — and the doc gets updated in
  the same commit.
- Write the failing test first; reference its TEST-SCENARIOS id in the name.
