# browsers/ — replacing AdsPower

AdsPower costs money per profile. This directory tests whether a free stack can
do the same job: hold a Pinterest session behind its own proxy, keep it alive,
and hand the cookies to the vault.

**Result, measured 2026-08-20: both work. patchright is the pick; DrissionPage
needed one thing built for it and then matched.**

| | proxy w/ auth | exit IP correct | authenticated after | time |
|---|---|---|---|---|
| **patchright** | ✅ native | ✅ per profile | 4/4 | **3.6–9.5s** |
| **DrissionPage** | ⚠️ via relay | ✅ per profile | 2/2 | 7.1–12.5s |

### The DrissionPage problem, and the fix

Chromium's `--proxy-server` **cannot carry credentials** — that is Chromium, not
the library. AdsPower solves it with a helper extension; Playwright takes
username/password as separate fields and answers the challenge itself.

DrissionPage does neither. Its `set_proxy` only *warns*, then passes the string
through regardless:

```python
if search(r'.*?:.*?@.*?\..*', proxy):
    print(UNSUPPORTED_USER_PROXY)
return self.set_argument('--proxy-server', proxy)
```

So Chromium drops the password, the upstream answers 407, and the browser
**silently exits from the host IP** while every status field says success.

`browsers/proxy_relay.py` fixes it: an unauthenticated proxy on localhost that
adds the credentials on the way out.

```
browser --(no auth)--> 127.0.0.1:PORT --(Basic auth)--> Webshare
```

**Why not Webshare's IP whitelist?** It exists
(`/api/v2/proxy/ipauthorization/`, currently empty) and needs no code. It was
rejected because it binds the proxies to one public IP, is account-wide, and
fails in the worst possible way: if the runner's IP ever changes, the proxy
stops authenticating and Chromium falls back to a **direct connection** — the
exact mismatch this layer exists to prevent. The relay works from any machine
and fails loudly instead.

### The check that actually caught this

Every driver reports "launched, loaded, cookies returned" while exiting from
the wrong address. So both drivers now fetch `api.ipify.org` first and compare
the answer to the proxy they were handed:

```
patchright    ads_k1fx40wf  ✅ exit IP 163.123.202.173
              ads_k1fy47um  ✅ exit IP 163.123.203.141
DrissionPage  ads_k1fx40wf  ✅ exit IP 163.123.202.173
              ads_k1fy47um  ✅ exit IP 163.123.203.141
```

Distinct per profile, matching each one's assigned proxy. Without this check
the first DrissionPage run looked like a pass.

### Which to adopt

**patchright**, for now: native proxy auth, roughly twice as fast, no extra
moving part. DrissionPage is the nicer architecture (CDP-direct, no Playwright
control plane, and benchmarks favour that class) and is now fully working — keep
it as the fallback if patchright ever stops tracking upstream Playwright.

## What is actually being tested

The scraper is curl_cffi and stays that way. A browser here does four things:

1. launch wearing this profile's proxy and user agent
2. load this profile's cookies
3. keep the session alive (one page load)
4. hand the cookies back

**The stealth bar is a login and an occasional page load, not 188 automated
requests.** That is why this is plausible at all.

### The pass condition is not "it launched"

Pinterest serves a signed-out browser a perfectly normal page, and a signed-out
API caller plausible *public* data. Both look like success. So a driver passes
only when the cookies it hands back still buy **authenticated** data through
curl_cffi — the real transport. The bench also runs a baseline first, because
if the exported cookies are already dead every driver fails and the browsers
get the blame.

## Two bugs found while building this

**`__Secure-` cookies.** Pinterest's jar contains `__Secure-s_a`, and CDP
rejects the *entire* `setCookies` call with a flat "Invalid cookie fields" if
such a cookie arrives without `secure: true`. patchright failed on every profile
until `cookie_records()` was added. The all-or-nothing behaviour is lucky: a
lenient protocol would have loaded the browser with the session cookie missing,
which looks exactly like an ordinary signed-out visit.

**Playwright proxy credentials.** They must be separate `username`/`password`
fields. Inline `http://user:pass@host` in `server` is accepted and silently
ignored — a run that exits from the wrong IP while reporting success.

## Commands

```bash
python -m browsers.identities export     # vault -> browsers/identities.json
python -m browsers.bench                 # both drivers, one identity
python -m browsers.bench --only patchright --profiles 4
python -m browsers.bench --headed        # watch it
```

`bench` **never touches the vault** — it reads the exported file. Deciding which
browser to adopt must not disturb the pool it is measuring, so there is no need
to empty the vault before running it.

If you do want the pool empty for some other test:

```bash
python -m browsers.identities clear      # backs up FIRST, then clears
python -m browsers.identities restore browsers/identities.json
```

`clear` refuses to run without writing a backup — not a flag, a precondition.
The cookies are the accounts, and a login is the one thing that cannot be
automated back. The backup was proven by restoring into a scratch namespace and
making a live authenticated call from a restored identity.

⚠️ `browsers/identities.json` holds live sessions and proxy passwords in the
clear. It is gitignored. Treat it like `.env` and delete it when you are done.

