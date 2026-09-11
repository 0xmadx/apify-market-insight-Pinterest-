"""A dead session must remove itself, report itself, and heal itself.

    .venv/Scripts/python.exe -m tests.test_session_recovery

WHY THIS EXISTS. Traced 2026-09-10, end to end, and every link was broken:

  * `transport.py` classified `auth_expired`/`blocked` correctly and then
    told NOBODY WHICH PROFILE. `SessionVault.mark_blocked` had **zero callers
    in the entire repository** — the one function whose job was taking a dead
    session out of rotation was dead code itself.
  * So the corpse stayed in `valid_profiles` until it happened to age out at
    PROFILE_MAX_AGE. With five profiles and a shuffled pool, roughly one run
    in five drew it and failed, for up to fifteen minutes, per death.
  * `main.py` caught only `VaultEmpty` and `RedisError`, so the resulting
    `TrendsAPIError` was UNCAUGHT: the customer got a raw failure instead of
    the capacity message written for exactly this moment.
  * Eviction is `SREM valid_profiles`, and `keepalive.load_from_vault()` built
    its work list from that same set — so evicting a profile hid it from the
    only service that could repair it. A human re-logged in and nothing
    happened, because the refresher no longer knew the profile existed.
  * Meanwhile that refresher kept launching a real Chromium for the corpse
    every 5 minutes (~8s a pass) to re-learn one unchanged fact.

Runs against a LOCAL Redis under its own throwaway namespace, like
`test_vault`. Deliberately NOT `REDIS_URL`: this suite writes and deletes, and
the gate must never bill the production vault.
"""
import json
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, ".")

from browsers.keepalive import _backoff_remaining, load_from_vault, write  # noqa: E402
from src.config import Config  # noqa: E402
from src.lazy import LazySession  # noqa: E402
from src.vault import SessionVault  # noqa: E402

TEST_PLATFORM = "__test_recovery"
LIVE_COOKIES = {"_auth": "1", "_pinterest_sess": "abc"}
PROXY = "http://user:pass@203.0.113.9:8080"

checks = []


def check(name, condition, detail=None):
    checks.append((name, bool(condition)))
    print(("  PASS  " if condition else "  FAIL  ") + name
          + ("" if condition or detail is None else f"  [{detail}]"))


def seed(vault, profile_id, *, cookies=LIVE_COOKIES, age=0):
    key = f"cookie:{TEST_PLATFORM}:{profile_id}"
    vault.r.delete(key)
    vault.r.hset(key, mapping={
        "cookies_json": json.dumps(cookies),
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0.0.0 Safari/537.36",
        "last_updated": str(time.time() - age),
        "is_valid": "1",
        "proxy": PROXY,
    })
    vault.r.sadd(f"valid_profiles:{TEST_PLATFORM}", profile_id)
    vault.r.delete(f"lease:{TEST_PLATFORM}:{profile_id}")


def wipe(vault):
    for key in vault.r.scan_iter(f"*{TEST_PLATFORM}*"):
        vault.r.delete(key)


def serving(vault, profile_id):
    return bool(vault.r.sismember(f"valid_profiles:{TEST_PLATFORM}", profile_id))


def on_roster(vault, profile_id):
    return bool(vault.r.sismember(f"known_profiles:{TEST_PLATFORM}", profile_id))


def strikes(vault, profile_id):
    raw = vault.r.hget(f"cookie:{TEST_PLATFORM}:{profile_id}", "auth_failures")
    return int(raw or 0)


