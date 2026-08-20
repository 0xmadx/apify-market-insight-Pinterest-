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

- all 373 offline checks pass on Linux — the code had only ever run on Windows
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

## 5. Updating it later — the standing loop

**Deployment goes one direction only.** There is no editing on Apify. This repo
is the source of truth; the deployed actor is a *snapshot* of it. Every change,
forever, is the same loop:

```
edit locally  →  release gate  →  apify push  →  smoke the cloud run
```

```bash
apify push        # uploads local source, Apify rebuilds the image
```

**Never push without running the gate below** (§ Before every release). It is
the only thing between a local edit and every customer's next run — and because
of the build tag, that next run may be minutes away.

### The build tag is the part that bites

`.actor/actor.json` carries `"buildTag": "latest"`. That means **every push
immediately changes what existing customers get**: a saved task pointing at
`latest` picks up the new build on its next run, with no action from them and
no notice.

| Change | What to do |
|---|---|
| Bug fix, new output field, new optional input | push to `latest` — additive, nothing breaks |
| **Renamed or removed field, changed default, removed operation** | bump `version` FIRST so customers on the old version keep working |

Additive is the common case and is safe. The test to apply: *could a customer's
existing code break if this landed silently tonight?* If yes, it is a version
bump, not a push.

Currently at `version: 1.0`.

### What never travels with the code

`.dockerignore` excludes `.env`, so **`REDIS_URL` is not in the image** —
verified by listing the built image, not by reading the file. It exists only as
an Apify secret (§3). The deployed actor and a local run read the same vault,
but the credential reaches them by different paths, on purpose.

`docs/`, `probes/` and `tests/` are excluded too. The image holds `src/`,
`.actor/`, `requirements.txt` and the Dockerfile — nothing else.

### The one thing that differs in the cloud

A local run reaches Redis at `localhost:6379`; a Docker run reaches it at
`host.docker.internal`. **An Apify container can reach neither.** That is the §1
blocker, and it is the only reason a cloud run can fail while every local check
passes. If a deployed run hangs or reports an empty vault, suspect `REDIS_URL`
before suspecting the code.

### After any wire-affecting change

This sits on a reverse-engineered API. If a push changes parsers, `vocab.py`
ceilings, or anything under `src/transport.py`, re-run the probes as well as the
tests — the suites read stored fixtures, and only the probes ask Pinterest.

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

## The image, rehearsed (2026-08-19)

The real `apify/actor-python:3.12` image was built and run against the vault
before shipping — the step that closes the gap between "works on my machine"
and "works in the cloud".

```bash
docker build -t pinterest-actor .
docker run --rm   -e REDIS_URL="redis://host.docker.internal:6379/0"   -e VAULT_PLATFORM=pinterest -e VAULT_WAIT_TIMEOUT=30   -v "<abs-path>/_actorstore:/usr/src/app/storage"   --add-host=host.docker.internal:host-gateway   pinterest-actor
```

Put the input at `_actorstore/key_value_stores/default/INPUT.json`. Results:
Python 3.12.13 on Linux, vault reached over a real network hop, `radar` 11
records and `crawl` 13 records, exit 0 both times.

**Two things this caught that nothing else could.**

`.dockerignore` correctly excludes `.env` — verified by listing the built
image, not by reading the file. But it did **not** exclude `docs/`, `probes/`
and `tests/`: 2.4MB of dev material was shipping inside the product, including
`docs/wire/` and `probes/results/` — the entire reverse-engineering corpus.
`src/` imports none of it (checked), so it is now excluded. The image holds
`src/`, `.actor/`, `requirements.txt` and the Dockerfile, and nothing else.

Input reaching a containerised actor is worth testing explicitly rather than
assuming. A first attempt logged `task={}` and ran the DEFAULT operation — the
mount had silently failed (Git Bash rewrites `/tmp/...` into a Windows path
Docker never sees). The actor was right; the harness was wrong. But that is
exactly what a mis-set input looks like in production: a successful run of the
wrong thing, with `task={}` as the only clue. Check that line in the log.

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
.venv/Scripts/python.exe -m tests.test_adspower     # the AdsPower syncer (phase 2)
```

373 checks total. `test_dispatch` also guards the docs: it fails if
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
