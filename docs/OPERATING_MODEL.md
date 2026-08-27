# The operating model — who does what, and why

Written 2026-08-25 from the operator's own description, because "which browser
tool are we using" kept getting re-litigated with a different answer each time.
The answer depends entirely on **which job** is being asked about, and there
are four jobs, not one.

## The four jobs

| # | Job | Where | Tool | How often |
|---|---|---|---|---|
| 1 | **Log a new account in** | operator's laptop | AdsPower (manual, by hand, behind that account's proxy) | once per account, ever |
| 2 | **Keep sessions alive** | GCP VM | `browsers/keepalive.py` (DrissionPage) | every 5 min, forever |
| 3 | **Sell the data** | Apify | `src/` — curl_cffi, no browser | per customer run |
| 4 | **Develop and fail over** | laptop / WSL | whichever of the above is being tested | as needed |

The vault (Upstash Redis) is the only thing all four touch. That is the whole
architecture: **one shared table, four independent writers and readers.**

```
  1. LOGIN (manual, rare)          2. KEEPALIVE (automatic, forever)
     laptop + AdsPower                GCP VM + DrissionPage
     human clicks, signs in           no human, every 5 minutes
              │                                  │
              └──── cookies + proxy ────▶ ┌──────────────┐
                                          │   Upstash    │
                                          │  the vault   │ ◀── 3. ACTOR (Apify)
                                          └──────────────┘        curl_cffi
```

## Why AdsPower stays, and what it is NOT for

AdsPower is **a login station and a spare tyre.** It is not the production
session keeper any more — GCP is.

It stays installed because:

- **Signing in is the one thing that cannot be automated safely.** Pinterest's
  login flow is its most defended surface, and a flagged login burns the
  account immediately. A human doing it by hand, behind the right proxy, once
  per account, is the low-risk path. (This is Stage 4 in
  `browsers/README.md` — deliberately never built.)
- **Failover.** If GCP breaks, sessions must not go stale within 15 minutes
  (`PROFILE_MAX_AGE`).
- **A test bench** for new features before they reach the VM.

⚠️ **For failover, reach for `keepalive.py` locally — not AdsPower.** It is
the same code the VM runs, against the same `identities.json`, so a failover is
one command on the laptop rather than a different system with different
behaviour. AdsPower's own sync (`adspower/sync_cookies.py`) still works and is
the second fallback, but running the *same* writer in both places is what makes
the failover boring.

## The free-AdsPower question — corrected

An earlier verdict in `docs/DONUTBROWSER_EVAL.md` said "don't switch, it's
$99/mo". **That was answering the wrong question.** It assumed AdsPower needed
to launch profiles programmatically on a schedule. Under the model above it
does not — GCP does that, with our own free code.

What job 1 actually needs, and what Donut Browser's **free** tier gives:

| Need | Donut Browser free |
|---|---|
| Unlimited local profiles | ✅ |
| A proxy per profile, with credentials | ✅ (`create_proxy`, `update_profile` — no paywall in the source) |
| Launch by hand, from the GUI, to sign in | ✅ |
| Get the cookies back out | ✅ `export_cookies` (json / netscape) |
| Launch programmatically on a timer | ❌ $99/mo — **and not needed** |

The one real constraint: `export_cookies` is a **Tauri command, not a REST
endpoint** (`src-tauri/src/lib.rs`, not in the `api_server.rs` route table). So
cookies come out through the GUI's export button, or by reading the profile
directory — not over HTTP. For a job that happens once per account, that is
fine.

**So Donut Browser is a viable free replacement for AdsPower's real role.**
Worth trialling on the next new account; not worth migrating the existing eight
for.

## What still costs money

| | Cost | Why it is unavoidable |
|---|---|---|
| Webshare proxies | existing | one exit IP per account is the whole safety model |
| Upstash | free tier is tight — see below | the vault must be reachable by both GCP and Apify |
| GCP VM | ~$25/mo, covered ~12 months by the $300 credit | somewhere that is not the laptop |
| AdsPower | **$0 once job 1 moves to a free tool** | |

⚠️ Upstash volume: keepalive alone is roughly **7,000 commands/day** (288
passes × 8 profiles × ~3 writes). Free tiers commonly cap near 10k. Either
budget for the paid tier — cents at this volume — or widen the keepalive
interval, but never past `PROFILE_MAX_AGE` (900s), which `keepalive.py` already
refuses.

## Shipping changes

One direction only, and it does not change with this model:

```
develop + test locally  →  release gate  →  push  →  smoke the real thing
```

Two targets now instead of one, and they are independent:

- **Apify** gets `src/` only. `.dockerignore` excludes `browsers/` and
  `adspower/`; the image is ~335MB precisely because no browser is in it.
- **GCP** gets `browsers/` and never gets pushed to Apify.

A change to `browsers/` cannot affect a customer, and a change to `src/` cannot
affect the session farm — which is the point of keeping them apart.
