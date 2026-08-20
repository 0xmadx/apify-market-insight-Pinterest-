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
| [`_captures/`](_captures/) | The operator's **raw research drops**, dated. Source material for `wire/`, kept unedited so a claim can be traced back to what arrived. |
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
