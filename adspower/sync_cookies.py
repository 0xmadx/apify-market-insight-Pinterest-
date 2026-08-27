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
    python3 sync_cookies.py --group pinterest  # that group + pinterest-*
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request

ADS = "http://127.0.0.1:50325"

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
#   proxy       AdsPower's `user_proxy_config`, written STRAIGHT TO REDIS by
#               this script — the Go server has no proxy field to route it
#               through. The scraper then exits from the same IP the browser
#               does. The vault refuses to lease a profile without one
#               (REQUIRE_PROXY), because an unproxied profile exits from the
#               host and mixes a residential identity into a proxied pool.
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

# A group may carry a COUNTRY SUFFIX: "pinterest-fr" holds accounts that were
# created behind a French exit IP. They are the same platform and belong in the
# same vault pool — an account's country decides which proxy it may wear, not
# which trends it can fetch (region is a request parameter, never derived from
# the IP). So the suffix is a routing hint for `assign_proxies.py` and nothing
# more, and the sync must sweep the whole FAMILY or those accounts never reach
# the vault at all.
#
#   --group pinterest   ->  pinterest, pinterest-fr, pinterest-de   (the family)
#                       ->  NEVER etsy, and never etsy-anything
GROUP_SEPARATOR = "-"


def platform_of(group):
    """Which vault pool a group writes into. 'pinterest-fr' -> 'pinterest'.

    One pool, many countries. Splitting the pool per country would halve the
    concurrency for no safety gain: the thing that must stay consistent is one
    ACCOUNT to one exit IP, which the per-profile proxy already guarantees.
    """
    return (group or DEFAULT_GROUP).split(GROUP_SEPARATOR, 1)[0]


def in_family(group_name, family):
    """Is this profile's group part of the requested family?

    Exact match, or the family plus a suffix. Deliberately NOT a prefix test:
    `startswith("etsy")` would also match a group called "etsyshop", and the
    whole point of grouping is that an Etsy session can never land in the
    Pinterest pool.
    """
    group_name = group_name or ""
    return (group_name == family
            or group_name.startswith(family + GROUP_SEPARATOR))

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


def proxy_url(row):
    """`user_proxy_config` -> a proxy URL curl_cffi understands, or None.

    AdsPower is the SINGLE SOURCE OF TRUTH for a profile's exit IP, and that is
    the whole point: the browser that created the cookies and the scraper that
    replays them both derive their proxy from this one field, so they cannot
    drift apart. Assigning proxies in our own code instead would create a second
    source of truth, and the first silent disagreement is a cookie jar sent from
    an IP it was never born on — exactly the mismatch `Identity` exists to
    prevent.

    Verified 2026-08-19 that AdsPower returns `proxy_password` in the clear on
    read, which is what makes this direction possible at all.

    `no_proxy` returns None, and the caller must then CLEAR any stored proxy —
    see sync_one. A stale proxy is worse than none.
    """
    cfg = row.get("user_proxy_config") or {}
    if cfg.get("proxy_soft") in (None, "", "no_proxy"):
        return None
    host, port = cfg.get("proxy_host"), cfg.get("proxy_port")
    if not host or not port:
        return None
    scheme = (cfg.get("proxy_type") or "http").lower()
    if scheme not in ("http", "https", "socks5", "socks5h"):
        scheme = "http"
    user, pwd = cfg.get("proxy_user"), cfg.get("proxy_password")
    auth = f"{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(pwd, safe='')}@"         if user and pwd else ""
    return f"{scheme}://{auth}{host}:{port}"


