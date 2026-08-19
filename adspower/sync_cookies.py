"""Pull cookies out of AdsPower profiles and feed the vault — no extension.

The Chrome extension exists so a HUMAN's browser can volunteer its cookies:
it lives in the browser, watches `chrome.cookies.onChanged`, and pushes. On a
headless server there is no human and no reason to wait for a push. AdsPower
already hands out a CDP port for every profile it starts, and
`Network.getAllCookies` returns the same cookies the extension would have
scraped.

So this walks the profiles instead:

    GET  /api/v1/user/list              which profiles exist
    GET  /api/v1/browser/start          -> a CDP websocket per profile
    CDP  Network.getAllCookies          the cookies themselves
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

# The Local API answers "Too many request per second" to a burst. That reads
# like a failure and is only pacing, so every call goes through here.
RATE_LIMIT_SECONDS = 1.2

# What SessionManager requires to consider a profile usable. A profile missing
# these is signed out, whatever else it carries.
AUTH_COOKIES = ("_auth", "_pinterest_sess")

_last_call = 0.0


def ads_call(path, key, timeout=90):
    """One rate-limited AdsPower Local API call."""
    global _last_call
    wait = RATE_LIMIT_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(ADS + path, headers={"api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    _last_call = time.monotonic()
    if payload.get("code") != 0:
        raise RuntimeError(f"AdsPower {path.split('?')[0]}: {payload.get('msg')}")
    return payload.get("data") or {}


def list_profiles(key, group=None):
    data = ads_call(f"/api/v1/user/list?page=1&page_size=100", key)
    rows = data.get("list") or []
    if group:
        rows = [r for r in rows if (r.get("group_name") or "") == group]
    return rows


async def read_cookies(ws_url):
    """Network.getAllCookies over CDP. Returns the raw cookie list."""
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 2, "method": "Network.getAllCookies"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 2:
                return (msg.get("result") or {}).get("cookies") or []


async def read_user_agent(ws_url):
    """The profile's real UA. AdsPower spoofs it per profile, and the vault
    stores it so `curl_cffi` replays the SAME identity the cookies belong to —
    a session replayed under a different UA is a mismatch worth avoiding."""
    import websockets
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Browser.getVersion"}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                return (msg.get("result") or {}).get("userAgent")


def post_to_vault(profile_id, cookies, user_agent, dry_run=False):
    """Byte-identical to the extension's payload — see background.js."""
    cookie_json = {c["name"]: c["value"] for c in cookies}
    body = {
        "cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
        "cookie_json": cookie_json,
        "platform": "pinterest",
        "cookie_name": "all_cookies",
        "user_agent": user_agent,
        "profile_id": profile_id,
    }
    if dry_run:
        return f"DRY-RUN would POST {len(cookie_json)} cookies"
    req = urllib.request.Request(
        GO_SERVER, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {GO_TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("message", "ok")


def sync_one(row, key, dry_run=False, log=print):
    user_id = row["user_id"]
    name = row.get("name") or f"(unnamed {user_id})"
    profile_id = f"ads_{user_id}"

    started = False
    try:
        data = ads_call(f"/api/v1/browser/start?user_id={user_id}&open_tabs=0", key)
        started = True
        ws_url = ((data.get("ws") or {}).get("puppeteer"))
        if not ws_url:
            log(f"  {name:22} SKIP — AdsPower started it but returned no CDP url")
            return False

        cookies = asyncio.run(read_cookies(ws_url))
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

        ua = asyncio.run(read_user_agent(ws_url))
        result = post_to_vault(profile_id, pin, ua, dry_run)
        log(f"  {name:22} OK   — {len(pin)} cookies (auth: {','.join(have)}) "
            f"-> cookie:pinterest:{profile_id} [{result}]")
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
    ap.add_argument("--group", help="only profiles in this AdsPower group")
    ap.add_argument("--dry-run", action="store_true",
                    help="read and report, write nothing to the vault")
    args = ap.parse_args()

    import os
    key = args.key or os.environ.get("ADS_API_KEY", "").strip()
    if not key:
        print("no api key — pass --key or set ADS_API_KEY", file=sys.stderr)
        return 2

    rows = list_profiles(key, args.group)
    if not rows:
        print("no profiles matched — nothing to do (a real answer, not an error)")
        return 0

    print(f"{len(rows)} profile(s)"
          + (f" in group {args.group!r}" if args.group else "")
          + (" — DRY RUN, nothing will be written" if args.dry_run else ""))
    synced = sum(sync_one(r, key, args.dry_run) for r in rows)

    print(f"\n{synced}/{len(rows)} synced to the vault.")
    if synced == 0:
        print("Nothing was written. If every profile says 'never signed in', "
              "that is the answer: a human has to sign them in to Pinterest "
              "once. No sync can copy a session that does not exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
