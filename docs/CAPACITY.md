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