def write_proxy(profile_id, proxy, redis_url, dry_run=False,
                platform="pinterest"):
    """Mirror the profile's proxy into the vault beside its cookies.

    Written DIRECTLY to Redis, not through the Go cookie server, because that
    server has no proxy field and it belongs to the extension's path — adding
    one would be a session-layer change. `vault.py` already reads
    `data.get("proxy")`, and its own comment anticipated this: phase 2 "changes
    the writer, not this code".

    Clearing matters as much as setting. If a proxy is removed in AdsPower the
    stored one MUST go, or the scraper keeps exiting from an IP the browser has
    stopped using — a mismatch that looks like nothing until it is a ban.
    """
    if dry_run:
        return "proxy: " + (proxy.split("@")[-1] if proxy else "none")
    try:
        import redis
        r = redis.Redis.from_url(redis_url, decode_responses=True,
                                 socket_connect_timeout=3)
        key = f"cookie:{platform}:{profile_id}"
        if proxy:
            r.hset(key, "proxy", proxy)
            return "proxy set " + proxy.split("@")[-1]
        removed = r.hdel(key, "proxy")
        return "proxy cleared" if removed else "no proxy"
    except Exception as exc:
        # Loud, because silently skipping this is how the IPs drift apart.
        return f"PROXY WRITE FAILED ({type(exc).__name__}) - IPs may now differ"


def vault_has_ua(profile_id, redis_url, platform="pinterest"):
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
        return bool(r.hget(f"cookie:{platform}:{profile_id}", "user_agent"))
    except Exception:
        return False


def list_profiles(key, group=None):
    data = ads_call(f"/api/v1/user/list?page=1&page_size=100", key)
    rows = data.get("list") or []
    if group:
        rows = [r for r in rows if in_family(r.get("group_name"), group)]
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


def build_payload(profile_id, cookies, user_agent, platform="pinterest"):
    """The canonical identity shape this project writes — byte-identical to
    what the Chrome extension produces (see its background.js). Pure, so it is
    testable without a socket, and `write_cookies` builds on it so these tests
    cover the path that actually runs.

    `cookie_json` is keyed by NAME, so a cookie present on several domains
    (csrftoken, _ir, g_state all are) collapses to one entry, last wins. The
    extension does `cookieJson[c.name] = c.value` and collapses identically —
    that agreement is the point, not an accident.
    """
    return {
        "cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
        "cookie_json": {c["name"]: c["value"] for c in cookies},
        "platform": platform,
        "cookie_name": "all_cookies",
        "user_agent": user_agent,
        "profile_id": profile_id,
    }


def write_cookies(profile_id, cookies, user_agent, redis_url, dry_run=False,
                  platform="pinterest"):
    """Write the identity straight to Redis, no Go server in the middle.

    WHY THIS EXISTS. Cookies used to go through the Go cookie server, which
    lives in the *Etsy* project and writes to whichever Redis IT was configured
    for. That was fine while both projects shared one Redis. The moment this
    project got its own (`pinterest-redis`, port 6380) it stopped being fine in
    the worst way: `write_proxy` writes DIRECTLY and would land in the new
    vault, while cookies went through the Go server into the OLD one. Half an
    identity in each — and `src.status` would show a profile with a proxy and no
    cookies, which reads as "never signed in" rather than "split brain".

    That path (`post_to_vault`) was deleted once this became the only writer;
    keeping a dead function pointed at another project's server was a footgun,
    not a fallback.

    Same fields, same key schema, same shape `browsers/identities.py:restore()`
    already writes. Nothing new is invented here; the hop is simply removed.
    """
    if dry_run:
        return f"DRY-RUN would write {len(cookies)} cookies"
    try:
        import redis

        r = redis.Redis.from_url(redis_url, decode_responses=True,
                                 socket_connect_timeout=3)
        # One source of truth for the shape. `cookie_json` is keyed by NAME,
        # collapsing a cookie that exists on several domains to one entry --
        # byte-identical to what the extension produces, so both writers agree.
        payload = build_payload(profile_id, cookies, user_agent, platform)
        jar = payload["cookie_json"]
        fields = {
            "cookies_json": json.dumps(jar),
            "cookie": payload["cookie"],
            "last_updated": str(time.time()),
            "is_valid": "1",
        }
        # ⚠️ ONLY when non-empty. The Go server this replaced HSET user_agent
        # only if it had one, so a UA captured by an earlier --ua-mode run
        # SURVIVED a later run that did not start a browser. sync_one() relies
        # on that and says so; the first version of this function wrote
        # `user_agent or ""` unconditionally and wiped every UA in the pool.
        #
        # The vault then refuses the profile ("no user_agent") — correctly,
        # because replaying a jar under an unknown browser is the mismatch the
        # whole session layer exists to avoid. Measured: 6 fresh profiles,
        # 0 usable.
        if user_agent:
            fields["user_agent"] = user_agent
        r.hset(f"cookie:{platform}:{profile_id}", mapping=fields)
        r.sadd(f"valid_profiles:{platform}", profile_id)
        return f"{len(jar)} cookies -> redis"
    except Exception as exc:
        return f"COOKIE WRITE FAILED ({type(exc).__name__})"


