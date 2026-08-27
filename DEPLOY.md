# Deploy runbook

**For whoever deploys this — human or agent, in a fresh session.** Read this
top to bottom before running anything. `docs/DEPLOY.md` has the reasoning; this
is the order of operations.

Branch: **`main`**. It is the only branch. Both deploy targets come from it.

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
| Keep sessions alive | **local WSL (Ubuntu)** | `adspower-sync.timer` | every 5 min |
| Serve customers | Apify | `src/` | per run |
| The vault | Upstash | Redis | always |

**The session keeper is local WSL, not GCP.** GCP (`browsers/keepalive.py`,
Step 4) is the eventual replacement and remains optional — it saves the
AdsPower subscription and takes the laptop off the critical path, but it earns
nothing. Until it is cut over, the laptop being asleep means the pool goes
stale in 15 minutes (`PROFILE_MAX_AGE`).

### Why a local writer is enough — and the one thing it requires

The vault is a network Redis, so **where the writer runs is irrelevant to the
reader.** WSL writes to Upstash over TLS; Apify reads the same table. Neither
knows about the other, nothing is tunnelled, and no port on the WSL box is ever
exposed. This is the "one shared table, independent writers and readers" model
in [`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md).

What it requires is the part that is easy to miss: **migrating the vault copies
the data, it does not move the writer.** Both must point at the same Redis, or
the actor reads a table nobody is refreshing — a frozen snapshot that goes
stale in 15 minutes and then serves nothing.

Measured on 2026-08-26, immediately after the Upstash migration:

```
Upstash          0/8 usable   ages ~13,400s   ← what Apify would have read
local 6380       6/6 usable   ages ~280s      ← where WSL was still writing
```

Both are "the vault". Only one was alive. See § Point the WSL writer at Upstash.

Read [`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md) before answering any
question about AdsPower vs the free stack. The answer differs per job, and it
has been re-litigated more than once.

---

## Before you start

```bash
./ship.sh check          # runs the full gate, deploys nothing
```

Expect **16/16 endpoints OK** and **529 checks passing**. If the gate fails,
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

## Step 1 — GitHub

History has been scanned (see [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)): no
secret was ever committed. Re-run the scan if in doubt; do not skip it, because
a push publishes every commit ever made.

**Make the repo private.** `docs/wire/` is the reverse-engineered endpoint
corpus. That research is the actual product.

```bash
gh auth login
gh repo create pinterest-apify --private --source=. --remote=origin --push
```

## Step 2 — Upstash (the only real blocker) — ✅ done 2026-08-26

Database `pinterest-apify-vault` (global, `us-east-1` primary) created via the
Upstash API. `.env` points at it; the local vault was migrated, not moved, so
`redis://localhost:6380/0` still works as a fallback:

```bash
python -m browsers.migrate_vault --to 'rediss://...' --dry-run   # look first
python -m browsers.migrate_vault --to 'rediss://...'
```

It copies only this project's keys, leaves `cookie:etsy*` alone, and **copies
rather than moves** — the local vault keeps working as a fallback until the new
one is proven.

Verified:

```bash
python -m src.status        # 6/8 usable, TLS resolved per profile — confirmed
```

⚠️ **Check the command budget.** Keepalive alone is roughly **7,000
commands/day** (288 passes × 8 profiles × ~3 writes). Free tiers commonly cap
near 10k. Either pay — it is cents at this volume — or widen `--interval`, but
never past `PROFILE_MAX_AGE` (900s), which `keepalive.py` already refuses.

## Step 2b — Point the WSL writer at Upstash

**Do this before Step 3.** Until it is done the Upstash vault is a frozen
snapshot, and a deployed actor reading it will fail with `VaultEmpty` — or
worse, lease a stale profile.

The syncer resolves its target as `--redis-url` → `$REDIS_URL` → a
WSL-to-Windows-host fallback (`sync_cookies.py`). systemd supplies it from
`/etc/adspower/api.env`, so that one line is the switch:

```bash
wsl -d Ubuntu
sudo nano /etc/adspower/api.env        # REDIS_URL=rediss://default:...@...upstash.io:6379
sudo systemctl restart adspower-sync.timer
sudo systemctl start adspower-sync.service   # run one pass now, don't wait 5 min
journalctl -u adspower-sync.service -n 20 --no-pager
```

Verify from Windows — this is the check that matters, and the ages are the
answer:

```bash
.venv/Scripts/python.exe -m src.status     # expect ages < 300s, N/N usable
```

If ages keep climbing, the writer is still pointed somewhere else. Note the
fallback in `sync_cookies.py` is port **6379**, while this project's local
container is **6380** — a missing `REDIS_URL` does not error, it quietly writes
to the wrong place.

**Latency is not a concern, but the timeout was worth checking.** The syncer
opens Redis with `socket_connect_timeout=3`. Measured WSL → Upstash over TLS:
**min 446ms / median 483ms / max 517ms** — about 6× headroom. No change needed;
recorded so a future timeout failure is recognised as drift rather than
debugged from scratch.

⚠️ **Command budget.** Six profiles every 5 min at ~3 writes each is roughly
**5,200 commands/day**, against a 10k/day free tier. That leaves ~4,800/day for
actual customer runs, which is fine for testing and thin for launch. Widen the
timer interval or pay before it matters — but never past `PROFILE_MAX_AGE`
(900s).

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
vault password. This is the step nothing local can verify, and the most common
reason a deploy that passed every check still fails in the cloud.

`ship.sh apify` now **runs the smoke itself** rather than printing it, because
a printed command is the one that gets skipped:

```bash
./smoke.sh              # radar — 2 requests, the cheapest possible proof
./smoke.sh shopping     # heavier, once radar passes
```

It calls `run-sync-get-dataset-items` — the same endpoint an integrator writes
— and **treats a zero-record success as a failure**. A run that returns 200
with an empty dataset reads as "Pinterest has nothing trending" when it really
means the vault was empty or the session was signed out. That is this project's
defining failure mode, so the smoke refuses to pass it. It also fails if the
records are `_demo` fixtures rather than live data.

`SKIP_SMOKE=1` bypasses it and says so loudly. There is no good reason to use it.

**Rollback**, since the push is live the moment it lands:

```bash
git checkout <last-good-sha> && ./ship.sh apify
```

There is no "undo" on Apify — rolling back is pushing the previous commit
forward. Know the SHA before you push, not after.

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
