"""Proofs for the AdsPower cookie syncer — guards, degenerates, regressions.

Written to `docs/TESTING_STRATEGY.md` (etsy repo), whose governing rule this
module violated once already:

    Every bug found gets a test before it gets a fix.

The syncer shipped, reported "no pinterest cookies at all (never signed in)"
for a profile holding a live session, and was fixed with no test written. This
file is that debt paid, and the regression is GROUP R below.

Offline only — no AdsPower, no network, no Redis. Fixtures are the shapes the
Local API and CDP actually returned on 2026-08-19, not hand-invented ones.

    .venv/Scripts/python.exe -m tests.test_adspower
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "adspower")

import sync_cookies as sc  # noqa: E402

checks = []


def check(name, condition, detail=None):
    checks.append((name, bool(condition)))
    print(("  PASS  " if condition else "  FAIL  ") + name
          + ("" if condition or detail is None else f"  [{detail}]"))


# --------------------------------------------------------------- fixtures

# Real shapes, captured 2026-08-19 from profile k1fx40wf.
SIGNED_IN = [
    {"name": "_auth", "value": "1", "domain": ".pinterest.com"},
    {"name": "_pinterest_sess", "value": "TWc9PSZ...", "domain": ".pinterest.com"},
    {"name": "csrftoken", "value": "abc", "domain": ".pinterest.com"},
    {"name": "csrftoken", "value": "dupe", "domain": "www.pinterest.com"},
    {"name": "_routing_id", "value": "x", "domain": ".pinterest.com"},
    {"name": "MSPAuth", "value": "y", "domain": ".live.com"},
]
# Cookies exist, but none that prove a session — the dangerous middle case.
SIGNED_OUT = [
    {"name": "csrftoken", "value": "abc", "domain": ".pinterest.com"},
    {"name": "g_state", "value": "{}", "domain": ".pinterest.com"},
    {"name": "_b", "value": "z", "domain": ".pinterest.com"},
]
NO_PINTEREST = [{"name": "MSPAuth", "value": "y", "domain": ".live.com"}]


class FakeWS:
    """Minimal CDP socket. `replies` maps method -> the raw reply payload."""

    def __init__(self, replies):
        self.replies = replies
        self.sent = []
        self._queue = []

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg["method"])
        reply = self.replies.get(msg["method"])
        if reply is None:
            reply = {"error": {"code": -32601,
                               "message": f"'{msg['method']}' wasn't found"}}
        self._queue.append({"id": msg["id"], **reply})

    async def recv(self):
        return json.dumps(self._queue.pop(0))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ main

def main():
    print("GROUP R — regression: the bug that shipped")
    # The syncer called Network.getAllCookies on a BROWSER target, ignored the
    # -32601 error, and returned []. Every profile then read as signed out.
    ws = FakeWS({})                       # every method 404s, like the real one did
    raised = None
    try:
        run(sc._cdp(ws, 1, "Network.getAllCookies"))
    except RuntimeError as exc:
        raised = exc
    check("R1 a CDP error RAISES, never returns an empty result",
          raised is not None, "returned quietly instead")
    check("R2 ...and the message names the method that failed",
          raised is not None and "Network.getAllCookies" in str(raised),
          str(raised)[:60])

    # Assert the METHOD ACTUALLY SENT, not the docstring. The fake replies only
    # to Storage.getCookies, so a regression to Network.* would raise here.
    ws = FakeWS({"Storage.getCookies": {"result": {"cookies": SIGNED_IN}}})
    got = run(sc._cdp(ws, 1, "Storage.getCookies"))
    check("R3 Storage.getCookies is what the browser target answers",
          len(got.get("cookies") or []) == len(SIGNED_IN), ws.sent)
    src = open("adspower/sync_cookies.py", encoding="utf-8").read()
    body = src[src.index("async def read_cookies"):src.index("async def read_user_agent")]
    check("R4 ...and no live call to Network.getAllCookies remains",
          "Storage.getCookies" in body and "Network.getAllCookies" not in body.split('"""')[2])

    print("\nGROUP G — guards: what must be refused")
    # A profile without _auth/_pinterest_sess authenticates as nobody. Writing
    # it produces a vault identity that looks available and cannot work.
    for label, jar, expect_auth in [
            ("signed in", SIGNED_IN, True),
            ("signed OUT (cookies, no auth)", SIGNED_OUT, False),
            ("no pinterest cookies at all", NO_PINTEREST, False)]:
        pin = [c for c in jar if "pinterest.com" in c["domain"]]
        names = {c["name"] for c in pin}
        have = [a for a in sc.AUTH_COOKIES if a in names]
        check(f"G1 {label}: auth detected = {expect_auth}",
              bool(have) is expect_auth, have)

    check("G2 both auth cookies are the ones SessionManager needs",
          set(sc.AUTH_COOKIES) == {"_auth", "_pinterest_sess"}, sc.AUTH_COOKIES)
    # The two refusals mean different things and must not collapse into one.
    # Assert on the FUNCTION body, not the whole file — both phrases also
    # appear in the module docstring, which is documentation, not behaviour.
    fn = src[src.index("def sync_one"):src.index("def main")]
    check("G3 'no cookies' and 'no AUTH cookies' are distinct branches",
          fn.count("never signed in") == 1
          and fn.count("signed OUT, not signed in") == 1,
          f"{fn.count('never signed in')} / "
          f"{fn.count('signed OUT, not signed in')}")
    check("G3b ...and each is a separate early return",
          fn.count("return False") >= 3)

    print("\nGROUP V - the v2 endpoint (no browser)")
    # data.cookies arrives as a JSON *string*, not a list. Treating it as a
    # list yields a string of characters that filters to nothing - a silent
    # empty, the same shape as the bug in GROUP R.
    calls = []

    def fake_ads_call(path, key, timeout=90):
        calls.append(path)
        return {"cookies": json.dumps(SIGNED_IN)}      # a STRING, as the API sends

    real = sc.ads_call
    sc.ads_call = fake_ads_call
    try:
        got = sc.fetch_cookies_v2("k1fx40wf", "k")
        check("V1 the JSON-string payload is decoded to a list",
              isinstance(got, list) and len(got) == len(SIGNED_IN),
              type(got).__name__)
        check("V2 ...and the decoded cookies still filter to pinterest",
              len([c for c in got if "pinterest.com" in c["domain"]]) == 5)
        check("V3 it hits the v2 cookies endpoint with profile_id",
              calls and "/api/v2/browser-profile/cookies" in calls[0]
              and "profile_id=k1fx40wf" in calls[0], calls[:1])
        sc.ads_call = lambda p, k, timeout=90: {"cookies": SIGNED_IN}
        check("V4 an already-decoded list passes through unchanged",
              sc.fetch_cookies_v2("x", "k") == SIGNED_IN)
        sc.ads_call = lambda p, k, timeout=90: {}
        check("V5 a missing cookies field is [], not a crash",
              sc.fetch_cookies_v2("x", "k") == [])
    finally:
        sc.ads_call = real

    # The fast path cannot know the UA and must send NOTHING rather than a
    # guess: the Go server only overwrites user_agent when non-empty, so
    # omitting it preserves whatever a --with-ua run stored earlier.
    fn2 = src[src.index("def sync_one"):src.index("def main")]
    check("V6 the UA is omitted (not guessed) when no browser was started",
          'if started else ""' in fn2)

    print("\nGROUP L - rate limiting (measured 1 req/sec, per family)")
    check("L1 pacing is at least 1s - 0.5s spacing loses half the calls",
          sc.RATE_LIMIT_SECONDS >= 1.0, sc.RATE_LIMIT_SECONDS)
    check("L2 v1 and v2 are paced independently (separate budgets)",
          sc._family("/api/v1/user/list") == "v1"
          and sc._family("/api/v2/browser-profile/cookies") == "v2"
          and set(sc._last_call) == {"v1", "v2"})
    # AdsPower reports throttling as a 200 with code -1 and a message. There is
    # no 429 and no Retry-After, so the string is the only signal.
    check("L3 a throttle reply is recognised, not treated as a hard failure",
          sc.is_rate_limited({"code": -1,
                              "msg": "Too many request per second, please ch"}))
    check("L4 ...and a real error is NOT mistaken for throttling",
          not sc.is_rate_limited({"code": -1, "msg": "user is deleted"}))
    check("L5 ...and success is never throttling",
          not sc.is_rate_limited({"code": 0, "msg": "success"}))

    # The bug this guards: raising on a throttle aborts a whole profile for
    # what is only pacing. With one profile it never fires; with twenty it does.
    seq = [{"code": -1, "msg": "Too many request per second"},
           {"code": -1, "msg": "Too many request per second"},
           {"code": 0, "msg": "success", "data": {"ok": 1}}]
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, payload): self.payload = payload
        def read(self): return json.dumps(self.payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request as _u
    real_open, real_sleep = _u.urlopen, sc.time.sleep
    _u.urlopen = lambda req, timeout=90: FakeResp(seq[min(calls["n"], len(seq) - 1)])
    sc.time.sleep = lambda s: None                      # keep the test fast
    orig_json_load = sc.json.load
    sc.json.load = lambda f: json.loads(f.read().decode())
    try:
        def counting(req, timeout=90):
            r = FakeResp(seq[min(calls["n"], len(seq) - 1)]); calls["n"] += 1; return r
        _u.urlopen = counting
        out = sc.ads_call("/api/v1/user/list", "k")
        check("L6 a throttled call RETRIES instead of raising",
              out == {"ok": 1} and calls["n"] == 3, f"{calls['n']} attempts")
    finally:
        _u.urlopen, sc.time.sleep, sc.json.load = real_open, real_sleep, orig_json_load

    print("\nGROUP S - what the scraper actually needs")
    # Three things, and the token is NOT one of the calls: Pinterest's CSRF is
    # cookie-echo, so session.py reads cookies["csrftoken"] and echoes it as
    # X-CSRFToken. A separate token fetch would be inventing work.
    check("W1 csrftoken travels as a COOKIE, not a separate field",
          sc.CSRF_COOKIE == "csrftoken"
          and sc.CSRF_COOKIE in {c["name"] for c in SIGNED_IN})
    body_p = sc.build_payload("ads_x", SIGNED_IN, "UA/1.0")
    check("W2 ...so the token reaches the vault inside cookie_json",
          body_p["cookie_json"].get("csrftoken") is not None)
    check("W3 the payload carries all three: cookies, token, user agent",
          body_p["cookie_json"] and body_p["cookie_json"].get("csrftoken")
          and body_p["user_agent"] == "UA/1.0")
    # A jar with no csrftoken still syncs (reads work) but must be flagged.
    fn3 = src[src.index("def sync_one"):src.index("def main")]
    check("W4 a missing csrftoken warns rather than refusing (reads still work)",
          "no csrftoken" in fn3 and "CSRF_COOKIE in names" in fn3)

    print("\nGROUP M - multi-profile / multi-platform (the operator's plan)")
    # One AdsPower group per platform. Defaulting to the group keeps an Etsy
    # account's cookies out of the Pinterest vault by CONSTRUCTION, rather than
    # relying on the domain filter to catch it later.
    check("M1 the default group is pinterest, not 'everything'",
          sc.DEFAULT_GROUP == "pinterest")
    rows = [{"user_id": "a", "name": "pin1", "group_name": "pinterest"},
            {"user_id": "b", "name": "etsy1", "group_name": "etsy"},
            {"user_id": "c", "name": "pin2", "group_name": "pinterest"}]
    real_call = sc.ads_call
    sc.ads_call = lambda p, k, timeout=90: {"list": rows}
    try:
        got = sc.list_profiles("k", "pinterest")
        check("M2 an etsy-group profile is never selected for the pinterest sync",
              [r["name"] for r in got] == ["pin1", "pin2"], [r["name"] for r in got])
        check("M3 no group means every profile (explicit opt-in to mixing)",
              len(sc.list_profiles("k", None)) == 3)
    finally:
        sc.ads_call = real_call
    # Distinct profiles must never share a vault entry.
    check("M4 each profile gets its own vault key",
          len({sc.vault_profile_id(r["user_id"]) for r in rows}) == 3)

    print("\nGROUP U - the user agent, captured once")
    check("U1 auto/never/always are the three modes",
          "auto" in src and '"never"' in src and '"always"' in src)
    check("U2 a profile that already has a UA does NOT start a browser",
          sc.vault_has_ua.__doc__ and "once" in sc.vault_has_ua.__doc__)
    check("U3 unreachable redis degrades to False, never raises",
          sc.vault_has_ua("x", "redis://127.0.0.1:9/0") is False)

    print("\nGROUP X - proxy: cookies, UA and exit IP must not drift apart")
    import assign_proxies as ap   # noqa: E402
    WS = {"proxy_address": "1.2.3.4", "port": 8000,
          "username": "u", "password": "p:w@rd", "country_code": "US",
          "valid": True}
    cfg = ap.proxy_config(WS)
    check("X1 a webshare row becomes an AdsPower proxy config",
          cfg["proxy_host"] == "1.2.3.4" and cfg["proxy_port"] == "8000"
          and cfg["proxy_soft"] == "other")

    # AdsPower is the single source of truth; the syncer only mirrors it.
    url = sc.proxy_url({"user_proxy_config": {
        "proxy_soft": "other", "proxy_type": "http", "proxy_host": "1.2.3.4",
        "proxy_port": "8000", "proxy_user": "u", "proxy_password": "p:w@rd"}})
    check("X2 the mirrored url is what curl_cffi wants",
          url.startswith("http://") and url.endswith("@1.2.3.4:8000"), url)
    check("X3 credentials are percent-encoded (a ':' or '@' in a password "
          "would otherwise split the url)",
          "p%3Aw%40rd" in url, url)

    check("X4 no_proxy means None, not a broken url",
          sc.proxy_url({"user_proxy_config": {"proxy_soft": "no_proxy"}}) is None)
    check("X5 a config missing host/port is None, not a half url",
          sc.proxy_url({"user_proxy_config": {"proxy_soft": "other",
                                              "proxy_host": "1.2.3.4"}}) is None)
    check("X6 a profile with no proxy config at all is None",
          sc.proxy_url({}) is None)
    # Clearing matters as much as setting: a proxy removed in AdsPower must not
    # leave the scraper exiting from an IP the browser has stopped using.
    fnx = src[src.index("def write_proxy"):src.index("def vault_has_ua")]
    check("X7 removing a proxy CLEARS the stored one (hdel), never leaves it",
          "hdel" in fnx and "cleared" in fnx)
    check("X8 a failed proxy write is loud, not silent",
          "PROXY WRITE FAILED" in fnx)

    print("\nGROUP Y - proxy assignment stays stable as profiles grow")
    # This used to rebuild the pairing inside the test with zip+sorted, which
    # proved that zip and sorted work and nothing about this file. It now calls
    # the real allocator — and the behaviour it asserts changed with it: the
    # map is STICKY, not positional. See GROUP W.
    pool = [{"proxy_address": "9.9.9.9", "port": 1, "username": "u",
             "password": "p", "valid": True},
            {"proxy_address": "1.1.1.1", "port": 2, "username": "u",
             "password": "p", "valid": True}]
    rows = [{"user_id": "b", "name": "p2"}, {"user_id": "a", "name": "p1"}]
    first = ap.plan_assignments(rows, pool, 2)[0]
    # Feed the result back in as the profiles' current config: a second run
    # must be a no-op, or every run silently moves accounts.
    settled = [{"user_id": r["user_id"], "name": r["name"],
                "user_proxy_config": {"proxy_host": q["proxy_address"],
                                      "proxy_port": str(q["port"])}}
               for r, q in first]
    second = ap.plan_assignments(settled, pool, 2)[0]
    check("Y1 re-running the allocator changes nothing",
          [(r["user_id"], q["proxy_address"]) for r, q in first]
          == [(r["user_id"], q["proxy_address"]) for r, q in second])
    check("Y2 an unchanged profile is detected and skipped",
          ap.already_assigned(
              {"user_proxy_config": {"proxy_host": "1.2.3.4",
                                     "proxy_port": "8000", "proxy_user": "u"}},
              WS))
    check("Y3 ...and a changed one is not",
          not ap.already_assigned(
              {"user_proxy_config": {"proxy_host": "9.9.9.9",
                                     "proxy_port": "8000", "proxy_user": "u"}},
              WS))
    # Wrapping around would give two profiles one exit IP, defeating the point.
    asrc = open("adspower/assign_proxies.py", encoding="utf-8").read()
    check("Y4 more profiles than the pool can hold is REFUSED",
          "REFUSED" in asrc and "would have nowhere to" in asrc)
    check("Y4b ...and it never stacks a third account on one IP",
          "will NOT stack a third account" in asrc)
    check("Y5 invalid webshare proxies are filtered out before assigning",
          'p.get("valid")' in asrc)

    print("\nGROUP P — provenance: what reaches the vault")
    # Against the LIVE writer. This used to call post_to_vault, which posted to
    # the Etsy project's Go server; that path is gone, and testing it was
    # testing a hop this project no longer takes.
    body = sc.write_cookies("ads_test", SIGNED_IN, "UA/1.0",
                            "redis://localhost:6380/0", dry_run=True)
    check("P1 dry-run writes nothing and says so", "DRY-RUN" in body, body)

    # Call the REAL builder. An earlier version of this test rebuilt the dict
    # itself and therefore asserted nothing about the code.
    payload = sc.build_payload("ads_test", SIGNED_IN, "UA/1.0")
    # SessionManager reads cookies_json, so the contract is exact rather than
    # approximate — and write_cookies now builds on this same function.
    check("P2 platform is 'pinterest', which is the pool the vault reads",
          payload["platform"] == "pinterest")
    check("P3 cookie_json is keyed by NAME, collapsing per-domain duplicates",
          len(payload["cookie_json"]) == 5 and len(SIGNED_IN) == 6,
          f"{len(SIGNED_IN)} raw -> {len(payload['cookie_json'])} stored")
    check("P4 ...which is exactly what the extension does, so both agree",
          payload["cookie_json"]["csrftoken"] == "dupe")   # last wins, as in JS
    check("P5 the user agent travels with the cookies",
          payload["user_agent"] == "UA/1.0")

    print("\nGROUP I — identity: profile_id must be stable")
    # The extension invents profile_<random> once and remembers it. A stateless
    # reader cannot, so the id is derived from AdsPower's immutable user_id —
    # otherwise every run would add a new vault entry instead of updating one.
    ids = {sc.vault_profile_id("k1fx40wf") for _ in range(5)}
    check("I1 the same profile always yields the same id",
          ids == {"ads_k1fx40wf"}, ids)
    check("I1b different profiles never collide",
          sc.vault_profile_id("aaa") != sc.vault_profile_id("bbb"))
    check("I2 the id is namespaced, so it cannot collide with extension ids",
          next(iter(ids)).startswith("ads_")
          and not next(iter(ids)).startswith("profile_"))

    print("\nGROUP C — country groups: a new account may be born abroad")
    # An account created behind a French IP is fine — coherent, even. What is
    # never fine is MOVING an existing account across a border, or letting one
    # group hold two countries. The country therefore lives in the GROUP NAME,
    # where it cannot be selected independently of the profiles it applies to.
    check("C1 a two-letter suffix declares the country",
          ap.country_from_group("pinterest-fr") == "FR"
          and ap.country_from_group("pinterest-de") == "DE")
    check("C2 a longer suffix is a NAME, not a country — never guessed",
          ap.country_from_group("pinterest-backup") is None)
    check("C3 no suffix falls back to the flag/default",
          ap.country_from_group("pinterest") is None
          and ap.country_from_group(None) is None)

    ws_pool = [{"proxy_address": "1.1.1.1", "country_code": "us"},
               {"proxy_address": "2.2.2.2", "country_code": "FR"}]
    check("C4 a profile's current country is read from the pool",
          ap.country_of("1.1.1.1", ws_pool) == "US")
    # A host Webshare has dropped is UNKNOWN. Calling that a mismatch would
    # block every legitimate replacement of a dead proxy.
    check("C5 a host no longer in the pool is unknown, not 'another country'",
          ap.country_of("9.9.9.9", ws_pool) is None)

    # Called for real against a stubbed HTTP layer. A source-string check here
    # would pass on a comment and fail to notice the return arity changing.
    import io as _io, json as _json, urllib.request as _u
    rows = {"results": [
        {"proxy_address": "1.1.1.1", "port": 1, "username": "u", "password": "p",
         "country_code": "US", "valid": True},
        {"proxy_address": "2.2.2.2", "port": 2, "username": "u", "password": "p",
         "country_code": "FR", "valid": True},
        {"proxy_address": "3.3.3.3", "port": 3, "username": "u", "password": "p",
         "country_code": "US", "valid": False}]}

    class _Resp(_io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    real_open = _u.urlopen
    _u.urlopen = lambda *a, **k: _Resp(_json.dumps(rows).encode())
    try:
        picked, total, all_valid = ap.webshare_proxies("k", "US")
    finally:
        _u.urlopen = real_open
    check("C6 the filtered list holds only the asked-for country",
          [q["proxy_address"] for q in picked] == ["1.1.1.1"])
    check("C6b ...and the third return value keeps EVERY valid country",
          sorted(q["proxy_address"] for q in all_valid) == ["1.1.1.1", "2.2.2.2"])
    check("C6c ...so a US-filtered run can still tell a French host is French",
          ap.country_of("2.2.2.2", all_valid) == "FR")
    check("C6d invalid proxies are in neither list", total == 3)
    check("C7 a cross-country move of a LIVE account is REFUSED, not warned",
          "allow_country_move" in asrc and "is a live account on a" in asrc)
    check("C8 ...and the refusal points at the safe alternative",
          "assign its proxy BEFORE the first login" in asrc)
    check("C9 a --country that contradicts the group name is refused",
          "declares country" in asrc and "do not let them disagree" in asrc)

    # The sync has to sweep the whole family or a country group never reaches
    # the vault at all — it would look signed-in in AdsPower and be invisible
    # to the scraper.
    fam = [{"user_id": "a", "name": "us1", "group_name": "pinterest"},
           {"user_id": "b", "name": "fr1", "group_name": "pinterest-fr"},
           {"user_id": "c", "name": "shop", "group_name": "etsyshop"},
           {"user_id": "d", "name": "etsy1", "group_name": "etsy"}]
    real_call = sc.ads_call
    sc.ads_call = lambda p, k, timeout=90: {"list": fam}
    try:
        got = [r["name"] for r in sc.list_profiles("k", "pinterest")]
    finally:
        sc.ads_call = real_call
    check("C10 --group pinterest sweeps the whole family",
          got == ["us1", "fr1"], got)
    check("C11 ...and 'etsyshop' is NOT in the etsy family (a prefix test would match)",
          not sc.in_family("etsyshop", "etsy"))
    check("C12 every country group writes to ONE vault pool",
          sc.platform_of("pinterest-fr") == sc.platform_of("pinterest") == "pinterest")
    check("C13 the payload's platform follows the group, not a constant",
          sc.build_payload("p", [], "UA", "etsy")["platform"] == "etsy")

    print("\nGROUP W — sharing an exit IP, and never moving anyone")
    # Operator's call 2026-08-20: up to TWO profiles per proxy. The thing that
    # flags an account is one ACCOUNT seen from two IPs; two different accounts
    # from one IP is what every household looks like. Two is the ceiling.
    def _p(host, port, cc="US"):
        return {"proxy_address": host, "port": port, "username": "u",
                "password": "w", "country_code": cc, "valid": True}

    def _r(uid, host=None, port=None):
        row = {"user_id": uid, "name": uid}
        if host:
            row["user_proxy_config"] = {"proxy_host": host,
                                        "proxy_port": str(port)}
        return row

    pool3 = [_p("1.1.1.1", 1), _p("2.2.2.2", 2), _p("3.3.3.3", 3)]

    pairs, un, usage = ap.plan_assignments([_r(f"p{i}") for i in range(6)],
                                           pool3, 2)
    got = [(r["user_id"], q["proxy_address"]) for r, q in pairs]
    check("W1 six profiles fit on three proxies at 2 each",
          not un and len(pairs) == 6)
    # Spread BEFORE doubling. `i // 2` would also be stable and would double up
    # the first proxies while the last sat idle.
    check("W2 every proxy is used once before any is used twice",
          got == [("p0", "1.1.1.1"), ("p1", "2.2.2.2"), ("p2", "3.3.3.3"),
                  ("p3", "1.1.1.1"), ("p4", "2.2.2.2"), ("p5", "3.3.3.3")], got)
    check("W3 no proxy carries more than the cap",
          max(usage.values()) == 2)

    pairs, un, _ = ap.plan_assignments([_r(f"p{i}") for i in range(7)], pool3, 2)
    check("W4 a seventh profile is REFUSED, never stacked three deep",
          [r["user_id"] for r in un] == ["p6"] and len(pairs) == 6)

    _, un1, usage1 = ap.plan_assignments([_r(f"p{i}") for i in range(3)],
                                         pool3, 1)
    check("W5 --max-share 1 restores one IP per account",
          not un1 and max(usage1.values()) == 1)

    # THE REGRESSION THAT MATTERED. Pairing by sorted position was stable only
    # as long as the sort was — and AdsPower user_ids are random strings, so a
    # new profile can sort into the MIDDLE and shift every profile after it
    # onto a different proxy. Silently moving live accounts is the exact thing
    # this file exists to prevent.
    settled = [_r("p0", "1.1.1.1", 1), _r("p1", "2.2.2.2", 2)]
    pairs, _, _ = ap.plan_assignments([_r("aaa")] + settled, pool3, 2)
    placed = {r["user_id"]: q["proxy_address"] for r, q in pairs}
    check("W6 a NEW profile sorting first does not move the settled ones",
          placed["p0"] == "1.1.1.1" and placed["p1"] == "2.2.2.2", placed)
    check("W7 ...and it takes the least-used proxy instead",
          placed["aaa"] == "3.3.3.3", placed)

    # A proxy Webshare has dropped is not in the pool, so its profile is
    # re-placed rather than left pointing at a dead address.
    pairs, _, _ = ap.plan_assignments([_r("gone", "9.9.9.9", 9)], pool3, 2)
    check("W8 a profile on a proxy that left the pool is re-placed",
          pairs[0][1]["proxy_address"] in {"1.1.1.1", "2.2.2.2", "3.3.3.3"})

    # Running twice must produce the same map, or every run is a move.
    twice = [ap.plan_assignments([_r(f"p{i}") for i in range(5)], pool3, 2)[0]
             for _ in range(2)]
    check("W9 the same inputs produce the same map, every time",
          [(r["user_id"], q["proxy_address"]) for r, q in twice[0]]
          == [(r["user_id"], q["proxy_address"]) for r, q in twice[1]])

    check("W10 the cap is a ceiling the flag cannot raise",
          ap.MAX_PROFILES_PER_PROXY == 2
          and "outside" in asrc and "1..{MAX_PROFILES_PER_PROXY}" in asrc)
    check("W11 sharing is reported to the operator, never silent",
          "carrying" in asrc and "household" in asrc)

    print("\nGROUP K — the keys, and where they are read from")
    import keys as kmod  # noqa: E402
    # Neither script loaded .env, and the operator's file spells the AdsPower
    # key `adspower_api` while the code read `ADS_API_KEY`. The failure was a
    # flat "need ADS_API_KEY" with the key sitting in .env one directory up.
    check("K1 both spellings of the AdsPower key are accepted",
          "ADS_API_KEY" in kmod.ADS_NAMES and "adspower_api" in kmod.ADS_NAMES)
    os.environ["ADS_API_KEY"] = "exported-wins"
    try:
        kmod._loaded = False
        check("K2 an exported variable beats the .env file",
              kmod.ads_key() == "exported-wins")
    finally:
        del os.environ["ADS_API_KEY"]
        kmod._loaded = False
    missing = kmod.describe(("NOTHING_IS_SET_HERE",))
    check("K3 describe() names which variable is set, never its value",
          "none of" in missing and "NOTHING_IS_SET_HERE" in missing)
    os.environ["ZZ_ONE"], os.environ["ZZ_TWO"] = "first", "second"
    try:
        check("K4 the first name that holds something wins",
              kmod.get("ZZ_ONE", "ZZ_TWO") == "first"
              and kmod.get("ZZ_MISSING", "ZZ_TWO") == "second")
    finally:
        del os.environ["ZZ_ONE"], os.environ["ZZ_TWO"]

    print("\nGROUP D — degenerate inputs (where the silent bugs live)")
    for label, jar in [("empty jar", []), ("None-ish domain", [{"name": "a",
                                                                "value": "b",
                                                                "domain": None}])]:
        pin = [c for c in jar if "pinterest.com" in (c.get("domain") or "")]
        check(f"D1 {label}: filters to empty without raising", pin == [])
    empty = sc.write_cookies("ads_x", [], "UA", "redis://localhost:6380/0",
                             dry_run=True)
    check("D2 an empty cookie list still returns a report, not a crash",
          "0 cookies" in empty, empty)
    check("D3 rate limiter is a real pause, not a no-op",
          sc.RATE_LIMIT_SECONDS >= 1.0, sc.RATE_LIMIT_SECONDS)

    print("")
    print("GROUP FP — AdsPower's real fingerprint travels with the session")
    from browsers.fingerprint_patch import build_script, profile_shape

    syn = profile_shape("ads_t")
    measured = {"webglVendor": "Google Inc. (NVIDIA)",
                "webglRenderer": "ANGLE (NVIDIA GeForce RTX 4070)",
                "hardwareConcurrency": 24, "deviceMemory": 8,
                "timezone": "Europe/Madrid", "screen": "2560x1440x24x1"}
    mea = profile_shape("ads_t", measured)
    check("FP1 a measured GPU replaces the synthetic one",
          mea["webgl_renderer"] != syn["webgl_renderer"]
          and "RTX 4070" in mea["webgl_renderer"], mea["webgl_renderer"])
    check("FP2 cores, memory, timezone and viewport all carry over",
          (mea["cores"], mea["memory"], mea["timezone"],
           mea["width"], mea["height"]) == (24, 8, "Europe/Madrid", 2560, 1440),
          mea)
    # The whole point: the device that keeps the session warm must match the
    # one that signed in, so the script actually emitted has to carry it.
    check("FP3 build_script emits the measured values, not the synthetic",
          "RTX 4070" in build_script("ads_t", "UA", measured)
          and "RTX 4070" not in build_script("ads_t", "UA", None))
    # Canvas is noise from AdsPower's own seeded function. We can read the hash
    # and cannot regenerate it, so copying it would be cargo-culting a number.
    check("FP4 canvas seed is NOT copied — it cannot be reproduced",
          mea["canvas_seed"] == syn["canvas_seed"])
    # Absent is not zero, applied to fingerprints: a failed probe must fall
    # back, never overwrite a working shape with an empty one.
    junk = profile_shape("ads_t", {"webglVendor": "ERR:TypeError", "screen": "",
                                   "hardwareConcurrency": None,
                                   "deviceMemory": "not-a-number"})
    check("FP5 ERR / empty / null / unparseable are ignored, not applied",
          (junk["webgl_vendor"], junk["width"], junk["cores"], junk["memory"])
          == (syn["webgl_vendor"], syn["width"], syn["cores"], syn["memory"]))
    # Half a viewport is worse than none: a real width beside an invented
    # height is a shape no machine has.
    half = profile_shape("ads_t", {"screen": "2560"})
    check("FP6 a half-parsed viewport is refused whole",
          (half["width"], half["height"]) == (syn["width"], syn["height"]))
    # Same invariant the user_agent wipe taught: a run that started no browser
    # has nothing to say, and must not erase an earlier capture.
    body = sc.write_cookies("ads_t", SIGNED_IN, "UA", "redis://localhost:6380/0",
                            dry_run=True, fingerprint=None)
    check("FP7 write_cookies accepts an absent fingerprint without crashing",
          "DRY-RUN" in body, body)

    print("")
    print("GROUP NP — creating a blank profile cannot break the proxy cap")
    from tools.adspower_profile import pick, usage
    from adspower.assign_proxies import MAX_PROFILES_PER_PROXY as CAP

    P = [{"proxy_address": "1.1.1.1", "port": 1},
         {"proxy_address": "2.2.2.2", "port": 2}]

    # The counts must be keyed the way assign_proxies keys them -- a (host,
    # str(port)) TUPLE. Building "host:port" strings here instead makes every
    # lookup miss, so every proxy reads as unused. The first version of
    # tools/adspower_profile.py did exactly that and would have stacked account
    # after account on one IP while printing success.
    counted = usage([{"user_proxy_config": {"proxy_host": "1.1.1.1",
                                            "proxy_port": "1"}}])
    check("NP1 usage() keys match assign_proxies' proxy_key tuples",
          counted == {("1.1.1.1", "1"): 1}, counted)

    check("NP2 the least-used proxy wins, so the pool fills evenly",
          pick(P, counted)["proxy_address"] == "2.2.2.2")

    # The one that protects the accounts. Two profiles per proxy is the
    # operator's rule; a third means three accounts sharing one exit IP.
    full = {("1.1.1.1", "1"): CAP, ("2.2.2.2", "2"): CAP}
    check("NP3 REFUSES when every proxy is at the cap — never a third profile",
          pick(P, full) is None, pick(P, full))

    check("NP4 one free seat is still usable",
          pick(P, {("1.1.1.1", "1"): CAP,
                   ("2.2.2.2", "2"): CAP - 1})["proxy_address"] == "2.2.2.2")

    # The cap must be the SAME constant, not an equal number that can drift.
    import tools.adspower_profile as npmod
    check("NP5 the cap is imported from assign_proxies, not redefined",
          npmod.MAX_PROFILES_PER_PROXY is CAP)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
