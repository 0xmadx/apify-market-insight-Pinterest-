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

## Phase 1 — where we are

One local Chrome, one Pinterest profile, the extension beaming, home IP, no
proxy. Everything in here is built for exactly that and nothing more.

**Phase 2**, when it comes: AdsPower profiles and Webshare proxies, chained per
profile and per account through the vault. The seam for it is described at the
bottom — one field, already read, currently always `None`.

---

## Self-contained, on purpose

This is its own repository, on the Desktop, outside the Etsy project. It shares
exactly **one** thing with the Etsy repo it was lifted out of: the Redis vault,
because the Chrome extension writes the sessions there. It shares no code, no
imports, and no configuration.

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
| The Redis key schema (`cookie:{platform}:{id}`, `valid_profiles:{platform}`) | Unchanged, so the existing Chrome extension and Go cookie server keep working with no edit |
| The vault read path, narrowed | Etsy platforms, the seller-token guard and `shop_id` all deleted — none of it applies |
| `curl_cffi` with Chrome impersonation | The point is the TLS/JA3 handshake. A plain Python client announces itself at the transport layer no matter how correct the headers above it are, and these are the operator's real logged-in cookies — too expensive to burn on a fingerprint mismatch that costs nothing to avoid |
| Bounded waiting | An empty vault must fail, never hang |
| "Absent is not zero" | Carried into `scraper.py` as a standing rule |

| Left behind | Why |
|---|---|
| `scrapling` | `pinterest/core/client.py` in the parent repo imports `StealthyFetcher` and **nothing calls it** — every working Pinterest call goes through a plain HTTP client. Scrapling's value is auto-healing HTML selectors and a Camoufox stealth browser. These endpoints return JSON, so there are no selectors to heal, and the stealth fetcher would drag a headless browser into an actor that needs none — several times the Apify compute cost, for nothing gained |
| `httpx` | Swapped out for `curl_cffi`. Same synchronous shape, plus the fingerprint |
| Proxy rotation, AdsPower | Not phase 1 |

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
tests/                     5 suites, 339 offline checks — see below
probes/                    live wire probes; probe_endpoints is the release gate
docs/                      product docs + the reverse-engineering corpus
```

## Where everything is

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | ⭐ **start here** — the endpoint graph (nodes, edges, decision points), the 4-actor product, the shared-vault economics |
| [docs/wire/07-API-REFERENCE.md](docs/wire/07-API-REFERENCE.md) | every endpoint, param, and measured limit |
| [docs/wire/08-BUILD-GUIDE.md](docs/wire/08-BUILD-GUIDE.md) | call chains, normalisation rules, validation checklist |
| [docs/API.md](docs/API.md) | ⭐ **the reference** — endpoint, all 45 inputs, every output field, errors, cost |
| [docs/CUSTOMER-GUIDE.md](docs/CUSTOMER-GUIDE.md) | ⭐ **what a buyer reads** — the four questions, worked examples, how to read the numbers honestly |
| `api_sim.py` | a local Apify-shaped API + browser playground: `python api_sim.py` → http://localhost:8080 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | the Apify runbook — the one open decision, secrets, measured cost per run, and what a customer must be told |
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

Run everything offline — **339 checks, no network**:

```bash
.venv/Scripts/python.exe -m tests.test_incremental          #  20 — freshness layer
.venv/Scripts/python.exe -m tests.test_shopping_api         #  54 — vocab + parsers
.venv/Scripts/python.exe -m tests.test_shopping_traversal   #  33 — shopping walk
.venv/Scripts/python.exe -m tests.test_full_project         #  95 — keywords/moments/radar
.venv/Scripts/python.exe -m tests.test_dispatch             # 137 — the customer-facing path
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

Whichever you pick, the Chrome extension keeps posting to the Go server and the
Go server writes to that Redis instead of the local one. Nothing else changes.

**A consequence worth being explicit about:** the actor only works while a Chrome
somewhere is beaming a live Pinterest session. If you list this on the Apify
store, every customer's run draws on *your* session — that is a shared-account
model, not a per-customer one, and it caps concurrency at the number of live
profiles you keep. Worth deciding deliberately before it becomes the design by
default.

---

## The phase-2 seam

Nothing proxy-related is implemented. Where it goes when it is:

- `Identity.proxy` is already read **from the profile hash**, not from a global,
  and is `None` today. A cookie and the IP it was born on are one identity;
  recombining them across runs is itself a fingerprint. Phase 2 writes `proxy`
  next to `cookies_json` for each profile and this code changes not at all.
- AdsPower is desktop software with a *local* API (`127.0.0.1:50325`). It cannot
  run in an Apify container. The shape has to be: AdsPower on a machine you
  control → a sync agent reads each profile's cookies and proxy → writes them to
  the vault → the actor reads the vault. The same picture as today with the
  extension swapped out.
- Reading cookies out of an AdsPower profile is **not** a plain REST call — the
  local API starts the browser and hands back a CDP websocket, and the cookies
  come from `Network.getAllCookies` over it. That needs verifying against a
  running instance. AdsPower was not installed here (port 50325 dead), so nothing
  about it has been probed and nothing should be built on assumption.
- Webshare (`GET /api/v2/proxy/list/`, `Authorization: Token …`) is the natural
  source, with each proxy health-checked against a Pinterest canary before it
  enters the pool — a proxy that resolves but draws a 403 from the target is
  worse than no proxy, because it burns the session attached to it.
