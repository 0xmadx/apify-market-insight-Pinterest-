"""HTTP transport built from a leased vault identity.

curl_cffi, impersonating Chrome. The point is the TLS/JA3 handshake: a plain
Python HTTP client announces itself as one at the transport layer no matter how
correct the headers above it are, and the cookies we are spending here are the
operator's real logged-in session — too expensive to burn on a fingerprint
mismatch we can avoid for free.

No browser, headless or otherwise, ever runs here.
"""
import contextlib
import re

from curl_cffi import requests

from .config import Config
from .vault import Identity, SessionVault


class Blocked(RuntimeError):
    """Pinterest refused this session. Distinct from a transport failure."""


def classify(response) -> str:
    """What kind of 'no' is this?

    Conflating these is how a dead cookie gets reported as a rate limit and the
    operator waits an hour instead of re-logging in.

      ok            usable response
      malformed     our request was wrong — the session is fine
      auth_expired  the session is dead — the fix is in Chrome
      rate_limited  the session is fine, we asked too fast
      blocked       bot detection fired on this identity

    `malformed` earns its own case from a live probe: Pinterest answers a
    /resource/ call that is missing the mandatory `x-pinterest-pws-handler`
    header with **403 Invalid Resource Request**. Folding that into auth_expired
    sends the operator to re-login in Chrome over a header bug, and — worse —
    invites the caller to evict a perfectly healthy profile. Never mark a
    `malformed` response as blocked.
    """
    code = response.status_code
    if code == 429:
        return "rate_limited"
    if code in (401, 403):
        body = (response.text or "")[:2000].lower()
        if "invalid resource request" in body:
            return "malformed"
        if "captcha" in body or "unusual traffic" in body or "px-captcha" in body:
            return "blocked"
        return "auth_expired"
    if code >= 500:
        return "ok"  # server-side; retrying on the same identity is correct
    return "ok"


# Ordered, and the order is load-bearing: Edge's UA contains "Chrome/", and
# every Chromium UA contains "Safari/". First match wins, most specific first.
_UA_VERSION = (
    ("edge", re.compile(r"Edg(?:e|A|iOS)?/(\d+)")),
    ("firefox", re.compile(r"Firefox/(\d+)")),
    ("chrome", re.compile(r"(?:Chrome|Chromium)/(\d+)")),
    ("safari", re.compile(r"Version/(\d+)")),
)


def browser_major(text):
    """The major version a UA string or an impersonate target name claims.

    None when there is no version to read — unknown is not zero, and a caller
    must not treat "cannot tell" as "matches".

    A UA and a target name are parsed DIFFERENTLY on purpose. An earlier
    version fell back to "the first number in the string" for both, and on
    `Mozilla/5.0 ... Version/18.0 Safari/605.1.15` that returned **5** — the
    Mozilla compatibility token, which every UA on earth has carried since
    1994. It then matched Safari 18 to a Safari 15 fingerprint.
    """
    if not text:
        return None
    if "Mozilla" in text or "/" in text:
        for _family, pattern in _UA_VERSION:
            match = pattern.search(text)
            if match:
                return int(match.group(1))
        return None
    # A target name: "chrome146", "firefox133", "safari18_4", "chrome131_android".
    match = re.match(r"[a-z_]+?(\d+)", text)
    return int(match.group(1)) if match else None


def browser_family(text):
    """Which browser a UA or a target belongs to: chrome | firefox | safari | edge.

    ORDER MATTERS and is not alphabetical. Every Chromium UA contains the word
    "Safari" (the AppleWebKit legacy token) and Edge's contains "Chrome", so
    testing for Safari first would call Chrome Safari, and testing for Chrome
    first would call Edge Chrome. Most specific wins.
    """
    if not text:
        return None
    low = text.lower()
    if "edg/" in low or low.startswith("edge"):
        return "edge"
    if "firefox" in low or "gecko/" in low:
        return "firefox"
    if "chrome" in low or "chromium" in low:
        return "chrome"
    if "safari" in low:
        return "safari"
    return None


def impersonate_targets():
    """Every fingerprint this build of curl_cffi can actually produce.

    Read from the installed library rather than hardcoded, because the list
    grows with every release and a stale copy would silently exclude the newest
    — the exact target a freshly-updated browser needs most.
    """
    import typing

    from curl_cffi.requests.impersonate import BrowserTypeLiteral
    return sorted(typing.get_args(BrowserTypeLiteral))


