# Pinterest Trends — an Apify actor

Reads Pinterest Trends using **a session the operator's own Chrome already
holds**, pulled from the Redis vault. It never logs in, never stores a
credential, and never runs a browser.

Pinterest publishes this data through a filtered interface. The actor asks the
same backend the same questions — unfiltered, joined together, and in a shape
that goes straight into a spreadsheet.

**Five operations.** Four answer one question each; the fifth follows the links
between them.

| Operation | The question | Requests | Records |
|---|---|---|---|
| `radar` | What is Pinterest itself featuring right now? | 2 | 11 |
| `keywords` | What are people searching for, and is it still growing? | ~14 | 10 |
| `moments` | When does this season actually start? | ~16 | 13 |
| `shopping` | What products are people clicking through to buy? | ~38 | 57 |
| `crawl` | Follow Pinterest's navigation, the way you would click it | ~17 | 74 |

Press Run with nothing filled in and you get what is trending now.

**New to this?** → [docs/CUSTOMER-GUIDE.md](docs/CUSTOMER-GUIDE.md) — what you
get (real sample records), five worked use cases, and how to read the numbers
honestly ·
**Integrating?** → [docs/API.md](docs/API.md) — every input and output field ·
**Changing the code?** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## What is in here, and where each part runs

This repo holds three different things that never run on the same machine.
Confusing them is the fastest way to get lost, so:

| | Runs on | Who needs it | Ships to Apify |
|---|---|---|---|
| `src/` | **Apify** | everyone | ✅ **the only code that ships** |
| `.actor/` | **Apify** | everyone | ✅ manifest + input schema |
| `browsers/` | **a GCP VM** | whoever runs the session farm | ✗ |
| `adspower/` | **the operator's laptop** | whoever logs accounts in | ✗ |
| `probes/` `tests/` | anywhere, **before** a release | whoever changes the code | ✗ |
| `docs/` | nowhere — it is the reference | everyone | ✗ |

`src/` imports **none** of the others, and the image excludes them, so the
actor cannot accidentally depend on operator tooling.
[docs/VAULT_SEPARATION.md](docs/VAULT_SEPARATION.md) explains why that boundary
is enforced rather than assumed.

### The lab is not in this repo

The **lab** is the operator's own machine: AdsPower with the logged-in profiles,
and a local Redis container. Neither is code, neither is committed, and neither
is needed to run or deploy the actor. `adspower/` is only the *client* that
talks to it.

That is the separation to keep straight:

| | What it is | Where it lives |
|---|---|---|
| **the lab** | AdsPower + a local Redis. Where a human signs an account in | the operator's laptop, **not in git** |
| **this repo** | all the code, docs and deploy tooling | GitHub, cloned anywhere |
| **production** | Apify (the actor) + Upstash (the vault) + a GCP VM (keepalive) | the cloud |

Sessions move lab → vault → actor. Code moves repo → production. The two flows
meet only at the vault, which is why the actor never needs the lab and the lab
never needs Apify.

### Using this yourself

Everything except the sessions is reproducible from this repo alone:

```bash
./preflight.sh        # what is missing on this machine
./ship.sh check       # 16/16 live endpoints, then 551 offline checks
./run_local.sh        # the real actor, locally, against a real vault
```

What you must bring is a **vault with a live Pinterest session in it** — that is
the one thing no code here can create for you, by design.
[DEPLOY.md](DEPLOY.md) is the ordered runbook; its first section is the
deployment model, which is worth reading before any command.

---

## Where we are

**Phase 2 landed.** Eight AdsPower profiles, each on its own Webshare exit IP,
chained per profile through the vault — the seam described at the bottom of this
file is no longer `None`. The vault refuses to lease a profile that has no proxy.

**Phase 3 is built and measured but not cut over.** `browsers/` is a free
replacement for AdsPower's session-keeping: per-profile fingerprints (3/3
distinct and stable), headless Linux with no root, 7 profiles refreshed in 53s.
AdsPower is still the live writer; the switch is deliberate and observable, not
a flag. See [docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md).

**Not deployed yet.** The actor runs locally and in its real container against
a non-localhost Redis. GitHub is done — the canonical repo is
`git@github.com:0xmadx/pinterest-apify.git` (private). The Upstash database
exists but **its copy is stale and no writer points at it**, which is the
current blocker. Apify and the GCP VM follow, in that order —
[DEPLOY.md](DEPLOY.md) carries the state table and the runbook.

Two scripts answer "is it safe to ship" before and after a push:
`./preflight.sh` (tooling, auth, repo visibility, `.env` hygiene, and whether
the vault has a live writer) and `./smoke.sh` (calls the deployed actor and
fails a zero-record success).

