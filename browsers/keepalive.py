"""Keep Pinterest sessions alive and fresh in the vault, without AdsPower.

    python -m browsers.keepalive --once          # one pass over every profile
    python -m browsers.keepalive                 # loop forever, every 5 min
    python -m browsers.keepalive --profiles 3 --headed

This is the replacement for `adspower/sync_cookies.py`. Same contract, no
paid product: for each profile, launch a browser wearing that profile's proxy,
user agent and fingerprint, load one Pinterest page, read the cookies back, and
write them to the vault.

WHY DrissionPage AND NOT patchright
-----------------------------------
patchright is faster and hides automation better, but it SILENTLY IGNORES init
scripts, so every profile on one host reports that host's hardware — same GPU,
same core count, same canvas, same fonts. Measured 1/3 distinct. DrissionPage
honours `Page.addScriptToEvaluateOnNewDocument`, which is the only way to give
each profile its own identity: 3/3 distinct and 3/3 stable. Fingerprint
diversity is the thing AdsPower is actually paid for, so it decides the driver.

THE RULES THIS INHERITS FROM sync_cookies.py, AND WHY
-----------------------------------------------------
1. **A signed-out jar is never written.** Cookies can exist and still be
   logged out; Pinterest then answers with plausible PUBLIC data and the run
   "succeeds" while collecting the wrong thing. That is the defining failure
   mode of this codebase.
2. **Cookies, user agent and proxy are written together.** They are one
   identity. A jar stored without its UA gets replayed under a different
   browser; without its proxy, from a different country.
3. **`last_updated` is stamped only on a verified pass.** The vault reads it as
   "someone confirmed this recently" and refuses anything older than
   PROFILE_MAX_AGE (900s). Stamping it after a failed check would make a dead
   session look fresh — worse than letting it expire.

AND ONE RULE THAT IS NEW HERE
-----------------------------
4. **The exit IP is verified before anything is written.** Chromium falls back
   to a direct connection when a proxy fails, and every other signal — launched,
   loaded, cookies returned — still reports success. Writing cookies harvested
   over the wrong IP is how an account gets flagged, so the check happens first
   and a mismatch aborts that profile.
"""
import argparse
import json
import pathlib
import sys
import time

from src.config import Config

PINTEREST = "https://www.pinterest.com/"
IP_ECHO = "https://api.ipify.org"
AUTH_COOKIES = ("_auth", "_pinterest_sess")
# Where each profile's Chromium user-data lives. Per profile, never shared:
# DrissionPage's auto-assigned folder collided on a second launch
# (BrowserConnectError: "the user folder does not conflict"), and a persistent
# directory is better anyway — history, cache and localStorage accumulate, so
# the browser looks like a device that has been here before rather than a fresh
# install every cycle.
PROFILE_ROOT = pathlib.Path("browsers/profiles")


def profile_dir(profile_id):
    path = PROFILE_ROOT / profile_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def profile_port(profile_id):
    """A stable debug port per profile.

    `auto_port()` cannot be used here. It blanks the address and lets
    DrissionPage pick a port AND a throwaway user-data dir together; calling
    `set_user_data_path` afterwards sets `_auto_port = False` and leaves the
    address empty, so `connect_browser` dies on `address.split(':')` before the
    browser ever starts. Measured 2026-08-20 — it surfaced as a bare
    "not enough values to unpack".

    Derived from the id rather than allocated, so the same profile always uses
    the same port: two passes cannot interleave onto each other's browser, and
    a stuck Chromium is identifiable from `netstat` alone.
    """
    import hashlib

    digest = hashlib.sha1(f"port::{profile_id}".encode()).digest()
    return 20000 + (int.from_bytes(digest[:2], "big") % 30000)


class Result:
    def __init__(self, profile_id):
        self.profile_id = profile_id
        self.ok = False
        self.reason = ""
        self.cookies = {}
        self.exit_ip = None
        self.seconds = None


