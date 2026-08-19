---
name: pinterest-trends-coder
description: Use when writing, extending, or debugging ANY code in the pinterest-apify repo — transport, parsers, traversals, actors, or tests. Enforces the endpoint-graph rules (normalisation scope, one-vertical-per-call, lowercase keywords, end_date bootstrap), the parser discipline that prevents silent-None reads, and the session-layer boundary. Trigger on any implementation task, "build the actor", "add an endpoint", "fix the parser", or investigating wrong/missing data.
---

# Pinterest Trends coder

You are coding against a **reverse-engineered API with no contract**. Everything
known about it was measured on the wire and written down. Your job is to keep
code and wire-truth identical — and the defining failure mode you are guarding
against is **a plausible wrong number, not a crash**.

## Read first, in this order (non-negotiable)

1. `docs/ARCHITECTURE.md` — the endpoint graph, edges, decision nodes, actors.
2. `docs/07-API-REFERENCE.md` — every param, limit, and measured trap.
3. `docs/TEST-SCENARIOS.md` — the definition of done for what you're building.
4. `probes/results/*.json` — real response shapes. Your parser's ground truth.

## The five rules that already cost someone weeks

1. **Never index a raw response key inline.** Every endpoint gets ONE named
   parser; all consumers go through it. Before shipping a parser, diff the keys
   it reads against the keys the probe fixture actually contains (scenario B2).
   The parent project fetched correct data and read `None` out of it for its
   entire life because of camelCase-vs-snake_case drift.
2. **Normalise the double spelling.** `/metrics/` returns BOTH `has_prediction`
   and `hasPrediction` (same value, verified live). `/related_terms/` returns
   only `hasPrediction`. Emit one canonical field from the parser; treat
   both-absent as `None`, never `False`.
3. **Absent is not zero.** Unreturned field → `null` + `_meta.basis`. Empty-200
   is a real answer with a cause: wrong param (`top_products` event≠
   OUTBOUND_CLICK), wrong level (L2 id to `top/`), unsupported region
   (editorial outside US/CA/GB+IE). Label the cause; never emit a bare `[]`.
4. **Counts are relative to their response.** Never compare `normalizedCount` /
   `percent_relative_volume` / `normal_counts` across two responses. Comparing
   terms → one `/metrics/` call with `normalize_against_group=true`. Comparing
   categories → same vertical, same call. **One vertical per `top/` call,
   always.** Cross-scope comparisons use only the percentage fields. Stamp
   every relative number with its scope id (scenario D5).
5. **Validate before the wire.** Builders refuse — with the measured reason —
   anything the API is known to reject or silently mis-serve: uppercase
   keywords, `aggregation≠2`, `predicted_days>91`, `days>730`, limits over
   ceiling, `shouldMock=true`, invalid preset, non-L1 verticals, per-region
   moment slugs, region-capability violations.

## Wire discipline

- `end_date` comes from `/latest_available_date/` at bootstrap. `today()` in a
  request builder is a bug (future dates 400; data lags ~4 days).
- Header `X-Pinterest-PWS-Handler: trends/index.js` on every request — exact
  string. Its absence produces 403 "Invalid Resource Request", which
  `src/session.py:classify()` reports as **`malformed`**: that means YOUR
  REQUEST IS WRONG. Never evict/mark a profile over it, and never "fix" it by
  re-logging in.
- 429s carry no rate-limit headers — back off blindly (exponential). Nothing in
  this codebase has ever handled a real 429 wrongly yet; keep it that way.
- Match multi-term responses **by term**, never by index — the API silently
  drops no-data terms.
- Style A payload lives at `resource_response.data`; errors at
  `resource_response.error.message_detail`. Drop `client_context` (account PII)
  before anything is stored or logged.

## Layer boundaries

- `src/vault.py`, `src/session.py`, the Go server, the Chrome extension: **use,
  never modify** without explicit operator instruction. No Playwright, no
  headless browsers, ever — the operator's real browser holds the session; the
  code only replays it (curl_cffi, Chrome-impersonated TLS).
- The freshness layer (`src/state.py`, `src/cache.py`, `src/context.py`) is
  built and tested — plug into `ctx.get()` / `ctx.seen()` / `Record`; don't
  reinvent it. Records are marked seen only AFTER the dataset push (main.py owns
  this; keep it there).
- New scraping logic yields `Record(scope, id, data, fields)` — pick `fields`
  as the metric keys so changed numbers re-emit but reordered noise doesn't.

## When the doc and the wire disagree

The wire wins — but a *changed* wire is news, not noise: update the doc in the
same commit, cite the probe evidence, and re-run `probes/probe_endpoints.py`
(16/16 must be OK before any release; see TEST-SCENARIOS group G). If you need a
fact no doc records, probe it live through a leased session (vault permitting,
`python -m src.status` first) rather than reasoning about it — one live call has
repeatedly beaten three plausible theories.

## Tests

Write the offline test against a probe fixture BEFORE the code it proves.
Follow `tests/test_incremental.py` style: plain check() output, throwaway Redis
namespace, real assertions on real shapes. Live tests: ≤2 requests each, skip
cleanly when the vault has no usable profile. Every scenario you implement is
one from `docs/TEST-SCENARIOS.md` — reference its id (B1, D3, E2…) in the test
name so coverage is auditable.
