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
having no proxy. So the assignment is **sticky**:

    1. a profile that already has a proxy still in the pool KEEPS it
    2. everything left over goes to the least-used proxy, ties by (host, port)

Nothing that already has an exit IP is ever given a different one — not when a
profile is added, not when the pool grows, not when it shrinks. (An earlier
version paired by sorted position, which was stable only as long as the sort
was: AdsPower user_ids are random, so a new profile could sort into the middle
and shift every profile after it onto a different proxy.)

⚠️ UP TO TWO PROFILES MAY SHARE AN EXIT IP (`MAX_PROFILES_PER_PROXY`).
The danger is one ACCOUNT seen from two IPs, not two accounts from one IP —
the latter is every household and office. Two is where that stops being
believable, so it is a ceiling and `--max-share` can only tighten it.

⚠️ MORE PROFILES THAN THE POOL CAN HOLD is refused, never stacked deeper.
`--partial` assigns what fits and leaves the rest with NO proxy, which is safe:
they exit from the host, and the browser and the scraper still agree.

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

# Both invocation styles have to work: this file is RUN as
# `python adspower/assign_proxies.py` (which puts adspower/ on the path,
# so the bare import resolves) and IMPORTED as `adspower.assign_proxies`
# by tools/adspower_profile.py, which reuses its proxy rules rather than
# keeping a second copy of the two-per-proxy cap.
try:
    from . import keys
except ImportError:      # run as a script, not imported as a package
    import keys

