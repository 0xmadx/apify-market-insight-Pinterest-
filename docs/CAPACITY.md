# CAPACITY — measured, and what actually breaks

*Every number here came from running it on 2026-08-19 against 4 live profiles.
Where something is projected it says so.*

This corrects an over-optimistic earlier reading. A test showing "10/10 clients
served on 2 profiles" was the **friendly** case — warm cache, one shared
question. With genuinely distinct work, nothing is shareable and every client
needs the wire.

---

## Sustained load is not the problem

```
4 profiles, sustained:

  cached-ish run        3s  ->   4,800 runs/hour  =  115,200/day
  typical keyword run  10s  ->   1,440 runs/hour  =   34,560/day
  full shopping run    38s  ->     379 runs/hour  =    9,095/day
```

100 customers doing 5 runs a day is **500 runs/day** — about 5% of capacity on
the *slowest* operation. Volume is nowhere near the limit.

## Bursts are

```
4 profiles · 3s per run · 60s customer patience

  100 arriving in the same instant    60/100 served,  40 failed, median wait 32s
  100 spread over one minute          96/100 served,   4 failed, median wait 14s
```

**Spread is what decides it.** Simultaneous arrival is a load test, not
traffic; a minute of spread already serves 96%.

The failure is arithmetic, not tuning:

```
100 clients x 3s        = 300 client-seconds of work
4 profiles in parallel  =  75s wall clock, minimum
customers wait 60s      -> everyone past that fails
```

No configuration fixes 75 > 60. Only three things move it.

## The three levers

| lever | effect | cost |
|---|---|---|
| **poll faster** | 41 → 60 served (done, below) | free |
| more profiles | linear | expensive, and 100 uniform identities is itself a detection signal |
| **async queue** | 100/100 | a subsystem |

### Done: `WAIT_INTERVAL` 5s → 1s

A waiting run re-checked for a free profile every 5 seconds, so a freed profile
sat **idle for up to 5s** before anyone noticed. That made sense with one
profile and long runs; with several profiles and short runs it threw away half
the pool.

```
  WAIT_INTERVAL=5s ->  39/100 served
  WAIT_INTERVAL=1s ->  60/100 served
```

**+50% on the worst case from one number.** Not lower than 1s — below that the
polling itself becomes Redis load and the win is already banked.

### Not done, deliberately: the async job queue

It is designed ([SCALING-DESIGN.md](SCALING-DESIGN.md) §3) and **not built**,
because what it buys is narrower than it first appears.

It does not add throughput — there is plenty. It converts the one bad case from
*"40 customers get an error"* into *"40 customers wait 30 seconds"*. That is
genuinely worth having, and it is worth having **when bursts are observed**,
not before: its fairness rules and timeouts need real arrival patterns to tune
against, and inventing them from a load test would bake in guesses.

**Build it when:** the logs show clients hitting `VaultEmpty`, or a burst
pattern appears (a daily dashboard refresh, a scheduled customer job).

## What to instrument first

None of these is measured in production today, and every decision above depends
on them:

- **lease wait time** — the number that says whether bursts are real
- **`VaultEmpty` count** — how often a customer is actually turned away
- **cache hit rate** — how much of the load never reaches Pinterest
- **429s from Pinterest** — never observed once in this project; worth knowing
  if that changes

Instrument before buying accounts. The expensive lever should be pulled last
and with evidence.


---

## ⚠️ The rule that protects the accounts themselves

**A run must never outlive its lease.**

The vault hands out a profile with a `SET NX` lease that expires after
`LEASE_TTL` (900s), so a crashed run cannot hold a profile forever. But that
same expiry means a run taking LONGER than the TTL loses its lease mid-flight —
and the vault then hands that profile to somebody else. **Two runs, one
Pinterest session, two IPs.** That is how an account gets flagged.

It was reachable from customer input: `maxRequests` had **no maximum at all**.
`maxRequests: 5000` is a ~5000s run against a 900s lease.

Now capped at **400** — half the TTL at a measured ~1s/request, so even a run
averaging 2s/request stays inside. **Refused, not clamped:** a customer who
asked for 5000 and quietly got 400 would read the short result as "Pinterest
had no more", which is the wrong answer wearing the right shape.

### The pool is smaller than it looks — on purpose

`REQUIRE_PROXY` (default on) refuses to lease a profile that has no proxy, so
the pool size that matters is **profiles with an exit IP**, not profiles in
Redis.

```
2026-08-20, live pool:   6 profiles usable
                         5 AdsPower, each on its own Webshare US IP
                         1 Chrome extension, no proxy   -> now refused

                         5 usable
```

That is a real 17% cut in concurrency, taken deliberately. An unproxied profile
exits from whatever host the run is on, which in a mixed pool means the same
customer's requests come from a datacentre IP one run and the operator's home
connection the next — and one Pinterest account's cookies get replayed from an
address they were never born behind. **A smaller pool delays a run; an
unproxied identity risks the account.** The refusal is a skip, not an eviction,
so the profile stays visible in `python -m src.status` with the reason attached.

Every profile added from here needs a proxy *before* its first login — but not
a proxy of its own. Since 2026-08-20 **two profiles may share one exit IP**
(`MAX_PROFILES_PER_PROXY`, operator's call), so the ceiling is:

```
6 US proxies x 2 = 12 concurrent US accounts
```

That is the cheap way to grow this number, and it is cheap because of an
asymmetry: one ACCOUNT seen from two IPs reads as a stolen session, while two
different accounts from one IP reads as a household. Pinterest cannot treat the
second as fraud without banning families.

Two is a ceiling, not a starting point — three or more on one
residential-looking address is a farm. Profiles are spread one-per-proxy before
any proxy is doubled, so a pool that comfortably fits never shares.

The cost is concentration, not identity: two profiles sharing an address can be
leased at once, so that IP can carry double the request rate. At 3-38s per run
that is fine, and it is the first thing to revisit if 429s ever appear — which
they never have in this project.

### A crash does NOT ban an account

Worth stating plainly, because the intuition runs the other way. A crashed run
**strands** its profile — the account sits unused until the lease expires. That
is the over-cautious failure, not the dangerous one. Observed during testing:
three killed threads left all three profiles locked with ~90s to run.

The cost is availability, not accounts: 15 minutes of a profile being idle for
a run that needed 3-38 seconds.

### Still open: lease renewal

The proper fix for the stranding is a heartbeat — hold a short TTL (~60s) and
refresh it every few seconds while the run is alive. A crash stops the
heartbeat and frees the profile in seconds; a long run keeps renewing and never
loses it. Same guarantee, ~15x faster recovery, and it would make the 400 cap
unnecessary.

**Not built.** Lowering `LEASE_TTL` WITHOUT renewal would be actively
dangerous — it creates exactly the mid-flight expiry the cap above exists to
prevent. Renewal and a lower TTL must land together or not at all.
