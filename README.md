# Pinterest Apify actor — the floor

An Apify actor that scrapes Pinterest using **a session the operator's own Chrome
already holds**, pulled from the Redis vault. The actor never logs in, never
stores a credential, and never runs a browser.

This directory is the floor only: session plumbing, actor packaging, health
check. **The Pinterest endpoint logic is not here** — it goes in
[`src/scraper.py`](src/scraper.py) once the API notes land.

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
src/scraper.py             ← the Pinterest logic goes here (currently raises)
src/main.py                actor entrypoint: lease → scrape → dataset
src/status.py              vault health check
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
