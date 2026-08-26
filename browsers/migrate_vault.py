"""Move this project's vault keys from one Redis to another.

    python -m browsers.migrate_vault --to redis://localhost:6380/0 --dry-run
    python -m browsers.migrate_vault --to redis://localhost:6380/0

Used to give the Pinterest project its OWN Redis instead of sharing the Etsy
project's container, and usable again to move to Upstash.

WHAT IT COPIES, AND WHAT IT REFUSES TO
--------------------------------------
Only keys this project owns:

    cookie:{platform}:*      the identities
    valid_profiles:{platform}   the pool
    seen:{platform}:*        what has already been delivered
    watermark:{platform}:*
    cache:{platform}:*       (skipped by default — it rebuilds itself)

`cookie:etsy:*` and `cookie:etsy_private:*` are NEVER touched. That is the
whole point: the two projects shared a Redis process, and separating them means
copying one side out, not copying everything and sorting it later.

COPY, NEVER MOVE. The source keys are left exactly as they are, so a failed
migration costs nothing and the old vault stays a working fallback until the
new one is proven. Deleting the originals is a separate, deliberate act.

TTLs travel with the keys. `seen:` entries expire (SEEN_TTL, 7 days) and
recreating them without their remaining lifetime would either resurrect
deliveries that should have aged out, or drop ones that should not have.
"""
import argparse
import sys

import redis

from src.config import Config

# `cache:` is deliberately absent. It is derived data with its own TTLs, it
# rebuilds on first use, and copying it would carry stale Pinterest responses
# into a fresh vault for up to their full TTL.
PREFIXES = ("cookie:{p}:*", "valid_profiles:{p}", "seen:{p}:*",
            "watermark:{p}:*")


def keys_for(client, platform):
    found = []
    for pattern in PREFIXES:
        found.extend(client.scan_iter(match=pattern.format(p=platform),
                                      count=500))
    return sorted(set(found))


def copy_key(src, dst, key):
    """Copy one key with its type and TTL. Returns what it did."""
    kind = src.type(key)
    ttl = src.pttl(key)          # milliseconds; -1 no expiry, -2 missing

    if kind == "hash":
        data = src.hgetall(key)
        if not data:
            return "empty"
        dst.delete(key)
        dst.hset(key, mapping=data)
    elif kind == "set":
        members = src.smembers(key)
        if not members:
            return "empty"
        dst.delete(key)
        dst.sadd(key, *members)
    elif kind == "string":
        dst.set(key, src.get(key))
    else:
        # Refuse rather than guess. A silently skipped key is how a migration
        # reports success and loses data.
        return f"UNSUPPORTED type {kind}"

    if ttl and ttl > 0:
        dst.pexpire(key, ttl)
    return kind


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="destination redis:// URL")
    ap.add_argument("--from", dest="source", default=None,
                    help="source URL (default: this project's REDIS_URL)")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = Config()
    platform = args.platform or config.PLATFORM
    source_url = args.source or config.REDIS_URL

    if source_url == args.to:
        print("source and destination are the same URL", file=sys.stderr)
        return 2

    src = redis.Redis.from_url(source_url, decode_responses=True)
    dst = redis.Redis.from_url(args.to, decode_responses=True)
    for name, client, url in (("source", src, source_url),
                              ("destination", dst, args.to)):
        try:
            client.ping()
        except Exception as exc:
            print(f"cannot reach {name} ({url}) — {exc}", file=sys.stderr)
            return 2

    keys = keys_for(src, platform)
    print(f"platform '{platform}': {len(keys)} key(s) to copy")
    print(f"  from {source_url}")
    print(f"  to   {args.to}")

    # Say out loud what is being left behind, so "did it take the Etsy data?"
    # has a visible answer rather than a trusted one.
    others = [k for k in src.scan_iter(match="cookie:*", count=500)
              if not k.startswith(f"cookie:{platform}:")]
    if others:
        namespaces = sorted({k.split(":")[1] for k in others})
        print(f"  leaving untouched: {', '.join(namespaces)} "
              f"({len(others)} keys)")

    if args.dry_run:
        for key in keys[:40]:
            print(f"    would copy  {src.type(key):<7} {key}")
        if len(keys) > 40:
            print(f"    ... and {len(keys) - 40} more")
        return 0

    copied, problems = 0, []
    for key in keys:
        result = copy_key(src, dst, key)
        if result.startswith("UNSUPPORTED"):
            problems.append(f"{key}: {result}")
        elif result != "empty":
            copied += 1
    print(f"\ncopied {copied} key(s)")
    for problem in problems:
        print(f"  ⚠️  {problem}", file=sys.stderr)

    # Verify by re-reading the destination, not by trusting the writes.
    landed = keys_for(dst, platform)
    pool_src = src.scard(f"valid_profiles:{platform}")
    pool_dst = dst.scard(f"valid_profiles:{platform}")
    print(f"destination now holds {len(landed)} key(s), "
          f"pool {pool_dst}/{pool_src} profiles")
    if pool_dst != pool_src:
        print("  ⚠️  pool sizes differ — do NOT switch REDIS_URL yet",
              file=sys.stderr)
        return 1

    print("\nThe SOURCE is untouched and still works. Switch REDIS_URL when "
          "ready, verify with `python -m src.status`, and only then consider "
          "deleting the old keys.")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
