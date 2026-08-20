"""Give each AdsPower profile its own Webshare proxy, stably.

    Webshare  --(this script)-->  AdsPower profile
                                        |
                              sync_cookies.py mirrors it
                                        v
                                  the vault  -->  curl_cffi

AdsPower stays the single source of truth for a profile's exit IP. This script
only WRITES it there; `sync_cookies.py` reads it back and mirrors it to the
vault. Nothing assigns a proxy to the scraper directly, because two writers
means two truths and the first disagreement is a cookie jar sent from an IP it
was never born on.

THE PAIRING MUST BE STABLE, and that is the whole design constraint. A profile
whose exit IP changes between runs looks like a hijacked account — worse than
having no proxy. So the assignment is by **sorted position**, not round-robin
from a counter and not random:

    profiles sorted by user_id  x  proxies sorted by (address, port)

Same inputs always produce the same pairing. Adding a profile at the end does
not reshuffle the ones before it; adding a proxy does not either, as long as
the pool only grows.

⚠️ REMOVING a proxy from the Webshare pool DOES reshuffle everything after it.
That is unavoidable with positional pairing and is why `--dry-run` prints the
whole map: check it before applying when the pool has shrunk.

⚠️ FEWER PROXIES THAN PROFILES is refused, not wrapped around. Two profiles
sharing an exit IP defeats the point of separate identities, and silently
doubling up is exactly the kind of plausible-looking wrong state this codebase
refuses elsewhere.

    python3 assign_proxies.py --dry-run     # show the map, change nothing
    python3 assign_proxies.py               # apply it
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ADS = "http://127.0.0.1:50325"
WEBSHARE = "https://proxy.webshare.io"
DEFAULT_GROUP = "pinterest"
RATE_LIMIT_SECONDS = 1.15

_last = 0.0


def ads_call(path, key, body=None, timeout=60):
    """Rate-limited AdsPower call. GET unless `body` is given."""
    global _last
    wait = RATE_LIMIT_SECONDS - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    headers = {"api-key": key}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(ADS + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.load(r)
    _last = time.monotonic()
    if payload.get("code") != 0:
        raise RuntimeError(f"AdsPower {path.split('?')[0]}: {payload.get('msg')}")
    return payload.get("data") or {}


def webshare_proxies(key):
    """Every proxy on the account, sorted so the ordering is reproducible.

    Only `valid` ones are returned: assigning a proxy Webshare has already
    marked dead would hand a profile an exit IP that fails on first use, and
    the failure would look like a dead session rather than a dead proxy.
    """
    req = urllib.request.Request(
        f"{WEBSHARE}/api/v2/proxy/list/?mode=direct&page=1&page_size=100",
        headers={"Authorization": "Token " + key})
    with urllib.request.urlopen(req, timeout=40) as r:
        rows = json.load(r).get("results") or []
    live = [p for p in rows if p.get("valid")]
    return sorted(live, key=lambda p: (p["proxy_address"], p["port"])), len(rows)


def proxy_config(p):
    """A Webshare row -> AdsPower's `user_proxy_config`."""
    return {
        "proxy_soft": "other",
        "proxy_type": "http",
        "proxy_host": p["proxy_address"],
        "proxy_port": str(p["port"]),
        "proxy_user": p["username"],
        "proxy_password": p["password"],
    }


def already_assigned(row, p):
    """True when this profile already points at exactly this proxy.

    Skipping an unchanged profile is not just speed: every update is a write to
    the operator's real AdsPower config, and the fewer of those the better.
    """
    cfg = row.get("user_proxy_config") or {}
    return (cfg.get("proxy_host") == p["proxy_address"]
            and str(cfg.get("proxy_port")) == str(p["port"])
            and cfg.get("proxy_user") == p["username"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", default=DEFAULT_GROUP,
                    help=f"AdsPower group to assign within (default {DEFAULT_GROUP!r})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the pairing, write nothing")
    args = ap.parse_args()

    ads_key = os.environ.get("ADS_API_KEY", "").strip()
    ws_key = os.environ.get("WEBSHARE_API", "").strip()
    if not ads_key or not ws_key:
        print("need ADS_API_KEY and WEBSHARE_API in the environment",
              file=sys.stderr)
        return 2

    proxies, total = webshare_proxies(ws_key)
    rows = [r for r in (ads_call("/api/v1/user/list?page=1&page_size=100",
                                 ads_key).get("list") or [])
            if not args.group or (r.get("group_name") or "") == args.group]
    rows.sort(key=lambda r: r["user_id"])

    print(f"{len(rows)} profile(s) in group {args.group!r} · "
          f"{len(proxies)} valid proxies"
          + (f" ({total - len(proxies)} invalid, skipped)" if total > len(proxies) else "")
          + (" · DRY RUN" if args.dry_run else ""))

    if not rows:
        print("no profiles — nothing to do (a real answer, not an error)")
        return 0
    if len(proxies) < len(rows):
        # Wrapping around would give two profiles one exit IP, which defeats
        # the separation the proxies are FOR.
        print(f"\nREFUSED: {len(rows)} profiles but only {len(proxies)} valid "
              f"proxies. Two profiles sharing an exit IP defeats the point of "
              f"separate identities, so this will not wrap around. Buy "
              f"{len(rows) - len(proxies)} more, or move profiles out of the "
              f"group.", file=sys.stderr)
        return 1

    changed = 0
    for row, p in zip(rows, proxies):
        name = row.get("name") or row["user_id"]
        where = f"{p['proxy_address']}:{p['port']} {p.get('country_code', '??')}"
        if already_assigned(row, p):
            print(f"  {name:22} = {where}   (unchanged)")
            continue
        if args.dry_run:
            print(f"  {name:22} -> {where}   WOULD SET")
            changed += 1
            continue
        ads_call("/api/v1/user/update", ads_key,
                 {"user_id": row["user_id"], "user_proxy_config": proxy_config(p)})
        print(f"  {name:22} -> {where}   set")
        changed += 1

    print(f"\n{changed} changed, {len(rows) - changed} already correct.")
    if changed and not args.dry_run:
        print("Now run sync_cookies.py so the vault picks the new IPs up — "
              "until it does, the scraper still exits from the OLD ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
