# Deploy runbook

**For whoever deploys this — human or agent, in a fresh session.** Read this
top to bottom before running anything. `docs/DEPLOY.md` has the reasoning; this
is the order of operations.

Branch: **`main`**. It is the only branch. Both deploy targets come from it.

## State as of 2026-08-26 — read this before the runbook

| | |
|---|---|
| Step 1 GitHub | ✅ **done** — clone **`git@github.com:0xmadx/pinterest-apify.git`** (private). That name is canonical |
| Step 2 Upstash | ⚠️ database exists, **copy is stale**, writer still points at the lab. This is the blocker |
| Steps 3–6 | not started |
| The gate | **544 checks** across seven suites (not 529 — older docs and the architecture artifact still say 529) |
| The lab vault | 21 keys, 7 profiles, **6/6 usable**, all `ads_*` |

Three things changed on 2026-08-26 that older notes do not reflect:

1. **The Chrome extension is no longer one of our writers.** It belongs to the
   Etsy project and writes into their Redis. Anything describing three writers
   — extension, AdsPower, keepalive — is out of date; we have two.
   [`docs/VAULT_SEPARATION.md`](docs/VAULT_SEPARATION.md).
2. **Two extension-origin profiles were removed from the vault**, so it is
   `6/6` now, not `6/8`. Upstash's copy predates that.
3. **`maxRecords` now reports whether it cut the answer short.** Additive, so
   safe on `buildTag: latest` — no version bump needed.

---

## What this is, in one paragraph

An Apify actor that sells Pinterest Trends data. It reads Pinterest's API using
a real logged-in session that a human earned in a browser — the actor itself
never logs in, never stores a password, and never runs a browser. Sessions live
in a Redis "vault"; the actor leases one, makes its requests with `curl_cffi`
impersonating Chrome, and hands it back.

**The failure mode this whole codebase is built against is a plausible wrong
number, not a crash.** Pinterest answers a signed-out request with normal-looking
*public* data, so a broken session produces a successful-looking run full of the
wrong figures. Most of the guards exist for that.

## Four jobs, three machines

| Job | Where | What runs | How often |
|---|---|---|---|
| Log an account in | operator's laptop | AdsPower, **by hand** | once per account, ever |
| Keep sessions alive | GCP VM | `browsers/keepalive.py` | every 5 min |
| Serve customers | Apify | `src/` | per run |
| The vault | Upstash | Redis | always |

