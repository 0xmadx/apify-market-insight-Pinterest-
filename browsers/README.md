# browsers/ — replacing AdsPower

AdsPower costs money per profile. This directory tests whether a free stack can
do the same job: hold a Pinterest session behind its own proxy, keep it alive,
and hand the cookies to the vault.

**Result, measured 2026-08-20: patchright. DrissionPage is disqualified on one
specific thing.**

| | proxy w/ auth | cookies survive | authenticated after | time |
|---|---|---|---|---|
| **patchright** | ✅ | ✅ 8–9 back | **4/4** | 3.6–5.9s |
| **DrissionPage** | ❌ refuses | ✅ (no proxy) | 1/1 no proxy · **0/1 with** | 8.5–10.1s |

DrissionPage says it plainly:

```
You seem to be setting up a proxy that uses the account password, which is not
supported for the time being, and can be implemented by the plug-in itself.
```

Chromium cannot take proxy credentials on the command line; AdsPower and
Playwright both solve it, DrissionPage does not. **It then ignores the proxy and
carries on** — so the browser exits from the host IP while looking like it
worked. That is the exact mismatch the whole session layer exists to prevent,
and it is worse than an error.

Without a proxy DrissionPage authenticates fine and is architecturally the
nicer design (CDP-direct, no Playwright control plane). It is not ruled out
forever — it is ruled out until proxy auth works without a helper extension.

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
