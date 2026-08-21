"""Vault health check. Run this before any live run.

    python -m src.status

An empty pool is the single most common reason a run produces nothing, and it
looks identical to a site change from inside the scraper. Check it here instead.
"""
import sys

from .config import Config
from .session import DRIFT_WARN_AT, fingerprint_drift
from .vault import SessionVault


def main():
    config = Config()
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
    drifts = [(r["profile_id"], fingerprint_drift(r.get("user_agent"),
                                                  config.IMPERSONATE))
              for r in rows if r.get("user_agent")]
    worst = max((d for _, d in drifts if d is not None), default=None)
    if worst is not None and worst >= DRIFT_WARN_AT:
        example = next(p for p, d in drifts if d == worst)
        print(f"\n⚠️  FINGERPRINT DRIFT: IMPERSONATE={config.IMPERSONATE} but "
              f"{example} announces a browser {worst} major versions away.")
        print("    The UA header and the TLS handshake are two claims about "
              "one browser, and they disagree. Raise IMPERSONATE to the "
              "closest target curl_cffi ships.")
    return 0 if usable else 1


def _redact(url):
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return url


if __name__ == "__main__":
    sys.exit(main())
