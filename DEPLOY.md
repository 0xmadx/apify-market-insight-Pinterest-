# Deploy runbook

**For whoever deploys this — human or agent, in a fresh session.** Read this
top to bottom before running anything. `docs/DEPLOY.md` has the reasoning; this
is the order of operations; [`docs/DEPLOY-LOG.md`](docs/DEPLOY-LOG.md) is the
record — what was actually provisioned, its resource names, and the evidence
behind each "done".

Branch: **`main`**. It is the only branch. Both deploy targets come from it.

## The deployment model — what, why, and when

Everything below only makes sense on top of these five facts. They are the
model; the steps are just consequences of it.

**1. There are two environments, and no staging.** The operator's laptop is the
*lab*; Apify + Upstash + the GCP VM are *production*. Nothing sits between them.
The closest thing to a staging run is `./run_local.sh`, which executes the real
actor on Linux against the real vault — a rehearsal, not an environment.

**2. Deployment flows one way, always.** The repo is the source of truth, the
deployed actor is a snapshot of it, and **nothing is ever edited on Apify.**

```
edit locally  ->  ./preflight.sh  ->  ./ship.sh check  ->  ./ship.sh apify  ->  ./smoke.sh
```

**3. Every push IS a release.** `.actor/actor.json` carries `buildTag: latest`,
so a push reaches existing customers on their next run, minutes later, with no
announcement and no opt-in. There is no "deploy to prod later" step to forget —
pushing *is* that step.

**4. That makes one question the whole of release management.** Before any
push, answer it in writing:

> *Could a customer's existing code break if this landed silently tonight?*

| Answer | What to do |
|---|---|
| No — new field, new optional input, bug fix, better error | ship on `latest` |
| Yes — renamed field, changed default, removed operation, changed meaning | bump `version` in `.actor/actor.json` FIRST, leave the old version running |

The compatibility contract customers are held to is in
[`docs/API.md`](docs/API.md) § Compatibility. Read it before deciding, because
what you are allowed to change is defined there, not here.

**5. There is no undo on Apify. Rollback is a forward push.**

```bash
git checkout <last-good-sha> && ./ship.sh apify
```

So **know the good SHA before you push, not after.** `git log --oneline -1`
takes two seconds and is the entire discipline.

### When to deploy

| Trigger | Then |
|---|---|
| A bug fix or additive change, gate green | ship it — no ceremony, that is what `latest` is for |
| A breaking change | `version` bump first, then ship |
| A red gate, or a probe at FAIL | **stop.** A failing probe means Pinterest moved; reconcile code with today's wire, never deploy around it |
| An empty or stale vault | stop. The actor will fail loudly with `VaultEmpty`, which is correct, but you will have spent a deploy to learn it |
| Friday afternoon, no one watching | it is a session-backed scraper on someone else's API. Ship when you can watch the smoke test |

---

## Where things stand

| | |
|---|---|
| Step 1 GitHub | ✅ **done** — clone **`git@github.com:0xmadx/pinterest-apify.git`** (private). That name is canonical |
| Step 2 Upstash | ✅ **done** — re-migrated (21 keys, 6 profiles, all `ads_*`), writer repointed and verified |
| Step 3 Apify | ✅ **done 2026-08-27** — actor `yMtXPlrwLkTb9Zzpx`, build 1.0.4 on `latest`, `REDIS_URL` set as a secret. Smoke passed: 11 live records |
| Step 4 GCP | ✅ **done** — `keepalive.timer` live on `pinterest-keepalive`, confirmed writing to Upstash |
| Step 5 retire AdsPower writer | ✅ **done 2026-08-27** — `adspower-sync.timer` disabled; GCP verified as sole writer |
| The gate | **555 checks** across seven suites, 16/16 endpoints — last green 2026-08-27 |
| The vault | Upstash, **6/6 usable**, written by GCP alone on a 5-minute timer |

**✅ THE DEPLOY IS COMPLETE.** All five steps are done. What follows is the
release loop (Part A) and maintenance, not first-time setup.

**GCP is now the only writer.** The overlap was the rollback window for Step 5
and it is spent. Verified before switching AdsPower off, and again seven minutes
after: all six in-pool profiles refreshed inside one cycle (211-225s). Rollback
is `sudo systemctl enable --now adspower-sync.timer` on the WSL box.

