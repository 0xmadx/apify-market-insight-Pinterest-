"""The exit-code contract `python -m src.status` makes to anything automated.

    .venv/Scripts/python.exe -m tests.test_status_healthcheck

Nothing tested this before — grep confirms `test_vault.py` only MENTIONS
`src.status` in a comment, never calls `status.main()`. That mattered less
while a human ran it and read the words. It matters now: a scheduled health
check is about to decide, every 30 minutes, whether to open or close a
GitHub issue based purely on this exit code. If a future refactor of
status.py silently changes what 2/1/0 mean, the workflow would misbehave —
stop detecting real outages, or open false alarms — with nothing here to
catch it. This is that catch.

WHY `dataclasses.replace()`, NOT `os.environ` patching — found the hard way
writing this suite. `Config.REDIS_URL`'s default is `os.environ.get(...)`,
a bare dataclass field default. Those are evaluated ONCE at class-definition
time (module import), not per instantiation — so `Config()` called later in
the same process ignores any os.environ change made after import and keeps
returning whatever `.env` held at import time. Confirmed by the first,
failed draft of this test: every override was silently ignored and it kept
hitting the REAL production Upstash. Same reason tests/test_vault.py uses
`replace(Config(), REDIS_URL=test_url)` instead of setting the env var —
this suite follows that established pattern, and status.main() was given an
injectable `config` parameter to make that possible (src/status.py).

Against a LOCAL Redis only (TEST_REDIS_URL, default redis://localhost:6380/0)
and an isolated platform namespace — never the production vault.
"""
import os
import sys
import time
from dataclasses import replace

sys.path.insert(0, ".")

import redis  # noqa: E402

from src import status  # noqa: E402
from src.config import Config  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/0")
TEST_PLATFORM = "__test_status_healthcheck"


def main():
    base = Config()

    # A1 — genuinely unreachable Redis must return 2. Port 1 on localhost: no
    # firewall/routing timeout to wait out (this is loopback), just an
    # immediate connection-refused, so this check stays fast.
    unreachable = replace(base, REDIS_URL="redis://127.0.0.1:1/0",
                          PLATFORM=TEST_PLATFORM)
    code = status.main(unreachable)
    check("A1: an unreachable Redis returns exit code 2", code == 2, code)

    # Everything past here needs the real local test Redis.
    test_cfg = replace(base, REDIS_URL=TEST_URL, PLATFORM=TEST_PLATFORM)
    r = redis.Redis.from_url(TEST_URL, decode_responses=True,
                             socket_connect_timeout=3)
    try:
        r.ping()
    except Exception as exc:
        print(f"  SKIPPED A2-A4 — local test Redis not reachable at "
              f"{TEST_URL} ({exc}). Start it: docker start pinterest-redis")
        failed = [n for n, ok in checks if not ok]
        print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
        return 1 if failed else 0

    valid_key = f"valid_profiles:{TEST_PLATFORM}"
    cookie_key = f"cookie:{TEST_PLATFORM}:fake_profile"
    r.delete(valid_key, cookie_key)  # isolated namespace, but start clean

    try:
        # A2 — reachable, but the isolated namespace has nothing in it: 1.
        code = status.main(test_cfg)
        check("A2: reachable Redis with an empty pool returns exit code 1",
              code == 1, code)

        # A3 — one fresh, usable profile written for real: 0. Genuinely
        # complete, not a shortcut: status.py's own usability check (read
        # directly, not assumed) requires cookies present, a fresh
        # heartbeat, a user_agent, AND a proxy (REQUIRE_PROXY defaults on) —
        # missing any one of those produces a real "problem" and 1, not 0.
        r.sadd(valid_key, "fake_profile")
        r.hset(cookie_key, mapping={
            "cookies_json": '[{"name": "_auth", "value": "x"}]',
            "is_valid": "1",
            "last_updated": str(time.time()),
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "Chrome/146.0.0.0 Safari/537.36",
            "proxy": "http://user:pass@203.0.113.1:8080",
        })
        code = status.main(test_cfg)
        check("A3: reachable Redis with one fresh profile returns exit code 0",
              code == 0, code)

        # A4 — the pool is non-empty but every profile is stale: back to 1,
        # not 0. A health check that reports "fine" on a dead writer is
        # worse than one that reports "empty" — see PROFILE_MAX_AGE.
        r.hset(cookie_key, "last_updated", str(time.time() - 100000))
        code = status.main(test_cfg)
        check("A4: a pool of only STALE profiles returns exit code 1, not 0",
              code == 1, code)
    finally:
        r.delete(valid_key, cookie_key)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
