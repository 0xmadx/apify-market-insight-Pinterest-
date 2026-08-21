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

| **proxy** | AdsPower's `user_proxy_config` | written straight to Redis by this script, because the Go server has no proxy field. The scraper then exits from the same IP the browser does |

Since 2026-08-20 the vault **refuses to lease a profile with no proxy**
(`REQUIRE_PROXY`, default on). A profile without one exits from whatever host
the run is on, so a pool holding both proxied and unproxied profiles sends the
same customer's requests from a datacentre IP one run and a residential one the
next. Measured on the live pool that day: 5 proxied AdsPower profiles and 1
Chrome-extension profile with none, so about one run in six went out from the
operator's home IP and nothing said so. `python -m src.status` now names it.

**Verified end to end 2026-08-19** — an AdsPower-sourced identity driving the
real API:

```
identity : <Identity ads_k1fx40wf cookies=11 age=0s>
UA       : Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 …
CSRF hdr : a95fb059cc972b572807fdbd        <- echoed from the csrftoken cookie
terms    : ['nails', 'hairstyles', 'wallpaper', 'nail ideas', 'nail inspo']
```

### Proxies — one per profile, AdsPower owns the truth

```
Webshare  --assign_proxies.py-->  AdsPower profile
                                        |
                            sync_cookies.py mirrors it
                                        v
                                   the vault  -->  curl_cffi
```

**The same proxy must be used by both, and it is not optional.** `Identity`'s
own docstring says why: *"cookies + the UA + the exit IP … travel together or
not at all."* Cookies created behind one IP and replayed from another are a
worse signal than no proxy at all.

So AdsPower is the **single source of truth** for a profile's exit IP.
`assign_proxies.py` only writes it there; `sync_cookies.py` reads it back and
mirrors it into the vault. Assigning proxies in our own code would create a
second truth, and the first silent disagreement is the mismatch above.

**Verified end to end 2026-08-19:**

```
webshare 163.123.202.173:5458 -> AdsPower profile -> vault -> scraper
exit IP  : 163.123.202.173          <- the profile's own proxy
pinterest: ['nails', 'hairstyles', 'wallpaper', 'nail ideas']
```

AdsPower returns `proxy_password` **in the clear** on read, which is what makes
this direction possible; if that ever changes, Webshare has to become the
source of truth instead and the two sides need reconciling.

```bash
python3 assign_proxies.py --dry-run   # show the map, change nothing
python3 assign_proxies.py             # apply, then run sync_cookies.py
```

**The map is sticky.** A profile that already has a proxy still in the pool
keeps it — always, no exceptions. Only profiles with no proxy (or one Webshare
has dropped) get placed, and they go to the least-used proxy, ties broken by
(host, port) so the result repeats exactly.

Nothing that has an exit IP is ever handed a different one: not when a profile
is added, not when the pool grows, not when it shrinks.

> An earlier version paired by **sorted position** — profiles by `user_id`,
> proxies by (address, port). That is stable only as long as the sort is, and
> AdsPower `user_id`s are random strings: a new profile could sort into the
> *middle* and shift every profile after it onto a different proxy. Silently
> moving live accounts is the one thing this file exists to prevent. Position
> pairing was right when no profile had a proxy yet; now that they do, what
> they already have is the authority.

### ⚠️ Up to TWO profiles may share one exit IP

`MAX_PROFILES_PER_PROXY = 2`. Operator's call, 2026-08-20.

The asymmetry is the whole reason this is safe:

| | reads as |
|---|---|
| one **account** seen from two IPs | a stolen session — the thing that gets you banned |
| two **accounts** seen from one IP | a household, an office, a shared wifi |

Pinterest cannot treat the second as fraud without banning families. It can and
does treat the first as compromise. Sharing an IP between two *different*
accounts is therefore cheap; replaying one account's cookies from a second
address is not, and remains impossible here — the proxy lives in the profile's
own hash beside its cookies.

**Two is a ceiling, not a default to tune upward.** Three, five, ten accounts
on one residential-looking address is a farm and the correlation is trivial to
see. `--max-share` can only *lower* it; `--max-share 3` is refused.

What it costs: the two profiles sharing an address can be leased at the same
time, so that IP can carry double the request rate of a single-account one.
Acceptable at this volume — a run is 3–38s and a few dozen requests — and the
first thing to reconsider if Pinterest ever starts answering 429.

What it buys, at 6 US proxies:

```
--max-share 1      6 US accounts    (what it was)
--max-share 2     12 US accounts    (what it is)
```

Profiles are spread one-per-proxy before any proxy is doubled, so a pool that
comfortably fits never shares at all. Every run prints how many IPs are
carrying two, so sharing is never something you find out about later.

⚠️ **More profiles than the pool can hold is refused, never stacked deeper.**
`--partial` assigns what fits and leaves the rest with **no** proxy — safe,
because they exit from the host, and the browser and the scraper still agree.
Note the vault will not lease a profile without a proxy (`REQUIRE_PROXY`), so
those profiles are parked, not silently exposed.

