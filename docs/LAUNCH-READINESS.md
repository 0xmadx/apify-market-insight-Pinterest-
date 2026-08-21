# LAUNCH READINESS — the AdsPower MVP

*Reviewed 2026-08-20. Every number here was measured on the day, by a command
in this repo that anyone can re-run. Where something is an estimate it says so.*

**Verdict: ready for a soft launch. It was not ready this morning.**

Four defects were found during this review, one of them a launch blocker that
would have produced refunds rather than bug reports. All four are fixed and
proven. Two items remain, and both are operator actions rather than code.

---

## What was found

| # | Defect | How it would have shown up | Status |
|---|---|---|---|
| 1 | **Seen-set shared by all customers** | Customer B pays, gets an **empty dataset**, for 7 days | fixed `a0d2071` |
| 2 | **Fill lock survived a failed request** | One error → 30s stall + N duplicate requests at an already-failing endpoint | fixed `e129685` |
| 3 | **Vault leased profiles with no proxy** | ~1 run in 6 exited from the operator's home IP | fixed `11b5773` |
| 4 | **Image shipped the operator toolchain** | AdsPower/Webshare clients + bytecode inside the cloud artefact | fixed `a0d2071` |

### 1 was the blocker, and it is worth understanding why

The seen-set key was `seen:{platform}:{scope}` — no tenant. On Apify every
customer's run reaches one shared Redis, so the second customer to ask a
question got nothing and paid for it.

**This is not a wrong number. It is a missing one, and that is worse here.** A
wrong number gets argued with. An empty dataset reads as "this actor is broken",
and the customer leaves a one-star review instead of a bug report.

The fix turns on an asymmetry that is easy to get backwards:

| | shared? | why |
|---|---|---|
| **response cache** | **yes** — this is the economics | two customers asking the same question in the same hour should cost Pinterest one request. Sharing an *answer* is free. |
| **seen-set** | **no** | it records what has been *delivered*, and delivery is per customer. |

Proven in the real image against the real vault:

```
customer_alice   cache: 3 hits, 0 misses · wire: 0 · done: 11 new, 0 skipped
customer_bob     cache: 3 hits, 0 misses · wire: 0 · done: 11 new, 0 skipped
```

Both served. Zero requests to Pinterest between them.

---

## Measured capacity

`python -m probes.stress` — committed for this review, because CAPACITY.md and
SCALING.md quoted numbers from scratch files nobody kept. It never touches
Pinterest (work is a sleep) and never touches the live pool (throwaway
namespace).

```
6 profiles · 100 clients arriving at once · 3s runs
  100/100 served · 53.6s wall (floor 50s) · p50 wait 24.9s · 16-17 runs per profile
  EXCLUSIVITY ✅ no profile ever held by two runs

6 profiles · 100 clients at once · 10s runs
  42/100 served · floor 167s against 60s patience

6 profiles · 100 clients spread over 60s · 10s runs
  70/100 served
```

**Spread is what decides it, and the limit is arithmetic, not tuning:**

```
clients x seconds-per-run / profiles = minimum wall clock
```

No setting beats that. The honest sustained figure is **profiles x (patience /
run length)** — about **36 concurrent 10-second runs** on six profiles.

Sustained volume is a non-issue: 100 customers at 5 runs/day is 500 runs
against ~9,000/day capacity on the *slowest* operation.

**Cache stampede:** 50 clients on one cold key → **1 request, 0.8s**.
Singleflight holds.

---

## By discipline

**Engineering.** 509 offline checks across seven suites; 16/16 endpoints
answered live today. The codebase's own habit — refuse rather than guess, absent
is not zero, provenance on every number — is real and consistently applied. The
weak spot found was that guards were not always *tested*: the vault lease path
had four refusals and zero tests until today.

**Distributed systems.** Lease exclusivity held across every stress run — the
one invariant that protects the accounts. Singleflight works. The failure path
did not, and now does. **Still missing: lease renewal.** A crashed run strands
its profile for 900s. That is the over-cautious failure, not the dangerous one,
so it is a scaling item and not a blocker.

**Cloud.** The image builds (497MB) and runs the real actor against a
non-localhost Redis, leasing an identity and fetching live. That is the deploy
shape. Two things to know:

- **Each run opens 3 Redis connections** (vault, cache, state). 100 concurrent
  runs = 300. Free serverless tiers commonly cap at 100 — check the plan's
  connection limit, not just its request quota.
- **The GCP credit does not directly solve the Redis problem.** Memorystore is
  VPC-only with no public endpoint, and Apify runs outside your VPC. Using GCP
  means a Compute Engine VM running Redis with TLS and auth — real ops work.
  **Upstash remains the recommendation** for the MVP: `rediss://`, TLS and
  password out of the box, nothing to operate.

**Data.** Every record carries `_meta` with `basis`, `end_date_basis`,
`normalization_scope`, and where relevant a warning against the wrong
comparison ("never compare counts across trends"). Provenance travels *with*
the number rather than living in documentation. This is the strongest part of
the product and it is what a serious buyer will notice.

**Product.** The dedup default is now correct per customer. `fullRescan` exists
for customers who want everything each time.

---

## Before `apify push`

1. **Network-reachable Redis** — the only true blocker. Upstash, point the vault
   writer at it, set `REDIS_URL` as an Actor secret.
2. **Rotate the AdsPower API key.** It has appeared in logs and on screen.
3. Firewall `*:50325` before any public VPS.

## First week after launch

Instrument what every future decision depends on, none of which is measured in
production today: **lease wait time · `VaultEmpty` count · cache hit rate ·
429s from Pinterest** (never observed once in this project's life).

Then, in order of what the numbers will justify: lease renewal → more accounts
→ the async queue.

---

## On the Playwright branch

Worth recording before it starts, because the operator's own note in the Etsy
repo says the opposite conclusion was reached once already: *"remote browser,
not Playwright — Playwright gets blocked."*

That was measured against **Etsy**, not Pinterest, so it does not automatically
transfer. But two things about this codebase do:

- The actor's economics assume **no browser** — `apify/actor-python:3.12` uses
  roughly a quarter of the compute units of a Playwright image. A browser-based
  actor is a different cost structure, not just a different session source.
- Everything downstream of the vault is indifferent to how cookies arrive.
  `Identity` is cookies + UA + exit IP, and any filler that writes those three
  works. **The switch is a writer swap, not a rewrite** — which is exactly why
  shipping the AdsPower MVP now does not foreclose it.

Test the stealth question on its own before betting the session layer on it: a
Playwright profile that Pinterest silently degrades (public data in place of
authenticated data) fails in the one way this codebase is built to catch.