Read [`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md) before answering any
question about AdsPower vs the free stack. The answer differs per job, and it
has been re-litigated more than once.

### The operator's machine is the LAB, not a deploy target

Nothing is deployed *from* the laptop and nothing production runs *on* it. It
holds AdsPower (manual login only), a local `pinterest-redis` on 6380, and the
working copy. Deployment happens from a clone of the GitHub repo, against
Upstash and Apify. When this runbook says "the vault", it means **Upstash** —
the local 6380 is the lab's own copy and is not what customers read.

Corollary: **the Chrome extension is not one of our writers.** It belongs to the
Etsy project and writes into *their* Redis. Our writers are AdsPower today and
`keepalive.py` on GCP after step 5. See
[`docs/VAULT_SEPARATION.md`](docs/VAULT_SEPARATION.md) — read it before pointing
anything at any Redis.

---

## Before you start

```bash
./ship.sh check          # runs the full gate, deploys nothing
```

Expect **16/16 endpoints OK** and **544 checks passing**. If the gate fails,
stop — do not deploy around it. A failing probe usually means Pinterest changed
something, and the fix is to reconcile the code with today's wire, not to skip
the check.

### Credentials needed

| | What | Notes |
|---|---|---|
| GitHub | `gh auth login` + a repo | **Required before GCP** — the VM clones the repo |
| Upstash | a `rediss://` URL | free tier is tight, see below |
| Apify | `apify login` | |
| GCP | `gcloud auth login` | |

Nothing else. `browsers/identities.json` does **not** need copying anywhere —
keepalive reads the vault directly.

---

## Step 1 — GitHub — ✅ DONE 2026-08-26

Pushed to **`git@github.com:0xmadx/pinterest-apify.git`**, private, full
history on `main`. **Clone that one.** Do not work from a copy of the
operator's laptop.

A second private repo, `0xmadx/apify-market-insight-Pinterest-`, is kept as a
**mirror of the same history** — same commits, pushed from the same working
copy. It is a copy, not a fork: never push to it directly and never treat it as
a second source of truth. If the two ever disagree, `pinterest-apify` wins.

Verified at push time: `.env`, `browsers/identities.json` and
`.env.backup-premigration` are all untracked.

⚠️ **One credential IS in history and should be rotated.**
`GO_TOKEN = "super_secret_key_123"` — the *Etsy* project's Go cookie server
bearer token — was committed in `fe4a60c` and removed in `3ad4a80`. Scope is
small: private repo, and the service it opens is bound to `172.31.144.1:8000`
on the operator's own WSL box, not the internet. **Rotate it in the Etsy
project** rather than rewriting history — rotating makes the published value
worthless, which beats hiding it. Everything else in history came back clean:
the `ADS_API_KEY` / `WEBSHARE` hits are all `...` placeholders in docs.

## Step 2 — Upstash (the only real blocker)

⚠️ **A database already exists and its copy is STALE.** It was migrated before
2026-08-26 and holds 23 keys / 8 profiles, including two extension-origin
profiles (`profile_ldu6ypke8`, `profile_p5ewxsodn`) that have since been removed
from the lab vault. The lab is now **21 keys / 7 profiles / 6 usable, all
`ads_*`**. Re-migrate before trusting it; do not deploy against the old copy.

Re-migrating is safe and self-correcting: `copy_key` replaces
`valid_profiles:pinterest` wholesale and the lease path reads that set, so the
two extension profiles drop out of the pool on their own. Their hash keys linger
at the destination as dead weight — invisible and harmless.

Take the `rediss://` URL, then move the vault:

```bash
python -m browsers.migrate_vault --to 'rediss://...' --dry-run   # look first
python -m browsers.migrate_vault --to 'rediss://...'
```

It copies only this project's keys, leaves `cookie:etsy*` alone, and **copies
rather than moves** — the local vault keeps working as a fallback until the new
one is proven.

Then point the **writer** at it, not just `.env`. This is the step that has
failed twice, both times with both halves reporting success:

```bash
# 1. the reader
sed -i "s|^REDIS_URL=.*|REDIS_URL=rediss://...|" .env

# 2. the WRITER — root-owned, and it BEATS .env and the code default
sudo sed -i "s|^REDIS_URL=.*|REDIS_URL=rediss://...|" /etc/adspower/api.env
sudo systemctl start adspower-sync.service
```

Verify **before** trusting it — and the number to expect is `6/6`, not `6/8`:

```bash
python -m src.status        # expect 6/6 usable, TLS resolved per profile
```

If it reads `0/6` while the sync says `6/6 synced`, the writer and the reader
are pointed at different Redises. That is the failure, every time.

⚠️ **Check the command budget.** Keepalive alone is roughly **7,000
commands/day** (288 passes × 8 profiles × ~3 writes). Free tiers commonly cap
near 10k. Either pay — it is cents at this volume — or widen `--interval`, but
never past `PROFILE_MAX_AGE` (900s), which `keepalive.py` already refuses.

## Step 3 — Apify

```bash
./ship.sh apify
```

The gate runs first, then it asks you one question no test can answer:
`buildTag` is `latest`, so **a push changes what existing customers get on
their next run**, minutes later, with no notice. Additive changes are safe; a
renamed field, changed default or removed operation needs a `version` bump
first.

Set `REDIS_URL` as an Actor **secret**, never in `actor.json` — it carries the
vault password.

**Everything else has a working default**, and on Apify two of them resolve
correctly on their own: `DEDUP_SCOPE` falls back to `APIFY_USER_ID`, which the
platform sets, so the seen-set is per customer without being configured; and
`IMPERSONATE` is derived per identity from that profile's own user agent rather
than pinned. Set an env var only when overriding a default on purpose — and if
you do, **write down which and why**, because a deployment nobody can reproduce
from this file is the next outage.

Then smoke it:

```bash
apify call --input '{"operation":"radar","region":"US"}'    # 2 requests, 11 records
```

**You are live after this step.** Everything below saves money; nothing below
earns it.

## Step 4 — GCP (the laptop leaves the critical path)

```bash
gcloud compute scp browsers/deploy_gcp.sh <vm>:~/
gcloud compute ssh <vm>
git clone <your-repo> ~/pinterest-apify
REDIS_URL='rediss://...' bash deploy_gcp.sh
```

It provisions Python, Chromium (no root — `patchright install chromium`),
Windows fonts, and the systemd timer. It refuses to run without `REDIS_URL` and
explains why localhost is wrong there.

Verify:

```bash
systemctl list-timers keepalive.timer
sudo journalctl -u keepalive.service -n 30 --no-pager
python -m src.status
```

## Step 5 — stop AdsPower being the writer (only after step 4 proves itself)

Let GCP hold the pool for a day first. Then:

```bash
sudo systemctl disable --now adspower-sync.timer     # on the WSL box
```

Leave AdsPower **installed**. It is still how a human logs a new account in,
and re-enabling that one timer is the entire rollback.

---

## Adding an account later

1. Create the profile in AdsPower, assign its proxy (`adspower/assign_proxies.py`)
2. **Log in by hand** — never automated, this is Pinterest's most defended flow
3. `python adspower/sync_cookies.py --group pinterest`
4. GCP picks it up on the next 5-minute cycle. **No files are copied.**

---

## Traps that will bite

**Capacity is your profile count.** Six profiles means six concurrent runs. The
limit is arithmetic — `clients × seconds ÷ profiles` — and no configuration
beats it. Scaling means buying proxies and accounts, not tuning.

**`buildTag: latest`** reaches existing customers immediately.

**A test must never assert a count Pinterest owns.** Three did; they failed on
a day nothing in this repo changed, because a vertical returned 16 categories
instead of 19. Assert the response's own totals instead.

**Absent is not zero.** A missing field is `None` with a reason, never `0` and
never `False`. Fabricating a value to make a check pass is the one thing this
codebase is most careful about.

**Run the release gate in order.** `probe_endpoints` rewrites the fixtures the
suites read, so the tests must run *after* it. `ship.sh` already does this.

**A service's `EnvironmentFile` silently beats `.env` and every code default.**
This cost 90 minutes of a dead pool on 2026-08-26. The vault moved to
`pinterest-redis` (6380); `.env` was updated, the hardcoded fallback in
`sync_cookies.py` was updated — and `/etc/adspower/api.env`, root-owned and
unreadable without sudo, still said 6379. It won.

Both halves reported success the whole time:

    sync_cookies   6/6 synced to the vault      (into the OLD container)
    src.status     0/8 usable right now         (reading the NEW one)

Nothing surfaced the mismatch, because neither side can see the other. When
you change where the vault lives, grep for the URL in **all four** places:
`.env`, the code default, any `EnvironmentFile`, and the systemd unit itself.

**From WSL, use `127.0.0.1`, not the gateway IP, to reach a Docker port.**
Measured 2026-08-26: `172.31.144.1:6380` and `127.0.0.1:6380` reached
DIFFERENT Redis instances from inside WSL — dbsize 8 versus 23. A marker key
written through the gateway was invisible to the container. Docker Desktop's
WSL integration forwards published ports to `127.0.0.1` inside the distro, and
that is the path that actually lands. The gateway happened to work for 6379,
which is exactly why it was trusted for 6380.

**A writer that replaces another must not drop fields the old one preserved.**
`write_cookies` replaced a POST to the Go cookie server. The Go server HSET
`user_agent` only when it had one, so a UA captured by an earlier run SURVIVED
a later run that started no browser — and `sync_one` says so in a comment. The
replacement wrote `user_agent or ""` unconditionally and wiped every UA in the
pool. The vault then refused all six profiles ("no user_agent"), correctly:
replaying a cookie jar under an unknown browser is the mismatch the session
layer exists to prevent. Fresh cookies, zero usable.

**The code the timer runs may not be the code you edited.** The WSL sync runs
from `~/pinterest-apify/`, a SEPARATE copy of this repo. It had drifted far
enough to be missing `write_cookies` entirely. `deploy_gcp.sh` avoids this by
using `git pull`, so the VM always reports a commit hash you can check against
this repo — do the same anywhere else that runs this code.

**`.gitignore` is `.env*`, not `.env`.** A backup copy called
`.env.backup-premigration` was committed once because the rule matched one exact
name. See `SECURITY_AUDIT.md`.

## If something looks wrong

| Symptom | Look at |
|---|---|
| Actor returns nothing | `python -m src.status` — is the vault fresh? |
| "No leasable profile" | the writer stopped; check `keepalive.timer` |
| Profile refused | no proxy (`REQUIRE_PROXY`) or signed out — status says which |
| Empty dataset, run "succeeded" | seen-set — is `DEDUP_SCOPE` per customer? |
| Probe FAIL | Pinterest changed. Reconcile docs + parsers; do not skip |