def main():
    test_url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/0")
    cfg = replace(Config(), REDIS_URL=test_url, PLATFORM=TEST_PLATFORM,
                  WAIT_TIMEOUT=1, REQUIRE_PROXY=True)
    vault = SessionVault(cfg)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis at {test_url} — {exc}")
        print("This suite needs a LOCAL Redis (the lab container). It uses a "
              "throwaway namespace and deliberately does NOT use REDIS_URL, so "
              "it cannot bill the production vault.")
        return 2
    wipe(vault)

    # ------------------------------------------- GROUP R — strikes, not hair triggers
    print("\nGROUP R — one bad response must not cost an account")
    seed(vault, "alpha")
    retired = vault.report_rejection(TEST_PLATFORM, "alpha", "auth_expired")
    check("R1 the first rejection does NOT retire the profile", retired is False)
    check("R2 ...it stays in the serving pool", serving(vault, "alpha"))
    check("R3 ...and the strike is recorded", strikes(vault, "alpha") == 1)
    check("R4 ...with a reason a human can act on",
          "auth_expired" in (vault.r.hget(f"cookie:{TEST_PLATFORM}:alpha",
                                          "last_error") or ""))

    retired = vault.report_rejection(TEST_PLATFORM, "alpha", "auth_expired")
    check("R5 the second consecutive rejection DOES retire it", retired is True)
    check("R6 ...it leaves the serving pool", not serving(vault, "alpha"))

    # ------------------------------------------- GROUP K — retired is not forgotten
    print("\nGROUP K — a retired profile stays visible to the refresher")
    check("K1 it is on the known_profiles roster", on_roster(vault, "alpha"))

    records = load_from_vault(vault, TEST_PLATFORM)
    ids = [r["profile_id"] for r in records]
    check("K2 keepalive still picks it up (this is the whole fix)",
          "alpha" in ids, ids)
    record = next((r for r in records if r["profile_id"] == "alpha"), None)
    check("K3 ...marked as NOT in the serving pool, so write() re-adds it",
          record is not None and record["in_set"] is False)

    # The heal. A successful refresh is the proof that a human fixed it.
    write(vault, TEST_PLATFORM, record, LIVE_COOKIES)
    check("K4 a successful refresh returns it to service, unaided",
          serving(vault, "alpha"))
    check("K5 ...and clears the strikes, so it starts clean",
          strikes(vault, "alpha") == 0)
    check("K6 ...and clears last_error",
          not (vault.r.hget(f"cookie:{TEST_PLATFORM}:alpha", "last_error") or ""))

    # ------------------------------------------- GROUP V — the operator can still see it
    print("\nGROUP V — src.status must not lose sight of a damaged profile")
    wipe(vault)
    seed(vault, "beta")
    vault.report_rejection(TEST_PLATFORM, "beta", "blocked")
    vault.report_rejection(TEST_PLATFORM, "beta", "blocked")
    rows = {r["profile_id"]: r for r in vault.describe(TEST_PLATFORM)}
    check("V1 describe() still lists a retired profile", "beta" in rows, list(rows))
    check("V2 ...flagged as not serving",
          "beta" in rows and rows["beta"]["serving"] is False)
    check("V3 ...carrying its strike count",
          "beta" in rows and rows["beta"]["strikes"] == 2)

    # ------------------------------------------- GROUP B — don't relaunch a browser at a corpse
    print("\nGROUP B — back off only from failures a human must fix")
    now = time.time()
    human = {"last_error": "signed OUT — missing _auth",
             "last_error_at": str(now - 60)}
    machine = {"last_error": "exit IP 1.2.3.4 is not the proxy 5.6.7.8",
               "last_error_at": str(now - 60)}
    check("B1 a signed-out profile is left alone for a while",
          _backoff_remaining(human, now=now) > 0)
    check("B2 a proxy failure is retried immediately — a machine fixes that",
          _backoff_remaining(machine, now=now) == 0)
    old = {"last_error": "signed OUT — missing _auth",
           "last_error_at": str(now - 7200)}
    check("B3 ...and the backoff expires, so a re-login is found promptly",
          _backoff_remaining(old, now=now) == 0)
    check("B4 a healthy profile is never backed off",
          _backoff_remaining({"last_error": "", "last_error_at": None},
                             now=now) == 0)

    # ------------------------------------------- GROUP O — rotation never picks the corpse
    print("\nGROUP O — a refused run swaps onto a DIFFERENT account")
    wipe(vault)
    seed(vault, "one")
    seed(vault, "two")
    session = LazySession(cfg)
    session._ensure()
    first = session.identity.profile_id
    rotated = session.rotate("auth_expired")
    check("O1 it rotated", rotated is True)
    check("O2 ...onto a different profile, even though one strike does not "
          "retire — the failed lease is held across the acquire",
          session.identity is not None
          and session.identity.profile_id != first,
          f"{first} -> {session.identity and session.identity.profile_id}")
    check("O3 ...the failed profile carries a strike",
          strikes(vault, first) == 1)
    check("O4 ...and is released, not stranded until the lease TTL",
          not vault.r.exists(f"lease:{TEST_PLATFORM}:{first}"))
    check("O5 the rotation is counted for RUN_METRICS", session.rotations == 1)
    session.close()

    # With nothing left to rotate onto, it must report failure rather than
    # loop — main.py turns that into the ordinary capacity message.
    wipe(vault)
    seed(vault, "only")
    solo = LazySession(cfg)
    solo._ensure()
    check("O6 with no replacement available, rotate() reports False",
          solo.rotate("auth_expired") is False)
    solo.close()

    wipe(vault)
    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
