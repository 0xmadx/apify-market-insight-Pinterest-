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

**Built: `sync_cookies.py`.** Run it against a live AdsPower:

```bash
export ADS_API_KEY=...
python3 sync_cookies.py --dry-run           # read and report, write nothing
python3 sync_cookies.py --group pinterest   # only that group
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

The Local API rate-limits to roughly **one request per second** — a burst
returns `"Too many request per second"`, which reads like a failure but is just
pacing.
