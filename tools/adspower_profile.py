"""Create a BLANK AdsPower profile with a proxy already attached.

The one step in adding an account that had no tooling. `assign_proxies.py`
assigns proxies to profiles that already exist; nothing created the profile, so
that part was done by hand in the AdsPower UI -- which is where the proxy gets
forgotten, or a third profile quietly lands on a proxy already carrying two.

WHAT THIS DOES NOT DO, AND WILL NOT
-----------------------------------
It does not log in to Pinterest. It cannot. Pinterest's login is its most
defended surface and a flagged attempt burns the account immediately, so a human
signs in by hand, once per account, behind that account's own proxy. This script
gets everything ready up to that point and then stops.

EVERYTHING REUSED, NOTHING REIMPLEMENTED
----------------------------------------
`ads_call`, `webshare_proxies`, `proxy_config`, `country_from_group` and
`MAX_PROFILES_PER_PROXY` all come from `adspower/assign_proxies.py`. A second
copy of the proxy rules is a second copy that drifts, and the two-per-proxy cap
is the operator's rule -- it must be the SAME constant, not an equal number.
"""
import argparse
import sys

from adspower.assign_proxies import (
    MAX_PROFILES_PER_PROXY,
    ads_call,
    country_from_group,
    proxy_config,
    proxy_key,
    webshare_proxies,
)
from adspower.keys import ads_key, webshare_key


def existing(key, group):
    """Profiles in this group, so proxy usage can be counted before assigning."""
    rows = ads_call("/api/v1/user/list?page=1&page_size=100", key).get("list") or []
    return [r for r in rows if not group or (r.get("group_name") or "") == group]


def group_id_for(key, group):
    """AdsPower's numeric id for a group name.

    Creating without one drops the profile into the default group, where
    `sync_cookies.py --group pinterest` would never find it -- the profile would
    exist, look fine in the UI, and never reach the vault.
    """
    for g in ads_call("/api/v1/group/list?page=1&page_size=100", key).get("list") or []:
        if (g.get("group_name") or "") == group:
            return g.get("group_id")
    raise SystemExit(
        "no AdsPower group named " + repr(group) + ". Create it in AdsPower "
        "first -- the group is what routes a profile to the right vault pool.")


def usage(rows):
    """proxy_key -> how many profiles already exit through it.

    Keyed with `proxy_key()` from assign_proxies, NOT a string built here.
    That function returns a (host, str(port)) TUPLE -- because AdsPower stores
    the port as a string and Webshare returns an int -- and a "host:port" string
    silently never matches it. The first version of this file did exactly that:
    every proxy then looked unused, so it always picked the first one and would
    NEVER have refused at the cap. It would have quietly put a third and fourth
    account on one IP while reporting success.
    """
    counts = {}
    for r in rows:
        cfg = r.get("user_proxy_config") or {}
        host, port = cfg.get("proxy_host"), cfg.get("proxy_port")
        if host and port:
            k = (host, str(port))
            counts[k] = counts.get(k, 0) + 1
    return counts


def pick(proxies, counts):
    """The least-used proxy still under the cap, or None.

    Least-used rather than first-free so the pool fills evenly instead of
    stacking two profiles on one proxy while others sit idle.
    """
    free = [(counts.get(proxy_key(p), 0), i, p) for i, p in enumerate(proxies)
            if counts.get(proxy_key(p), 0) < MAX_PROFILES_PER_PROXY]
    return min(free)[2] if free else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", default="pinterest",
                    help="AdsPower group; also decides the proxy's country")
    ap.add_argument("--name", default=None,
                    help="profile name (default: <group>_<n>)")
    ap.add_argument("--country", default=None,
                    help="override the country derived from the group")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key, ws = ads_key(), webshare_key()
    if not key:
        raise SystemExit("no AdsPower API key (ADS_API_KEY / adspower_api)")
    if not ws:
        raise SystemExit("no Webshare API key (WEBSHARE_API)")

    country = args.country or country_from_group(args.group)
    rows = existing(key, args.group)
    counts = usage(rows)
    proxies, total, _ = webshare_proxies(ws, country)

    print(str(len(rows)) + " profile(s) in group " + repr(args.group) + " · "
          + str(len(proxies)) + " valid "
          + (country or "ANY-COUNTRY") + " proxies"
          + (" (of " + str(total) + ")" if total > len(proxies) else "")
          + (" · DRY RUN" if args.dry_run else ""))

    chosen = pick(proxies, counts)
    if chosen is None:
        # Refusing is the correct answer. Creating the profile anyway would
        # either leave it proxy-less -- exiting from this machine's own IP,
        # which the vault refuses to lease -- or put a third account on a proxy,
        # which is the operator's stated cap.
        raise SystemExit(
            "no proxy is under the " + str(MAX_PROFILES_PER_PROXY) + "-profile "
            "cap in " + (country or "any country") + ". Buy another proxy "
            "before adding another account; a profile without one exits from "
            "this host and the vault will refuse to lease it.")

    name = args.name or (args.group + "_" + str(len(rows) + 1))
    key_of = proxy_key(chosen)
    where = key_of[0] + ":" + key_of[1]
    print("  new profile : " + name)
    print("  proxy       : " + where + "  (now carrying "
          + str(counts.get(key_of, 0) + 1) + " of "
          + str(MAX_PROFILES_PER_PROXY) + ")")

    if args.dry_run:
        print("\n  DRY RUN — nothing created")
        return 0

    gid = group_id_for(key, args.group)
    data = ads_call("/api/v1/user/create", key, body={
        "name": name,
        "group_id": str(gid),
        "user_proxy_config": proxy_config(chosen),
        # AdsPower generates the fingerprint. Deliberately not specified: its
        # own is internally consistent, and `sync_cookies.py --ua-mode` measures
        # whatever it produced and stores it so keepalive replays the SAME
        # machine on the VM. Inventing one here would break that chain.
        "fingerprint_config": {"automatic_timezone": "1"},
    })
    user_id = data.get("id") or data.get("user_id")
    print("  created     : user_id=" + str(user_id))
    print()
    print("  NEXT, AND IT IS A HUMAN STEP:")
    print("   1. open the profile in AdsPower and LOG IN to Pinterest BY HAND")
    print("      (never automated — a flagged login burns the account)")
    print("   2. python adspower/sync_cookies.py --group " + args.group)
    print("      run it with a browser start at least once: that is what")
    print("      captures the user agent AND the fingerprint")
    print("   3. GCP picks it up within 5 minutes. Nothing is copied anywhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
