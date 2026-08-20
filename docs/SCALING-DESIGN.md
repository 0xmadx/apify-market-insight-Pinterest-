# THE DESIGN — 10 profiles serving 100 clients

*Written after researching how commercial scrapers solve this. The short
answer: **none of them buy one identity per customer.** Every one decouples
"customer asks" from "we fetch", and the industry has names for the pieces.*

Read [SCALING.md](SCALING.md) first — it establishes that concurrent capacity
equals profile count today.

---

## The measurement that reframes the problem

```
8 clients ask the SAME uncached question simultaneously:
  upstream fetches : 8    (ideal: 1)
  waste            : 7 duplicate Pinterest hits, 7 identities tied up

and once warm:
  upstream fetches : 0
```

The cache works perfectly **after** the first fill and not at all **during**
it. That is a textbook **cache stampede** — every TTL expiry lets the whole
waiting crowd through at once. With `trends` on a 6h TTL and 100 customers,
that is a herd of up to 100 identical requests every six hours, burning 100
identities to fetch one answer.

**This is the real ceiling, not the profile count.** Fix it and 10 profiles go
a long way. Leave it and 100 profiles still stampede.

## What the industry actually does

| Pattern | Who does it | What it buys |
|---|---|---|
| **Async job API** — submit, get an id, poll or webhook | ScraperAPI, Bright Data | the customer never waits on an identity |
| **Per-customer concurrency cap** | Bright Data 429s past a parallel-job limit; ScraperAPI burst-limits per second | one customer cannot starve the rest |
| **Request coalescing / singleflight** | the standard cache-stampede fix (Go's `singleflight`; Redis `SET NX` across processes) | N duplicate asks cost **one** fetch |
| **Bounded worker pool + queue** | every distributed scraper design | throughput stays flat instead of collapsing |
| **Backoff with jitter, then a dead-letter queue** | ditto | retry storms do not re-synchronise |

Note what is *not* on that list: one identity per concurrent customer.

## The arithmetic for 10 profiles

The variable that matters is **distinct questions**, not customers. A hundred
people researching Pinterest overlap heavily — regions, verticals and top
keywords are a small set.

```
100 clients / ~20 distinct questions

                        today          with coalescing
  upstream fetches       100                 20
  identities needed      100                 10   (two rounds)
  Pinterest sees     100x the same       20 requests
```

With coalescing plus lazy leasing, **10 profiles stops being the constraint**.
20 requests spread over 10 IPs is nothing.

## The four changes, in dependency order

**1. Singleflight on cache miss.** ✅ **BUILT 2026-08-19.** Measured through the real transport: 8 clients on one cold key made **1** request instead of 8, in 1.7s, saving 7 identities. A warm key still costs nothing. A waiter whose winner never delivers fetches it itself rather than failing — a duplicate request is the right outcome, silence is not.

*(original plan follows)* `SET NX` a short lock on the cache key.
The winner fetches; the losers poll for the result. This is the change that
turns 100 clients into 20 requests, and it costs nothing — Redis already holds
the cache, so the lock lives beside it.

**2. Lazy leasing.** ✅ **BUILT 2026-08-19.** Measured with the same test that
started this:

```
10 concurrent clients, 2 usable profiles, warm cache
  succeeded       : 10/10   (was 6/10)
  failed          : 0       (was 4 VaultEmpty)
  identities used : 1 of 10 clients
  elapsed         : 2.1s
```

The bootstrap (`/latest_available_date/`) had to be cached for this to pay off
at all — it was the one uncached call every run makes, so every run leased an
identity just to ask "what is your newest date?". A 300s TTL collapses that,
and stays short because every other cache key contains `end_date`, so this
value is what expires the rest of the cache.

*(original plan follows)* Acquire an identity *after* the cache miss and after
winning the lock — not at run start. Today `main.py` wraps the whole run, so a
run that is entirely cache hits still occupies a profile. Combined with (1),
only the winner of each key ever needs an identity at all.

**3. Async job API.** Accept the job, return an id, let the worker pool drain
the queue. This is what makes `VaultEmpty` stop being a customer-visible
failure: nobody waits 60 seconds for a lease. Apify's own
`run-sync-get-dataset-items` is a convenience wrapper over an async run — the
async form is the honest one at scale.

**4. Per-customer fair share.** Cap concurrent jobs per customer and
round-robin when draining. Without it, one customer submitting 500 URLs owns
the pool. Bright Data's answer is a hard per-account cap and a 429; that is the
simple version and it works.

## Different keywords — and the trap in fixing it

Coalescing only collapses **identical** requests. Two customers asking about
different keywords genuinely need different answers. But a keyword request is
not one thing:

| part | shared between customers? |
|---|---|
| `latest_available_date` | ✅ always |
| the 383-row taxonomy | ✅ always |
| trending discovery for a region | ✅ same region/preset |
| **per-keyword stats** | ❌ unique |

So the shared spine is free now; the leaves are not. The obvious fix is to
cache **per term** rather than per request-batch — measured, the whole batch is
one cache entry, so a customer asking for one of three already-fetched terms
misses entirely.

### ⚠️ But per-term caching is UNSAFE for `/metrics/`

`normalize_against_group=true` means a term's counts are scaled against the
*other terms in the same request*. Measured 2026-08-19, same term, same dates,
same window:

```
'eye makeup' beside 'eyelashes'                        -> [30, 32, 36]
'eye makeup' beside 'nails','hairstyles','wallpaper'   -> [ 1,  1,  2]
```

**Thirty-fold difference, purely from the company it keeps.** Caching that per
term would serve a `36` next to a fresh `1` for the same keyword — a number
that looks ordinary and is not comparable. `_meta.normalization_scope` carries
the term count for exactly this reason.

`/demographics/` was measured **identical** across both groups — per-term
percentages, not relative volumes — so that one IS cached per term. Result on
the real path:

```
A: 3 fresh keywords      wire=3
B: SAME 3 keywords       wire=0    <- and no identity taken at all
C: 1 known + 1 new       wire=2
```

The honest limit: partial overlap still costs one request (the missing terms
are batched into a single call), so this saves *payload*, not *requests*, until
the overlap is total. Full overlap is where it pays.

## Sizing, from the measured ~1s per request

```
10 profiles · lazy leasing · most work coalesced or cached
  concurrent upstream fetches   ~10
  a full shopping run            38 requests -> ~38s
  sustained                      ~950 runs/hour of DISTINCT work
                                 + unlimited cached reads
```

"Plus unlimited cached reads" is the whole point: once a question is answered,
serving it again costs no identity, no proxy and no Pinterest request — so
customer count and identity count stop being the same number.

## Redis on GCP — what to provision

Memory is trivial; cache entries are small JSON. Two things matter more:

- **Connections, not gigabytes.** Every concurrent worker holds a client. Size
  the tier by connection count and pool per process. This is the most likely
  first failure on a hosted tier and it has **not been tested here** — it is a
  projection from how the code opens clients.
- **Persistence.** Losing the vault costs a re-sync (~2s per profile,
  recoverable). Losing the **seen-set** re-emits everything to every customer.
  Turn it on.

Memorystore Basic is enough to start, and the $300 credit covers a small
instance for months. Do not size up before the connection count is measured.

## What not to do

- **Do not buy 100 accounts.** A hundred identities issuing the same request
  sequence at the same times looks like one actor in a hundred hats. The
  uniformity is the signal, not the volume.
- **Do not raise concurrency past the identity count.** The lease exists to
  stop two runs sharing one session. Defeating it to gain throughput trades an
  account for a request.
- **Do not add threads inside a run.** AdsPower is 1 req/sec and Pinterest's
  limit is unmeasured. Parallelism belongs *across* jobs, in the worker pool,
  never inside one traversal.

## Sources

- [Distributed web scraping architecture](https://www.scrapehero.com/how-to-build-and-run-scrapers-on-a-large-scale/) — queue, worker fleet, per-domain limits
- [Scaling without rate limits or bans](https://mrscraper.com/blog/scale-web-scraping-without-rate-limits-ip-bans) — session isolation per account
- [Redis request coalescing](https://oneuptime.com/blog/post/2026-01-21-redis-request-coalescing/view) · [Redis cache stampede](https://oneuptime.com/blog/post/2026-01-21-redis-cache-stampede/view)
- [Singleflight in Go](https://medium.com/pickme-engineering-blog/singleflight-in-go-a-clean-solution-to-cache-stampede-02acaf5818e3)
- [Bright Data concurrency caps](https://docs.brightdata.com/datasets/scrapers/scrapers-library/overview) · [ScraperAPI concurrency](https://docs.scraperapi.com/faq/js-rendering/js-rendering-concurrency)