def impersonate_for(user_agent, fallback="chrome", targets=None):
    """Pick the TLS fingerprint that matches THIS identity's browser.

    Returns `(target, basis)`, where basis is how the choice was reached:

        exact     a target for this browser and this major version
        nearest   same browser family, closest version below (or above)
        fallback  the UA could not be read, or its family has no targets

    WHY PER IDENTITY AND NOT A CONFIG CONSTANT. Every profile in the vault is a
    different browser — AdsPower spoofs a distinct UA per profile and they
    auto-update on their own schedules. One global `IMPERSONATE` cannot be
    right for all of them, and it silently rots: it was 26 versions adrift when
    this was measured on 2026-08-20, because nothing recomputes a constant.
    Deriving it from the UA the vault hands back means the handshake tracks the
    browser automatically, including browsers added years from now.

    NEVER CROSSES FAMILIES. A Firefox jar replayed over a Chrome handshake is a
    worse signal than a stale version number, so an unmatched family falls back
    rather than picking "the closest number" from the wrong browser.
    """
    targets = targets if targets is not None else impersonate_targets()
    family = browser_family(user_agent)
    major = browser_major(user_agent)
    if not family or major is None:
        return fallback, "fallback"

    # Safari's target names do not encode a comparable number. The set holds
    # `safari155`, `safari15_5`, `safari180`, `safari184`, `safari260` — that is
    # 15.5 written two ways, 18.0, 18.4 and 26.0, so "180 > 155" is arithmetic
    # about strings, not about versions. Rather than guess a mapping and be
    # confidently wrong, Safari falls back. Nothing here browses as Safari
    # (every vault profile is Chromium), so this costs nothing today and stays
    # honest if that ever changes.
    if family == "safari":
        return fallback, "fallback"

    # `chrome131_android` and `safari180_ios` are different DEVICES, not just
    # versions. Matching a desktop UA to a mobile fingerprint would contradict
    # the platform the UA already declared.
    mobile_ua = any(token in (user_agent or "") for token in
                    ("Android", "iPhone", "iPad", "Mobile"))
    candidates = []
    for name in targets:
        if browser_family(name) != family:
            continue
        if ("android" in name or "_ios" in name) != mobile_ua:
            continue
        version = browser_major(name)
        if version is not None:
            candidates.append((version, name))
    if not candidates:
        return fallback, "fallback"

    for version, name in sorted(candidates, reverse=True):
        if version == major:
            return name, "exact"
    # Prefer the newest target at or below the real browser: claiming to be
    # OLDER than you are is ordinary (browsers lag), claiming to be NEWER is a
    # version that may not exist yet.
    below = [(v, n) for v, n in candidates if v <= major]
    if below:
        return max(below)[1], "nearest"
    return min(candidates)[1], "nearest"


def fingerprint_drift(user_agent, impersonate):
    """How many browser versions apart the UA header and the TLS handshake are.

    Three claims about one browser go out on every request: the cookies, the
    User-Agent, and the TLS/JA3 handshake. A fingerprinter reads all three, and
    they are cheap to keep consistent — so an inconsistency is pure downside.

    Measured 2026-08-20: the vault's profiles announced Chrome 150 while
    `IMPERSONATE` was still the old default `chrome124`. Twenty-six versions,
    on every request, for the life of the project. A small gap is ordinary — a
    real browser lags its own release train — which is exactly why this returns
    the DISTANCE and lets the caller decide, instead of a boolean.

    None means one side carried no version and the comparison is unknowable.
    """
    ua_major = browser_major(user_agent)
    imp_major = browser_major(impersonate)
    if ua_major is None or imp_major is None:
        return None
    return abs(ua_major - imp_major)


# More than this many versions apart is worth telling the operator about. Two
# is normal drift; ten is a browser update nobody followed up on.
DRIFT_WARN_AT = 6


def build_session(identity: Identity, config: Config = None):
    """A curl_cffi session wearing exactly one identity: its cookies, its UA,
    and its exit IP.

    All three come from the same profile hash, so a cookie jar is never sent
    over a UA or an address it was not born behind. `identity.proxy` is None
    only when REQUIRE_PROXY is off, and the session then exits from the host.
    """
    config = config or Config()

    # DERIVED PER IDENTITY, not read from a constant. Every profile in the pool
    # is a different browser on its own update schedule, so one global value
    # cannot be right for all of them and rots the moment any of them updates.
    # `config.IMPERSONATE` is now only the FALLBACK, for a UA we cannot read.
    target, basis = impersonate_for(identity.user_agent, config.IMPERSONATE)
    identity.impersonate = target
    identity.impersonate_basis = basis

    kwargs = {
        "impersonate": target,
        "timeout": config.REQUEST_TIMEOUT,
    }
    if identity.proxy:
        # The proxy comes from the same hash as the cookies — written there by
        # `adspower/sync_cookies.py`, mirroring what the AdsPower browser itself
        # uses — so the scraper and the browser that made the session share one
        # exit IP.
        kwargs["proxies"] = {"http": identity.proxy, "https": identity.proxy}

    session = requests.Session(**kwargs)

    for name, value in identity.cookies.items():
        session.cookies.set(name, value, domain=config.COOKIE_DOMAIN)

    # impersonate already sets a coherent Chrome header set including Sec-Ch-Ua;
    # only what is specific to this identity or to Pinterest is overridden.
    session.headers.update({
        "User-Agent": identity.user_agent,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.pinterest.com/",
        "X-Requested-With": "XMLHttpRequest",
        "X-Pinterest-AppState": "active",
    })

    # Pinterest's CSRF scheme is cookie-echo: the csrftoken cookie goes back as a
    # header. Absent is not empty — with no cookie, send no header and let the
    # endpoint layer see the real failure rather than a forged one.
    csrf = identity.cookies.get("csrftoken")
    if csrf:
        session.headers["X-CSRFToken"] = csrf

    return session


@contextlib.contextmanager
def leased_session(config: Config = None, platform: str = None):
    """Lease an identity, yield (session, identity), always hand the lease back.

        with leased_session() as (session, identity):
            r = session.get(url)

    The lease returning on exit is the point: without it a crashed run strands
    the operator's only profile until the TTL expires.
    """
    config = config or Config()
    platform = platform or config.PLATFORM
    vault = SessionVault(config)

    identity = vault.acquire(platform)
    print(f"[session] leased {identity!r}")
    session = build_session(identity, config)
    try:
        yield session, identity
    finally:
        session.close()
        vault.release(platform, identity.profile_id)
        print(f"[session] released {identity.profile_id}")
