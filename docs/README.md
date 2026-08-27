# Docs — start here

Four audiences, four doors. Pick the one that matches why you opened this.

| You are… | Read | |
|---|---|---|
| **evaluating this** — does it do what I need? | run `python api_sim.py` → http://localhost:8080 | click it, no reading |
| **using it** — I bought it, now what? | [CUSTOMER-GUIDE.md](CUSTOMER-GUIDE.md) | the four questions, worked examples, how to read the numbers honestly |
| **integrating it** — what can I send, what comes back? | [API.md](API.md) | every input, every output field, errors, cost |
| **building on it** — I have to change this code | [ARCHITECTURE.md](ARCHITECTURE.md) → [wire/](wire/) | the graph first, then the endpoint reference |
| **shipping it** — get it onto Apify | [DEPLOY.md](DEPLOY.md) | one open decision, then a runbook |

---

## The whole set

### Product — what a customer touches

| File | |
|---|---|
| [CUSTOMER-GUIDE.md](CUSTOMER-GUIDE.md) | The tutorial. Five operations in plain language, worked scenarios for each, the two time controls (`endDate` vs `dateRange`), and the section that matters most: **reading the numbers honestly** — why there are no absolute volumes, why `null` is not zero, why the data is 4 days behind, and why audience shifts by action. |
| [API.md](API.md) | The reference. One endpoint, 45 inputs, full output field tables per operation, errors, measured cost. `tests/test_dispatch.py` asserts this page against the schema and against the records actually emitted, in both directions — so it cannot drift from the code. |
| `../api_sim.py` | A local Apify-shaped API + browser playground. Runs the real traversals; falls back to committed fixtures when no session is live, labelled `DEMO`. |

### Build — how it works and how to change it

| File | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **The one to read first.** The endpoint graph — 5 datasets funnelling into one keyword pipeline — the edges (which response field feeds which request param), the decision nodes, §2.2b on the `crawl` that finally walks those edges, and the product built on top. |
| [TEST-SCENARIOS.md](TEST-SCENARIOS.md) | What "done" means. Scenario ids `A1…G2`, every expected value measured rather than invented, plus a coverage ledger of what is deliberately not implemented. |
| [BUILD-PLAN.md](BUILD-PLAN.md) | The phases, and the Phase-0 findings that shaped everything: what the wire returns that the docs under-describe. |
| [DEPLOY.md](DEPLOY.md) | Rehearse locally (`../run_local.sh`), then push. The one open decision is a network-reachable Redis. |
| [LAUNCH-READINESS.md](LAUNCH-READINESS.md) | **Is it shippable?** The 2026-08-20 review: four defects found (one a blocker that returned empty datasets to paying customers), what is still open, and the measured capacity behind the answer. |
| [VAULT_SEPARATION.md](VAULT_SEPARATION.md) | **The standing rule: this lab never reaches into the Etsy project.** Who owns which Redis, container and writer; the three dormant couplings cut on 2026-08-26 and why each would have failed silently; why `cookie:pinterest:*` keeps reappearing in Etsy's Redis and why that is theirs to fix, not ours to keep deleting. |
| [SESSION_SOURCES.md](SESSION_SOURCES.md) | which of the three session-source options (extension / AdsPower / stealth-browsers) is live, deferred, or ruled out — and what a fourth (Donut Browser) would need to prove |
| [CAPACITY.md](CAPACITY.md) | **What actually breaks, measured.** Sustained load is ~5% of capacity; bursts are the only failure. Reproduce any of it with `python -m probes.stress`. |
| [SCALING-DESIGN.md](SCALING-DESIGN.md) | **10 profiles serving 100 clients.** The cache stampede is the real ceiling, not the profile count — measured 8 clients, 8 identical fetches. Singleflight, lazy leasing, async jobs, fair share, with what ScraperAPI and Bright Data actually do. |
| [SCALING.md](SCALING.md) | **What breaks first, and at what number.** Measured: concurrent capacity equals profile count. The seven levels in the order they fail, and the fix order — lazy leasing before buying accounts. |

### Wire — the reverse-engineering corpus

[`wire/`](wire/) — research notes on Pinterest's own endpoints. Source material,
not product docs. Start at [wire/07-API-REFERENCE.md](wire/07-API-REFERENCE.md)
(every endpoint, param and measured trap) and
[wire/08-BUILD-GUIDE.md](wire/08-BUILD-GUIDE.md) (call chains); the numbered
`01`–`06` files are organised by Pinterest UI page.
[wire/README.md](wire/README.md) explains which drafts were superseded and why.

### Two folders named "captures" — they hold different things

| Folder | What |
|---|---|
| `../probes/captures/` | **Browser-interception evidence** — the two things no API probe could reach: the persisted GraphQL query behind moment demographics, and the pin-page call carrying price and the outbound merchant URL. Both are implemented and verified; this folder holds the tooling and the re-capture procedure for when Pinterest rotates the query hash. |

---

## The rule this project runs on

**The wire wins.** These documents are a snapshot of a reverse-engineered API
with no contract. When a doc and Pinterest disagree, Pinterest is right and the
doc gets fixed in the same commit that found it — with the probe evidence cited.

`probes/probe_endpoints.py` re-checks all 16 endpoints before any release,
`probes/coverage.py` reports any response field no parser surfaces, and
`probes/history_caps.py` re-measures how far back each `endDate` reaches. All
three are in the [release checklist](DEPLOY.md#before-every-release).

`history_caps` exists because that particular failure is invisible to a test:
past its window an endpoint answers **HTTP 200 with an empty list**, so a cap
that has drifted too permissive returns a confident "nothing was trending".

The docs are checked too. `tests/test_dispatch.py` fails the build if
[API.md](API.md) and the schema disagree in either direction, or if any
operation emits a field its output table does not document.

### Where the raw research drops went

`docs/_captures/` held the operator's dated doc drops, kept unedited beside the
consolidated `wire/` set. Removed 2026-08-27 — 11 files, 6,607 lines, about a
fifth of the repo:

- **9 markdown files.** 4 byte-identical to their `wire/` counterpart, 5 older
  versions of the same documents. Its own README said so: *"the other 8 files in
  this drop were byte-identical or near-identical to what was already canonical
  — nothing else to merge."*
- **1 taxonomy JSON** (2,387 lines). All 383 categories in it are present in
  `probes/results/3.6-shopping_product_categories.json`, checked id by id with
  zero missing — and that fixture is rewritten by every `probe_endpoints` run,
  so it is the fresher copy.
- its README.

The folder existed so a claim could be traced back to what arrived, which is the
job git history already does — and its own README pointed at `git log` for the
actual diff.

Nothing was lost. Recover any of it from the last commit that carried it:

```bash
git show aca8e97:docs/_captures/2026-08-19-07APIREFERENCE.md
git checkout aca8e97 -- docs/_captures/          # or restore the lot
```
