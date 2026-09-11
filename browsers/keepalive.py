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
        # "measured" once AdsPower's real fingerprint has been captured for
        # this profile, "synthetic" while it is still the sha1(profile_id)
        # shape. Recorded so a silent fallback is visible in the journal
        # instead of being indistinguishable from the real thing.
        self.fp_basis = "synthetic"


def refresh(record, headless=True, url=PINTEREST):
    """One profile: launch, verify, harvest. Writes nothing."""
    from DrissionPage import Chromium, ChromiumOptions

    from .fingerprint_patch import build_script
    from .drivers import browser_path, cookie_records, expected_exit
    from .proxy_relay import ProxyRelay

    result = Result(record["profile_id"])
    if record.get("fingerprint"):
        result.fp_basis = "measured"
    started = time.monotonic()
    relay = None
    browser = None
    try:
        options = ChromiumOptions()
        chrome = browser_path()
        if chrome:
            options.set_browser_path(chrome)
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
                     source=build_script(record["profile_id"],
                                         record["user_agent"],
                                         record.get("fingerprint")))

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


def load_from_vault(vault, platform):
    """The profile list, read from the vault instead of a file.

    THE POINT: adding an account should not mean copying a file to a server.
    With `--file`, a new profile reaches the vault (AdsPower syncs it) and then
    sits there unnoticed, because this service was reading a snapshot taken
    before it existed — so every new account required re-exporting
    identities.json and scp-ing it to the VM, by hand, forever.

    The vault already holds the same three fields `identities.json` carries, so
    reading them straight from it makes a new account appear on the next cycle
    with nothing copied.

    Profiles with no cookies or no user agent are skipped rather than attempted:
    a half-written profile is a real state (someone is mid-setup), not an error,
    and launching a browser for it would waste ~8s to discover that.

    THE ROSTER IS NOT THE SERVING POOL. This used to read `valid_profiles`
    alone, which meant a profile the actor evicted vanished from this list --
    so the one service that could have brought it back never looked at it
    again, and a re-login in AdsPower healed nothing until someone ran
    `sync_cookies.py` by hand. `known_profiles` holds the roster; the union is
    what this refreshes. A profile that starts answering again is re-added to
    the serving pool by `write()` on the very next pass, automatically.
    """
    serving = set(vault.r.smembers(f"valid_profiles:{platform}") or [])
    roster = set(vault.r.smembers(f"known_profiles:{platform}") or [])

    records = []
    for profile_id in sorted(serving | roster):
        data = vault.r.hgetall(f"cookie:{platform}:{profile_id}")
        if not data:
            continue
        try:
            cookies = json.loads(data.get("cookies_json") or "{}")
        except (ValueError, TypeError):
            cookies = {}
        if not cookies or not data.get("user_agent"):
            continue
        # AdsPower's real fingerprint, captured at login by
        # `adspower/sync_cookies.py`. Absent for profiles synced before that
        # existed, and absent is fine -- build_script falls back to the
        # synthetic shape rather than refusing.
        try:
            measured = json.loads(data.get("fingerprint_json") or "null")
        except (ValueError, TypeError):
            measured = None
        records.append({"profile_id": profile_id,
                        "cookies": cookies,
                        "user_agent": data.get("user_agent"),
                        "proxy": data.get("proxy"),
                        # Is this profile ALREADY in the serving pool? When it
                        # is, re-adding after every pass buys nothing and costs
                        # one command per profile per pass -- 1,728/day at 6
                        # profiles on a 5-minute timer, against a 10,000/day
                        # free tier. When it is NOT (an evicted profile that
                        # just refreshed successfully), the SADD in `write()` is
                        # exactly what returns it to service. Computed per
                        # profile rather than hardcoded True, which is what it
                        # was when this list could only contain serving
                        # profiles. `--file` records carry no guarantee and
                        # still SADD.
                        "in_set": profile_id in serving,
                        # Carried so a pass can decide NOT to launch a browser
                        # -- see `_backoff_remaining`. Without these the only
                        # way to learn a profile is signed out is to spend ~8s
                        # launching Chromium to be told again.
                        "last_error": (data.get("last_error") or "").strip(),
                        "last_error_at": data.get("last_error_at"),
                        "fingerprint": measured if isinstance(measured, dict)
                                       else None})
    return records


def write(vault, platform, record, cookies):
    """Store the identity. Mirrors browsers/identities.py:restore()."""
    profile_id = record["profile_id"]
    fields = {
        "cookies_json": json.dumps(cookies),
        "user_agent": record["user_agent"],
        # RULE 3: only reached on a verified pass.
        "last_updated": str(time.time()),
        "is_valid": "1",
        # Cleared here, inside the HSET we are already sending, so a fixed
        # profile stops reporting yesterday's failure at no extra cost.
        "last_error": "",
        # Same free ride, and it is what closes the recovery loop. The ACTOR
        # counts rejections into `auth_failures` and retires a profile at the
        # threshold (src/vault.py:report_rejection). A profile that answers a
        # real browser again has proven the session is alive, so the count goes
        # back to zero HERE -- riding along in an HSET already being sent,
        # rather than costing the actor one Redis command per good request just
        # to record the ordinary case.
        "auth_failures": "0",
    }
    if record.get("proxy"):
        fields["proxy"] = record["proxy"]
    vault.r.hset(f"cookie:{platform}:{profile_id}", mapping=fields)
    # Only when membership is not already established -- see `in_set` above.
    # The heartbeat is the HSET; the SADD only ever mattered for a profile the
    # set did not already contain.
    if not record.get("in_set"):
        vault.r.sadd(f"valid_profiles:{platform}", profile_id)


