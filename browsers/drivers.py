"""Two candidate replacements for AdsPower, behind one interface.

    python -m browsers.bench                 # run both, compare
    python -m browsers.bench --only drission

WHAT A DRIVER HAS TO DO — and it is much less than "scrape with a browser"
-------------------------------------------------------------------------
The scraper is curl_cffi and always will be. A browser here does four things:

    1. launch wearing this profile's proxy and this profile's user agent
    2. load this profile's cookies
    3. keep the Pinterest session alive (one page load)
    4. hand the cookies back, so the vault gets the refreshed jar

That is the whole contract. It means the stealth bar is a login and an
occasional page load — not 188 automated requests — which is why this is
plausible at all.

THE TEST THAT MATTERS
---------------------
"Did the page load?" is worthless. Pinterest answers a signed-out browser with
a perfectly normal-looking page, and answers a signed-out API call with
plausible PUBLIC data. Both look like success.

So a driver passes only when the cookies it hands back still AUTHENTICATE — the
same check the vault applies, plus a live authenticated API call through
curl_cffi. That is the thing the product actually needs; anything less is a
browser that starts.
"""
import pathlib
import time

PINTEREST = "https://www.pinterest.com/"
# Answers with the caller's exit IP in plain text. The ONLY way to prove a
# browser is really behind its proxy: every library here will happily report a
# successful launch while Chromium has quietly fallen back to a direct
# connection, and that fallback is invisible from inside the page.
IP_ECHO = "https://api.ipify.org"
# Present only on a signed-in session. The same pair the vault requires, so a
# driver cannot pass a jar the vault would then refuse.
AUTH_COOKIES = ("_auth", "_pinterest_sess")


def browser_path():
    """Where Chromium lives. None means "let the library find it".

    Windows has Chrome installed where DrissionPage already looks. A Linux VM
    usually has nothing, and `apt install chromium` needs root — which a deploy
    script should not assume it has.

    So the fallback is the Chromium **patchright already downloaded**: one
    `patchright install chromium` puts a known-good build under
    ~/.cache/ms-playwright with no root at all, and it is the same build these
    drivers were tested against on Windows. Reusing it means the browser is not
    a second thing to provision.

    CHROME_PATH overrides everything, for a host that has its own.
    """
    import os

    override = (os.environ.get("CHROME_PATH") or "").strip()
    if override:
        return override

    cache = pathlib.Path.home() / ".cache" / "ms-playwright"
    if not cache.is_dir():
        return None

    # SEARCH, do not hardcode. The first version of this listed
    # "chrome-linux/chrome" — the OLD Playwright layout. The current one is
    # "chrome-linux64/chrome", so it found nothing on Linux and DrissionPage
    # answered with a Chinese "cannot find browser executable" that says
    # nothing about which path was tried. A glob survives the next rename.
    def build_number(folder):
        # Sort by the numeric build, not lexically: "chromium-1200" sorts
        # after "chromium-1234" as a string, so the older build would win.
        tail = folder.name.rsplit("-", 1)[-1]
        return int(tail) if tail.isdigit() else -1

    # `chromium_headless_shell-*` is a cut-down binary with no full browser
    # surface. Skipped: it can start, and then behaves differently from the
    # thing that was tested, which is the worst kind of substitute.
    folders = [f for f in cache.glob("chromium-*") if f.is_dir()]
    for folder in sorted(folders, key=build_number, reverse=True):
        for name in ("chrome", "chrome.exe", "Chromium"):
            for found in folder.rglob(name):
                if found.is_file():
                    return str(found)
    return None


def expected_exit(proxy_url):
    """The host a proxy URL should make us appear from."""
    import urllib.parse

    return urllib.parse.urlparse(proxy_url).hostname or ""


