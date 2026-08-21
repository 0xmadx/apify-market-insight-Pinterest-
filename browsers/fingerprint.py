"""Do N patchright profiles look like N different people, or one person N times?

    python -m browsers.fingerprint                  # 4 profiles, 2 passes each
    python -m browsers.fingerprint --profiles 6
    python -m browsers.fingerprint --plain          # no variation, see the problem

THE QUESTION THIS ANSWERS
-------------------------
patchright removes the signs that a browser is AUTOMATED. It does nothing about
making one browser look different from another. That distinction is the whole
reason AdsPower costs money: it is not selling stealth, it is selling VARIETY.

Twenty accounts on twenty proxies whose browsers report identical canvas
hashes, identical WebGL renderers and identical font metrics are twenty
accounts that can be tied together in one query — the separate exit IPs do not
help at all.

TWO PASS CONDITIONS, AND THE SECOND IS THE ONE PEOPLE FORGET
------------------------------------------------------------
  DISTINCT   different profiles must not share a fingerprint
  STABLE     the SAME profile must produce the same fingerprint every launch

Stability matters as much as distinctness, and randomising per launch is the
obvious wrong fix. A profile whose fingerprint changes every run looks like a
brand new device presenting the same login cookies — which is a stronger signal
of automation than never changing at all. So every knob here is derived from a
hash of the profile id: stable forever, different per profile, and needing no
stored state to reproduce.

WHY THE SIGNALS ARE MEASURED IN-PAGE AND NOT ON A FINGERPRINTING SITE
---------------------------------------------------------------------
A third-party checker adds a network dependency, a rate limit, and its own
opinion about what matters. Reading the same primitives directly is faster,
repeatable offline, and — the real reason — it reports the RAW values, so a
change can be traced to the knob that caused it instead of to a score.
"""
import argparse
import hashlib
import json
import statistics
import sys

from .bench import load

# Everything here runs on about:blank. Canvas, WebGL and the Intl APIs need no
# origin, and avoiding a real page keeps the measurement free of whatever a
# site decides to serve today.
FP_SCRIPT = r"""
() => {
  const out = {};
  const nav = navigator;
  out.userAgent = nav.userAgent;
  out.platform = nav.platform;
  out.languages = (nav.languages || []).join(",");
  out.hardwareConcurrency = nav.hardwareConcurrency ?? null;
  out.deviceMemory = nav.deviceMemory ?? null;
  out.maxTouchPoints = nav.maxTouchPoints ?? null;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.screen = [screen.width, screen.height, screen.colorDepth,
                window.devicePixelRatio].join("x");

  // Canvas: the classic. Text rendering differs with fonts, GPU and driver.
  try {
    const c = document.createElement("canvas");
    c.width = 240; c.height = 60;
    const ctx = c.getContext("2d");
    ctx.textBaseline = "top";
    ctx.font = "16px 'Arial'";
    ctx.fillStyle = "#f60";
    ctx.fillRect(0, 0, 120, 30);
    ctx.fillStyle = "#069";
    ctx.fillText("Pinterest fingerprint ☁ 1234", 2, 15);
    out.canvas = c.toDataURL().slice(-96);
  } catch (e) { out.canvas = "ERR:" + e.name; }

  // WebGL: the vendor/renderer pair is the strongest single hardware signal.
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null;
    out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
    out.webglVersion = gl.getParameter(gl.VERSION);
  } catch (e) { out.webglVendor = "ERR:" + e.name; }

  // Font metrics: which fonts exist, inferred from text width per family.
  try {
    const probes = ["Arial", "Courier New", "Georgia", "Tahoma", "Verdana",
                    "Times New Roman", "Comic Sans MS", "Impact"];
    const span = document.createElement("span");
    span.style.fontSize = "48px";
    span.textContent = "mmmmmmmmmmlli";
    document.body.appendChild(span);
    out.fonts = probes.map(f => {
      span.style.fontFamily = `'${f}', monospace`;
      return Math.round(span.offsetWidth);
    }).join(",");
    span.remove();
  } catch (e) { out.fonts = "ERR:" + e.name; }

  // The automation tells patchright is supposed to have removed. Not part of
  // the identity hash — this is a separate question from "are they distinct".
  out.webdriver = nav.webdriver ?? null;
  out.pluginCount = (nav.plugins || []).length;
  return out;
}
"""

