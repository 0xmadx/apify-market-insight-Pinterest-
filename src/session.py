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


def browser_major(text):
    """The Chrome/Firefox major version a UA or an impersonate target claims.

    None when there is no version to read — unknown is not zero, and a caller
    must not treat "cannot tell" as "matches".
    """
    if not text:
        return None
    match = re.search(r"(?:Chrome|Firefox)/(\d+)", text) or re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


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

    kwargs = {
        "impersonate": config.IMPERSONATE,
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
