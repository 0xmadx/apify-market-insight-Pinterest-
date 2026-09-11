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
then all 12 offline suites. The probes run BEFORE the suites deliberately: under
`check` they rewrite the fixtures the suites read, so the tests run against
today's wire.

**Only `check` rewrites them.** On `apify` and `gcp` the same 16 endpoints are
still probed and drift still fails the gate, but no tracked file changes. This
was fixed 2026-09-10: rewriting fixtures on every target put a guaranteed
tree-dirtying step in front of the gate's own clean-tree refusal, so the order
below could not complete — it was hand-worked-around twice in one session — and
it broke rollback, where `git checkout <sha> && ./ship.sh apify` must deploy
that commit, not that commit with today's fixtures written over it.

**3. Commit.** The gate refuses a dirty tree for any target but `check` —
production must match a commit you can point at. If `check` rewrote
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

     ⚠️ **`./ship.sh apify` is run by the OPERATOR, at a real terminal — never
     through an agent's Bash.** Line 105 is `read -r -p "... [y/N] " risky`
     under `set -euo pipefail`: with no tty the read hits EOF, the script
     aborts on the spot, and it does so *after* the vault check, the 16 live
     probes and all 14 suites have already run. The visible result is a gate
     that appears to stop for no reason, having spent several minutes and a
     leased account to get there. That prompt is also the compatibility
     question itself, which is the operator's call and not an agent's.

     An agent's job ends one step earlier: **gate green, here is my written
     answer to the breaking-change question and why, now you run it.**
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
