"""Run the candidate browsers against real identities and compare them.

    python -m browsers.bench                    # both, one identity, headless
    python -m browsers.bench --only drission
    python -m browsers.bench --headed           # watch it
    python -m browsers.bench --profiles 3

Reads `browsers/identities.json` (make it with `python -m browsers.identities
export`). Never touches the vault: this decides WHICH browser to adopt, and a
benchmark that writes to the pool would contaminate the thing it is measuring.

THE PASS CONDITION IS NOT "IT LAUNCHED"
---------------------------------------
A driver passes when the cookies it hands back still buy AUTHENTICATED data
through curl_cffi — the real transport, the one the product uses. Pinterest
serves a signed-out browser a normal page and a signed-out caller plausible
public data, so every weaker check reports success for a broken session.

Each identity is used by at most one driver at a time, and the drivers run in
sequence, because two browsers holding one Pinterest session from one IP at the
same moment is the exact thing the vault's lease exists to prevent.
"""
import argparse
import json
import pathlib
import sys

from .drivers import DRIVERS, verify_authenticated

DEFAULT_FILE = "browsers/identities.json"


def load(path):
    document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    records = []
    for record in document.get("profiles") or []:
        try:
            cookies = json.loads(record.get("cookies_json") or "{}")
        except (ValueError, TypeError):
            cookies = {}
        if not cookies or not record.get("user_agent"):
            continue
        records.append({"profile_id": record["profile_id"],
                        "cookies": cookies,
                        "user_agent": record["user_agent"],
                        "proxy": record.get("proxy")})
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--only", choices=sorted(DRIVERS))
    ap.add_argument("--profiles", type=int, default=1,
                    help="how many identities to test each driver against")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser — slower, but the only way to see a "
                         "challenge page that the HTML check might miss")
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"no {path} — run:  python -m browsers.identities export",
              file=sys.stderr)
        return 2

    records = load(path)
    if not records:
        print("no usable identities in the file (need cookies AND a user agent)",
              file=sys.stderr)
        return 1
    records = records[:args.profiles]

    # BASELINE FIRST. If the cookies in the file are already dead, every driver
    # fails and the benchmark would blame the browsers for it.
    print("baseline — do these cookies authenticate BEFORE any browser runs?")
    alive = []
    for record in records:
        ok, detail = verify_authenticated(record["cookies"],
                                          record["user_agent"],
                                          record.get("proxy"))
        print(f"  {record['profile_id']:<24} "
              f"{'✅ live' if ok else '❌ dead'}  {detail}")
        if ok:
            alive.append(record)
    if not alive:
        print("\nEvery identity in the file is already signed out. Refresh the "
              "export before benchmarking — otherwise this measures nothing.",
              file=sys.stderr)
        return 1

    chosen = {args.only: DRIVERS[args.only]} if args.only else DRIVERS
    results = []
    labels = {}
    for name, run in chosen.items():
        print(f"\n{'=' * 62}\n{name}")
        for record in alive:
            result = run(record, headless=not args.headed)
            if result.cookies_out:
                ok, detail = verify_authenticated(result.cookies_out,
                                                  record["user_agent"],
                                                  record.get("proxy"))
                result.authenticated = ok
                result.detail = detail
            else:
                result.detail = result.error or "no cookies came back"
            results.append(result)
            labels[result.name] = name        # DriverResult uses the pretty name
            print(f"  {record['profile_id']:<24} "
                  f"launched={'y' if result.launched else 'n'} "
                  f"loaded={'y' if result.loaded else 'n'} "
                  f"cookies={len(result.cookies_out)} "
                  f"{result.seconds}s")
            print(f"      {'✅ AUTHENTICATED' if result.ok else '❌ FAILED'} — "
                  f"{result.detail}")
            if result.detected:
                print("      ⚠️  the page looks like a bot challenge")
            if result.ua_seen and result.ua_seen != record["user_agent"]:
                # The browser must wear the UA the cookies were born under, or
                # the jar is being replayed under a different browser.
                print(f"      ⚠️  UA NOT APPLIED — page reports "
                      f"{result.ua_seen[:60]}")

    print(f"\n{'=' * 62}\nSUMMARY")
    for name in chosen:
        mine = [r for r in results if labels.get(r.name) == name]
        passed = sum(1 for r in mine if r.ok)
        seconds = [r.seconds for r in mine if r.seconds]
        print(f"  {name:<14} {passed}/{len(mine)} authenticated"
              + (f" · {min(seconds)}-{max(seconds)}s" if seconds else ""))
    return 0 if any(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
