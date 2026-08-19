# DEPLOY — getting this onto Apify

Everything here is ready to run; one decision is yours (§1). Nothing below has
been executed yet — this is the runbook, not a record.

---

## 1. The one blocker: a reachable Redis

The actor runs in Apify's cloud. Your vault is a Docker container on your desk.
`localhost` inside an Apify container means **that container**, so the actor
would find an empty vault and fail with `VaultEmpty` — correctly, but uselessly.

| Option | Cost | What changes |
|---|---|---|
| **Upstash Redis** (recommended) | free tier covers this | sign up, take the `rediss://` URL, point the Go cookie server at it, set the same URL as an Apify secret. Nothing in `src/` changes. |
| VPS running the vault | ~$5/mo | you already have the compose file; needs firewall + `requirepass` + TLS. More control, more upkeep. |
| Tunnel from this machine | free | fragile — the actor fails whenever your desk machine sleeps. Fine for a demo, not for a listing. |

The extension and the Go server keep working exactly as they do now; only the
Redis address moves.

## 1b. Rehearse it locally first — `./run_local.sh`

Before paying for anything, the deploy is rehearsable on this machine. WSL runs
the actor on **Linux 3.12** (the same base as `apify/actor-python:3.12`) and
reaches the vault at the **Windows host IP** — a real network hop from a
separate namespace, the same shape as an Apify container reaching Upstash.

```bash
./run_local.sh                       # radar — 2 requests
./run_local.sh keywords
./run_local.sh shopping '{"verticals":["1042"],"drillTopN":1}'
```

**Proven this way on 2026-08-19** (Ubuntu WSL, `REDIS_URL` pointed at
`172.31.144.1:6379`, never localhost):

- all 334 offline checks pass on Linux — the code had only ever run on Windows
- the actor boots the real Apify SDK, reads `INPUT.json`, and reaches a
  networked Redis
- with an empty vault it fails **exactly as designed**: `ERROR No leasable
  'pinterest' profile after 30s`, terminal status *"No usable Pinterest session
  in the vault."*, exit code 1, and **no dataset written** — the F1 contract
  (fail loudly, never emit an empty "successful" dataset) proven on the real
  SDK rather than asserted in a unit test

**Full run, 2026-08-19** — with a live session, `radar` completed on Linux:
`using profile profile_ldu6ypke8` → `pushed 11` → `exit_code: 0`, 12 files in
`storage/datasets/default/`. `shopping` with `enrichTopN=3` then verified the
last unproven endpoint (§3.19 `PinResource`), returning real prices and
merchant URLs.

So the deploy path is **rehearsed, not merely documented**. The only thing left
untested is Apify's own cloud.

## 1c. Show it to someone — `api_sim.py`

```bash
.venv/Scripts/python.exe api_sim.py     # → http://localhost:8080
```

A browser playground plus the real Apify endpoint shape
(`POST /v2/acts/pinterest-trends/run-sync-get-dataset-items`), running the same
`src/scraper.py` the actor runs. An integrator writes that exact call and only
changes the host when you go live.

Falls back to the committed fixtures when no session is in the vault, labelled
`DEMO` on the page and `"_demo": true` on every record — so the product can be
demonstrated at any time without a customer ever mistaking replayed data for
fresh data.

Verified LIVE 2026-08-19: `shopping` with `enrichTopN=2` returned Mascaras
(Beauty > Makeup > Eye makeup > Mascaras), 25 search queries, audience stamped
`OUTBOUND_CLICK`, and Amazon $14.85 / Thrive $46.80 with outbound URLs — 8
requests.

## 2. Push

```bash
npm i -g apify-cli
apify login
cd /c/Users/0xdevy/Desktop/pinterest-apify
apify push
```

## 3. Secrets — set in the Apify console, never in `actor.json`

| Variable | Value | Secret? |
|---|---|---|
| `REDIS_URL` | the `rediss://…` from §1 | **yes** — it carries the vault password |
| `VAULT_PLATFORM` | `pinterest` | no |
| `PROFILE_MAX_AGE` | `900` | no |
| `LEASE_TTL` | `900` | no |

## 4. Smoke the deployed actor

Run `radar` first — 2 requests, 11 records, the cheapest possible proof that the
cloud actor can reach the vault and Pinterest:

```json
{ "operation": "radar", "region": "US" }
```

Then `shopping` with a small cap:

```json
{ "operation": "shopping", "verticals": ["1042"], "drillTopN": 1, "maxRecords": 10 }
```

---

## What a customer must understand before buying

These are product facts, not deployment details, and they belong in the listing
description rather than being discovered mid-run.

