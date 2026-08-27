# Vault separation — this lab is not the Etsy lab

**Standing rule, and it is not a preference: nothing in this project may read,
write, call, or depend on anything belonging to the Etsy project.** Not its
Redis, not its Go cookie server, not its Chrome extension, not its containers,
not its tokens. If a change needs something from over there, the answer is to
build our own, not to reach across.

This file exists because the reaching-across was never deliberate. Every
coupling found so far arrived as a leftover from the split, sat dormant because
something else happened to be set, and would have failed **silently** — with
both sides reporting success — the day that something else went missing.

---

## Who owns what

| | Ours | Etsy's — do not touch |
|---|---|---|
| Redis | `pinterest-redis` **:6380** | `scraper-redis` **:6379** |
| Cookie writer | AdsPower → `adspower/sync_cookies.py` | Chrome extension → Go server **:8000** |
| Other containers | none | `cookie-server-go`, `python-scraper` |
| Profiles | AdsPower group `pinterest*`, ids `ads_*` | group `etsy*` |

Our actor reads 6380 and nothing else. Our writer writes 6380 and nothing else.

---

## What was removed, 2026-08-26

Three couplings, none of them running, all of them live traps:

| Where | Was | Why it mattered |
|---|---|---|
| `src/config.py` | `REDIS_URL` fell back to `localhost:6379` | an unset env var reads **Etsy's pool** and looks like it worked |
| `run_local.sh` | exported `6379`, via the WSL gateway IP | wrong container *and* the gateway path that reaches a different instance |
| `adspower/sync_cookies.py` | `post_to_vault()` + `GO_SERVER` + `GO_TOKEN` | POSTed to Etsy's server carrying a hardcoded bearer token, in a repo headed for GitHub |

`post_to_vault` was dead — its own docstring said so — but a dead function
pointed at another project's server is a footgun, not a fallback. Re-enabling it
would have written cookies into 6379 while `write_proxy` wrote the proxy into
6380: half an identity in each container, showing up as "never signed in"
rather than "split brain".

`build_payload` deliberately survived it. Its tests assert things the live
writer needs too — `cookie_json` keyed by name, per-domain duplicates
collapsing, platform following the group — so `write_cookies` now builds on it.
The tests moved from covering a hop we no longer take to covering the path that
runs.

Also that day: 22 `*pinterest*` keys deleted from 6379 (its 8 `etsy` keys left
alone), and two extension-era `profile_*` entries removed from **our** 6380,
where they had been sitting stale and proxy-less since the migration.

---

## Expected, not a bug: pinterest keys reappearing in 6379

The Etsy extension is built for **both** sites — its own description says so
and its `host_permissions` include `*://*.pinterest.com/*`. With a Pinterest
tab open it grabs Pinterest cookies and posts them to the Etsy Go server, which
writes `cookie:pinterest:*` into 6379. Measured: deleted, back within minutes.

**Leave them.** That is Etsy's extension writing into Etsy's container. It is
their side reaching at Pinterest, not ours leaking into theirs. Those keys are
orphans — we read 6380, nothing reads them. Deleting them repeatedly is
pointless; the writer is still running. The durable fix is to drop Pinterest
from that extension's manifest and `background.js`, which is **a change in the
Etsy repo** and therefore not ours to make unasked.

---

## Verify

```bash
grep -rn "6379\|GO_SERVER\|cookie_server\|:8000" --include=*.py --include=*.sh src/ adspower/ browsers/
```

Expect nothing but comments. Then:

```bash
python -m src.status        # every profile ads_*, fresh, proxied
```

A `profile_*` id in 6380 means a writer other than AdsPower got in. Any
remaining `etsy` in our code should be an isolation **guard** — `platform_of()`
keeping Etsy profiles out of our pool, `migrate_vault` refusing to touch
`cookie:etsy*` — never a dependency.

## If the vault ever moves again

`REDIS_URL` lives in four places and they do not agree by default:

```
1. systemd EnvironmentFile  (/etc/adspower/api.env)   <- WINS, root-only
2. the process environment
3. .env
4. the code fallback in sync_cookies.py
```

On the **GCP VM** it is a different file with the same power:

```
/etc/pinterest-keepalive/env    <- WINS there, written by browsers/deploy_gcp.sh
```

Grep all of them. On 2026-08-26 (3) and (4) were updated, (1) was not, and the
pool was dead for 90 minutes while `sync_cookies` reported `6/6 synced` and
`src.status` reported `0/8 usable`. Neither half can see the other.

And from WSL use `127.0.0.1`, never the gateway IP: measured the same day,
`172.31.144.1:6380` and `127.0.0.1:6380` reached **different Redis instances**
(dbsize 8 vs 23).
