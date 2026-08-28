"""Proofs for the lease path — the file that protects the accounts themselves.

`SessionVault._try_lease` decides which Pinterest account each run wears, and
until 2026-08-20 it had **no test at all**. It grew four refusals (no hash, no
cookies, signed out, stale heartbeat) and a fifth was added the day this file
was written; none of them had ever been proven to fire.

Runs against the real Redis under a throwaway platform namespace, the same way
`test_incremental` does. A fake would not exercise `SET NX`, which is the whole
of the mutual exclusion — the one thing a fake would be most tempted to get
approximately right and actually get wrong.

    .venv/Scripts/python.exe -m tests.test_vault
"""
import importlib
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import replace

sys.path.insert(0, ".")

from src.config import Config  # noqa: E402
from src.session import build_session  # noqa: E402
from src.vault import SessionVault, VaultEmpty  # noqa: E402

TEST_PLATFORM = "__test_vault"
LIVE_COOKIES = {"_auth": "1", "_pinterest_sess": "abc", "csrftoken": "tok"}
PROXY = "http://user:pass@203.0.113.9:8080"

checks = []


def check(name, condition, detail=None):
    checks.append((name, bool(condition)))
    print(("  PASS  " if condition else "  FAIL  ") + name
          + ("" if condition or detail is None else f"  [{detail}]"))


def seed(vault, profile_id, *, cookies=LIVE_COOKIES, proxy=PROXY, age=0):
    """Write one profile into the throwaway namespace, as the Go server would."""
    key = f"cookie:{TEST_PLATFORM}:{profile_id}"
    fields = {
        "cookies_json": json.dumps(cookies),
        "user_agent": "Mozilla/5.0 (test)",
        "last_updated": str(time.time() - age),
        "is_valid": "1",
    }
    if proxy is not None:
        fields["proxy"] = proxy
    vault.r.delete(key)
    vault.r.hset(key, mapping=fields)
    vault.r.sadd(f"valid_profiles:{TEST_PLATFORM}", profile_id)
    vault.r.delete(f"lease:{TEST_PLATFORM}:{profile_id}")


def wipe(vault):
    for key in vault.r.scan_iter(f"*{TEST_PLATFORM}*"):
        vault.r.delete(key)


def in_pool(vault, profile_id):
    return bool(vault.r.sismember(f"valid_profiles:{TEST_PLATFORM}", profile_id))


def quiet_acquire(config):
    """Acquire, swallowing both the VaultEmpty and the log lines."""
    with redirect_stdout(io.StringIO()):
        try:
            return SessionVault(config).acquire(TEST_PLATFORM)
        except VaultEmpty:
            return None


