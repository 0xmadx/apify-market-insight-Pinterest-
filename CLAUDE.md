# CLAUDE.md — working notes for pinterest-apify

A family of **Apify actors selling enriched Pinterest Trends data**, built on a
session the operator's own Chrome holds. The actor never logs in, never stores a
credential, and never runs a browser. The defining failure mode to guard against,
inherited from this project's parent: **a plausible wrong number, not a crash**.

## Orientation

| Question | Read |
|---|---|
| How do the 18 endpoints link, what is the product | `docs/ARCHITECTURE.md` — start here |
| Exact params, limits, measured traps | `docs/07-API-REFERENCE.md` |
| Call chains + validation checklist | `docs/08-BUILD-GUIDE.md` |
| What "done" means for any code | `docs/TEST-SCENARIOS.md` (scenario ids A1…G2) |
| Real response shapes | `probes/results/*.json` + `probes/RESULTS.md` |
| Which spec drafts are superseded and why | `docs/README.md` |

**Before writing any code, invoke the `pinterest-trends-coder` skill** — it
carries the enforced rules (parser discipline, normalisation scopes,
one-vertical-per-call, the double-spelled `has_prediction`). It is not advisory.

## Running things

```bash
.venv/Scripts/python.exe -m src.status              # vault health — ALWAYS first
.venv/Scripts/python.exe -m tests.test_incremental  # 20 offline checks
.venv/Scripts/python.exe -m probes.probe_endpoints  # 16 live endpoint probes (needs vault)
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
- Not reproducible: moment Age/Gender (persisted GraphQL — use the
  moments→keywords→/demographics/ workaround, label it `derived`), merchant
  endpoints (need a catalog), `publish_state=DRAFT`.

## State of the build

**Built + tested:** session layer (vault lease, curl_cffi identity, classify) ·
freshness layer (response cache per-kind TTL, seen-set with content
fingerprints, mark-after-push ordering) · actor skeleton (`src/main.py`) ·
endpoint probe harness (16/16 OK on 2026-08-18).

**Not built (docs first, by operator decision):** the graph layer (transport
A/B as reusable module, named parsers, vocab, traversals) and the four actors —
`pinterest-keyword-research`, `pinterest-shopping-trends`,
`pinterest-seasonal-moments`, `pinterest-trend-radar`. Build order and
per-scenario acceptance live in `docs/TEST-SCENARIOS.md` §priorities.

## Working style that has paid off

- Probe the wire before theorising; one live call beats three plausible theories.
- Diff response keys against the keys the code reads — the single check that
  would have caught the parent project's biggest bug at any point.
- When a doc and the wire disagree, the wire wins — and the doc gets updated in
  the same commit.
- Write the failing test first; reference its TEST-SCENARIOS id in the name.