def refresh(record, headless=True, url=PINTEREST):
    """One profile: launch, verify, harvest. Writes nothing."""
    from DrissionPage import Chromium, ChromiumOptions

    from .fingerprint_patch import build_script
    from .drivers import cookie_records, expected_exit
    from .proxy_relay import ProxyRelay

    result = Result(record["profile_id"])
    started = time.monotonic()
    relay = None
    browser = None
    try:
        options = ChromiumOptions()
        options.set_user_agent(record["user_agent"])
        options.set_argument("--no-sandbox")
        # Order matters: the data path disables auto-port, so the port must be
        # set explicitly and after it.
        options.set_user_data_path(profile_dir(record["profile_id"]))
        options.set_local_port(profile_port(record["profile_id"]))
        if headless:
            options.headless()
        if record.get("proxy"):
            # Chromium cannot carry proxy credentials; the relay adds them.
            relay = ProxyRelay(record["proxy"]).start()
            options.set_proxy(relay.url)

        browser = Chromium(options)
        page = browser.latest_tab
        page.run_cdp("Page.addScriptToEvaluateOnNewDocument",
                     source=build_script(record["profile_id"]))

        # RULE 4, and it comes first: everything after this would report
        # success over a direct connection.
        if record.get("proxy"):
            page.get(IP_ECHO)
            result.exit_ip = (page.run_js(
                "return document.body.innerText") or "").strip()[:45]
            if expected_exit(record["proxy"]) not in result.exit_ip:
                result.reason = (f"exit IP {result.exit_ip} is not the proxy "
                                 f"{expected_exit(record['proxy'])}")
                return result

        for cookie in cookie_records(record["cookies"]):
            page.set.cookies(cookie)
        page.get(url)

        harvested = {c["name"]: c["value"] for c in page.cookies()}
        missing = [c for c in AUTH_COOKIES if c not in harvested]
        if missing:
            # RULE 1. Not an error — a real state, and the fix is a human
            # logging in. Saying which cookie is gone is the difference between
            # "re-login" and an afternoon of debugging.
            result.reason = f"signed OUT — missing {', '.join(missing)}"
            return result

        result.cookies = harvested
        result.ok = True
        result.reason = f"{len(harvested)} cookies"
        return result
    except Exception as exc:
        result.reason = f"{type(exc).__name__}: {str(exc)[:90]}"
        return result
    finally:
        try:
            if browser:
                browser.quit()
        except Exception:
            pass
        if relay:
            relay.stop()
        result.seconds = round(time.monotonic() - started, 1)


def write(vault, platform, record, cookies):
    """Store the identity. Mirrors browsers/identities.py:restore()."""
    profile_id = record["profile_id"]
    fields = {
        "cookies_json": json.dumps(cookies),
        "user_agent": record["user_agent"],
        # RULE 3: only reached on a verified pass.
        "last_updated": str(time.time()),
        "is_valid": "1",
    }
    if record.get("proxy"):
        fields["proxy"] = record["proxy"]
    vault.r.hset(f"cookie:{platform}:{profile_id}", mapping=fields)
    vault.r.sadd(f"valid_profiles:{platform}", profile_id)


def one_pass(records, vault, platform, headless=True, log=print):
    """Every profile, in sequence. Returns (written, skipped)."""
    written = skipped = 0
    for record in records:
        result = refresh(record, headless=headless)
        if result.ok:
            write(vault, platform, record, result.cookies)
            written += 1
            mark = "OK  "
        else:
            skipped += 1
            mark = "SKIP"
        ip = f" via {result.exit_ip}" if result.exit_ip else ""
        log(f"  {result.profile_id:<24} {mark} {result.reason}{ip} "
            f"({result.seconds}s)")
    return written, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="browsers/identities.json")
    ap.add_argument("--once", action="store_true",
                    help="one pass and exit, instead of looping")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between passes. Must stay comfortably under "
                         "PROFILE_MAX_AGE (900s) or the vault starts evicting "
                         "profiles this service is keeping alive.")
    ap.add_argument("--profiles", type=int, default=0,
                    help="cap how many to process (0 = all)")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    from src.vault import SessionVault

    from .bench import load

    config = Config()
    records = load(args.file)
    if args.profiles:
        records = records[:args.profiles]
    if not records:
        print(f"no identities in {args.file} — run "
              f"`python -m browsers.identities export`", file=sys.stderr)
        return 2

    if args.interval >= config.PROFILE_MAX_AGE:
        # A cycle longer than the freshness window means every profile is stale
        # by the time it is refreshed again — the pool would empty and refill
        # forever while looking like it was working.
        print(f"REFUSED: --interval {args.interval}s is not shorter than "
              f"PROFILE_MAX_AGE ({config.PROFILE_MAX_AGE}s). Profiles would go "
              f"stale between passes.", file=sys.stderr)
        return 2

    vault = SessionVault(config)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis — {exc}", file=sys.stderr)
        return 2

    print(f"{len(records)} profile(s) · platform {config.PLATFORM} · "
          f"{'one pass' if args.once else f'every {args.interval}s'}")

    while True:
        started = time.time()
        print(f"\n[{time.strftime('%H:%M:%S')}] pass")
        written, skipped = one_pass(records, vault, config.PLATFORM,
                                    headless=not args.headed)
        elapsed = time.time() - started
        print(f"  {written} written · {skipped} skipped · {elapsed:.0f}s")

        if elapsed > args.interval:
            # Loud, because the consequence is silent: passes start overlapping
            # or falling behind, and profiles age out mid-cycle.
            print(f"  ⚠️  the pass took longer than the interval "
                  f"({elapsed:.0f}s > {args.interval}s) — either fewer "
                  f"profiles per host, or a longer interval, or both")
        if args.once:
            return 0 if written else 1
        time.sleep(max(0, args.interval - elapsed))


if __name__ == "__main__":
    sys.exit(main())
