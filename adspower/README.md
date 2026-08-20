# AdsPower headless — the WSL rehearsal for a VPS

Phase 2. The goal is that the operator's laptop stops being the machine that
has to stay awake: AdsPower runs on a server, its profiles hold the logged-in
Pinterest sessions, and their cookies reach the same Redis vault the scraper
already reads.

WSL is the rehearsal, not a shortcut — same `.deb`, same flags, same systemd
unit a VPS would use.

---

## What works, verified 2026-08-19

```
adspower-global 8.7.23  · Ubuntu 24.04 · systemd service · survives session exit
Local API on :50325     · 5 profiles listed · browser starts with a CDP port
```

Profiles synced down from the AdsPower **account** automatically. No login step
was needed on the fresh Linux install — the risk that headless would strand us
at an un-clickable login screen did not materialise.

## Four things that block a naive install

Every one of these looks like something else when it happens.

| Symptom | Cause | Fix |
|---|---|---|
| `wget https://adspower.com` gives an HTML file | that is the homepage, not a package | real URL is on `version.adspower.net` — read it out of the download page's HTML |
| their own `install.sh` fails | returns **HTTP 405**, any user agent | ignore it, use the `.deb` |
| app starts, prints nothing bad, exits ~20s later | `FATAL: GPU process isn't usable. Goodbye.` — Chromium's GPU process cannot initialise under WSL | `--disable-gpu --disable-software-rasterizer` |
| app runs, then vanishes when you close the terminal | WSL tears down the distro's processes with the last session | run it as a **systemd service** |

Plus one that is easy to misread: `--headless=true` still needs a display.
Electron exits without one, so `xvfb-run` is required even though the flag says
headless.

The `.deb`:

```
https://version.adspower.net/software/linux-x64-global/8.7.23/AdsPower-Global-8.7.23-x64.deb
311 MB · ~700 MB installed · needs libgtk-3-0, libxss1, libatspi2.0-0, libsecret-1-0
```

## Install

```bash
wget -O ads.deb https://version.adspower.net/software/linux-x64-global/8.7.23/AdsPower-Global-8.7.23-x64.deb
sudo dpkg -i ads.deb; sudo apt-get -f install -y
sudo apt-get install -y xvfb
# then the unit in this directory
```

## ⚠️ Two security facts, both measured

**The API key ends up in the log in plaintext.** AdsPower echoes its own
command line at startup, so `~/adspower/logs/*` contains
`--api-key=<key>`. The unit keeps the key in root-only
`/etc/adspower/api.env` (chmod 600), but that does not stop the app logging it.
Treat those logs as secret, and rotate the key if one has been shared.

**It listens on `*:50325`, not localhost.** Harmless inside WSL. On a public
VPS that is an open door to every profile and every stored session — bind it to
localhost or firewall the port before the box is reachable.

## Where this stops, and why

Two things are unresolved, and neither is a code problem:

**1. A signed-in profile syncs end to end.** ✅ Resolved 2026-08-19 —
`ads_k1fx40wf` reached the vault with 11 cookies including `_auth` and
`_pinterest_sess`, and `src.status` reports it usable.

Profiles that were *created but never opened* (`last_open_time: "0"`) genuinely
have no session, and the syncer says so. That part was always true. What was
NOT true was the first run's claim that **every** profile was signed out — see
the CDP note below.

**2. The Chrome extension does not load into AdsPower's browser** — and it is
the wrong mechanism anyway, so `sync_cookies.py` replaces it (see below).

<details><summary>the extension detail, kept for the record</summary>

`--load-extension=/home/devy/adspower/extension` **is** present in SunBrowser's
command line (confirmed from `/proc/<pid>/cmdline`), yet the only service worker
the browser registers is AdsPower's own
(`gcaiimgaiohlnlflkjjmcohobkpbbnfi`). Ours never appears.

</details>

### Why the API, not the extension

The extension exists because a **human's** Chrome needs to volunteer its
cookies. On a headless server there is no human and no reason for the browser
to push: AdsPower already exposes a CDP port per started profile, and
`Network.getAllCookies` returns the same cookies the extension would have
scraped.

So the shape that fits a VPS is a small syncer that:

1. lists profiles — `GET /api/v1/user/list`
2. starts each — `GET /api/v1/browser/start?user_id=…` → CDP port
3. pulls cookies over CDP — `Network.getAllCookies`
4. POSTs them to the Go cookie server exactly as the extension does
   (`Authorization: Bearer …`, `platform: "pinterest"`, a stable `profile_id`)

Same destination, same Redis keys, same `SessionManager` on the other side —
and no extension to load, no browser to keep in the foreground. The Go server
and the vault do not change at all.

### The fast path: `/api/v2/browser-profile/cookies`

AdsPower added an endpoint that returns a profile's cookie jar **without
starting the browser** — which is the whole ballgame for a server:

```
GET /api/v2/browser-profile/cookies?profile_id=<id>
```

| | cookies | user agent | time / profile |
|---|---|---|---|
| **v2 endpoint** (default) | ✅ | ✗ | **~2s** |
| browser + CDP (`--with-ua`) | ✅ | ✅ | ~20s + a full Chromium |

Measured 2026-08-19: 131 cookies, 16 pinterest, both `_auth` and
`_pinterest_sess`, browser never started. Whole run: **2.3s**.

⚠️ The docs say this needs **"Professional plan or higher"**. It answered on a
Base plan. Treat the gate as unverified rather than absent — if it starts
returning an error, that is the reason, and `--with-ua` still works.

⚠️ `data.cookies` is a **JSON string**, not a list. Iterating it without
decoding gives characters, which filter to nothing — a silent empty of exactly
the kind GROUP R covers. `fetch_cookies_v2()` decodes it; a test pins that.

**The user agent is only obtainable from a running browser.** No v1 or v2
endpoint exposes it (`user/list` has no UA field; `browser-profile/detail` is
404). So `--with-ua` starts one. Run it **once per profile**: the Go server
only HSETs `user_agent` when non-empty, so later fast runs omit it and the
stored value survives. The fast path sends nothing rather than a guess.

### What the scraper needs — three things, and only three

| | where it comes from | note |
|---|---|---|
| **cookies** | v2 endpoint | the session itself |
| **csrftoken** | **inside the cookies** | Pinterest's CSRF is *cookie-echo*: `session.py` reads `cookies["csrftoken"]` and sends it back as `X-CSRFToken`. There is no token call to make. A jar without it still syncs — reads work — but the run warns, because POSTs will fail |
| **user_agent** | a browser, **once** | AdsPower spoofs a different UA per profile. Replaying cookies under another profile's UA is exactly the mismatch a fingerprinter looks for, so it is stored beside them |

`Identity` also carries `proxy`, always `None` in phase 1. AdsPower knows each
profile's proxy (`user_proxy_config`) but the Go server has no proxy field, so
wiring it means changing the session layer — deliberately not done here.

**Verified end to end 2026-08-19** — an AdsPower-sourced identity driving the
real API:

```
identity : <Identity ads_k1fx40wf cookies=11 age=0s>
UA       : Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 …
CSRF hdr : a95fb059cc972b572807fdbd        <- echoed from the csrftoken cookie
terms    : ['nails', 'hairstyles', 'wallpaper', 'nail ideas', 'nail inspo']
```

### One group per platform

The operator keeps one AdsPower group per platform — `pinterest` holds
Pinterest accounts, `etsy` holds Etsy. So the syncer **defaults to
`--group pinterest`**: an Etsy account's cookies are excluded *by
construction*, not by trusting a domain filter downstream to catch them. Each
profile gets its own vault key (`ads_<user_id>`), so adding accounts is just
adding profiles to the group.

### The user agent, captured once

`--ua-mode auto` (the default) reads the vault to see whether this profile
already has a UA. If it does, no browser starts and the run is ~1.4s. If it
does not — a new profile — it starts one, captures the UA, and never needs to
again.

| mode | behaviour |
|---|---|
| `auto` *(default)* | browser only for profiles with no stored UA |
| `never` | cookies only, fastest |
| `always` | re-capture every run — for when a fingerprint changed |

Unreachable Redis degrades to "no UA stored", costing one unnecessary browser
start rather than refusing to sync.

**Built: `sync_cookies.py`.** Run it against a live AdsPower:

```bash
export ADS_API_KEY=...
python3 sync_cookies.py                     # group=pinterest, ua-mode=auto
python3 sync_cookies.py --dry-run           # read and report, write nothing
python3 sync_cookies.py --ua-mode never     # cookies only, fastest
python3 sync_cookies.py --ua-mode always    # re-capture the UA too
python3 sync_cookies.py --group ''          # every profile (mixes platforms)
```

It walks every profile, starts it, reads cookies over CDP, and POSTs a payload
byte-identical to the extension's. Two refusals are deliberate:

- a profile with **no** `_auth` / `_pinterest_sess` is not written. Those are
  what `SessionManager` requires; storing a cookie-less profile creates an
  identity that looks available and cannot authenticate — precisely the defect
  the Etsy side already shipped once.
- every skip prints its reason. "no pinterest cookies at all (never signed in)"
  and "cookies exist but none of the auth ones" mean different things and must
  not collapse into one silent skip.

`profile_id` is `ads_<user_id>` — derived from AdsPower's immutable id rather
than random like the extension's, so re-running **updates** a profile's vault
entry instead of growing a new one each time.

Working run, 2026-08-19:

```
1 profile(s)
  (unnamed k1fx40wf)  OK — 16 cookies (auth: _auth,_pinterest_sess)
                         -> cookie:pinterest:ads_k1fx40wf
  1/1 synced to the vault.
```

```
$ python -m src.status
  ads_k1fx40wf   cookies=11   age=4s   [OK]
```

### ⚠️ The bug this shipped with, and what it looked like

The first version called **`Network.getAllCookies`** and ignored CDP's `error`
field, returning `result.get("cookies") or []`. That method does not exist on a
**browser-level** target — AdsPower hands out a browser websocket, so CDP
replied:

```
-32601  'Network.getAllCookies' wasn't found
```

The empty list came back, and every profile was reported
**"no pinterest cookies at all (never signed in)"** — including one holding a
live session with `_auth` and `_pinterest_sess`. A plausible wrong answer,
produced by swallowing an error that said exactly what was wrong.

Two fixes, both worth keeping:

- **`Storage.getCookies`**, not `Network.getAllCookies`. Measured on the same
  profile: Storage → 133 cookies (16 pinterest), Network → `-32601`.
- `_cdp()` now **raises** on an `error` reply. A CDP call that fails must not
  be indistinguishable from one that legitimately found nothing.

Note 16 raw cookies become 11 in the vault: `cookie_json` is keyed by name, and
`csrftoken` / `_ir` / `g_state` each appear on several domains. The extension
does the same thing (`cookieJson[c.name] = c.value`), so the two agree.

## Handy commands

```bash
sudo systemctl status adspower            # is it up
sudo journalctl -u adspower -f            # follow it
curl -s localhost:50325/status            # {"code":0,"msg":"success"}
curl -s -H "api-key: $KEY" "localhost:50325/api/v1/user/list?page_size=20"
curl -s -H "api-key: $KEY" "localhost:50325/api/v1/browser/start?user_id=<id>"
curl -s -H "api-key: $KEY" "localhost:50325/api/v1/browser/stop?user_id=<id>"
```

## Rate limiting — measured, because guessing costs whole profiles

It is **1 request per second**, not the 120/min it is often described as:

| attempted | result |
|---|---|
| ~1300/min (no gap) | 1 ok, **9 limited** |
| **~120/min (0.5s gap)** | 5 ok, **5 limited** — exactly half |
| ~57/min (1.05s gap) | **8 ok, 0 limited** |

Half the calls failing at 0.5s spacing is what a strict 1/sec window looks like
from outside.

**v1 and v2 keep separate budgets.** Alternating them with no gap, the first
call to each family succeeded and only the second of each was limited — so
`sync_cookies.py` paces per family, which is roughly twice as fast as treating
the API as one queue.

**A throttle is not a failure.** AdsPower reports it as a normal HTTP 200 with
`code: -1` and a message — no 429, no `Retry-After`, so the string is the only
signal. The first version of `ads_call` raised on it, which aborts a whole
profile for what is only pacing; with one profile that never fired, with twenty
it would. It now retries (the window clears in ~0.6s) and only raises after
four attempts, saying that something else must be sharing the budget.