**Runs draw on the operator's Pinterest session.** Concurrency is capped by the
number of live profiles in the vault — one beaming Chrome is one concurrent run.
The lease enforces it; a second run waits, then fails with a readable message
rather than hanging or emitting an empty dataset.

**A run needs a live session to exist at all.** If the vault is empty the actor
fails fast (`VaultEmpty` after `WAIT_TIMEOUT`) instead of returning zero rows —
deliberate, because an empty dataset reads as "Pinterest has nothing", which
would be a lie.

**Data lags ~4 days.** Every record carries `_meta.end_date`, which is
Pinterest's settled date, not the run date. Customers comparing to "today" will
otherwise think the data is stale when it is simply how Pinterest publishes.

**No absolute volumes exist anywhere.** Every count is peak-normalised within
its own response, and `_meta.normalization_scope` names the scope. Two records
with different scopes are not comparable; the field makes that checkable rather
than a footnote.

**Price/merchant enrichment is opt-in and costs 1 request per pin.**
`enrichTopN` defaults to 0. A category returns up to 33 products, so enriching
everything across seven verticals is hundreds of requests on a shared session.

---

## Cost per run, measured

**Zero-input runs** — a customer who presses Run with nothing filled in:

| Operation | Requests | Records | What they get |
|---|---|---|---|
| `radar` | **2** | 11 | Pinterest's curated spotlight + Editors' Picks |
| `keywords` | **14** | 10 | the growing keywords, enriched with forecast + audience |
| `moments` | **16** | 13 | every seasonal moment, phases + curves for the live ones |
| `shopping` | **38** | 57 | trending categories across the 3 UI-visible verticals |

No operation requires input. `shopping` defaults to the three verticals
Pinterest's own UI exposes rather than all seven — all-seven with the default
drill depth is ~86 requests before the customer has expressed any preference,
on a session every customer shares.

**With customer input:**

| Operation | Requests | Records |
|---|---|---|
| `radar` | **2** | 11 |
| `shopping`, 1 vertical, drill 2 | **6** | 19 |
| `shopping`, 3 verticals, drill 2 | 10 | 34 |
| `keywords`, 10 terms, no related | **4** | 10 |
| `keywords`, 10 terms + related | 14 | 10 |
| `moments`, weekly, 2 drilled | ~17 | 13 |
| `+ enrichTopN=5` | +5 per drilled category | — |

Batching is why these are low: `metrics` and `demographics` take arrays, so N
categories or N keywords cost one call each, not N. `top_products`,
`related_terms` and `PinResource` have no batch form and are the only per-item
costs in the system.

---

## Before every release

```bash
.venv/Scripts/python.exe -m src.status              # vault green
.venv/Scripts/python.exe -m probes.probe_endpoints  # 16/16 must be OK
.venv/Scripts/python.exe -m probes.coverage         # 0 unread leaves
.venv/Scripts/python.exe -m probes.history_caps     # endDate windows still hold
.venv/Scripts/python.exe -m tests.test_incremental
.venv/Scripts/python.exe -m tests.test_shopping_api
.venv/Scripts/python.exe -m tests.test_shopping_traversal
.venv/Scripts/python.exe -m tests.test_full_project
.venv/Scripts/python.exe -m tests.test_dispatch     # the customer-facing path
```

334 checks total. `test_dispatch` also guards the docs: it fails if
`.actor/input_schema.json` and `docs/API.md` disagree in either direction, or
if any operation emits a field its output table does not document.

The probe suite is the contract this API does not have. An endpoint dropping to
FAIL is stop-ship until the docs and parsers are reconciled with the new wire
truth — see `docs/TEST-SCENARIOS.md` group G.

**Run it in this order.** `probe_endpoints` rewrites `probes/results/*.json`,
which the test suites read as fixtures — so the tests must run *after* it, on
the data it just fetched. That is the point: it is the only step that checks
the suites against today's wire rather than a stored copy. Tests therefore must
never assert a count Pinterest owns (how many categories a vertical holds, how
many products a category has); assert the invariant instead, or the release
sequence fails itself. Two assertions did exactly that and were fixed on
2026-08-19.

`probes.history_caps` is the other measurement that cannot be a unit test: past
its window each endpoint answers **HTTP 200 with an empty list**, so a cap that
has drifted too permissive is invisible in every other check. It reports drift
in the dangerous direction explicitly.

**One standing maintenance item:** the moment-demographics `queryHash` in
`src/vocab.py` is a persisted-query hash and rotates when Pinterest redeploys.
When it does, `StaleQueryHash` fires, moments degrade to the `derived` audience,
and the fix is a re-capture — the procedure is in `probes/captures/README.md`.
Never guess a hash.
