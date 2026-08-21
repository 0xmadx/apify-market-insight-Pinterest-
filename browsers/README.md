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