## Stage 1 result — fingerprint diversity flips the choice

`python -m browsers.fingerprint --driver {patchright|drission}`

| | automation hidden | profiles look different | verdict |
|---|---|---|---|
| **patchright** | ✅ nothing injected | ❌ **1/3 distinct** | FAIL |
| **DrissionPage** + patch | ⚠️ injected, leak closed | ✅ **3/3 distinct, 3/3 stable** | PASS |

**patchright silently ignores `add_init_script`.** Measured: the script never
runs and `navigator.hardwareConcurrency` reports the host's real 12. That is
patchright working as designed — injected scripts are themselves detectable —
and it means every profile on one machine shares the host's hardware identity.

Two profiles, signal by signal, under patchright:

```
SAME   canvas · webglVendor · webglRenderer · fonts
SAME   hardwareConcurrency (12 — the host's real core count)
DIFFER screen        1366x768x24x1  vs  1366x768x24x2
```

Only `screen` differs, and only in the devicePixelRatio digit. Three accounts a
fingerprinter ties together in one query; the separate exit IPs do not help.

DrissionPage honours `Page.addScriptToEvaluateOnNewDocument`, so
`browsers/fingerprint_patch.py` can give each profile its own GPU, core count,
memory, screen and canvas — all derived from a hash of the profile id, so they
are **stable forever** rather than random per launch. A fingerprint that changes
every run is worse than one that never changes: it is a new device presenting
the same login cookies.

### The trade, stated plainly

    patchright     undetectable, but every profile is the same machine
    DrissionPage   every profile is a different machine, but it is patching JS

The obvious `Function.prototype.toString` leak is closed — all patched
functions report `[native code]`, including `toString` itself, verified. This
is **not undetectable**, only not-trivially-detectable, and it does not claim to
survive a dedicated fingerprinting vendor. That is proportionate here:
Pinterest, on sessions that log in rarely and load one page per keepalive cycle.

**Recommendation: DrissionPage for the keepalive farm.** Fingerprint diversity
is the thing AdsPower is actually being paid for, and it is the only one of the
two that can deliver it. patchright stays as the faster driver for anything that
does not need a distinct identity.

## Stage 2 — the keepalive service

`browsers/keepalive.py` replaces `adspower/sync_cookies.py`. Same contract, no
paid product.

**Proven end to end 2026-08-20**, into a vault namespace AdsPower has never
written to — so nothing about the result depends on AdsPower still running:

```
7 profile(s) · platform pinterest_ka · one pass
  ads_k1fx40wf      OK   9 cookies via 163.123.202.173 (6.5s)
  ads_k1fy47um      OK   9 cookies via 163.123.203.141 (5.9s)
  ads_k1fy6dnh      OK   8 cookies via 199.187.190.159 (9.2s)
  ads_k1fy6e67      OK  10 cookies via 45.56.159.126  (8.8s)
  ads_k1fy6eoy      OK   9 cookies via 45.56.182.90   (7.5s)
  ads_k1fyn0gc      OK  12 cookies via 72.1.134.148   (8.5s)
  profile_p5ewxsodn OK  11 cookies                    (5.8s)
  7 written · 0 skipped · 53s
```

…then leased from that pool and fetched real authenticated data:

```
[session] leased <Identity ads_k1fx40wf cookies=9 age=45s>
  moments  : 5
  keywords : nails, hairstyles, wallpaper, nail ideas
```

### The four rules it will not break

Three are inherited from `sync_cookies.py`; the fourth is new.

1. **A signed-out jar is never written.** Cookies can exist and still be logged
   out, and Pinterest then answers with plausible PUBLIC data — the run
   "succeeds" while collecting the wrong thing.
2. **Cookies, user agent and proxy are written together.** One identity. A jar
   stored without its UA gets replayed under a different browser.
3. **`last_updated` is stamped only on a verified pass.** The vault reads it as
   "someone confirmed this recently"; stamping after a failed check would make
   a dead session look fresh.
4. **The exit IP is verified BEFORE anything is written.** Chromium falls back
   to a direct connection when a proxy fails, and every other signal still
   reports success. A mismatch aborts that profile.

`--interval` is refused if it is not shorter than `PROFILE_MAX_AGE` — a cycle
longer than the freshness window empties and refills the pool forever while
looking like it works.

### One DrissionPage trap

`auto_port()` blanks the address and picks a throwaway user-data dir; calling
`set_user_data_path` afterwards sets `_auto_port = False` and leaves the address
empty, so `connect_browser` dies on `address.split(':')` — surfacing as a bare
`ValueError: not enough values to unpack`. Ports are now derived per profile
and set explicitly, after the data path.

Persistent per-profile directories are better regardless: history, cache and
localStorage accumulate, so the browser looks like a device that has been here
before instead of a fresh install every cycle.

## Stage 3 — what a cycle costs

**~7.6s per profile, sequential, single-threaded.**

```
 7 profiles   53s   measured
20 profiles  152s   projected — fits a 300s cycle comfortably
39 profiles  296s   the ceiling at 5-minute intervals
```