**What actually ships is 26 files** — `src/` (20), `.actor/` (2),
`requirements.txt`, `Dockerfile`, `.dockerignore`, `.env.example`. About 9% of
the repo's ~60,000 lines. `docs/`, `probes/`, `tests/`, `adspower/` and
`browsers/` are all excluded, and `src/` imports none of them, so the exclusion
cannot break the actor. Two reasons, not one: an image should hold what it
runs, and `docs/wire/` plus `probes/results/` **are the product** — the measured
map of an API with no contract does not belong inside the artefact shipped to
run it.

⚠️ Verify that list rather than trusting the rule. `.dockerignore` excluded
shell scripts by exact name until 2026-08-27, so `preflight.sh` and `smoke.sh`
shipped inside the image from the day they were added — silently, because
nothing references them and nothing fails.

---

## Self-contained, on purpose

This is its own repository, outside the Etsy project, and since 2026-08-25 it
shares **nothing** with it. The vault used to live in the Etsy project's Redis
container; it now has its own — `pinterest-redis` on 6380, migrated with
`browsers/migrate_vault.py`, which copies only this platform's keys and leaves
`cookie:etsy*` untouched. No shared code, no shared imports, no shared config,
and now no shared process.

- [`.env`](.env) is loaded from **this directory by explicit path**, never by
  upward search. A `find_dotenv()` walking up would have silently inherited the
  Etsy repo's Redis URL and user agent — two projects reading one `.env` is how a
  change to one breaks the other with no edit in sight.
- No module here imports from `core/`, `etsy/` or `pinterest/`.
- Nothing outside this directory is needed at runtime.

---

## What was taken from the parent repo, and what was left

| Taken | Why |
|---|---|
| The Redis key schema (`cookie:{platform}:{id}`, `valid_profiles:{platform}`) | Unchanged, so any writer that produced it still works. The **schema** was taken; the parent's Redis, Go server and extension were not — this project runs its own container and its own writer ([`docs/VAULT_SEPARATION.md`](docs/VAULT_SEPARATION.md)) |
| The vault read path, narrowed | Etsy platforms, the seller-token guard and `shop_id` all deleted — none of it applies |
| `curl_cffi` with Chrome impersonation | The point is the TLS/JA3 handshake. A plain Python client announces itself at the transport layer no matter how correct the headers above it are, and these are the operator's real logged-in cookies — too expensive to burn on a fingerprint mismatch that costs nothing to avoid |
| Bounded waiting | An empty vault must fail, never hang |
| "Absent is not zero" | Carried into `scraper.py` as a standing rule |

| Left behind | Why |
|---|---|
| `scrapling` | `pinterest/core/client.py` in the parent repo imports `StealthyFetcher` and **nothing calls it** — every working Pinterest call goes through a plain HTTP client. Scrapling's value is auto-healing HTML selectors and a Camoufox stealth browser. These endpoints return JSON, so there are no selectors to heal, and the stealth fetcher would drag a headless browser into an actor that needs none — several times the Apify compute cost, for nothing gained |
| `httpx` | Swapped out for `curl_cffi`. Same synchronous shape, plus the fingerprint |
| Proxy rotation, AdsPower | Not phase 1 — both landed later, see below |

## What was added

**A lease.** The parent vault has none. Two concurrent actor runs would draw the
same profile and drive one Pinterest session from two IPs at once — the fastest
way to get it flagged. `SET NX` with a TTL makes that impossible, and the TTL
means a crashed run returns the profile without help.

**Freshness that fails closed.** The parent vault skips the staleness check
entirely when `last_updated` is absent. Here, unknown freshness is not freshness:
no heartbeat means eviction. Your vault currently holds two profiles nearly three
days stale that the old check would have handed out.

**A `malformed` verdict in `classify()`.** Found by probing, not by reasoning:
Pinterest answers a `/resource/` call missing the mandatory
`x-pinterest-pws-handler` header with **403 Invalid Resource Request**. Reading
that as `auth_expired` sends the operator to re-login in Chrome over a header
bug, and invites the caller to evict a healthy profile. It now has its own case.

---

## Layout

```
.actor/actor.json          Apify actor manifest
.actor/input_schema.json   run input (queries, maxRecords, vaultPlatform)
Dockerfile                 apify/actor-python:3.12 — no browser image
.env                       this project's config, read by explicit path only
src/config.py              env-driven config
src/vault.py               lease/release an Identity from Redis
src/session.py             curl_cffi session wearing exactly one identity
src/state.py               seen-set + watermark — never re-pull what we hold
src/cache.py               response cache, TTL per endpoint kind
src/context.py             what the scraper is handed (ctx.get, ctx.seen)
src/records.py             the Record the scraper yields
src/scraper.py             dispatch: operation -> traversal -> Records
src/main.py                actor entrypoint: lease → scrape → dedup → dataset
src/transport.py           the two call styles + cache + blind backoff
src/vocab.py               measured ceilings/enums, refused before the wire
src/parsers.py             one named parser per endpoint
src/shopping.py  keywords.py  moments.py  radar.py   the four traversals
src/crawl.py               the fifth: follows the links between them
src/status.py              vault health check
tests/                     7 suites, 551 checks — see below
probes/                    live wire probes; probe_endpoints is the release gate
docs/                      product docs + the reverse-engineering corpus
```