def note_failure(vault, platform, profile_id, reason):
    """Put the reason a profile failed WHERE THE OPERATOR CAN SEE IT.

    `refresh()` already works out exactly what went wrong -- "signed OUT --
    missing _auth" is a different job from "exit IP is not the proxy": one needs
    a fresh login, the other needs a proxy fixed. Until this existed that
    sentence went to this host's journal and no further, so from the operator's
    laptop a profile simply went quiet and `src.status` could say "stale" and
    nothing more. Finding out why meant SSH-ing into the VM.

    One extra command, and only on a failure -- successes clear the field inside
    the HSET they already send. Failures are meant to be rare; if this is
    costing you commands, that is the alarm working.
    """
    try:
        vault.r.hset(f"cookie:{platform}:{profile_id}",
                     mapping={"last_error": reason[:200],
                              "last_error_at": str(time.time())})
    except Exception:
        # Never let bookkeeping break the pass that is still refreshing the
        # other profiles.
        pass


# How long to leave a profile alone once only a human can fix it.
#
# A signed-out account does not become signed in because we relaunched a
# browser. At the 5-minute cadence a single dead profile costs ~8s of Chromium
# every pass -- 288 launches a day to re-learn one unchanged fact, on a VM that
# also has live profiles to refresh. An hour still finds the fix promptly: the
# operator logs in, and the profile is back in service within one cycle of that.
HUMAN_RETRY_SECONDS = 3600


def _backoff_remaining(record, now=None):
    """Seconds to wait before retrying a profile only a person can fix.

    0 means try it now. Deliberately keyed on the SHAPE of the failure, the
    same distinction `refresh()` already draws and `src.status` already prints:

        "signed OUT — missing _auth"        a human logs in      -> back off
        "exit IP ... is not the proxy"      a machine fixes it   -> retry now

    Backing off the second kind would leave a fixable profile cold for an hour.
    """
    if "signed OUT" not in (record.get("last_error") or ""):
        return 0
    try:
        failed_at = float(record.get("last_error_at") or 0)
    except (TypeError, ValueError):
        return 0
    if not failed_at:
        return 0
    elapsed = (now if now is not None else time.time()) - failed_at
    return max(0, HUMAN_RETRY_SECONDS - elapsed)


def one_pass(records, vault, platform, headless=True, log=print):
    """Every profile, in sequence. Returns (written, skipped)."""
    written = skipped = 0
    for record in records:
        waiting = _backoff_remaining(record)
        if waiting:
            # Not an error and not silence: the profile is known-dead, the
            # reason is already recorded, and a human is the fix.
            log(f"  {record['profile_id']:<24} WAIT needs a human login "
                f"— next attempt in {int(waiting // 60)}m")
            skipped += 1
            continue
        result = refresh(record, headless=headless)
        if result.ok:
            write(vault, platform, record, result.cookies)
            written += 1
            mark = "OK  "
        else:
            note_failure(vault, platform, result.profile_id, result.reason)
            skipped += 1
            mark = "SKIP"
        ip = f" via {result.exit_ip}" if result.exit_ip else ""
        log(f"  {result.profile_id:<24} {mark} {result.reason}{ip} "
            f"({result.seconds}s)")
    return written, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=None,
                    help="read the profile list from this exported file "
                         "instead of the vault. The vault is the default "
                         "because a file is a SNAPSHOT: a newly added account "
                         "reaches the vault and then sits unnoticed until "
                         "someone re-exports and copies the file to the host. "
                         "Use this only to run against identities the vault "
                         "does not have — a restore, or a test.")
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
    vault = SessionVault(config)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis — {exc}", file=sys.stderr)
        return 2

    if args.file:
        records = load(args.file)
        source = args.file
    else:
        records = load_from_vault(vault, config.PLATFORM)
        source = f"the vault ({config.PLATFORM})"
    if args.profiles:
        records = records[:args.profiles]
    if not records:
        print(f"no usable identities in {source}.\n"
              f"A profile needs cookies AND a user agent to be refreshed; one "
              f"with neither has not been signed into yet.", file=sys.stderr)
        return 2

    if args.interval >= config.PROFILE_MAX_AGE:
        # A cycle longer than the freshness window means every profile is stale
        # by the time it is refreshed again — the pool would empty and refill
        # forever while looking like it was working.
        print(f"REFUSED: --interval {args.interval}s is not shorter than "
              f"PROFILE_MAX_AGE ({config.PROFILE_MAX_AGE}s). Profiles would go "
              f"stale between passes.", file=sys.stderr)
        return 2

    print(f"{len(records)} profile(s) from {source} · "
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