⚠️ **Count the POOL, not the keys.** The check that verified this cutover first
reported "NOBODY IS WRITING" because it scanned every `cookie:pinterest:*` key
and took the oldest — 653,746s. That was `ads_k1fymck0`, an orphan with a cookie
key that is deliberately absent from `valid_profiles`, plus two extension-era
leftovers. The actor only leases what is IN THE SET, so that is what freshness
means. `src.status` was printing `6/6 usable` two lines above the false alarm.

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

## Credentials needed

| | What | Notes |
|---|---|---|
| GitHub | `gh auth login` + a repo | **Required before GCP** — the VM clones the repo |
| Upstash | a `rediss://` URL | free tier is tight, see below |
| Apify | `apify login` | |
| GCP | `gcloud auth login` | |

Nothing else. `browsers/identities.json` does **not** need copying anywhere —
keepalive reads the vault directly.

---

# Part A — the release loop

**This is the part you do forever.** Once the infrastructure in Part B exists,
shipping a change is these four commands and nothing else. Read this section
even if the project is already live; Part B is mostly history.

```bash
./preflight.sh                      # 1. is the environment sane?
./ship.sh check                     # 2. is the code sane?
./ship.sh apify                     # 3. gate again, then push
./smoke.sh                          # 4. does the DEPLOYED thing work?
```

Each answers a different question, and none substitutes for another:

| | Proves | Fails when |
|---|---|---|
| `preflight.sh` | tooling, auth, `.env` hygiene, and that a **live writer** is filling the vault | credentials missing, `REDIS_URL` local, pool stale |
| `ship.sh check` | 16/16 live endpoints, then 555 offline checks | Pinterest moved, or you broke something |
| `ship.sh apify` | the push itself — asks the `buildTag` question first | dirty tree, red gate |
| `smoke.sh` | the **deployed** actor returns real records; a zero-record success is a FAILURE | vault unreachable from Apify, secret unset |

Only `smoke.sh` touches the deployed thing. Nothing local can tell you the
cloud actor works, and the most common cause of "passed everything, still
broke" is `REDIS_URL` not set as an Actor secret.

**Order is load-bearing.** `ship.sh check` runs `probe_endpoints` *before* the
suites, because probing rewrites the fixtures the suites then read. Run the
tests first and you test today's code against yesterday's wire. `ship.sh`
already sequences this; do not hand-run the pieces out of order.

**Rollback:** `git checkout <last-good-sha> && ./ship.sh apify`. There is no
undo — see the model above.

---

# Part B — first-time provisioning

**Mostly done.** Steps 1, 2 and 4 are complete; only Step 3 remains. Full
evidence for each in [`docs/DEPLOY-LOG.md`](docs/DEPLOY-LOG.md) — what was
built, its resource names, and what each check actually proved. Re-read these
steps only when rebuilding a piece from scratch.

## Step 1 — GitHub — ✅ DONE 2026-08-26

Pushed to **`git@github.com:0xmadx/pinterest-apify.git`**, private, full
history on `main`. **Clone that one.** Do not work from a copy of the
operator's laptop.

**One repo, one remote, one branch.** There was briefly a second private repo,
`0xmadx/apify-market-insight-Pinterest-`, kept as a mirror. It is no longer a
push target: two remotes with nothing enforcing sync is a drift waiting to
happen, and a stale duplicate of a private commercial scraper is how a future
session clones the wrong thing. Both were identical at `74d7fba` when the mirror
was dropped, so nothing was lost — that repo is frozen at that commit and should
be deleted on GitHub.

If you see any remote here other than `origin -> pinterest-apify`, that is the
defect, not a feature.

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

## Step 2 — Upstash — ✅ DONE 2026-08-27

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

## Step 3 — Apify — ⬅ THE REMAINING BLOCKER

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

## Step 4 — GCP (the laptop leaves the critical path) — ✅ DONE 2026-08-27

Live: project `pinterest-keepalive`, VM `pinterest-keepalive` (e2-medium,
Ubuntu 24.04, `us-central1-a`), `keepalive.timer` active and enabled.

**Clone first; do not scp `deploy_gcp.sh` from the Windows checkout** — see the
CRLF trap below. The repo is private, so the VM needs its own credential:

```bash
# on the VM — the private half never leaves it
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
# add the .pub under repo Settings → Deploy keys, READ-ONLY
git clone git@github.com:0xmadx/pinterest-apify.git ~/pinterest-apify
cd ~/pinterest-apify && REDIS_URL='rediss://...' bash browsers/deploy_gcp.sh
```

A deploy key is scoped to one repo; a personal access token would hand the VM
the whole account.

### No public IP, SSH through IAP