## Where everything is

| Doc | Purpose |
|---|---|
| [DEPLOY.md](DEPLOY.md) | ⭐ **deploying it** — the ordered runbook: GitHub, Upstash, Apify, GCP, and what bites |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | ⭐ **start here** — the endpoint graph (nodes, edges, decision points), the 4-actor product, the shared-vault economics |
| [docs/VAULT_SEPARATION.md](docs/VAULT_SEPARATION.md) | ⭐ **before touching any Redis** — the standing rule that this lab never reaches into the Etsy project, who owns which container, and the couplings that had to be cut |
| [docs/SESSION_SOURCES.md](docs/SESSION_SOURCES.md) | ⭐ **which writer fills the vault** — extension vs AdsPower vs stealth-browsers, what each costs, what's proven, what's deferred |
| [docs/wire/07-API-REFERENCE.md](docs/wire/07-API-REFERENCE.md) | every endpoint, param, and measured limit |
| [docs/wire/08-BUILD-GUIDE.md](docs/wire/08-BUILD-GUIDE.md) | call chains, normalisation rules, validation checklist |
| [docs/API.md](docs/API.md) | ⭐ **the reference** — endpoint, all 45 inputs, every output field, errors, cost |
| [docs/CUSTOMER-GUIDE.md](docs/CUSTOMER-GUIDE.md) | ⭐ **what a buyer reads** — the four questions, worked examples, how to read the numbers honestly |
| `api_sim.py` | a local Apify-shaped API + browser playground: `python api_sim.py` → http://localhost:8080 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | the Apify runbook — the one open decision, secrets, measured cost per run, what a customer must be told, and **§5 the standing update loop** (how a change reaches production, and why `buildTag: latest` means a push is immediate) |
| [docs/TEST-SCENARIOS.md](docs/TEST-SCENARIOS.md) | acceptance scenarios (A1…G2) — the definition of done for the coding agent |
| [probes/RESULTS.md](probes/RESULTS.md) | live probe: 16/16 endpoints verified, raw responses in `probes/results/` |
| [CLAUDE.md](CLAUDE.md) | working rules for any agent in this repo |
| `.claude/skills/pinterest-trends-coder/` | the enforced coding skill — invoked before writing any code |
| [docs/README.md](docs/README.md) | which spec drafts were superseded and why |

**Current state:** all five operations are built, offline-tested and verified
live, on top of the shared graph layer (`transport.py`, `vocab.py`,
`parsers.py`) and the session + freshness layers. 16/16 endpoints answer, 0
response fields go unread.

Both of Pinterest's time controls are wired: `endDate` (which date am I looking
at) and `dateRange` (how much history the chart shows). They are different
questions and compose.

Run everything offline — **551 checks, no network**:

```bash
.venv/Scripts/python.exe -m tests.test_incremental          #  55 — freshness layer
.venv/Scripts/python.exe -m tests.test_shopping_api         #  54 — vocab + parsers
.venv/Scripts/python.exe -m tests.test_shopping_traversal   #  34 — shopping walk
.venv/Scripts/python.exe -m tests.test_full_project         #  95 — keywords/moments/radar
.venv/Scripts/python.exe -m tests.test_dispatch             # 164 — the customer-facing path
.venv/Scripts/python.exe -m tests.test_adspower             #  90 — the cookie syncer
.venv/Scripts/python.exe -m tests.test_vault                #  52 — the lease path (needs Redis)
```

Or the whole gate in the order a release requires — live probes first, because
they rewrite the fixtures the suites then read:

```bash
./ship.sh check
```

## Not pulling old data

Three separate layers, each blocking a different kind of "old":

| Layer | Blocks | Where |
|---|---|---|
| **Response cache** | re-requesting the same URL inside its TTL | `ctx.get(kind, url)` — TTL per kind: `trends` 6h, `detail` 12h, `search` 15m. Never caches a non-200, a blocked response, or an empty body |
| **Seen-set** | re-pushing a record already collected and unchanged | `ctx.seen(scope, id, record)` and a final pass in `main.py`. Re-collects when the id is new, the content fingerprint moved, or the entry is older than 7 days |
| **Session freshness** | running on a dead login | the vault refuses a profile with no heartbeat in 15 min, and now one missing `_auth` / `_pinterest_sess` |

