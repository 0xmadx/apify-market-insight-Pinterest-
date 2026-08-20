# SCALING — what breaks first, and at what number

*Written against measurements, not estimates. Every number below was produced
by running the thing on 2026-08-19; where something is a projection it says so.*

The question this answers: **100 customers scraping at once — what happens?**

The short version: **nothing breaks, but 98 of them wait and then fail.** The
binding constraint is not Pinterest, not Redis, and not CPU. It is the number
of logged-in profiles, because a lease is exclusive.

---

## The measurement that decides everything

```
10 clients · 2 profiles · each holding 2s
  elapsed    : 15.1s
  succeeded  : 6/10   {profile_p5ewxsodn: 3, ads_k1fx40wf: 3}
  VaultEmpty : 4      (waited 8s, gave up)
```

**Concurrent capacity == profile count.** `SessionVault.acquire` claims a
profile with `SET NX`; while one run holds it, no other run can. That
exclusivity is deliberate and correct — two runs driving one Pinterest session
from two IPs is the fastest way to get it flagged — but it means concurrency is
bought one account at a time.

And a run holds its lease for the *whole* run:

| operation | measured | requests |
|---|---|---|
| `radar` | 2.9s | 3 |
| `shopping` (1 vertical, no products) | 3.5s | 5 |
| `keywords` | 15.2s | 15 |
| `shopping` (full default) | ~38s (projected at the same ~1s/request) | 38 |

So with **N** profiles and a run of **R** requests, throughput is roughly
`N × 3600/R` runs per hour. Today, N=2 and a full shopping run: **~190
runs/hour**, and never more than 2 at the same instant.

100 simultaneous clients against that: 2 run, 98 queue, and each gives up after
`VAULT_WAIT_TIMEOUT` (60s) with `VaultEmpty`.

---

## The seven levels, in the order they break

### 1. Identities — **breaks first, at N = concurrent clients**

100 concurrent runs needs 100 signed-in Pinterest accounts, each with its own
proxy. That is not just cost: 100 accounts created and used in the same pattern
is itself a detection signal, which is the thing the whole antidetect setup
exists to avoid.

**Fix, in order of leverage:**

- **Lazy leasing** — the biggest available win and currently not done.
  `src/main.py` wraps the entire run in `leased_session(...)`, so a run that is
  100% cache hits still occupies an identity for its full duration. Measured
  earlier this session: a repeat crawl logged `cache: 6 hits, 0 misses` and
  still held a profile. Acquiring only on a cache **miss** would multiply
  effective capacity by roughly `1/(1-hit_rate)`.
- **Queue instead of fail.** `VaultEmpty` after 60s is right for a scheduled
  job and wrong for a paying customer. A real queue with a position and an
  honest ETA beats a 60s stall and an error.
- Then, and only then, buy more accounts.

### 2. The response cache — **already the main defence, and it is per-run today**

TTLs are per kind, which is the right shape:

| kind | TTL | why |
|---|---|---|
| `detail` | 12h | pin/product detail barely moves |
| `trends` | 6h | weekly series; Pinterest settles it ~4 days behind anyway |
| `default` | 1h | |
| `search` | 15min | rankings move |

100 customers asking overlapping questions cost Pinterest **one** request per
distinct question per TTL window. That is why the cache is a scaling feature
and not an optimisation. Its value rises with customer count, and it is the
reason lazy leasing pays off so heavily: at a high hit rate, most runs never
need an identity at all.

### 3. AdsPower Local API — **~250 profiles**

Measured: **1 request/second**, with v1 and v2 keeping separate budgets. The
sync does one v1 list plus one v2 cookies call per profile, so a cycle over
**P** profiles costs about `P × 1.15s`.

The heartbeat runs every 300s and must finish inside `PROFILE_MAX_AGE` (900s)
or profiles start being evicted mid-cycle.

```
  100 profiles -> ~115s per cycle   ✅ comfortable
  250 profiles -> ~290s per cycle   ⚠️ at the 5-minute tick
  400 profiles -> ~460s per cycle   ❌ profiles evict while the sync is running
```

**Fix:** shard across several AdsPower instances (each has its own 1/sec
budget), or lengthen the tick and raise `PROFILE_MAX_AGE` together — never one
without the other.

### 4. Proxies — **1 per profile, no sharing**

`assign_proxies.py` refuses to wrap around, deliberately: two profiles behind
one exit IP defeats the separation the proxies are for. So proxy count is a
hard floor on profile count. Currently **5**.

### 5. Redis — **fine locally, a real limit on hosted tiers**

Command volume is trivial. The risk is **connections**: every concurrent actor
opens its own client, and hosted Redis charges for or caps concurrent
connections. Upstash's free tier in particular is not sized for 100 simultaneous
containers.

**Fix:** connection pooling per actor, and check the tier's connection cap
before it is the thing that fails at 3am. This has not been tested — it is a
projection from how the code opens clients.

### 6. Pinterest itself — **unknown, and the one to instrument**

Per-account limits are not documented and have never been hit here: `429` has
never been observed once in this project. `transport.py` already backs off
blindly on one, because the API sends no rate-limit headers at all — no
`X-RateLimit-*`, no `Retry-After` — so there is nothing to read and pace
against.

The subtler risk at 100 accounts is not volume but **uniformity**: 100
identities issuing the same request sequence at the same times looks like one
actor wearing 100 hats. Jitter and varied traversal order matter more than raw
throughput here.

### 7. Apify — **per-account concurrency and memory**

Each run is a container. 100 concurrent runs is an Apify account limit and a
memory bill before it is a code problem.

---

## What to do, in order

1. **Lazy leasing.** Biggest win, no new accounts, no new cost. Acquire an
   identity on the first cache **miss**, not at run start.
2. **Queue with backpressure** instead of `VaultEmpty` after 60s. A customer
   deserves a position, not a stall.
3. **Instrument before scaling.** Log lease wait time, cache hit rate, and
   429s. Right now none of the three is measured in production, and every
   decision above depends on them.
4. **Then** buy accounts and proxies — the expensive, risky lever, and the one
   that should be pulled last.

## What is already right

- The lease is exclusive, so no two runs ever share an identity.
- Cookies, UA and exit IP travel together as one `Identity` — the thing that
  actually keeps accounts alive.
- The cache is shared, honest, and per-kind.
- The crawl budget is a hard cap, so one customer cannot run away with the
  session pool.
- `VaultEmpty` fails loudly instead of hanging, which is the right failure for
  a scheduled job even though it is the wrong one for a paying customer.