def cookie_records(cookies, domain=".pinterest.com"):
    """`{name: value}` -> the cookie dicts CDP will actually accept.

    ⚠️ COOKIE NAME PREFIXES ARE ENFORCED BY THE PROTOCOL, not by the site.
    Pinterest's jar contains `__Secure-s_a`, and CDP rejects the WHOLE
    `setCookies` call with a flat "Invalid cookie fields" if a `__Secure-`
    cookie arrives without `secure: true`. Measured 2026-08-20: patchright
    failed on every profile until this was added.

    That failure mode is the dangerous kind. The call is all-or-nothing, so one
    mis-shaped cookie drops the entire jar — and had it been lenient instead,
    the browser would have loaded with the session cookie missing and looked
    like an ordinary signed-out visit.

    Rules, from the cookie-prefix spec:
      __Secure-   must be secure
      __Host-     must be secure, path "/", and carry NO domain

    Everything here is https-only anyway, so `secure` is set on all of them
    rather than guessed per cookie.
    """
    records = []
    for name, value in (cookies or {}).items():
        if value is None:
            continue                       # absent is not empty-string
        record = {"name": str(name), "value": str(value),
                  "path": "/", "secure": True}
        if not str(name).startswith("__Host-"):
            record["domain"] = domain
        records.append(record)
    return records


class DriverResult:
    """What one driver did with one identity. Every field is measured."""

    def __init__(self, name, profile_id):
        self.name = name
        self.profile_id = profile_id
        self.launched = False
        self.loaded = False
        self.cookies_out = {}
        self.authenticated = None      # None = never got far enough to ask
        self.detected = None           # None = unknown, True = challenged
        self.error = None
        self.seconds = None
        self.ua_seen = None
        self.exit_ip = None            # what the BROWSER actually exits from
        self.exit_ok = None            # does it match the proxy it was given?

    @property
    def ok(self):
        return bool(self.authenticated)

    def __repr__(self):
        state = ("AUTHENTICATED" if self.authenticated else
                 "signed out" if self.authenticated is False else
                 f"failed: {self.error}")
        return f"<{self.name} {self.profile_id} {state}>"


def verify_authenticated(cookies, user_agent, proxy):
    """Do these cookies still buy authenticated data? The only question worth
    asking, and it is asked through the real transport, not the browser.

    A page that renders is not a session. Pinterest serves a signed-out browser
    a normal page and a signed-out API caller plausible public data, so the only
    honest check is an endpoint that answers differently when authenticated.
    """
    from src.config import Config
    from src.session import build_session
    from src.transport import TrendsClient
    from src.vault import Identity

    missing = [c for c in AUTH_COOKIES if c not in cookies]
    if missing:
        return False, f"jar is signed out — missing {', '.join(missing)}"

    identity = Identity(profile_id="probe", cookies=cookies,
                        user_agent=user_agent, proxy=proxy, age=0)
    session = build_session(identity, Config())
    try:
        client = TrendsClient(session, cache=None, delay=0)
        client.bootstrap()
        moments = client.style_a("/ads/v4/trends/moment/available/US")
        return bool(moments), f"{len(moments)} moments via {identity.impersonate}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"
    finally:
        session.close()


# ------------------------------------------------------------- DrissionPage