⚠️ A proxy removed in AdsPower **clears** the vault's copy. Leaving a stale one
would keep the scraper exiting from an IP the browser no longer uses — invisible
until it is a ban.

### One group per platform, one country per group

The operator keeps one AdsPower group per platform — `pinterest` holds
Pinterest accounts, `etsy` holds Etsy. So the syncer **defaults to
`--group pinterest`**: an Etsy account's cookies are excluded *by
construction*, not by trusting a domain filter downstream to catch them. Each
profile gets its own vault key (`ads_<user_id>`), so adding accounts is just
adding profiles to the group.

A group may also carry a **two-letter country suffix** — `pinterest-fr`,
`pinterest-de`. That is how a new account gets an exit IP outside the US.

```
group           country     vault pool
pinterest       US          pinterest
pinterest-fr    FR          pinterest      <- same pool
pinterest-de    DE          pinterest      <- same pool
etsy            —           etsy           <- never swept by a pinterest run
```

**One pool, many countries.** Splitting the vault per country would halve
concurrency for no safety gain: the thing that must stay consistent is *one
account to one exit IP*, and the per-profile proxy already guarantees that. The
country is a routing hint for `assign_proxies.py`, nothing more — the trends
region is a request parameter (`country=US`), never derived from the IP, so a
French-born account fetches identical US numbers.

`--group pinterest` sweeps the whole **family**: the group itself plus any
country suffix. It is a family test and not `startswith`, because
`startswith("etsy")` would also match a group called `etsyshop` — and the whole
point of grouping is that an Etsy session can never land in the Pinterest pool.

#### Adding an account in another country

The order matters, and it is the only order that is safe:

```bash
# 1. make the group in AdsPower, named for the country
#    2. create the profile in it — do NOT sign in yet
# 3. give it its proxy FIRST
python3 assign_proxies.py --group pinterest-fr --dry-run
python3 assign_proxies.py --group pinterest-fr
# 4. NOW open the profile and sign in to Pinterest, behind that IP
# 5. the 5-minute timer picks it up; or sync immediately:
python3 sync_cookies.py --group pinterest
```

The account is then *born* French — its cookies, its language, its feed and its
exit IP all agree, and they keep agreeing. Let Pinterest set the account's
country from the IP at signup; do not set it to US behind a French address.

The country comes from the group name rather than from `--country` because the
flag is the dangerous half. Run `--country FR` against the main group by
mistake and positional pairing hands the French IP to whichever profile sorts
first — an existing, live, US account. With the country in the name, the wrong
country and the wrong group cannot be selected independently. Passing a
`--country` that contradicts the group name is refused rather than obeyed.

#### ⚠️ Moving a live account across a border is refused

A same-country replacement is mild — real people change ISP. A cross-country
move is not: the account was created behind one country's IP and its cookies,
language and history all agree with that. `assign_proxies.py` **refuses** it:

```
REFUSED: pin-us-3 is a live account on a US proxy and this would move it to FR.
Its cookies, language and history were all born in US; an exit IP in FR is the
mismatch this setup exists to prevent.
A NEW account can be created behind any country — put it in a group named for
that country (e.g. 'pinterest-fr') and assign its proxy BEFORE the first login.
Pass --allow-country-move only if you know this account can survive the move.
```

A profile whose current proxy is no longer in the Webshare pool has an
**unknown** country, not a foreign one — Webshare dropping a proxy is ordinary,
and treating unknown as a mismatch would block every legitimate replacement of
a dead one.

#### The keys

Both scripts read the repo-root `.env` themselves, and accept either spelling of
the AdsPower key (`ADS_API_KEY` or `adspower_api`). An exported shell variable
beats the file. When one is missing the error names which variable *was* found —
`.env` unread and the key spelled differently in it are two different fixes, and
the old message said the same thing for both.

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

## ⚠️ The heartbeat — without it the profiles silently vanish

The vault evicts any profile whose `last_updated` is older than
`PROFILE_MAX_AGE` (900s). The Chrome extension never trips this because it
beams on every cookie change. A **manual** sync does, fifteen minutes later:

```
[vault] evicted pinterest/ads_k1fx40wf: stale (934s since heartbeat)
```

And it goes quietly — the scraper simply stops drawing that profile. So
`adspower-sync.timer` runs the sync every **5 minutes**, a 3× margin on the
900s timeout so one missed tick (AdsPower restarting, a throttle) never reaches
eviction. A tick costs ~2s and starts no browser.

```bash
sudo cp adspower-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now adspower-sync.timer
systemctl list-timers adspower-sync        # when it next fires
journalctl -u adspower-sync -f             # watch it run
```

The unit reads `/etc/adspower/api.env`, which must also carry `REDIS_URL` —
from inside WSL that is the Windows host, not localhost.

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