The VM needs outbound (apt, PyPI, the Chromium download, Upstash) and **no**
inbound. GCP's `default-allow-ssh` ships open to `0.0.0.0/0`, and this host
holds the Upstash password and every live Pinterest session:

```bash
gcloud compute routers create keepalive-router --region=<r> --network=default
gcloud compute routers nats create keepalive-nat --router=keepalive-router \
  --region=<r> --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges
gcloud compute instances delete-access-config <vm> --access-config-name="external-nat"
gcloud compute firewall-rules update default-allow-ssh --source-ranges=35.235.240.0/20
gcloud compute firewall-rules delete default-allow-rdp        # Linux VM
```

Then reach it with `gcloud compute ssh <vm> --tunnel-through-iap`. Note
`gcloud compute scp` on Windows uses PuTTY's `pscp`, which does **not** expand
`~` — destinations must be absolute (`vm:/home/<user>/`).

### Verifying a GCP rebuild

```bash
systemctl list-timers keepalive.timer
sudo journalctl -u keepalive.service -n 30 --no-pager
python -m src.status                       # expect 6/6, ads_* only
python -m browsers.fingerprint             # reads the VAULT, runs on the VM
```

Two checks that look redundant and are not:

**Prove GCP is the WRITER, not just that the vault looks fresh.** With both
writers live, a low heartbeat only proves *someone* wrote. Attribute by content:
the two leave different cookie counts for the same profile, so compare the
per-profile counts against each writer's known signature. Measured 2026-08-27:
Upstash showed GCP's 9/7/13, not AdsPower's 11/8/11.

⚠️ **On the VM, hand `browsers.fingerprint` the environment first.** There is
no `.env` there: the vault URL lives in `/etc/pinterest-keepalive/env`, and
systemd supplies it to `keepalive.service` through `EnvironmentFile`. An
interactive shell inherits none of that, so `Config` falls back to its localhost
default and the tool reports a Redis nobody asked for:

```
cannot reach Redis — Error 111 connecting to localhost:6380. Connection refused.
```

That message is about the *default*, not the vault, on a host whose service is
refreshing profiles correctly every five minutes. Source the env file and it
works:

```bash
cd ~/pinterest-apify
set -a; . <(sudo cat /etc/pinterest-keepalive/env); set +a
./.venv/bin/python -m browsers.fingerprint --driver drission --profiles 3
```

Measured on the VM 2026-08-27 — three profiles, three machines:

```
ads_k1fx40wf  1280x720  cores=4   ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11
ads_k1fy47um  1440x900  cores=6   ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11
ads_k1fy6dnh  1280x720  cores=12  ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11

DISTINCT 3/3 on the strong signals · STABLE 3/3 across launches · PASS
```

`Direct3D11` on a Linux VM is the Windows claim holding up, and the fonts are
what keep it holding. Note these are still the **synthetic** shapes: no profile
carries `fingerprint_json` yet, so `fp_basis` reads `synthetic` until each is
re-synced once with `--ua-mode always` (see Step 5).


**Prove the exit IP is the proxy, not the VM.** Every profile must exit through
its own proxy; none may show the VM's NAT address. `keepalive` checks this
before it writes, because Chromium falls back to a direct connection when a
proxy fails and every other signal still reports success.

⚠️ **If fonts are missing, install them by copying — do not trust apt.**
`ttf-mscorefonts-installer` downloads separately in its postinst and can leave
you with `ii` and an empty directory. `deploy_gcp.sh` now counts resolvable
families and warns below 5; the full incident is in
[`docs/DEPLOY-LOG.md`](docs/DEPLOY-LOG.md).

```bash
gcloud compute scp /c/Windows/Fonts/{arial,arialbd,georgia,tahoma,verdana,times,comic,impact,cour,trebuc}.ttf   <vm>:/home/<user>/.local/share/fonts/ --tunnel-through-iap
gcloud compute ssh <vm> --tunnel-through-iap --command 'fc-cache -f'
```

## Step 5 — stop AdsPower being the writer (only after step 4 proves itself)

Let GCP hold the pool for a day first. Then:

```bash
sudo systemctl disable --now adspower-sync.timer     # on the WSL box
```

Leave AdsPower **installed**. It is still how a human logs a new account in,
and re-enabling that one timer is the entire rollback.

---

## Running the account pool

Accounts die. A session gets signed out, a proxy expires, a login gets
challenged. The pool is not a thing you set up once — it is a thing you keep.
Three commands cover the whole life of an account.

### 1. See what is wrong — from anywhere

```bash
python -m src.status
```

Every profile, and for a broken one **why**:

