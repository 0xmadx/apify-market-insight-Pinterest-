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
import pathlib
import subprocess
import os
import sys
import time
import urllib.request

ADS = "http://127.0.0.1:50325"
WEBSHARE = "https://proxy.webshare.io"
DEFAULT_GROUP = "pinterest"
# The accounts are US — US region, US cookies, ip_country "us". The proxy
# must agree, or the cookies and the exit IP disagree about where the user
# is, which is the one thing this whole setup exists to avoid.
DEFAULT_COUNTRY = "US"
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


def webshare_proxies(key, country=None):
    """Valid proxies, optionally in one country, sorted reproducibly.

    ⚠️ COUNTRY IS NOT COSMETIC. An account signed in from the US that starts
    exiting through Paris or Bucharest is a WORSE signal than no proxy at all:
    the cookies say one country and the IP says another, which is precisely the
    mismatch an antidetect setup exists to prevent.

    The pool is MIXED and this is not hypothetical. A real listing on
    2026-08-19 held 12 valid proxies across US/FR/RO/DE/BE/IT, and sorted by
    address the FIRST one was French — so pairing by position without filtering
    would have handed a US account a French exit IP on the very first profile.

    Only `valid` ones are returned as well: assigning a proxy Webshare has
    already marked dead hands a profile an exit IP that fails on first use, and
    that failure reads as a dead session rather than a dead proxy.
    """
    req = urllib.request.Request(
        f"{WEBSHARE}/api/v2/proxy/list/?mode=direct&page=1&page_size=100",
        headers={"Authorization": "Token " + key})
    with urllib.request.urlopen(req, timeout=40) as r:
        rows = json.load(r).get("results") or []
    live = [p for p in rows if p.get("valid")]
    if country:
        live = [p for p in live
                if (p.get("country_code") or "").upper() == country.upper()]
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
    ap.add_argument("--country", default=DEFAULT_COUNTRY,
                    help=f"only use proxies in this country (default "
                         f"{DEFAULT_COUNTRY!r}). --country '' allows ANY "
                         f"country, which you almost certainly do not want.")
    ap.add_argument("--partial", action="store_true",
                    help="when there are fewer proxies than profiles, assign "
                         "the ones that fit and leave the rest WITHOUT a proxy "
                         "instead of refusing. Never shares a proxy between two "
                         "profiles — the leftovers exit from the host, which is "
                         "what they already do today.")
    ap.add_argument("--no-sync", action="store_true",
                    help="do NOT refresh the vault afterwards. By default a "
                         "change is synced immediately, because while "
                         "AdsPower and the vault disagree about a profile's "
                         "exit IP there are two truths and the scraper is "
                         "using the stale one.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the pairing, write nothing")
    args = ap.parse_args()

    ads_key = os.environ.get("ADS_API_KEY", "").strip()
    ws_key = os.environ.get("WEBSHARE_API", "").strip()
    if not ads_key or not ws_key:
        print("need ADS_API_KEY and WEBSHARE_API in the environment",
              file=sys.stderr)
        return 2

    proxies, total = webshare_proxies(ws_key, args.country or None)
    rows = [r for r in (ads_call("/api/v1/user/list?page=1&page_size=100",
                                 ads_key).get("list") or [])
            if not args.group or (r.get("group_name") or "") == args.group]
    rows.sort(key=lambda r: r["user_id"])

    print(f"{len(rows)} profile(s) in group {args.group!r} · "
          f"{len(proxies)} valid"
          + (f" {args.country}" if args.country else " ANY-COUNTRY")
          + " proxies"
          + (f" (of {total} in the pool)" if total > len(proxies) else "")
          + (" · DRY RUN" if args.dry_run else ""))

    if not rows:
        print("no profiles — nothing to do (a real answer, not an error)")
        return 0
    unassigned = []
    if len(proxies) < len(rows) and args.partial:
        # SAFE, and categorically different from wrapping: the leftover
        # profiles get NOTHING rather than a shared IP. A profile with no proxy
        # exits from the host, which is still coherent — its browser and its
        # scraper agree, because both use that host. What is never allowed is
        # two profiles pointing at one proxy.
        unassigned = rows[len(proxies):]
        rows = rows[:len(proxies)]

    if len(proxies) < len(rows):
        # Wrapping around would give two profiles one exit IP, which defeats
        # the separation the proxies are FOR.
        where = f" in {args.country}" if args.country else ""
        print(f"\nREFUSED: {len(rows)} profiles but only {len(proxies)} valid "
              f"proxies{where}. Two profiles sharing an exit IP defeats the "
              f"point of separate identities, so this will not wrap around. "
              f"Buy {len(rows) - len(proxies)} more{where}, or move profiles "
              f"out of the group.\n"
              f"Do NOT reach for --country '' to make the numbers work: an "
              f"account signed in from one country exiting through another is "
              f"a worse signal than no proxy at all.\n"
              f"Pass --partial to assign the {len(proxies)} that DO fit and "
              f"leave the rest without one — safe, because they exit from "
              f"the host, which is what they do today.", file=sys.stderr)
        return 1

    changed = 0
    for row, p in zip(rows, proxies):
        name = row.get("name") or row["user_id"]
        where = f"{p['proxy_address']}:{p['port']} {p.get('country_code', '??')}"
        if already_assigned(row, p):
            print(f"  {name:22} = {where}   (unchanged)")
            continue
        # Changing a proxy on an account that has ALREADY signed in MOVES
        # that account: its cookies were born behind the old IP and will be
        # replayed from the new one. Same country is mild — real users do
        # change ISP — but a different country is the exact mismatch this
        # setup exists to avoid, so it is never silent.
        old_host = (row.get("user_proxy_config") or {}).get("proxy_host")
        signed_in = row.get("last_open_time") not in ("0", "", None)
        note = (f"   ⚠️ MOVES a live account off {old_host}"
                if old_host and signed_in else "")

        if args.dry_run:
            print(f"  {name:22} -> {where}   WOULD SET{note}")
            changed += 1
            continue
        ads_call("/api/v1/user/update", ads_key,
                 {"user_id": row["user_id"], "user_proxy_config": proxy_config(p)})
        print(f"  {name:22} -> {where}   set{note}")
        changed += 1

    for row in unassigned:
        # Named, never silent: the operator must know which accounts are still
        # exiting from the host, so they can be finished when proxies arrive.
        print(f"  {(row.get('name') or row['user_id']):22} -- NO PROXY "
              f"(none left in {args.country or 'the pool'}) — still exits "
              f"from the host")

    print(f"\n{changed} changed, {len(rows) - changed} already correct"
          + (f", {len(unassigned)} left without a proxy" if unassigned else "")
          + ".")
    # Push the change through to the vault immediately rather than telling a
    # human to. AdsPower is the source of truth for a profile's exit IP; while
    # the vault disagrees there are effectively TWO truths, and relying on the
    # 5-minute timer left that window open every single time a proxy moved.
    if changed and not args.dry_run and not args.no_sync:
        print("\nsyncing the vault so it agrees with AdsPower ...")
        rc = subprocess.call(
            [sys.executable, str(pathlib.Path(__file__).with_name("sync_cookies.py")),
             "--group", args.group or "", "--ua-mode", "never"])
        if rc != 0:
            print("  ⚠️ the sync did not finish cleanly. Until it runs the vault "
                  "still holds the OLD proxies — run sync_cookies.py yourself "
                  "before scraping.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