def sync_one(row, key, dry_run=False, ua_mode="auto",
             redis_url=None, log=print, platform=None):
    user_id = row["user_id"]
    # The profile's OWN group decides the pool, not the group that was asked
    # for: a `--group pinterest` sweep picks up `pinterest-fr` profiles too, and
    # each must land where its own name says.
    platform = platform or platform_of(row.get("group_name"))
    name = row.get("name") or f"(unnamed {user_id})"
    profile_id = vault_profile_id(user_id)

    # `auto` is the default: start a browser ONLY when the vault has no UA yet.
    # A first sync pays ~20s once; every later run is the 2s fast path.
    if ua_mode == "always":
        need_ua = True
    elif ua_mode == "never":
        need_ua = False
    else:
        need_ua = not vault_has_ua(profile_id, redis_url, platform)
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
        # Direct, so cookies land in the SAME Redis write_proxy uses.
        result = write_cookies(profile_id, pin, ua, redis_url, dry_run,
                               platform)
        csrf = "" if CSRF_COOKIE in names else "  ⚠ no csrftoken — POSTs will fail"
        # After the cookies, never before: a proxy pointing at a profile with no
        # session would be a half-written identity.
        pxy = write_proxy(profile_id, proxy_url(row), redis_url, dry_run,
                          platform)
        log(f"  {name:22} OK   — {len(pin)} cookies (auth: {','.join(have)}) "
            f"-> cookie:{platform}:{profile_id} [{result}] [{pxy}]{csrf}")
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
                    help=f"AdsPower group FAMILY to sync — the group itself "
                         f"plus any country suffix, so 'pinterest' also sweeps "
                         f"'pinterest-fr' and 'pinterest-de'. All of them write "
                         f"to the one 'pinterest' vault pool. (default: "
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
    import keys
    key = args.key or keys.ads_key()
    if not key:
        print("no api key — pass --key, or set one of "
              + "/".join(__import__("keys").ADS_NAMES)
              + " (the repo-root .env is read automatically)",
              file=sys.stderr)
        return 2

    rows = list_profiles(key, args.group or None)
    if not rows:
        print("no profiles matched — nothing to do (a real answer, not an error)")
        return 0

    print(f"{len(rows)} profile(s)"
          + (f" in group {args.group!r}" if args.group else "")
          + (" — DRY RUN, nothing will be written" if args.dry_run else ""))
    # 6380, NOT 6379. This project got its own Redis container
    # (`pinterest-redis`) on 2026-08-25; 6379 is the Etsy project's, and
    # writing there now fills a vault nothing reads.
    #
    # ⚠️ 127.0.0.1, NOT the WSL gateway IP. Measured 2026-08-26: from inside
    # WSL, `172.31.144.1:6380` and `127.0.0.1:6380` reach DIFFERENT Redis
    # instances (dbsize 8 vs 23) — a marker key written through the gateway was
    # invisible to the container. Docker Desktop forwards published ports to
    # 127.0.0.1 inside the distro, and that is the path that lands. It is also
    # correct from Windows, so one value works for both.
    #
    # In production this fallback loses anyway: the unit's EnvironmentFile
    # (/etc/adspower/api.env) sets REDIS_URL and beats it. That file is exactly
    # what kept feeding the OLD container after the vault moved — the new vault
    # went 85 minutes without a write, `src.status` read 0/8 usable, and the
    # writer reported 6/6 synced the whole time. Grep all four places.
    redis_url = (args.redis_url or os.environ.get("REDIS_URL")
                 or "redis://127.0.0.1:6380/0")
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