# NOT ALL SIGNALS ARE WORTH THE SAME, and treating them as equal produced a
# false PASS on the first run of this file: three profiles differed ONLY in the
# devicePixelRatio digit of `screen`, and the tool called them distinct.
#
# STRONG signals are derived from the host's hardware and software, so they are
# identical across every profile on one machine unless something deliberately
# changes them. They are what a fingerprinter actually keys on.
STRONG_KEYS = ("canvas", "webglVendor", "webglRenderer", "fonts",
               "hardwareConcurrency", "deviceMemory")
# WEAK signals are cheap to vary and cheap to ignore. Useful texture, but a
# difference here is not evidence of two machines.
WEAK_KEYS = ("userAgent", "platform", "languages", "timezone", "screen")
IDENTITY_KEYS = STRONG_KEYS + WEAK_KEYS

# Plausible desktop shapes. Deliberately ordinary — a 1337x911 viewport is
# unique and therefore worse than a common one shared with millions of people.
VIEWPORTS = [(1920, 1080), (1536, 864), (1440, 900), (1366, 768),
             (1600, 900), (1280, 720), (2560, 1440), (1680, 1050)]
TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver",
             "America/Los_Angeles", "America/Phoenix"]
LOCALES = ["en-US"]          # US accounts. Varying this would contradict them.
CONCURRENCY = [4, 6, 8, 12, 16]


def profile_knobs(profile_id):
    """Deterministic per-profile browser shape, derived from the id.

    A hash, not a random draw and not a stored table: the same profile yields
    the same shape on every machine and every run, forever, with nothing to
    keep in sync. Randomising per launch would be the obvious wrong fix — see
    the module docstring.
    """
    digest = hashlib.sha1(profile_id.encode()).digest()
    width, height = VIEWPORTS[digest[0] % len(VIEWPORTS)]
    return {
        "viewport": {"width": width, "height": height},
        "timezone_id": TIMEZONES[digest[1] % len(TIMEZONES)],
        "locale": LOCALES[digest[2] % len(LOCALES)],
        "device_scale_factor": 1 + (digest[3] % 2),        # 1 or 2
        "hardware_concurrency": CONCURRENCY[digest[4] % len(CONCURRENCY)],
    }


def collect(record, vary=True, headless=True):
    """Launch one profile and read its fingerprint. Returns (signals, error)."""
    from patchright.sync_api import sync_playwright

    from .drivers import _playwright_proxy

    knobs = profile_knobs(record["profile_id"]) if vary else {}
    try:
        with sync_playwright() as p:
            launch = {"headless": headless}
            if record.get("proxy"):
                launch["proxy"] = _playwright_proxy(record["proxy"])
            if knobs.get("hardware_concurrency"):
                # No context option exists for this one; it is a launch flag.
                launch["args"] = [
                    f"--force-device-scale-factor={knobs['device_scale_factor']}"]
            browser = p.chromium.launch(**launch)

            context_args = {"user_agent": record["user_agent"]}
            if vary:
                context_args.update(
                    viewport=knobs["viewport"],
                    locale=knobs["locale"],
                    timezone_id=knobs["timezone_id"],
                    device_scale_factor=knobs["device_scale_factor"])
            context = browser.new_context(**context_args)

            if vary:
                # navigator.hardwareConcurrency has no launch flag or context
                # option, so it is patched before any page script runs.
                context.add_init_script(
                    "Object.defineProperty(navigator, 'hardwareConcurrency',"
                    f" {{get: () => {knobs['hardware_concurrency']}}});")

            page = context.new_page()
            signals = page.evaluate(FP_SCRIPT)
            browser.close()
            return signals, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:110]}"


def collect_drission(record, vary=True, headless=True):
    """Same measurement, through DrissionPage with the identity patch.

    The patch is injected with CDP `Page.addScriptToEvaluateOnNewDocument`,
    which patchright silently ignores and DrissionPage honours — the whole
    reason both drivers are here.
    """
    from DrissionPage import Chromium, ChromiumOptions

    from .fingerprint_patch import build_script
    from .proxy_relay import ProxyRelay

    relay = None
    browser = None
    try:
        options = ChromiumOptions().auto_port()
        options.set_user_agent(record["user_agent"])
        options.set_argument("--no-sandbox")
        if headless:
            options.headless()
        if record.get("proxy"):
            relay = ProxyRelay(record["proxy"]).start()
            options.set_proxy(relay.url)

        browser = Chromium(options)
        page = browser.latest_tab
        if vary:
            page.run_cdp("Page.addScriptToEvaluateOnNewDocument",
                         source=build_script(record["profile_id"]))
        page.get("about:blank")
        return page.run_js("return (" + FP_SCRIPT + ")()"), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:110]}"
    finally:
        try:
            if browser:
                browser.quit()
        except Exception:
            pass
        if relay:
            relay.stop()