def run_drission(record, headless=True, url=PINTEREST):
    """DrissionPage: CDP-direct, no WebDriver, no Playwright layer.

    The architectural argument for it is the absence of a control plane —
    nothing sits between Python and the browser process emitting the
    `Runtime.enable` sequence that detectors watch for.
    """
    result = DriverResult("DrissionPage", record["profile_id"])
    started = time.monotonic()
    page = None
    relay = None
    try:
        from DrissionPage import Chromium, ChromiumOptions

        from .proxy_relay import ProxyRelay

        options = ChromiumOptions().auto_port()
        options.set_user_agent(record["user_agent"])
        if record.get("proxy"):
            # Chromium's --proxy-server cannot carry credentials, and
            # DrissionPage only WARNS before passing them through anyway — so
            # the browser silently fell back to the host IP. The relay strips
            # that problem out: an unauthenticated proxy on localhost that adds
            # the password upstream. See browsers/proxy_relay.py.
            relay = ProxyRelay(record["proxy"]).start()
            options.set_proxy(relay.url)
        if headless:
            options.headless()
        # A fresh, isolated profile dir per run: reusing one would let a failed
        # run leave state that makes the next one look better than it is.
        options.set_argument("--no-sandbox")

        browser = Chromium(options)
        page = browser.latest_tab
        result.launched = True

        # Cookies must be set for the domain BEFORE the page that needs them
        # loads, or the first request goes out anonymous and Pinterest may
        # decide the session is new.
        for cookie in cookie_records(record["cookies"]):
            page.set.cookies(cookie)

        if record.get("proxy"):
            page.get(IP_ECHO)
            # innerText, not .html — the raw document is wrapped in Chromium's
            # plain-text viewer markup, so reading .html compares an IP against
            # "<html><head><meta name=..." and reports a mismatch that is not
            # one. A check that cries wolf gets switched off.
            result.exit_ip = (page.run_js(
                "return document.body.innerText") or "").strip()[:45]
            result.exit_ok = expected_exit(record["proxy"]) in result.exit_ip

        page.get(url)
        result.loaded = True
        result.ua_seen = page.run_js("return navigator.userAgent")
        html = (page.html or "")[:4000].lower()
        result.detected = ("captcha" in html or "unusual traffic" in html
                           or "px-captcha" in html)

        result.cookies_out = {c["name"]: c["value"] for c in page.cookies()}
        browser.quit()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {str(exc)[:120]}"
        try:
            if page is not None:
                page.browser.quit()
        except Exception:
            pass
    finally:
        if relay is not None:
            relay.stop()
    result.seconds = round(time.monotonic() - started, 1)
    return result


# ----------------------------------------------------------------- patchright

def run_patchright(record, headless=True, url=PINTEREST):
    """patchright: Playwright with the automation tells removed.

    Drop-in for the Playwright API, which is its real advantage — but note it
    removes AUTOMATION signals, it does not randomise FINGERPRINTS. Twenty
    patchright profiles share one canvas/WebGL/font signature unless something
    else varies them.
    """
    result = DriverResult("patchright", record["profile_id"])
    started = time.monotonic()
    try:
        from patchright.sync_api import sync_playwright

        with sync_playwright() as p:
            launch = {"headless": headless}
            if record.get("proxy"):
                launch["proxy"] = _playwright_proxy(record["proxy"])
            browser = p.chromium.launch(**launch)
            result.launched = True

            context = browser.new_context(user_agent=record["user_agent"])
            context.add_cookies(cookie_records(record["cookies"]))

            page = context.new_page()
            if record.get("proxy"):
                page.goto(IP_ECHO, wait_until="domcontentloaded", timeout=45000)
                result.exit_ip = page.evaluate(
                    "() => document.body.innerText").strip()[:45]
                result.exit_ok = expected_exit(
                    record["proxy"]) in result.exit_ip

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            result.loaded = True
            result.ua_seen = page.evaluate("() => navigator.userAgent")
            html = (page.content() or "")[:4000].lower()
            result.detected = ("captcha" in html or "unusual traffic" in html
                               or "px-captcha" in html)

            result.cookies_out = {c["name"]: c["value"]
                                  for c in context.cookies()}
            browser.close()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {str(exc)[:120]}"
    result.seconds = round(time.monotonic() - started, 1)
    return result


def _playwright_proxy(url):
    """`http://user:pass@host:port` -> Playwright's split form.

    Playwright wants credentials as separate fields; passing them inline in
    `server` is accepted and then silently ignored, which produces a run that
    exits from the WRONG IP while looking entirely successful.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = urllib.parse.unquote(parsed.username)
        proxy["password"] = urllib.parse.unquote(parsed.password or "")
    return proxy


DRIVERS = {"drission": run_drission, "patchright": run_patchright}