Three design decisions inside that are worth knowing:

- **Records are marked seen only after the dataset push returns.** Marking first
  means a crash between mark and push loses those records forever — the next run
  skips them and nothing notices. Marking after can at worst duplicate a batch,
  which is visible and fixable.
- **Dedup is on a content fingerprint, not just an id.** A pin's save count moves;
  identity-only dedup would freeze a live metric into a one-time snapshot. Pass
  `fields=` to say which keys count as a change.
- **A signed-in check, not just a cookies-present check.** A logged-out browser
  still has cookies, so the old check passed and every request went out anonymous
  — and Pinterest answers those with plausible *public* data. The run would
  "succeed" while collecting the wrong thing.

Two run switches, both off by default: `fullRescan` ignores the seen-set,
`forceRefresh` ignores the cache. Both still write, so a one-off full run does not
leave the next run with nothing to skip.

Run the checks:

```bash
.venv/Scripts/python.exe -m tests.test_incremental
```

## Running it

Check the vault before anything else — an empty pool is the most common reason a
run produces nothing, and from inside a scraper it looks identical to a site
change:

```bash
.venv/Scripts/python.exe -m src.status
```

First time, from this directory:

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
```

The scraper contract, once implemented:

```python
def run(session, task):   # curl_cffi Session, already carrying the identity
    yield {...}           # one dict per record → straight to the dataset
```

Verified live on 2026-08-18: the leased session reaches Pinterest's authenticated
API layer and gets a structured `resource_response` back with `client_context`.
Cookies, user agent and TLS all land. Only the endpoint logic is missing.

---

## Deploying to Apify

1. `npm i -g apify-cli`, then `apify login`.
2. From this directory: `apify push`.
3. Set **`REDIS_URL` as an Actor secret** (Settings → Environment variables,
   marked secret). Never in `actor.json` — it carries the vault password.

**The one blocker to solve before a cloud run works:** the actor runs in Apify's
cloud and your Redis is a Docker container on this desk. `localhost` there means
the actor's own container, so it will connect to nothing. Options:

| Option | Cost | Notes |
|---|---|---|
| Upstash Redis (serverless) | free tier covers this | TLS + password out of the box, `rediss://` URL, no server to run — the obvious pick |
| Small VPS running the vault | ~$5/mo | you already have the compose file; needs firewall + `requirepass` + TLS |
| Tunnel from this machine | free | fragile: the actor fails whenever the desk machine sleeps |

Whichever you pick, `adspower/sync_cookies.py` writes to that Redis instead of
the local one — one `REDIS_URL`. Nothing else changes.

⚠️ `REDIS_URL` lives in **four** places that disagree by default, and the
systemd `EnvironmentFile` beats both `.env` and the code. Moving the vault means
grepping all four; missing one killed the pool for 90 minutes while both halves
reported success. `docs/VAULT_SEPARATION.md` lists them.

**A consequence worth being explicit about:** the actor only works while a real
browser somewhere is keeping a live Pinterest session warm. If you list this on the Apify
store, every customer's run draws on *your* session — that is a shared-account
model, not a per-customer one, and it caps concurrency at the number of live
profiles you keep. Worth deciding deliberately before it becomes the design by
default.

---

## The phase-2 seam — closed

This section used to describe where proxies *would* go. They went there.

- `Identity.proxy` is read from the profile hash, not a global, and is now
  populated for every profile in the pool. A cookie and the IP it was born on
  are one identity; the vault refuses to lease a profile with no proxy
  (`REQUIRE_PROXY`), because an unproxied one exits from whatever host the run
  is on and mixes a residential identity into a proxied pool.
- AdsPower runs headless in WSL with a local API (`127.0.0.1:50325`), and
  `adspower/sync_cookies.py` reads each profile's cookies, user agent and proxy
  and writes all three to the vault together.
- ⚠️ **Reading those cookies is `Storage.getCookies`, NOT
  `Network.getAllCookies`.** This file said the latter, and it was wrong: that
  method does not exist on a browser-level CDP target. The first implementation
  swallowed the resulting error and reported "never signed in" for a profile
  holding a live session — a plausible wrong answer produced by ignoring an
  error that said exactly what was wrong. `tests/test_adspower.py` GROUP R is
  that bug's regression test.
- Webshare proxies are assigned one per profile by
  `adspower/assign_proxies.py`, country-matched to the account, and the pairing
  is **sticky** — a profile that has an exit IP is never given a different one.

The seam that is still open is the one after it: `browsers/` replaces
AdsPower's session-keeping entirely and is proven but not cut over. See
[docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md).
