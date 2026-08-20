import pathlib
p = pathlib.Path("adspower/assign_proxies.py")
t = p.read_text(encoding="utf-8")

old = '''def webshare_proxies(key):
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
    return sorted(live, key=lambda p: (p["proxy_address"], p["port"])), len(rows)'''
new = '''def webshare_proxies(key, country=None):
    """Valid proxies, optionally in one country, sorted reproducibly.

    ⚠️ COUNTRY IS NOT COSMETIC. A Pinterest account signed in from the US that
    starts exiting through Paris or Bucharest is a WORSE signal than no proxy
    at all — the cookies say one country and the IP says another, which is
    precisely the mismatch an antidetect setup exists to avoid.

    The pool is mixed: a real listing on 2026-08-19 held 12 proxies across
    US/FR/RO/DE/BE/IT, and sorted by address the FIRST one was French. Pairing
    by position without filtering would have handed a US account a French exit
    IP on the very first profile.

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
    return sorted(live, key=lambda p: (p["proxy_address"], p["port"])), len(rows)'''
assert t.count(old) == 1
t = t.replace(old, new)

old2 = '''    ap.add_argument("--dry-run", action="store_true",
                    help="print the pairing, write nothing")'''
new2 = '''    ap.add_argument("--country", default=DEFAULT_COUNTRY,
                    help=f"only use proxies in this country (default "
                         f"{DEFAULT_COUNTRY!r}). Pass --country '' to allow ANY "
                         f"country, which you almost certainly do not want: an "
                         f"account signed in from one country exiting through "
                         f"another is a worse signal than no proxy at all.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the pairing, write nothing")'''
assert t.count(old2) == 1
t = t.replace(old2, new2)

t = t.replace('DEFAULT_GROUP = "pinterest"',
              'DEFAULT_GROUP = "pinterest"\n'
              '# The accounts are US (US region, US cookies, ip_country us). Proxies must\n'
              '# match, or the cookies and the exit IP disagree about where the user is.\n'
              'DEFAULT_COUNTRY = "US"')

old3 = '    proxies, total = webshare_proxies(ws_key)'
new3 = '    proxies, total = webshare_proxies(ws_key, args.country or None)'
assert t.count(old3) == 1
t = t.replace(old3, new3)

old4 = '''    print(f"{len(rows)} profile(s) in group {args.group!r} · "
          f"{len(proxies)} valid proxies"'''
new4 = '''    print(f"{len(rows)} profile(s) in group {args.group!r} · "
          f"{len(proxies)} valid"
          + (f" {args.country}" if args.country else " ANY-COUNTRY")
          + " proxies"'''
assert t.count(old4) == 1
t = t.replace(old4, new4)

old5 = '''        print(f"\nREFUSED: {len(rows)} profiles but only {len(proxies)} valid "
              f"proxies. Two profiles sharing an exit IP defeats the point of "
              f"separate identities, so this will not wrap around. Buy "
              f"{len(rows) - len(proxies)} more, or move profiles out of the "
              f"group.", file=sys.stderr)'''
new5 = '''        where = f" in {args.country}" if args.country else ""
        print(f"\nREFUSED: {len(rows)} profiles but only {len(proxies)} valid "
              f"proxies{where}. Two profiles sharing an exit IP defeats the "
              f"point of separate identities, so this will not wrap around. "
              f"Buy {len(rows) - len(proxies)} more{where}, or move profiles "
              f"out of the group. Do NOT reach for --country '' to make the "
              f"numbers work: a US account exiting through another country is "
              f"a worse signal than no proxy at all.", file=sys.stderr)'''
assert t.count(old5) == 1
t = t.replace(old5, new5)
p.write_text(t, encoding="utf-8")
print("assign_proxies: country-aware")
