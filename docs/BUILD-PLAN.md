# BUILD PLAN — the ordered work, for the coding agent

> **Phase 0 is not optional and comes before any code.** The docs in this repo
> are a map of the wire as it was measured; the wire is the territory. Your
> first act is to re-measure it, mine what came back field by field, and update
> the map — because the responses contain data the docs under-describe, and the
> operator's product ideas depend on what is actually in them.

The verification loop, which every phase repeats:

```
run endpoints (structured) → capture full responses → inventory every field
      → diff against the docs → wire wins → update docs IN THE SAME COMMIT
      → only then build on it
```

---

## PHASE 0 — Measure, mine, reconcile (no product code yet)

### 0.1 Run the sweep
```bash
.venv/Scripts/python.exe -m src.status              # vault must be green first
.venv/Scripts/python.exe -m probes.probe_endpoints  # 16 endpoints, ~30s, 1 profile
.venv/Scripts/python.exe -m probes.inventory        # every field of every response
.venv/Scripts/python.exe -m probes.coverage         # what the parsers fail to surface
```
`probe_endpoints` writes full responses to `probes/results/*.json` and a verdict
table to `probes/RESULTS.md`. `inventory` enumerates **every path** in those
responses — the docs describe what the UI uses; the inventory shows what the
wire returns. The gap between them is where value hides.

Anything not OK → stop, reconcile the doc, re-run. 16/16 is the entry ticket to
Phase 1.

### 0.2 Findings already mined from the 2026-08-18 capture (verify, then chase)

