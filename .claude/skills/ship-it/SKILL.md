---
name: ship-it
description: Use for ANY release in pinterest-apify — "ship it", "deploy", "push to Apify", "update the farm", "release", "go live", or after changing src/, browsers/, .actor/ or an actor README. Encodes the full order (preflight, gate, commit, git push, deploy, smoke, verify) and the stop-points that protect customers and the account pool. Not advisory.
---

# Shipping this project

There are **two independent deploy targets** and one rule that binds them:
nothing reaches either without the gate. `ship.sh` already enforces most of
this; your job is to run it in the right order and not skip the steps it
cannot enforce for you.

| Target | Gets | Who it affects | Command |
|---|---|---|---|
| **apify** | `src/` only | paying customers, **immediately** (`buildTag: latest`) | `./ship.sh apify` |
| **gcp** | `browsers/` only | the session farm that keeps accounts alive | `./ship.sh gcp` |

A change to `browsers/` cannot affect a customer; a change to `src/` cannot
affect the farm. Never deploy both "just in case" — deploy what changed.

## The order, every time

**1. Is the world ready?** `./preflight.sh` — changes nothing, safe any time.
It answers what no test can: tooling installed, accounts authenticated, and
**the vault actually being written to**. A vault that is populated but frozen
reads as healthy and is not.

**2. Is the code right?** `./ship.sh check` — vault, then 16 live endpoints,
then every offline suite. The probes run BEFORE the suites deliberately: they
rewrite the fixtures the suites read, so the tests run against today's wire.

**3. Commit.** The gate refuses a dirty tree for any target but `check` —
production must match a commit you can point at. If the probes rewrote
`probes/results/`, commit that separately as a `chore(probes)`.

**4. `git push origin main` — DO NOT SKIP THIS.**

   This is the step that has no automation and is the one that silently
   breaks things. `ship.sh gcp` updates the farm by running `git fetch` +
   `git pull --ff-only` **on the VM**, so the farm can only ever receive what
   GitHub has. Measured 2026-09-10: local `main` was 11 commits ahead of
   `origin/main`, and the VM was at a commit from 2026-08-27 — **41 commits
   and two weeks stale**, missing three `browsers/` fixes including the
   Upstash cost reduction and the fingerprint carry-over. Nothing failed.
   Nothing warned. The farm just quietly ran old code.

   Pushing is also the only backup. Eleven commits of work existed on one
   laptop and nowhere else.

**5. Deploy what changed.**
   - `src/` changed → `./ship.sh apify`. It will ask the one question the
     gate cannot: *could a customer's existing code break if this landed
     silently tonight?* Renamed/removed field, changed default, removed
     operation → answer yes, bump `version` in `.actor/actor.json` first.
     Additive → safe on `latest`. It then pushes and **smokes the deployed
     actor**, printing rollback instructions if that fails.
   - `browsers/` changed → `./ship.sh gcp` (needs `GCP_VM`, optionally
     `GCP_ZONE`).

**6. Verify against reality, not against the push output.**
   - Apify: `ship.sh apify` already smokes. A zero-record success is a
     FAILURE here and is reported as one.
   - GCP: the deploy prints the VM's new commit — **read it and check it
     matches `git rev-parse --short HEAD`**. This is the step that would have
     caught the two-week drift. Then `python -m src.status` to confirm the
     pool is still being written to.

## Stop-points — not yours to decide

- **Never make an Actor public or submit it for Store review.** Publishing to
  the Store is the operator's decision alone, stated explicitly and more than
  once. Deploying a build is not publishing.
- **Never deploy without the operator saying so in this session.** A request
  to prepare a release is not a request to run it. Report and stop.
- **Never `SKIP_SMOKE=1`** unless the operator asks. It leaves the push live
  and unverified, which is worse than not pushing.
- **Never deploy a dirty tree**, and never work around the gate's refusal.

## When something is wrong

- Gate fails on a count Pinterest owns (item counts, row counts) → that is a
  stale assertion, not a regression. Fix the test to assert the invariant.
  Confirm it fails on a clean tree first, so you are not blaming your change.
- Smoke fails after a push → the push IS live. Roll back by deploying the last
  good commit: `git checkout <sha> && ./ship.sh apify`.
- Farm looks fresh but is old → compare the VM's HEAD to yours. "It is
  running" and "it is running this code" are different claims.
