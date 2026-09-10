"""Vault health check. Run this before any live run.

    python -m src.status

An empty pool is the single most common reason a run produces nothing, and it
looks identical to a site change from inside the scraper. Check it here instead.
"""
import sys

from .config import Config
from .session import DRIFT_WARN_AT, fingerprint_drift, impersonate_for
from .vault import SessionVault


def main(config=None):
    # Injectable, not just `Config()` — REDIS_URL's default is
    # `os.environ.get(...)` evaluated ONCE at class-definition time (a plain
    # dataclass field default, not a default_factory), so it is fixed at
    # first import and CANNOT be changed by setting os.environ later in the
    # same process. Tests need `dataclasses.replace(Config(), REDIS_URL=...)`
    # for exactly this reason (see tests/test_vault.py) — accepting `config`
    # here lets this function join that pattern instead of being untestable.
    config = config or Config()
    print(f"redis    : {_redact(config.REDIS_URL)}")
    print(f"platform : {config.PLATFORM}")

    vault = SessionVault(config)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"\nFAIL: cannot reach Redis — {exc}")
        print("If this is the parent repo's Docker vault, note that two Redis "
              "servers share port 6379 on this machine; `localhost` reaches the "
              "stale native one.")
        return 2

    rows = vault.describe()
    if not rows:
        print("\nFAIL: pool is empty. Open Chrome, sign in to Pinterest, and "
              "confirm the extension is beaming (the Go server logs each beacon).")
        return 1

    print(f"\n{len(rows)} profile(s):")
    usable = 0
    for row in rows:
        age = row["age_seconds"]
        fresh = age is not None and age <= config.PROFILE_MAX_AGE
        problems = []
        if not row["cookie_count"]:
            problems.append("no cookies")
        if age is None:
            problems.append("no heartbeat")
        elif not fresh:
            problems.append(f"stale {age}s")
        if not row["has_user_agent"]:
            problems.append("no user_agent")
        if config.REQUIRE_PROXY and not row.get("has_proxy"):
            problems.append("no proxy — would exit from this host")
        if row["leased"]:
            problems.append("currently leased")
        # Last, and it is the actionable one: the others say a profile is
        # unusable, this says what to DO about it. "signed OUT" means log in
        # again; "not the proxy" means fix the proxy. Different jobs.
        if row.get("last_error"):
            problems.append(f"last error: {row['last_error']}")

        if not problems:
            usable += 1
        state = "OK" if not problems else ", ".join(problems)
        print(f"  {row['profile_id']:<24} cookies={row['cookie_count']:<4} "
              f"age={age}s  [{state}]")

    print(f"\n{usable}/{len(rows)} usable right now.")

    # Three claims about one browser go out on every request: the cookies, the
    # User-Agent header, and the TLS/JA3 handshake. This is the only place the
    # operator would ever see them drift apart — the requests keep working
    # either way, which is exactly why nobody notices.
    print()
    problems = []
    for row in rows:
        ua = row.get("user_agent")
        if not ua:
            continue
        target, basis = impersonate_for(ua, config.IMPERSONATE)
        gap = fingerprint_drift(ua, target)
        note = basis
        if basis == "fallback":
            # The browser could not be read, so the handshake is a guess. This
            # is the only case where the three claims disagree without anyone
            # having chosen that, so it is always worth saying out loud.
            problems.append(f"{row['profile_id']}: UA unreadable, "
                            f"falling back to {target}")
        elif gap is not None and gap >= DRIFT_WARN_AT:
            # Not a misconfiguration — curl_cffi simply ships no target this
            # close. An upgrade is the fix; until then the gap is known rather
            # than hidden, which is the whole difference from before.
            problems.append(f"{row['profile_id']}: closest target {target} is "
                            f"still {gap} majors from its browser")
            note = f"{basis}, {gap} majors off"
        print(f"  {row['profile_id']:<24} tls={target:<18} ({note})")

    if problems:
        print("\n⚠️  FINGERPRINT:")
        for line in problems:
            print(f"    {line}")
        print("    Cookies, User-Agent and TLS handshake are three claims "
              "about one browser. Upgrading curl_cffi adds newer targets.")
    return 0 if usable else 1


def _redact(url):
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return url


if __name__ == "__main__":
    sys.exit(main())
