"""Pull cookies out of AdsPower profiles and feed the vault — no extension.

The Chrome extension exists so a HUMAN's browser can volunteer its cookies:
it lives in the browser, watches `chrome.cookies.onChanged`, and pushes. On a
headless server there is no human and no reason to wait for a push. AdsPower
already hands out a CDP port for every profile it starts, and
`Storage.getCookies` returns the same cookies the extension would have
scraped.

So this walks the profiles instead:

    GET  /api/v1/user/list              which profiles exist
    GET  /api/v1/browser/start          -> a CDP websocket per profile
    CDP  Storage.getCookies             the cookies themselves
    POST /update-cookie  (Go server)    byte-identical to what the extension sends
    GET  /api/v1/browser/stop           put it back

Nothing downstream changes. Same bearer token, same `platform: "pinterest"`,
same Redis keys (`cookie:pinterest:<profile_id>`), same `SessionManager`
reading them. The Go server and the vault do not know the difference.

WHY A STABLE profile_id MATTERS
    The extension invents `profile_" + random` once and keeps it in
    chrome.storage. We cannot do that — a fresh read has no memory — so the id
    is derived from AdsPower's own immutable `user_id`:

        ads_<user_id>       e.g. ads_k1fxtn6w

    Deterministic, so re-running updates that profile's vault entry instead of
    growing a new one every time.

REFUSALS, because a plausible wrong result is the failure mode here:
  * a profile with no `_auth` / `_pinterest_sess` cookie is NOT synced. Those
    two are what `SessionManager` requires; writing a cookie-less profile into
    the vault would produce an identity that looks available and cannot
    authenticate — the exact defect the Etsy side already suffered.
  * every skip is reported with its reason. A silent skip would read as "that
    profile has no cookies yet".

    python3 sync_cookies.py --dry-run          # look, change nothing
    python3 sync_cookies.py --group pinterest  # only that group
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request

ADS = "http://127.0.0.1:50325"
GO_SERVER = "http://172.31.144.1:8000/update-cookie"
GO_TOKEN = "super_secret_key_123"

# MEASURED 2026-08-19, because guessing this wrong costs whole profiles.
#
#   attempted ~1300/min (no gap)   ->  1 ok,  9 limited
#   attempted  ~120/min (0.5s gap) ->  5 ok,  5 limited   <- exactly half
#   attempted   ~57/min (1.05s gap)->  8 ok,  0 limited
#
# So the gate is **1 request per second**, not the 120/min it is sometimes
# described as. At 0.5s spacing precisely half the calls are rejected, which is
# what a strict 1/sec window looks like from the outside.
RATE_LIMIT_SECONDS = 1.15

# ⚠️ v1 and v2 keep SEPARATE budgets. Alternating them with no gap, the first
# call to each family succeeded and only the second of each was limited — so
# the pacing is per family, and interleaving them is roughly twice as fast as
# treating the whole API as one queue.
_last_call = {"v1": 0.0, "v2": 0.0}

# After a rejection the window clears in well under a second (0.3s still
# limited, 0.6s fine). So a rejected call is worth retrying, not surfacing.
RATE_LIMIT_BACKOFF = 0.7
RATE_LIMIT_RETRIES = 4

# WHAT THE SCRAPER ACTUALLY NEEDS — three things, and only three:
#
#   cookies     the session itself.
#   csrftoken   NOT a separate field. Pinterest's CSRF scheme is cookie-echo:
#               `session.py` reads identity.cookies["csrftoken"] and sends it
#               back as the X-CSRFToken header. So the token arrives WITH the
#               cookies and needs no extra call — but a jar missing it cannot
#               POST, which is why it is checked rather than assumed.
#   user_agent  stored beside the cookies so curl_cffi replays the SAME
#               identity the session belongs to. AdsPower spoofs a different UA
#               per profile, so borrowing another profile's would be exactly
#               the mismatch a fingerprinter looks for.
#
# (Identity also carries `proxy`, always None in phase 1. AdsPower knows each
# profile's proxy in `user_proxy_config`, but the Go server has no proxy field,
# so wiring it needs a change to the session layer — deliberately not done here.)
AUTH_COOKIES = ("_auth", "_pinterest_sess")

# Needed to POST. Its absence is worth a warning, not a refusal: read-only
# traversals work fine without it.
CSRF_COOKIE = "csrftoken"

# This project is Pinterest-only. The operator keeps one AdsPower group per
# platform — group "pinterest" holds Pinterest accounts, "etsy" holds Etsy —
# so defaulting to the group rather than "every profile" keeps an Etsy account's
# cookies out of the Pinterest vault entirely, instead of relying on the
# domain filter to catch it downstream.
DEFAULT_GROUP = "pinterest"

def _family(path):
    """Which rate-limit bucket a path belongs to. They are independent."""
    return "v2" if path.startswith("/api/v2") else "v1"


def is_rate_limited(payload):
    """AdsPower reports throttling as a normal 200 with code -1 and a message.

    It is NOT an HTTP 429 and carries no Retry-After, so the only signal is
    this string. Treating it as a hard failure aborts a profile for what is
    purely pacing — which is exactly what the first version did.
    """
    return payload.get("code") != 0 and "too many request" in str(
        payload.get("msg", "")).lower()


def ads_call(path, key, timeout=90):
    """One rate-limited AdsPower Local API call, retried through throttling.

    Paces per endpoint family (v1 and v2 have separate budgets) and retries a
    throttle reply rather than raising, because a rejected call means "you were
    early", not "this failed".
    """
    fam = _family(path)
    for attempt in range(RATE_LIMIT_RETRIES):
        wait = RATE_LIMIT_SECONDS - (time.monotonic() - _last_call[fam])
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(ADS + path, headers={"api-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
        _last_call[fam] = time.monotonic()

        if is_rate_limited(payload):
            if attempt == RATE_LIMIT_RETRIES - 1:
                raise RuntimeError(
                    f"AdsPower {path.split('?')[0]}: still throttled after "
                    f"{RATE_LIMIT_RETRIES} attempts. The Local API allows ~1 "
                    f"request/second per family; something else is sharing it.")
            time.sleep(RATE_LIMIT_BACKOFF * (attempt + 1))
            continue

        if payload.get("code") != 0:
            raise RuntimeError(
                f"AdsPower {path.split('?')[0]}: {payload.get('msg')}")
        return payload.get("data") or {}


def fetch_cookies_v2(user_id, key):
    """The profile's cookie jar WITHOUT starting a browser.

    `GET /api/v2/browser-profile/cookies` — added by AdsPower specifically so
    a logged-in profile's cookies can be read programmatically. Measured
    2026-08-19 on a Base plan (the docs say "Professional or higher"; it
    answered anyway, so the gate is worth re-testing rather than assumed):

        131 cookies, 16 pinterest, both _auth and _pinterest_sess — and the
        browser was never started.

    That is the whole win. The CDP path had to start the browser, wait for it,
    read, and stop it — ~20s and a full Chromium per profile. This is one GET.

    `data.cookies` arrives as a JSON *string*, not a list, so it is decoded
    here rather than in every caller.
    """
    data = ads_call(f"/api/v2/browser-profile/cookies?profile_id={user_id}", key)
    raw = data.get("cookies")
    if raw is None:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


def vault_has_ua(profile_id, redis_url):
    """Does the vault already hold a user agent for this profile?

    Read-only, and the ONLY reason the syncer touches Redis. It decides whether
    a browser has to be started: the UA is obtainable nowhere else (no v1 or v2
    endpoint exposes it — `fingerprint_config` is write-only, `user/list` has no
    ua field), but it also never changes unless the profile's fingerprint is
    edited. So capture it once and skip the ~20s browser start forever after.

    Unreachable Redis returns False rather than raising: the cost of being
    wrong is one unnecessary browser start, and refusing to sync because a
    freshness optimisation could not run would be the wrong trade.
    """
    try:
        import redis
        r = redis.Redis.from_url(redis_url, decode_responses=True,
                                 socket_connect_timeout=3)
        return bool(r.hget(f"cookie:pinterest:{profile_id}", "user_agent"))
    except Exception:
        return False


def list_profiles(key, group=None):
    data = ads_call(f"/api/v1/user/list?page=1&page_size=100", key)
    rows = data.get("list") or []
    if group:
        rows = [r for r in rows if (r.get("group_name") or "") == group]
    return rows


async def _cdp(ws, msg_id, method, params=None):
    """One CDP call that RAISES on an error reply.

    The first version of this ignored `error` and returned
    `result.get("cookies") or []`. `Network.getAllCookies` does not exist on a
    browser-level target — CDP answered
    `-32601 'Network.getAllCookies' wasn't found`, the empty list came back,
    and every profile was reported "never signed in" while actually holding a
    live session. A plausible wrong answer, produced by swallowing an error
    that said exactly what was wrong.
    """
    await ws.send(json.dumps({"id": msg_id, "method": method,
                              "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") != msg_id:
            continue                      # an event, not our reply
        if msg.get("error"):
            raise RuntimeError(f"CDP {method}: {msg['error'].get('message')}")
        return msg.get("result") or {}


async def read_cookies(ws_url):
    """The profile's whole cookie jar, via the browser target.

    `Storage.getCookies` — NOT `Network.getAllCookies`, which only exists on a
    *page* target. AdsPower hands out a browser-level websocket, so the Network
    domain method is simply absent there. Measured: Storage 133 cookies,
    Network -32601.
    """
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        result = await _cdp(ws, 1, "Storage.getCookies")
        return result.get("cookies") or []


async def read_user_agent(ws_url):
    """The profile's real UA. AdsPower spoofs it per profile, and the vault
    stores it so `curl_cffi` replays the SAME identity the cookies belong to —
    a session replayed under a different UA is a mismatch worth avoiding."""
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        return (await _cdp(ws, 1, "Browser.getVersion")).get("userAgent")


def vault_profile_id(user_id):
    """AdsPower's immutable id -> the vault key suffix.

    Its own function so a test can assert stability against the REAL rule
    rather than re-implementing it. The extension cannot do this: it invents
    `profile_<random>` once and remembers it in chrome.storage, which a
    stateless reader has no access to.
    """
    return f"ads_{user_id}"


def build_payload(profile_id, cookies, user_agent):
    """The exact body the Go server receives — byte-identical to the
    extension's (see background.js). Pure, so it is testable without a socket.

    `cookie_json` is keyed by NAME, so a cookie present on several domains
    (csrftoken, _ir, g_state all are) collapses to one entry, last wins. The
    extension does `cookieJson[c.name] = c.value` and collapses identically —
    that agreement is the point, not an accident.
    """
    return {
        "cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
        "cookie_json": {c["name"]: c["value"] for c in cookies},
        "platform": "pinterest",
        "cookie_name": "all_cookies",
        "user_agent": user_agent,
        "profile_id": profile_id,
    }


def post_to_vault(profile_id, cookies, user_agent, dry_run=False):
    body = build_payload(profile_id, cookies, user_agent)
    if dry_run:
        return f"DRY-RUN would POST {len(body['cookie_json'])} cookies"
    req = urllib.request.Request(
        GO_SERVER, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {GO_TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("message", "ok")


def sync_one(row, key, dry_run=False, ua_mode="auto",
             redis_url=None, log=print):
    user_id = row["user_id"]
    name = row.get("name") or f"(unnamed {user_id})"
    profile_id = vault_profile_id(user_id)

    # `auto` is the default: start a browser ONLY when the vault has no UA yet.
    # A first sync pays ~20s once; every later run is the 2s fast path.
    if ua_mode == "always":
        need_ua = True
    elif ua_mode == "never":
        need_ua = False
    else:
        need_ua = not vault_has_ua(profile_id, redis_url)
        if need_ua:
            log(f"  {name:22} .... no UA stored yet — starting the browser once "
                f"to capture it (later runs skip this)")

    started = False
    try:
        if need_ua:
            # The UA is only obtainable from a running browser — no v1 or v2
            # endpoint exposes it (checked: user/list has no ua field, and
            # browser-profile/detail is 404). So this path costs a full browser
            # start, and is opt-in for that reason.
            data = ads_call(
                f"/api/v1/browser/start?user_id={user_id}&open_tabs=0", key)
            started = True
            ws_url = ((data.get("ws") or {}).get("puppeteer"))
            if not ws_url:
                log(f"  {name:22} SKIP — started but no CDP url returned")
                return False
            cookies = asyncio.run(read_cookies(ws_url))
        else:
            # The fast path: one GET, no browser, no CDP, no websocket.
            cookies = fetch_cookies_v2(user_id, key)

        pin = [c for c in cookies if "pinterest.com" in (c.get("domain") or "")]
        names = {c["name"] for c in pin}
        have = [a for a in AUTH_COOKIES if a in names]

        if not pin:
            log(f"  {name:22} SKIP — no pinterest cookies at all (never signed in)")
            return False
        if not have:
            # The distinction that matters: cookies exist, but not the ones
            # that prove a session. Storing this would look available and fail.
            log(f"  {name:22} SKIP — {len(pin)} pinterest cookies but NONE of "
                f"{list(AUTH_COOKIES)}; that is signed OUT, not signed in")
            return False

        # Omitted deliberately when we did not start a browser: the Go server
        # only HSETs user_agent when it is non-empty, so a UA captured by an
        # earlier --with-ua run SURVIVES. Sending "" would not overwrite it
        # either, but sending a GUESS would — so we send nothing.
        ua = asyncio.run(read_user_agent(ws_url)) if started else ""
        result = post_to_vault(profile_id, pin, ua, dry_run)
        csrf = "" if CSRF_COOKIE in names else "  ⚠ no csrftoken — POSTs will fail"
        log(f"  {name:22} OK   — {len(pin)} cookies (auth: {','.join(have)}) "
            f"-> cookie:pinterest:{profile_id} [{result}]{csrf}")
        return True

    except Exception as exc:
        log(f"  {name:22} FAIL — {type(exc).__name__}: {str(exc)[:70]}")
        return False
    finally:
        if started:
            try:
                ads_call(f"/api/v1/browser/stop?user_id={user_id}", key)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", help="AdsPower api key (or ADS_API_KEY env)")
    ap.add_argument("--group", default=DEFAULT_GROUP,
                    help=f"AdsPower group to sync (default: "
                         f"{DEFAULT_GROUP!r}). Pass --group '' for "
                         f"every profile, but note that mixes "
                         f"platforms — one group per platform is "
                         f"the point.")
    ap.add_argument("--dry-run", action="store_true",
                    help="read and report, write nothing to the vault")
    ap.add_argument("--ua-mode", choices=("auto", "never", "always"),
                    default="auto",
                    help="auto (default): start a browser only for profiles the "
                         "vault has no user agent for, then never again. "
                         "never: cookies only, fastest. "
                         "always: re-capture the UA every run (~20s per "
                         "profile) — for when a profile's fingerprint changed.")
    ap.add_argument("--redis-url", default=None,
                    help="vault URL, read-only, used by --ua-mode auto to see "
                         "which profiles already have a UA. Defaults to "
                         "REDIS_URL, then the Windows host from WSL.")
    args = ap.parse_args()

    import os
    key = args.key or os.environ.get("ADS_API_KEY", "").strip()
    if not key:
        print("no api key — pass --key or set ADS_API_KEY", file=sys.stderr)
        return 2

    rows = list_profiles(key, args.group or None)
    if not rows:
        print("no profiles matched — nothing to do (a real answer, not an error)")
        return 0

    print(f"{len(rows)} profile(s)"
          + (f" in group {args.group!r}" if args.group else "")
          + (" — DRY RUN, nothing will be written" if args.dry_run else ""))
    redis_url = (args.redis_url or os.environ.get("REDIS_URL")
                 or "redis://172.31.144.1:6379/0")
    synced = sum(sync_one(r, key, args.dry_run, args.ua_mode, redis_url)
                 for r in rows)

    print(f"\n{synced}/{len(rows)} synced to the vault.")
    if synced == 0:
        print("Nothing was written. If every profile says 'never signed in', "
              "that is the answer: a human has to sign them in to Pinterest "
              "once. No sync can copy a session that does not exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