So **one host holds roughly 35 profiles** with no further work — well past the
20 that prompted this. Beyond that, in order: run 2–3 browsers in parallel, or
read cookies from the profile's SQLite store without launching at all.

The read-from-disk optimisation was planned for this stage and is **not
needed yet**. It is recorded, not built.

## Stage 5 — headless Linux, no root

Proven 2026-08-20 under WSL Ubuntu (the same shape as the eventual GCP VM),
against the Windows vault over a non-localhost Redis:

```
3 profile(s) · platform pinterest_linux · one pass
  ads_k1fx40wf   OK  9 cookies via 163.123.202.173  (9.3s)
  ads_k1fy47um   OK  9 cookies via 163.123.203.141  (17.6s)
  ads_k1fy6dnh   OK  8 cookies via 199.187.190.159  (9.4s)
  3 written · 0 skipped · 37s

LINUX end-to-end: 5 moments · nails, hairstyles, wallpaper, nail ideas
```

**No root anywhere.** `apt install chromium` needs it; `patchright install
chromium` does not, and it drops a known-good build under `~/.cache/
ms-playwright` that `drivers.browser_path()` finds. The browser is therefore
not a second thing to provision — and it is the same build the Windows tests
ran against.

Set `CHROME_PATH` to override on a host with its own Chromium.

Linux is slower per profile (~12s vs ~7.6s) because this ran over `/mnt/c`,
which is a slow filesystem. A native checkout on the VM should land closer to
the Windows number; re-measure there rather than assuming either.

### One trap, and why the fix is a search

The first `browser_path()` hardcoded `chrome-linux/chrome` — the OLD Playwright
layout. The current one is `chrome-linux64/chrome`, so it found nothing and
DrissionPage answered with a Chinese "cannot find browser executable" that names
no path. It now globs, and sorts by the numeric build (`chromium-1234` sorts
BEFORE `chromium-1200` as a string, so a lexical sort picks the older browser).
`chromium_headless_shell-*` is skipped: it starts, and then behaves differently
from the thing that was tested.

### The units

`browsers/keepalive.service` + `.timer`, modelled on the AdsPower pair.
`systemd-analyze verify` passes.

Two decisions worth knowing:

- **`Type=oneshot` driven by a timer**, not a long-running loop. A crashed pass
  is retried on the next tick, and a hung pass cannot wedge a process holding
  browsers open. `--once` is passed for the same reason: the timer owns the
  schedule, and running the internal loop under a timer would be two schedulers
  disagreeing.
- **5-minute interval is a safety constraint, not taste.** The vault evicts a
  profile whose heartbeat is older than `PROFILE_MAX_AGE` (900s), so the gap
  must stay well under it. 5 minutes is a 3x margin.

Install — **use the script, do not `cp` the unit**:

```bash
REDIS_URL='rediss://...' bash browsers/deploy_gcp.sh
```

⚠️ `browsers/keepalive.service` is a **reference copy written for WSL**. It
hardcodes `/home/devy` and a venv at `~/pinterest-actor` — which is not even
where this repo lives (`~/pinterest-apify`). Copying it to a VM with a
different username, or to any host that follows the documented layout,
produces a unit that dies at `ExecStart` with a bare `status=203/EXEC` and no
indication of why. `deploy_gcp.sh` generates the unit from the paths that
actually exist on the host instead, which is the whole reason it is a script
and not two `cp` commands.

It writes the environment file itself:

```
/etc/pinterest-keepalive/env      REDIS_URL, VAULT_PLATFORM
```

⚠️ That file is a systemd `EnvironmentFile`, so on the VM it **beats `.env`
and the code default** the same way `/etc/adspower/api.env` does on the WSL
box. It is a fifth home for `REDIS_URL`; see
[`../docs/VAULT_SEPARATION.md`](../docs/VAULT_SEPARATION.md).

## Stage 6 — the cutover, when you are ready

Run both writers against **different vault namespaces** for a day and compare,
rather than switching and hoping:

```bash
# keepalive writes its own pool; AdsPower keeps writing `pinterest`
VAULT_PLATFORM=pinterest_new python -m browsers.keepalive
VAULT_PLATFORM=pinterest_new python -m src.status     # freshness + fingerprint
python -m src.status                                  # AdsPower's, for contrast
```

When the new pool holds the same profiles at the same freshness for a day,
stop `adspower-sync.timer`, point `VAULT_PLATFORM` back at `pinterest`, and
keep `browsers/identities.json` as the undo.

## What patchright does NOT give you

**It removes automation tells. It does not randomise fingerprints.** Twenty
patchright profiles share one canvas/WebGL/font signature. That is the AdsPower
feature you would be dropping, and at twenty-plus accounts it is the one that
matters — distinct proxies do not help if the browsers are identical.

Before migrating for real, that needs an answer. Options, cheapest first:
per-profile `--user-data-dir` with varied viewport/locale/timezone (weak),
a fingerprint-injection layer, or keeping AdsPower for the accounts that matter
most and using patchright for the rest.

**And test the login separately.** Every result here is about *restoring* an
existing session. Signing in is Pinterest's most defended flow, and a flagged
login burns the account immediately. Prove that on a throwaway account first.