COLLECTORS = {"patchright": collect, "drission": collect_drission}


def identity_hash(signals, keys=IDENTITY_KEYS):
    subject = {k: signals.get(k) for k in keys}
    blob = json.dumps(subject, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def strong_hash(signals):
    """The part a fingerprinter would actually key on."""
    return identity_hash(signals, STRONG_KEYS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="browsers/identities.json")
    ap.add_argument("--profiles", type=int, default=4)
    ap.add_argument("--passes", type=int, default=2,
                    help="launches per profile — 2 is the minimum that can "
                         "detect an unstable fingerprint")
    ap.add_argument("--plain", action="store_true",
                    help="apply NO per-profile variation, to see the baseline "
                         "problem rather than the fix")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--driver", choices=sorted(COLLECTORS), default="patchright",
                    help="patchright ignores init scripts, so only 'drission' "
                         "can apply the identity patch")
    args = ap.parse_args()

    records = load(args.file)[:args.profiles]
    if not records:
        print(f"no identities in {args.file} — run "
              f"`python -m browsers.identities export`", file=sys.stderr)
        return 2

    mode = "NO variation (baseline)" if args.plain else "per-profile variation"
    collector = COLLECTORS[args.driver]
    print(f"{len(records)} profiles x {args.passes} passes · {args.driver} · {mode}\n")

    seen = {}
    for record in records:
        hashes = []
        for attempt in range(args.passes):
            signals, error = collector(record, vary=not args.plain,
                                       headless=not args.headed)
            if error:
                print(f"  {record['profile_id']:<24} FAILED — {error}")
                break
            digest = strong_hash(signals)
            hashes.append(digest)
            if attempt == 0:
                print(f"  {record['profile_id']:<24} {digest}  "
                      f"{signals.get('screen')}  "
                      f"{signals.get('timezone')}  "
                      f"cores={signals.get('hardwareConcurrency')}")
                print(f"  {'':<24} webgl={str(signals.get('webglRenderer'))[:52]}")
                print(f"  {'':<24} webdriver={signals.get('webdriver')} "
                      f"plugins={signals.get('pluginCount')}")
        if hashes:
            seen[record["profile_id"]] = hashes

    if not seen:
        print("\nnothing collected", file=sys.stderr)
        return 1

    print(f"\n{'=' * 62}")
    unstable = {p: h for p, h in seen.items()
                if len(h) > 1 and len(set(h)) > 1}
    distinct = {h[0] for h in seen.values()}

    print(f"DISTINCT  {len(distinct)}/{len(seen)} profiles differ on the "
          f"STRONG signals")
    print(f"          ({', '.join(STRONG_KEYS)})")
    if len(distinct) < len(seen):
        collisions = {}
        for profile, hashes in seen.items():
            collisions.setdefault(hashes[0], []).append(profile)
        for digest, profiles in collisions.items():
            if len(profiles) > 1:
                print(f"          ❌ {digest} shared by: {', '.join(profiles)}")
    else:
        print("          ✅ every profile looks like a different machine")

    thin = {p: h for p, h in seen.items() if len(h) < args.passes}
    measured = {p: h for p, h in seen.items() if len(h) >= args.passes}
    print(f"STABLE    {len(measured) - len(unstable)}/{len(measured)} keep the "
          f"same fingerprint across launches")
    if thin:
        # ONE SAMPLE AGREES WITH ITSELF. Counting a profile whose second launch
        # crashed as "stable" is the same class of bug this whole file exists
        # to catch: a check that cannot fail reporting success.
        for profile, hashes in thin.items():
            print(f"          ⚠️  {profile}: only {len(hashes)}/{args.passes} "
                  f"launches succeeded — stability UNMEASURED, not proven")
    if unstable:
        for profile, hashes in unstable.items():
            print(f"          ❌ {profile}: {' -> '.join(hashes)}")
        print("          A fingerprint that changes every launch is WORSE than "
              "one that never does:")
        print("          it is a new device presenting the same login cookies.")
    else:
        print("          ✅ each profile is the same machine every time")

    ok = len(distinct) == len(seen) and not unstable and not thin
    print(f"\nVERDICT   {'✅ PASS' if ok else '❌ FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