ADS = "http://127.0.0.1:50325"
WEBSHARE = "https://proxy.webshare.io"
# Runaway guard for the paginated listing, NOT a pool limit: 50 pages of 100 is
# 5,000 proxies, far past any plan here. It exists so a `next` link that never
# ends stops with a refusal instead of looping forever.
WEBSHARE_MAX_PAGES = 50
DEFAULT_GROUP = "pinterest"
# The accounts are US — US region, US cookies, ip_country "us". The proxy
# must agree, or the cookies and the exit IP disagree about where the user
# is, which is the one thing this whole setup exists to avoid.
DEFAULT_COUNTRY = "US"
# How many profiles may sit behind ONE exit IP. Operator's call, 2026-08-20.
#
# The thing that gets an account flagged is ONE ACCOUNT seen from two IPs — its
# cookies replayed from an address they were not born behind. The reverse, two
# DIFFERENT accounts from one IP, is what every household and office looks
# like, and Pinterest cannot treat it as fraud without banning families.
#
# 2 is where that stops being true. Three, five, ten accounts on one
# residential-looking address is a farm, and the correlation is trivial to see.
# So this is a hard ceiling, not a default to tune upward.
#
# What it costs: the two profiles sharing an IP can be leased at the same time,
# so that address can carry double the request rate of a single-account one.
# Acceptable at this volume (a run is 3-38s and a few dozen requests), and the
# first thing to reconsider if Pinterest ever starts answering with 429s.
MAX_PROFILES_PER_PROXY = 2
# Two-letter suffixes only. "pinterest-fr" declares a French group; a longer
# suffix ("pinterest-backup") is a name, not a country, and falls through to
# --country / the US default rather than being guessed at.
GROUP_SEPARATOR = "-"
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

    EVERY PAGE, NOT THE FIRST. This used to fetch page 1 of 100 and stop. A pool
    past 100 lost everything after row 100 — and because the valid/country
    filters run AFTER the fetch, dead or non-US rows on page 1 pushed real US
    proxies off the end with no error. It follows Webshare's `next` link until
    there is none, and refuses rather than returning a partial pool that would
    read as complete if the chain never ends.
    """
    url = f"{WEBSHARE}/api/v2/proxy/list/?mode=direct&page=1&page_size=100"
    rows, pages = [], 0
    while url:
        if pages >= WEBSHARE_MAX_PAGES:
            raise SystemExit(
                f"Webshare listing still had a next page after "
                f"{WEBSHARE_MAX_PAGES} pages ({len(rows)} proxies read). "
                "Refusing to assign from a pool that may be incomplete.")
        req = urllib.request.Request(
            url, headers={"Authorization": "Token " + key})
        with urllib.request.urlopen(req, timeout=40) as r:
            body = json.load(r)
        rows.extend(body.get("results") or [])
        url = body.get("next")
        pages += 1
    live = [p for p in rows if p.get("valid")]
    picked = live
    if country:
        picked = [p for p in live
                  if (p.get("country_code") or "").upper() == country.upper()]
    # The third return value is every valid proxy REGARDLESS of country. It is
    # what tells us which country a profile's CURRENT proxy is in — that host
    # is, by definition, usually outside the country being filtered for, so the
    # filtered list cannot answer it.
    return (sorted(picked, key=lambda p: (p["proxy_address"], p["port"])),
            len(rows), live)


def country_from_group(group):
    """A group named `pinterest-fr` declares its own country.

    The country belongs in the group NAME rather than in a flag the operator
    has to remember, because the flag is the dangerous half: run
    `--country FR` against the main group by mistake and positional pairing
    hands the French IP to whichever profile sorts first — an existing, live,
    US account. Encoding it in the name means the wrong country and the wrong
    group cannot be selected independently.

    Returns None for a group with no country suffix, which means "fall back to
    --country".
    """
    if not group or GROUP_SEPARATOR not in group:
        return None
    suffix = group.rsplit(GROUP_SEPARATOR, 1)[1]
    return suffix.upper() if len(suffix) == 2 and suffix.isalpha() else None


def country_of(host, proxies):
    """Which country a proxy host sits in, per the Webshare pool. None = gone.

    A host that is no longer in the pool is UNKNOWN, not "some other country":
    Webshare removing a proxy is the ordinary case, and treating unknown as a
    mismatch would block every legitimate replacement.
    """
    for p in proxies:
        if p["proxy_address"] == host:
            return (p.get("country_code") or "").upper() or None
    return None


def proxy_key(p):
    """Identity of a proxy as (host, port). Port is stringified because
    AdsPower stores it as a string and Webshare returns an int."""
    return (p["proxy_address"], str(p["port"]))


def plan_assignments(rows, proxies, max_share=MAX_PROFILES_PER_PROXY):
    """Decide which proxy each profile gets. Pure — no API calls, so testable.

    Two rules, in this order, and the order is the whole design:

    1. **STICKY.** A profile that already points at a proxy still in the pool
       KEEPS it. Nothing that has an exit IP ever gets a different one.
    2. **LEAST-USED FIRST** for everything left over, ties broken by
       (host, port) so the result is reproducible.

    Sticky-first replaces the old pairing-by-sorted-position, which was stable
    only as long as the sorted order was. AdsPower user_ids are random strings,
    so a NEW profile could sort into the middle and shift every profile after
    it onto a different proxy — silently moving live accounts, which is exactly
    what this file exists to prevent. Position pairing was the right call when
    no profile had a proxy yet; now that they do, what they have is the
    authority.

    Returns (pairs, unassignable, usage).
    """
    by_key = {proxy_key(p): p for p in proxies}
    usage = {proxy_key(p): 0 for p in proxies}

    pairs, pending = [], []
    for row in rows:
        cfg = row.get("user_proxy_config") or {}
        key = (cfg.get("proxy_host"), str(cfg.get("proxy_port")))
        if key in by_key and usage[key] < max_share:
            usage[key] += 1
            pairs.append((row, by_key[key]))
        else:
            pending.append(row)

    unassignable = []
    for row in pending:
        free = sorted((k for k, n in usage.items() if n < max_share),
                      key=lambda k: (usage[k], k))
        if not free:
            unassignable.append(row)
            continue
        usage[free[0]] += 1
        pairs.append((row, by_key[free[0]]))

    order = {id(r): i for i, r in enumerate(rows)}
    pairs.sort(key=lambda rp: order[id(rp[0])])
    return pairs, unassignable, usage


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
    ap.add_argument("--country", default=None,
                    help=f"only use proxies in this country. Defaults to the "
                         f"group's own suffix ('pinterest-fr' -> FR), then to "
                         f"{DEFAULT_COUNTRY!r}. Passing one that CONTRADICTS "
                         f"the group name is refused. --country '' allows ANY "
                         f"country, which you almost certainly do not want.")
    ap.add_argument("--allow-country-move", action="store_true",
                    help="permit moving an ALREADY SIGNED-IN account to a "
                         "proxy in a different country. Refused by default: "
                         "the account's cookies, language and history were all "
                         "born in one country, and an exit IP in another is "
                         "the exact mismatch this setup exists to prevent.")
    ap.add_argument("--max-share", type=int, default=MAX_PROFILES_PER_PROXY,
                    dest="max_share",
                    help=f"how many profiles may share one exit IP (default "
                         f"{MAX_PROFILES_PER_PROXY}, hard maximum "
                         f"{MAX_PROFILES_PER_PROXY}). Two different accounts "
                         f"from one address is a household; three or more is a "
                         f"farm. Lower it to 1 for one IP per account.")
    ap.add_argument("--partial", action="store_true",
                    help="when even --max-share cannot cover every profile, "
                         "assign the ones that fit and leave the rest WITHOUT "
                         "a proxy instead of refusing. The leftovers exit from "
                         "the host, which is what they already do today.")
    ap.add_argument("--no-sync", action="store_true",
                    help="do NOT refresh the vault afterwards. By default a "
                         "change is synced immediately, because while "
                         "AdsPower and the vault disagree about a profile's "
                         "exit IP there are two truths and the scraper is "
                         "using the stale one.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the pairing, write nothing")
    args = ap.parse_args()

    # A ceiling, not a default. Anyone can ask for FEWER profiles per IP; the
    # flag exists to tighten the rule, never to loosen it past what was
    # reasoned about. A flag that can raise its own maximum is not a limit.
    if not 1 <= args.max_share <= MAX_PROFILES_PER_PROXY:
        print(f"REFUSED: --max-share {args.max_share} is outside "
              f"1..{MAX_PROFILES_PER_PROXY}. Two different accounts behind one "
              f"address is what a household looks like and Pinterest cannot "
              f"treat it as fraud; three or more on one residential-looking IP "
              f"is a farm, and correlating them is trivial.", file=sys.stderr)
        return 2

    # The group name is the authority on country when it carries a suffix. A
    # flag that disagrees with it is a mistake, not an override — the two ways
    # of saying the same thing must never be able to say different things.
    declared = country_from_group(args.group)
    if args.country is None:
        country = declared or DEFAULT_COUNTRY
    elif declared and args.country.upper() != declared.upper():
        print(f"REFUSED: group {args.group!r} declares country {declared}, but "
              f"--country {args.country!r} was passed. Rename the group or drop "
              f"the flag; do not let them disagree.", file=sys.stderr)
        return 2
    else:
        country = args.country or None
    args.country = country

    ads_key = keys.ads_key()
    ws_key = keys.webshare_key()
    if not ads_key or not ws_key:
        # Name what WAS found. The old message said only what was missing, and
        # said it identically whether `.env` was unread or the key was spelled
        # differently in it — two very different fixes.
        print(f"missing a key.\n"
              f"  AdsPower ({'/'.join(keys.ADS_NAMES)}): "
              f"{keys.describe(keys.ADS_NAMES)}\n"
              f"  Webshare ({'/'.join(keys.WEBSHARE_NAMES)}): "
              f"{keys.describe(keys.WEBSHARE_NAMES)}\n"
              f"`.env` at the repo root is read automatically; an exported "
              f"variable wins over it.", file=sys.stderr)
        return 2

    proxies, total, all_valid = webshare_proxies(ws_key, args.country or None)
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
    if not proxies:
        where = f" in {args.country}" if args.country else ""
        print(f"\nREFUSED: no valid proxies{where} at all — there is nothing to "
              f"assign.", file=sys.stderr)
        return 1

    pairs, unassigned, usage = plan_assignments(rows, proxies, args.max_share)

    if unassigned and not args.partial:
        where = f" in {args.country}" if args.country else ""
        capacity = len(proxies) * args.max_share
        print(f"\nREFUSED: {len(rows)} profiles but only {len(proxies)} valid "
              f"proxies{where}, which hold {capacity} at {args.max_share} "
              f"profiles per exit IP. {len(unassigned)} would have nowhere to "
              f"go.\n"
              f"This will NOT stack a third account on an IP: two different "
              f"accounts from one address is a household, three or more is a "
              f"farm, and the correlation is trivial to see.\n"
              f"Buy {-(-len(unassigned) // args.max_share)} more{where}, move "
              f"profiles out of the group, or pass --partial to assign the "
              f"{len(pairs)} that fit and leave the rest with NO proxy — safe, "
              f"because they exit from the host, which is what they do today.\n"
              f"Do NOT reach for --country '' to make the numbers work: an "
              f"account signed in from one country exiting through another is "
              f"a worse signal than no proxy at all.", file=sys.stderr)
        return 1

    shared = sum(1 for n in usage.values() if n > 1)
    if shared:
        # Never silent. The operator asked for sharing; they should still see
        # exactly how much of it they got, every run.
        print(f"  ({shared} exit IP(s) carrying {args.max_share} profiles each "
              f"— two different accounts per address, which is what a household "
              f"looks like)")

    changed = 0
    for row, p in pairs:
        name = row.get("name") or row["user_id"]
        sharers = usage[proxy_key(p)]
        tag = f" ×{sharers}" if sharers > 1 else ""
        where = (f"{p['proxy_address']}:{p['port']} "
                 f"{p.get('country_code', '??')}{tag}")
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
        old_country = country_of(old_host, all_valid) if old_host else None
        new_country = (p.get("country_code") or "").upper() or None

        # A same-country replacement is mild — real people change ISP. A
        # CROSS-COUNTRY move is not: the account was created behind one
        # country's IP, and its language, its feed and its cookies all agree
        # with that. Moving it abroad is the mismatch the proxies exist to
        # prevent, so it is refused rather than warned about.
        if (signed_in and old_country and new_country
                and old_country != new_country and not args.allow_country_move):
            print(f"\nREFUSED: {name} is a live account on a {old_country} "
                  f"proxy and this would move it to {new_country}. Its cookies, "
                  f"language and history were all born in {old_country}; an exit "
                  f"IP in {new_country} is the mismatch this setup exists to "
                  f"prevent.\n"
                  f"A NEW account can be created behind any country — put it in "
                  f"a group named for that country (e.g. "
                  f"'{args.group or DEFAULT_GROUP}-{new_country.lower()}') and "
                  f"assign its proxy BEFORE the first login.\n"
                  f"Pass --allow-country-move only if you know this account can "
                  f"survive the move.", file=sys.stderr)
            return 1

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
