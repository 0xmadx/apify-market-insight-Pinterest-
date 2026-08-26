# Session sources — the three ways cookies get into the vault

Everything downstream — `src/vault.py`, `src/session.py`, the actor — reads
one thing: `cookie:{platform}:{profile_id}` in Redis, holding cookies + user
agent + proxy as one unit (`src/vault.py` `Identity`). **It does not care how
that hash was filled.** That's deliberate (D-28, carried from the Etsy repo):
swapping the writer is a config change, not a rewrite. This doc is the map of
which writer is which, so "AdsPower or stealth or extension" stops being a
question you have to re-derive each time.

| | Chrome extension | AdsPower | stealth-browsers |
|---|---|---|---|
| **Status here** | ❌ not used by this project | ✅ current, paid | 🧪 proven, not cut over |
| **Cost** | free | ~$X/mo per profile tier | free |
| **What it is** | a browser extension that beams cookies on change | a commercial antidetect browser + Local API | DrissionPage/patchright + a fingerprint patch, driven by our own code |
| **Fingerprint diversity** | whatever the human's real browser is | ✅ paid-for, per profile | ✅ built ourselves — `browsers/fingerprint_patch.py`, proven 3/3 distinct & stable |
| **Proxy auth** | n/a (human's own connection) | ✅ native | ⚠️ Chromium can't carry credentials; `browsers/proxy_relay.py` adds them |
| **Headless / server** | n/a (needs a human's Chrome open) | ✅ (`adspower/*.service`) | ✅ proven on WSL, no root (`browsers/keepalive.service`) |
| **Login automation** | n/a (human logs in) | manual, in the AdsPower UI | ❌ not built (Stage 4, deferred — see below) |
| **Code** | none — the Etsy repo's extension, shared vault schema only | `adspower/` | `browsers/` (on `main` since 2026-08-25) |

## Why the extension isn't a real option here

It's the Etsy project's Chrome extension. It beams cookies for *whichever*
site role is active, and Pinterest sessions written that way were found to
land with the wrong role and no usable cookies (`role` defaults to `"auto"`,
which matches neither `etsy` nor `pinterest` — see the parent repo's
`docs/SESSION_LAYER_FIX.md`). It's listed here only because the vault schema
is shared and someone will ask "what about the extension" eventually. The
project's own instruction, given early on, still stands: **use the API, not
the extension.**

## AdsPower — what it's actually being paid for

Not the automation-hiding. It's **fingerprint diversity per profile**:
distinct canvas/WebGL/font signatures, so N accounts on N proxies look like N
different machines. Measured directly (`browsers/README.md`, Stage 1): a
patchright profile with no equivalent patch shares the *host's* hardware
identity with every other profile on that host — 1/3 distinct. AdsPower
solves exactly that problem, and charges for it.

## stealth-browsers — what it replaces, and what it doesn't yet

`browsers/keepalive.py` is a drop-in replacement for `adspower/sync_cookies.py`
— same contract (launch, load cookies, verify, harvest, write to the vault),
proven end to end including headless Linux with zero root (`browsers/README.md`
Stages 1–5).

**Not replaced: bringing a brand-new account into the system (Stage 4,
login).** Confirmed deferred, twice. The agreed shape for later: a human logs
into AdsPower locally once per new account (AdsPower stays installed for this
one job), a *separate* tool/agent then pushes that session up to wherever the
farm can pick it up, on its own control plane, independent of `keepalive`.
That tool does not exist yet — this paragraph is the only record of the
design intent so it doesn't get lost.

## Evaluating a fourth option: Donut Browser

`donutbrowser-eval` branch. [zhom/donutbrowser](https://github.com/zhom/donutbrowser)
is free, open-source (AGPL-3.0), wraps a Chromium fork called *Wayfern* for
fingerprint spoofing, and advertises per-profile proxies (with auth — a real
win over the relay `proxy_relay.py` had to build), cookie import/export, and a
local REST API + MCP server. The point of testing it: if its API can create a
profile, assign a proxy, and export cookies programmatically, it could replace
*both* AdsPower's fee and the custom `fingerprint_patch.py` work in one move.

**Open, unverified as of this writing:** whether it runs headless on a server
with no display (docs say nothing either way — AdsPower didn't advertise this
either and turned out to work under Xvfb, so silence isn't a no), and what the
REST API actually exposes (no endpoint documentation found; needs installing
and probing directly, the same way AdsPower's Local API was reverse-engineered
in `adspower/README.md`).