```
ads_k1fy6dnh   cookies=8   age=1240s  [stale 1240s, last error: signed OUT — missing _auth]
```

That last part is the actionable half, and it decides what you do next:

| What it says | What it means | What to do |
|---|---|---|
| `signed OUT — missing _auth` | the account is logged out | re-login by hand, or retire it |
| `exit IP ... is not the proxy` | the proxy is dead; Chromium fell back to a direct connection | fix the proxy, keep the account |
| `no proxy` | never finished being set up | assign one, then sync |
| `stale` with no error | the writer stopped, not the account | check `keepalive.timer` |

⚠️ Before 2026-08-27 that reason existed only in the **VM's journal**. From the
laptop a profile just went quiet, and `src.status` could say "stale" and nothing
more — so every diagnosis started with an SSH session. `keepalive` now writes
the reason into the vault (one extra command, and only on a failure), which is
why it shows up here at all.

### 2. Retire one that is finished

```bash
python -m browsers.identities remove ads_k1fy6dnh
```

Prints what it is about to destroy before it does — these are live login
sessions, and a wrong id costs a manual re-login. Drops the profile from the
pool, its cookies and its lease. One profile at a time and named explicitly;
`clear` is the one that wipes a whole pool, and confusing the two would be
expensive.

### 3. Add a replacement

1. Create the profile in AdsPower, assign its proxy (`adspower/assign_proxies.py`)
2. **Log in by hand** — never automated, this is Pinterest's most defended flow
3. `python adspower/sync_cookies.py --group pinterest` — this is also what
   captures the fingerprint, so run it with a browser start at least once
4. GCP picks it up on the next 5-minute cycle. **No files are copied**, because
   `keepalive` reads the pool from the vault rather than from a snapshot

**Capacity is the profile count**, so this loop is also how you scale: six
profiles means six concurrent customer runs, and no configuration changes that.

### What this deliberately is not

There is no dashboard and no management service. At six accounts a UI would be
another thing to host, secure and patch, to replace three commands. Revisit at
roughly fifty, or when someone other than the operator has to run the pool.

---

## Upstash costs money per command — where it actually goes

Metered per command, free tier near 10,000/day. Counted from the code, not
estimated, at 6 profiles on 5-minute timers:

| | Commands per pass | Per day |
|---|---|---|
| AdsPower sync (`adspower-sync.timer`) | 4 x 6 = 24 | ~6,900 |
| GCP keepalive (`keepalive.timer`) | 1 + (3 x 6) = 19 | ~5,500 |
| **Both running** | | **~12,400 — over the free tier** |
| One actor run | ~5 to lease, plus cache/seen-set | tens |

**The writers are the whole bill; the readers are noise.** Optimising the actor's
read path saves nothing worth having, and the obvious idea — caching an identity
on Apify and only calling Upstash when it fails — is actively unsafe: the lease
(`SET NX`) is what stops two runs driving one Pinterest session from two IPs,
and a cached jar defeats both that and the 900s freshness rule. Do not.

Two safe levers, in order of size:

1. **Retire the AdsPower writer** (Step 5). Halves it, to ~5,500/day. The
   overlap was always a rollback window, not a steady state.
2. **Do not run the test suite against production.** `tests/test_vault.py` is
   pinned to a LOCAL Redis and ignores `REDIS_URL` deliberately — it writes,
   deletes and SCANs a keyspace, and billing production to test code is the
   kind of cost that never shows up in a review.

Raising the keepalive interval is NOT a third lever: `PROFILE_MAX_AGE` is 900s,
and `keepalive.py` already refuses anything that would let profiles age out.

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

**Never `scp` a shell script from the Windows checkout — clone on the VM.**
Measured 2026-08-27: `deploy_gcp.sh` copied from the laptop died at line 21 with
`set: pipefail: invalid option name`, because bash read `pipefail
`. The
committed blob is clean LF (`.gitattributes` says `*.sh text eol=lf`); it was
the *working-tree* copy that was CRLF, checked out before that rule existed —
git does not re-check-out files when attributes change. It was the only such
file in the repo, and it is the one that got copied. `git clone` on the VM
hands you LF, which is why the runbook clones rather than copies.

**A `.dockerignore` that lists filenames protects only those filenames.**
It named `run_local.sh` and `ship.sh`; `preflight.sh` and `smoke.sh` were added
later and silently began shipping inside the production image. Nothing
referenced them, nothing failed — they were just there. Now `*.sh`. Identical
in shape to the `.gitignore` rule below, and found the same way: by listing what
the artefact actually contains instead of trusting the rule.

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