def main():
    # NEVER the production vault. Config() reads REDIS_URL, which points at
    # Upstash once the vault has moved there -- and this suite writes, deletes
    # and SCANs a whole keyspace. Running the gate would then bill the
    # production database to test code, on a plan metered per command, and put
    # a scan across the live pool for no reason.
    #
    # Defaults to the local lab Redis, which is exactly what it is for. Set
    # TEST_REDIS_URL to point somewhere else deliberately.
    test_url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/0")
    base = replace(Config(), REDIS_URL=test_url)
    strict = replace(base, PLATFORM=TEST_PLATFORM, WAIT_TIMEOUT=1,
                     REQUIRE_PROXY=True)
    lenient = replace(strict, REQUIRE_PROXY=False)

    vault = SessionVault(strict)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis at {test_url} — {exc}")
        print("This suite needs a LOCAL Redis (the lab container). It uses a "
              "throwaway namespace, never touches the live pinterest keys, and "
              "deliberately does NOT use REDIS_URL so it cannot bill the "
              "production vault. Override with TEST_REDIS_URL if you mean to.")
        return 2
    wipe(vault)

    # ------------------------------------------------ GROUP P — the proxy gate
    print("\nGROUP P — an identity without an exit IP")
    # THE DEFECT: `_try_lease` checked cookies, auth cookies and heartbeat age,
    # and never the proxy. Measured on the live pool 2026-08-20 — 6 usable
    # profiles, 5 on their own Webshare IPs and one Chrome-extension profile
    # with none — so roughly one run in six went out from the operator's home
    # IP, silently, mixing a datacentre identity with a residential one.
    seed(vault, "bare", proxy=None)
    identity = quiet_acquire(strict)
    check("P1 an unproxied profile is NOT leased", identity is None)
    check("P2 ...and no lease was left behind for it",
          not vault.r.exists(f"lease:{TEST_PLATFORM}:bare"))

    # Skipped, not evicted. A missing proxy is an unfinished setup, not a burned
    # session; evicting would churn the pool (the writer re-adds it on its next
    # beacon) while hiding the state the operator has to act on.
    check("P3 it is SKIPPED, not evicted — still in the pool",
          in_pool(vault, "bare"))
    check("P4 ...and not marked invalid",
          vault.r.hget(f"cookie:{TEST_PLATFORM}:bare", "is_valid") == "1")

    # The escape hatch has to actually work, or a laptop with no proxies at all
    # cannot run the project.
    loose = quiet_acquire(lenient)
    check("P5 REQUIRE_PROXY=0 leases that same profile", loose is not None)
    check("P6 ...and its proxy is None, never a fabricated one",
          loose is not None and loose.proxy is None)
    SessionVault(lenient).release(TEST_PLATFORM, "bare")

    # An empty string in the hash is the shape a half-written profile takes.
    for label, value in (("an empty string", ""), ("whitespace", "   ")):
        wipe(vault)
        seed(vault, "blank", proxy=value)
        check(f"P7 a proxy that is only {label} counts as no proxy",
              quiet_acquire(strict) is None)

    # ----------------------------------------------- GROUP T — the happy path
    print("\nGROUP T — a fully configured identity")
    wipe(vault)
    seed(vault, "good")
    identity = quiet_acquire(strict)
    check("T1 a proxied, signed-in, fresh profile IS leased",
          identity is not None and identity.profile_id == "good")
    check("T2 the proxy travels with the identity", identity.proxy == PROXY)
    check("T3 so do the cookies and the UA",
          identity.cookies == LIVE_COOKIES
          and identity.user_agent == "Mozilla/5.0 (test)")

    # The gate is worthless if the proxy is dropped one layer further down.
    session = build_session(identity, strict)
    proxies = dict(getattr(session, "proxies", None) or {})
    check("T4 build_session actually sends over that proxy",
          proxies.get("https") == PROXY, proxies)
    check("T5 ...and echoes the csrftoken cookie back as a header",
          session.headers.get("X-CSRFToken") == "tok")
    session.close()

    # ---------------------------------------------- GROUP L — mutual exclusion
    print("\nGROUP L — one identity, one run")
    # Two runs driving one Pinterest session from two IPs is how an account gets
    # flagged. This lease is the only thing standing between the pool and that.
    check("L1 a leased profile cannot be leased twice",
          quiet_acquire(strict) is None)
    ttl = vault.r.ttl(f"lease:{TEST_PLATFORM}:good")
    check("L2 the lease expires unaided — a crash cannot strand it",
          0 < ttl <= strict.LEASE_TTL, ttl)

    SessionVault(strict).release(TEST_PLATFORM, "good")
    check("L3 releasing hands it straight back", quiet_acquire(strict) is not None)
    SessionVault(strict).release(TEST_PLATFORM, "good")

    # ----------------------------------------------- GROUP R — every refusal
    print("\nGROUP R — every other reason to refuse")
    # Unlike a missing proxy, each of these IS a dead session, so each evicts:
    # leaving it in the pool makes every later acquire walk past it again.
    for label, kwargs in (
        ("no cookies at all", dict(cookies={})),
        ("signed out — has cookies, missing _auth",
         dict(cookies={"csrftoken": "tok", "_b": "1"})),
        ("heartbeat older than PROFILE_MAX_AGE",
         dict(age=base.PROFILE_MAX_AGE + 60)),
    ):
        wipe(vault)
        seed(vault, "bad", **kwargs)
        check(f"R refused — {label}", quiet_acquire(strict) is None)
        check(f"R ...and evicted from the pool — {label}",
              not in_pool(vault, "bad"))

    wipe(vault)
    vault.r.sadd(f"valid_profiles:{TEST_PLATFORM}", "ghost")
    quiet_acquire(strict)
    check("R a pool member with no hash behind it self-heals",
          not in_pool(vault, "ghost"))

    # ------------------------------------------------ GROUP N — the reporting
    print("\nGROUP N — the operator has to be able to see it")
    wipe(vault)
    seed(vault, "bare", proxy=None)
    seed(vault, "good")
    rows = {r["profile_id"]: r
            for r in SessionVault(strict).describe(TEST_PLATFORM)}
    check("N1 describe() reports has_proxy per profile",
          rows["good"]["has_proxy"] is True
          and rows["bare"]["has_proxy"] is False)

    # `acquire` re-walks the pool every WAIT_INTERVAL, so an un-throttled log
    # line would print the same reason once a second for the whole timeout — and
    # a reason repeated sixty times reads as noise, not as the thing to fix.
    wipe(vault)
    seed(vault, "bare", proxy=None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            SessionVault(replace(strict, WAIT_TIMEOUT=3)).acquire(TEST_PLATFORM)
        except VaultEmpty:
            pass
    said = buf.getvalue().count("no proxy")
    check("N2 the skip reason is logged ONCE, not once per poll", said == 1, said)
    check("N3 ...and it names the fix, not just the symptom",
          "assign_proxies.py" in buf.getvalue()
          and "REQUIRE_PROXY=0" in buf.getvalue())

    # ------------------------------------------------- GROUP D — the default
    print("\nGROUP G — the UA header and the TLS handshake are one claim")
    # Three things go out on every request and all three claim to be the same
    # browser: the cookies, the User-Agent, and the TLS/JA3 handshake. Measured
    # 2026-08-20 the vault's profiles announced Chrome 150 while IMPERSONATE
    # was still chrome124 — 26 versions, on every request, for the life of the
    # project. Nothing surfaced it because the requests keep working.
    from src.session import (DRIFT_WARN_AT, browser_major,  # noqa: E402
                             fingerprint_drift)

    UA150 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
    check("G1 a Chrome major is read out of a real UA",
          browser_major(UA150) == 150)
    check("G2 ...and out of an impersonate target",
          browser_major("chrome146") == 146)
    check("G3 the old default was 26 versions adrift",
          fingerprint_drift(UA150, "chrome124") == 26)
    check("G4 the new default is close enough not to warn",
          fingerprint_drift(UA150, "chrome146") < DRIFT_WARN_AT)
    check("G5 ...and the old one WOULD have warned",
          fingerprint_drift(UA150, "chrome124") >= DRIFT_WARN_AT)

    # Unknown is not zero. A UA with no version must not read as a match.
    check("G6 an unreadable UA gives None, never 0",
          fingerprint_drift("some opaque agent", "chrome146") is None
          and fingerprint_drift(None, "chrome146") is None)
    check("G7 Firefox is read too — a Firefox-born cookie jar replayed over a "
          "Chrome handshake is the same mistake",
          browser_major("Mozilla/5.0 (X11; Linux x86_64; rv:147.0) "
                        "Gecko/20100101 Firefox/147.0") == 147)

    # The shipped default must actually be one curl_cffi can produce, or every
    # request falls back to something unstated.
    from curl_cffi.requests.impersonate import BrowserTypeLiteral  # noqa: E402
    import typing as _typing  # noqa: E402
    targets = set(_typing.get_args(BrowserTypeLiteral))
    check("G8 the default IMPERSONATE is a target curl_cffi actually ships",
          Config().IMPERSONATE in targets, Config().IMPERSONATE)

    print("\nGROUP H — the fingerprint follows the identity, not a constant")
    # A single IMPERSONATE constant cannot be right for a pool: every profile is
    # a different browser on its own update schedule, and a constant rots
    # silently — it was 26 versions adrift when measured on 2026-08-20. The
    # handshake is now derived per identity from that identity's own UA.
    from src.session import (browser_family, impersonate_for,  # noqa: E402
                             impersonate_targets)

    T = impersonate_targets()
    UA = {
        "chrome150": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                     " (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "chrome146": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                     " (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "firefox147": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101"
                      " Firefox/147.0",
        "edge120": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                   " Edg/120.0.0.0",
        "safari18": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                    " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0"
                    " Safari/605.1.15",
        "android131": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36"
                      " (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    }

    check("H1 an exact target is used when one exists",
          impersonate_for(UA["chrome146"], targets=T) == ("chrome146", "exact"))
    check("H2 the live vault's Chrome 150 resolves to the newest below it",
          impersonate_for(UA["chrome150"], targets=T) == ("chrome146", "nearest"))
    check("H3 Firefox gets a Firefox handshake, never a Chrome one",
          impersonate_for(UA["firefox147"], targets=T)[0] == "firefox147")

    # ORDER MATTERS in family detection and it is not alphabetical: every
    # Chromium UA contains "Safari", and Edge's contains "Chrome".
    check("H4 Edge is Edge, not Chrome (its UA contains 'Chrome/')",
          browser_family(UA["edge120"]) == "edge"
          and browser_family(UA["chrome150"]) == "chrome")
    check("H5 ...and a Chromium UA is not read as Safari",
          browser_family(UA["chrome150"]) != "safari")

    # A desktop UA must not be handed a mobile fingerprint: the UA already
    # declared the platform, and the handshake would contradict it.
    check("H6 mobile and desktop do not cross",
          impersonate_for(UA["android131"], targets=T)[0].endswith("_android")
          and not impersonate_for(UA["chrome150"],
                                  targets=T)[0].endswith("_android"))

    # Safari target names are not comparable numbers — safari155 is 15.5,
    # safari180 is 18.0, safari260 is 26.0. Guessing a mapping would be
    # confidently wrong, so it refuses instead.
    check("H7 Safari refuses rather than guessing an incomparable version",
          impersonate_for(UA["safari18"], "chrome", targets=T)[1] == "fallback")

    check("H8 an unreadable UA falls back and SAYS it fell back",
          impersonate_for("opaque", "chrome", targets=T) == ("chrome", "fallback")
          and impersonate_for(None, "chrome", targets=T)[1] == "fallback")

    # THE REGRESSION. `Mozilla/5.0` is in every UA ever written; a generic
    # "first number in the string" parse returned 5 for Safari and matched it
    # to a Safari 15 fingerprint.
    check("H9 the Mozilla/5.0 compatibility token is never read as the version",
          browser_major(UA["safari18"]) == 18 and browser_major(UA["chrome150"]) == 150)

    # The target list is read from the installed curl_cffi, so a newer release
    # is picked up without editing this file.
    check("H10 targets come from the library, not a hardcoded copy",
          len(T) > 20 and "chrome146" in T)

    # Every resolved target must be one curl_cffi can actually produce.
    resolved = {impersonate_for(ua, targets=T)[0] for ua in UA.values()}
    check("H11 every choice is a real curl_cffi target",
          resolved <= set(T) | {"chrome"}, sorted(resolved))

    print("\nGROUP D — the default is the safe one")
    check("D1 REQUIRE_PROXY defaults to ON", Config().REQUIRE_PROXY is True)

    # Config reads the environment when the module is imported, so this has to
    # reload it rather than set os.environ and hope.
    import src.config as config_module
    for value, expected in (("0", False), ("false", False), ("no", False),
                            ("", False), ("1", True), ("yes", True)):
        os.environ["REQUIRE_PROXY"] = value
        try:
            got = importlib.reload(config_module).Config().REQUIRE_PROXY
        finally:
            del os.environ["REQUIRE_PROXY"]
        check(f"D2 REQUIRE_PROXY={value!r} reads as {expected}", got is expected)
    importlib.reload(config_module)

    wipe(vault)
    print("")
    print("GROUP E — a dead profile says WHY, not just that it is dead")
    from browsers.keepalive import note_failure

    wipe(vault)
    seed(vault, "ads_broke", proxy="http://p:1@1.2.3.4:9", age=30)
    note_failure(vault, TEST_PLATFORM, "ads_broke",
                 "signed OUT — missing _auth, _pinterest_sess")
    row = [r for r in vault.describe(TEST_PLATFORM)
           if r["profile_id"] == "ads_broke"][0]
    # The whole point. "stale" tells you a profile is unusable; only the reason
    # tells you whether to re-login or fix a proxy -- and before this it lived
    # in the VM's journal where nobody reading src.status would ever see it.
    check("E1 the failure reason reaches the vault",
          "signed OUT" in row["last_error"], row["last_error"])
    check("E2 ...and is truncated, so a stack trace cannot bloat the record",
          len(row["last_error"]) <= 200)

    # A profile that recovers must stop reporting yesterday's failure, or the
    # operator retires a healthy account.
    vault.r.hset(f"cookie:{TEST_PLATFORM}:ads_broke",
                 mapping={"last_error": "", "last_updated": str(time.time())})
    row = [r for r in vault.describe(TEST_PLATFORM)
           if r["profile_id"] == "ads_broke"][0]
    check("E3 a recovered profile reports no error", row["last_error"] == "")

    # note_failure must never be the reason a pass dies: it is bookkeeping
    # running inside a loop that is still refreshing other profiles.
    class Broken:
        class r:
            @staticmethod
            def hset(*a, **k):
                raise RuntimeError("redis is down")
    try:
        note_failure(Broken(), TEST_PLATFORM, "ads_broke", "anything")
        survived = True
    except Exception:
        survived = False
    check("E4 a failure to record the failure does not break the pass", survived)
    wipe(vault)

    print("")
    print("GROUP M — the numbers scaling decisions depend on")
    wipe(vault)
    seed(vault, "ads_m1", proxy="http://p:1@1.2.3.4:9", age=30)

    v = SessionVault(strict)
    check("M1 wait is unmeasured until an acquire happens",
          v.last_wait_seconds is None, v.last_wait_seconds)
    # Acquire on an instance we hold, so the recorded value can be read back.
    # `quiet_acquire` builds its own vault internally and throws it away.
    with redirect_stdout(io.StringIO()):
        ident = v.acquire(TEST_PLATFORM)
    check("M2 a successful lease records how long it waited",
          ident is not None and isinstance(v.last_wait_seconds, float)
          and v.last_wait_seconds >= 0,
          f"identity={ident and ident.profile_id} wait={v.last_wait_seconds}")

    v3 = SessionVault(strict)
    got = None
    with redirect_stdout(io.StringIO()):
        try:
            got = v3.acquire(TEST_PLATFORM)
        except VaultEmpty:
            pass
    # It waited for a profile already leased by M2, so the number must be real
    # and must be at least the poll interval -- a 0 here would mean the timer
    # never ran.
    check("M3 a wait that actually happened is measured, not zero",
          v3.last_wait_seconds is not None and v3.last_wait_seconds > 0,
          v3.last_wait_seconds)

    # An empty vault still records the wait: how long a run burned before
    # failing is exactly what you want when the pool runs dry.
    wipe(vault)
    v4 = SessionVault(strict)
    with redirect_stdout(io.StringIO()):
        try:
            v4.acquire(TEST_PLATFORM)
        except VaultEmpty:
            pass
    check("M4 VaultEmpty records the wait too, not just success",
          v4.last_wait_seconds is not None, v4.last_wait_seconds)

    # None is not zero, applied to instrumentation: a fully cached run leases
    # nothing, and reporting 0s would make the cheapest runs look like the
    # fastest leases instead of no lease at all.
    from src.lazy import LazySession
    lazy = LazySession(strict)
    check("M5 a session that never leased reports None, not 0",
          lazy.lease_wait_seconds is None, lazy.lease_wait_seconds)

    from src.transport import TrendsClient
    check("M6 the 429 counter starts at zero and exists to be counted",
          TrendsClient(session=None, cache=None).rate_limited == 0)
    wipe(vault)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
