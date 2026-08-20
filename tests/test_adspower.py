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
    check("S1 csrftoken travels as a COOKIE, not a separate field",
          sc.CSRF_COOKIE == "csrftoken"
          and sc.CSRF_COOKIE in {c["name"] for c in SIGNED_IN})
    body_p = sc.build_payload("ads_x", SIGNED_IN, "UA/1.0")
    check("S2 ...so the token reaches the vault inside cookie_json",
          body_p["cookie_json"].get("csrftoken") is not None)
    check("S3 the payload carries all three: cookies, token, user agent",
          body_p["cookie_json"] and body_p["cookie_json"].get("csrftoken")
          and body_p["user_agent"] == "UA/1.0")
    # A jar with no csrftoken still syncs (reads work) but must be flagged.
    fn3 = src[src.index("def sync_one"):src.index("def main")]
    check("S4 a missing csrftoken warns rather than refusing (reads still work)",
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

    print("\nGROUP P — provenance: what reaches the vault")
    body = sc.post_to_vault("ads_test", SIGNED_IN, "UA/1.0", dry_run=True)
    check("P1 dry-run writes nothing and says so", "DRY-RUN" in body, body)

    # Call the REAL builder. An earlier version of this test rebuilt the dict
    # itself and therefore asserted nothing about the code.
    payload = sc.build_payload("ads_test", SIGNED_IN, "UA/1.0")
    # The Go server rejects anything but these platforms, and SessionManager
    # reads cookies_json — so the contract is exact, not approximate.
    check("P2 platform is 'pinterest' (the Go server 400s on anything else)",
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

    print("\nGROUP D — degenerate inputs (where the silent bugs live)")
    for label, jar in [("empty jar", []), ("None-ish domain", [{"name": "a",
                                                                "value": "b",
                                                                "domain": None}])]:
        pin = [c for c in jar if "pinterest.com" in (c.get("domain") or "")]
        check(f"D1 {label}: filters to empty without raising", pin == [])
    empty = sc.post_to_vault("ads_x", [], "UA", dry_run=True)
    check("D2 an empty cookie list still returns a report, not a crash",
          "0 cookies" in empty, empty)
    check("D3 rate limiter is a real pause, not a no-op",
          sc.RATE_LIMIT_SECONDS >= 1.0, sc.RATE_LIMIT_SECONDS)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