These came out of reading the inventory against the docs. Each is either a
**fact** (in the responses now, under-documented) or a **hypothesis** (H#) with
the probe that settles it. Chasing the hypotheses IS Phase 0 work — budget ~10
live requests total.

**F1 — `pin_id` is the bridge out of the Trends walled garden. Data CONFIRMED,
read-endpoint STILL OPEN (corrected 2026-08-19).** `top_products[].pin_id`,
`topics[].pins[].id`, `editorial[].pins[].id` are real pin ids. The trends
responses themselves contain no merchant URLs (only `i.pinimg.com` images), but
the pin's page on `www.pinterest.com` does: manually inspected pin
`4607745477126792832` (the #1 top product from our `3.10` probe) and found
merchant `Oriental Trading` (`www.orientaltrading.com`), product title, price
`$380.99`, rating, a shipping note, and a "Visit site" outbound link. Full
detail in [07-API-REFERENCE.md §3.19](07-API-REFERENCE.md).

> ⚠️ **Correction to an earlier version of this section.** It previously said
> the outbound URL "passes through a `/v3/offsite/` call" — true but
> misleading. `/v3/offsite/` takes the merchant `url` as an **input** param
> (`data.url`); it is a click-tracking/validation hop fired on interaction, not
> a lookup fired on load, and it is **confirmed NOT to be the endpoint that
> returns** merchant_name/price/host. Do not build against it expecting data
> back — it was checked and ruled out, not merely undocumented.

**Not yet captured: which call actually returns the data.** The fields are
confirmed to exist on the rendered page; the specific `ApiResource`/resource
call that returns them (likely a pin-detail GET, analogous to how
`top_products` wraps its own payload) has not been captured. **Action before
building the shopping actor's outbound-link field:** on
`www.pinterest.com/pin/{pin_id}/`, DevTools → Network → filter `resource/` →
find the response containing `merchant`/`price`/host fields → Copy as cURL or
export HAR, save under `probes/captures/`, then replay it through a leased
session to confirm it works outside a live browser tab before it becomes code.
Until that replay succeeds, treat the shape as known but the endpoint as
unverified server-side.

**F2 — `merchant_name` is competitive intel the docs shrug at.**
33 top products for one category each carry the selling merchant's name. Grouped,
that answers "who is winning outbound clicks in this category" — a customer-
facing field. Cost: zero extra requests. Add to the shopping actor's record.

**H2 — SETTLED 2026-08-19.** `top_trends_filtered`'s `{value, index}` growth
pairs: `index` is the 1..N **rank of the value within this response** (100 rows
→ indexes 1..100, one each, monotone with value). Not a strength score, fully
derivable by sorting, response-scoped. Recorded in doc #7 §3.12; raw evidence in
`probes/results/params/H2-top_trends_100rows.json`.

**F3 — editorial items carry campaign windows.** `start_date` (e.g. 2026-08-01),
`end_date`, `hide_keyword_percentages`, `is_ready_for_translation` — the docs
list them but the product ignores them. `start_date` tells the trend-radar actor
when an editorial push began — emit it.

**F4 — spotlight `time_series` has forecast fields, unpopulated in our sample.**
`normalized_count`, `normalized_predicted_lower/upper_bound` all null for all 5
trends captured. Absent-is-not-zero applies: parser keeps them nullable.
**H3:** they populate for *some* trends (the schema exists for a reason). Cheap
check on each future probe run; never assume always-null.

**F5 — confirmed dead ends (do not re-litigate):** `affinity` null in 10/10 ·
every `summary.*.total` = 0 (no absolute volumes anywhere) ·
`moment_interests.<id>.peaks` = `[]` · `3.8 growth_rates` can be all-null when
the window is short — a category with null growth is *unmeasured*, not flat.

**F6 — `pins[].color`** (dominant-color hex) exists on spotlight/editorial pins.
Trivial, but free: creative-palette signal, keep in the record.

### 0.3 Exit criteria for Phase 0
- [ ] probe 16/16 OK on a fresh run
- [ ] H1, H2 probed; results written into `docs/` (new file or section, cited
      with the probe date and raw JSON saved under `probes/results/`)
- [ ] any doc/wire mismatch fixed in the same commit that discovered it
- [ ] `probes/field_inventory.txt` regenerated and committed (it is the
      parser-coverage baseline for scenario B2)

---

## PHASE 1 — Graph layer ✅ BUILT (transport.py, vocab.py, parsers.py — all 16 endpoints parsed)

Order within the phase; each item cites its TEST-SCENARIOS group:

1. `src/transport.py` — style A/B + envelope unwrap + typed errors + blind
   backoff. Scenarios **A1–A5**. (Lift the working logic from
   `probes/probe_endpoints.py`; it is already wire-proven.)
2. `src/vocab.py` — regions, 24 interest ids, 7 working verticals, moment slugs
   *per region* (fetched, cached), the **two** age/gender schemes, ceilings.
   Scenarios **C1–C6** (builders refuse before the wire, citing the measured
   reason).
3. `src/parsers.py` — ONE named parser per endpoint. Every parser has a B2
   key-coverage test against the committed fixture: every key it reads must
   exist; keys it ignores are printed. The double-spelled `has_prediction`
   normalised here (**B1**). Absent → `None` + basis (**B3**). Parallel-array
   zip (**B4**), match-by-term (**B5**), per-region keywords (**B6**).

Definition of done: groups A, B, C green offline; no network in CI.

## PHASE 2 — Decision nodes and traversals ✅ BUILT (crystal-ball D1, phase gate D2, tri-state D3, derived-label D4, scope stamps D5)

The graph logic from ARCHITECTURE §2.3, as testable planners (they emit a
*planned request list* first — that is what D-tests assert on):

- crystal-ball branch (**D1**) · moment phase gate (**D2**) · empty-200
  tri-state labelling (**D3**) · moment-demographics workaround, `derived`
  labelled (**D4**) · normalisation scope stamps (**D5**).

## PHASE 3 — The traversals ✅ BUILT as one actor with 4 operations (operator may still split into separate Apify listings; the traversals are the shared core either way)

Build order = revenue order: **E1 keyword-research** (flagship) → **E2
shopping-trends** (now including F2 merchant intel + H1 outbound links if
proven) → **E3 seasonal-moments** → **E4 trend-radar**. Each actor: fixtures
first, then ONE budgeted live run (per-scenario request caps are written in
TEST-SCENARIOS group E). Records flow through the existing `Record`/`ctx`
freshness layer — do not reinvent it.

## PHASE 4 — Product behavior + deploy

Shared-vault behavior (**F1–F4**): polite queue, lease exclusivity at the actor
level, shared honest cache, planned-request budget caps. Then per-actor
`.actor/` packaging and `apify push` (needs the network-reachable Redis the
root README documents — Upstash; not built yet, operator decision pending).

## CONTINUOUS — the drift canary

`probes/probe_endpoints.py` before every release (**G1**); re-run B2 coverage
against the *fresh* probe JSONs, not just committed fixtures (**G2**). New keys
in fresh responses are logged as opportunities — that is Phase 0.2 happening
again, forever. This API has no contract; the probe suite is the contract we
maintain ourselves.

---

## Standing orders (from the operator, via the skill)

- Wire beats doc; a changed wire is news — update the doc in the discovering
  commit.
- A plausible wrong number is worse than a crash. Refuse/label rather than guess.
- Session layer: use, never modify. No browsers, ever.
- Every test cites its TEST-SCENARIOS id. Every live probe saves its raw JSON.
