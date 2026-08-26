# Donut Browser evaluation — is it a free AdsPower replacement?

Branch `donutbrowser-eval`. Question: [zhom/donutbrowser](https://github.com/zhom/donutbrowser)
is free and open-source; does it do what AdsPower does for this project —
per-profile fingerprint diversity, authenticated proxies, and cookie
extraction — well enough to drop the monthly AdsPower fee?

**Nothing installed yet.** Installing a new third-party desktop application
is a real system change (registry/services on Windows, a running process with
network access), so it needs a explicit go-ahead before this branch does that
— this file is the research pass that should happen first, same as
`browsers/README.md`'s Stage 0 read of the patchright/DrissionPage docs before
either was installed.

## What's known from the README alone (2026-08-20)

| | |
|---|---|
| License | AGPL-3.0 |
| Engine | wraps *Wayfern*, described as a privacy-focused Chromium fork |
| Platforms | macOS, Windows, Linux (deb/rpm/AppImage/Nix) |
| Proxies | per-profile, HTTP/HTTPS/SOCKS4/SOCKS5, **with auth** — advertised |
| Cookies | import/export, per profile — advertised |
| Automation | "Local API & MCP: REST API and Model Context Protocol server" |
| Maintenance | active — v0.29.6, 1,647 commits |

**If the proxy-auth claim holds**, it removes the one thing `browsers/
proxy_relay.py` had to build by hand for DrissionPage (Chromium's
`--proxy-server` cannot carry credentials natively). Worth confirming first,
since it's the cheapest single fact to falsify.

## What's unverified — no amount of reading answers these

1. **Headless / server mode.** No `--headless` flag, no Xvfb note, no Docker
   image mentioned anywhere in the README. AdsPower's docs were equally silent
   on this and it turned out to work fine under Xvfb (`adspower/README.md`),
   so absence of a mention is not evidence against — but it isn't evidence for
   it either. This is the one fact that decides whether it can run on a
   headless GCP VM at all, which is the whole point of evaluating it.
2. **What the REST API actually exposes.** No endpoint list, no example
   request. Specifically: can it (a) create a profile via API, (b) attach a
   proxy with credentials via API, (c) return that profile's cookies via API
   — without a human clicking anything? `browsers/keepalive.py` needs exactly
   these three, scriptable, on a schedule.
3. **Where cookies land** — its own encrypted store, or a plain Chromium
   profile dir `browsers/drivers.browser_path()`-style tooling could read
   directly. Changes whether `keepalive.py` could reuse it as a drop-in third
   driver alongside patchright/DrissionPage, or whether it needs its own
   integration.

## Proposed test order (mirrors how AdsPower and patchright/DrissionPage were
each evaluated in this repo — probe the real thing before writing code
against it)

1. Install on Windows (GUI works natively here — cheapest place to look,
   same reasoning as testing patchright/DrissionPage on Windows before WSL).
2. Create one profile by hand, assign one of the existing Webshare proxies
   (the same live proxies already used elsewhere in this project), confirm
   the exit IP is really the proxy — the same check every driver in
   `browsers/drivers.py` runs (`IP_ECHO`), because a tool that silently falls
   back to the host IP is worse than no proxy at all (this exact failure was
   found and fixed for DrissionPage, `browsers/proxy_relay.py`).
3. Find the REST API — port, auth, and whether it can do the three actions in
   the unverified list above without the GUI.
4. If (3) passes: try it under WSL/Xvfb, the same rehearsal shape as
   `adspower/README.md` and `browsers/README.md` Stage 5.
5. Only then: decide whether it replaces `browsers/fingerprint_patch.py` +
   `proxy_relay.py`, replaces AdsPower outright, or isn't worth the switch.

## Verdict

Not yet reached — nothing has been installed or run.
