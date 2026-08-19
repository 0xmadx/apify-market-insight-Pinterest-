"""Configuration for the Pinterest actor. Self-contained on purpose.

This project shares exactly one thing with the Etsy repo it was lifted out of:
the Redis vault, because the Chrome extension writes there. It shares no code, no
imports and — the point of this file — **no configuration**.

That is why the .env below is loaded from this directory by explicit path and
never by upward search. A `find_dotenv()` walking up would have picked up the
Etsy repo's .env, and this project would have silently inherited its Redis URL,
its user agent and its fingerprint settings. Two projects reading one .env is how
a change to one breaks the other with no edit in sight.
"""
import os
from dataclasses import dataclass
from pathlib import Path

# apify_pinterest/.env — this directory, never a parent. On Apify these are real
# environment variables and the file is absent, which is expected.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv

    if _ENV_FILE.is_file():
        load_dotenv(_ENV_FILE)
except ImportError:
    pass


@dataclass(frozen=True)
class Config:
    # The vault. Local dev: the Docker Redis the Go cookie server writes to.
    # Apify cloud: must be a NETWORK-REACHABLE Redis. A localhost URL there
    # resolves to the actor's own empty container and finds nothing.
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Which pool to draw identities from. The Chrome extension already posts
    # under this exact name, so nothing on the writing side needs to change.
    PLATFORM: str = os.environ.get("VAULT_PLATFORM", "pinterest")

    # A profile whose last_updated is older than this is dead, not worth trying.
    # The extension heartbeats every few minutes; 15 min reads as "Chrome has
    # been closed", not "one beacon was late".
    PROFILE_MAX_AGE: int = int(os.environ.get("PROFILE_MAX_AGE", "900"))

    # How long a leased identity is held before returning to the pool unaided.
    # Sized above the longest single run so a crash cannot strand the only
    # profile the operator has.
    LEASE_TTL: int = int(os.environ.get("LEASE_TTL", "900"))

    # Bounded, never infinite: an empty vault must fail, not hang.
    WAIT_TIMEOUT: int = int(os.environ.get("VAULT_WAIT_TIMEOUT", "60"))
    WAIT_INTERVAL: int = 5

    # curl_cffi TLS fingerprint. This has to stay plausible against the UA the
    # vault hands back — a Chrome 124 user agent over a Chrome 99 TLS handshake
    # is a mismatch that costs nothing to avoid.
    IMPERSONATE: str = os.environ.get("IMPERSONATE", "chrome124")

    COOKIE_DOMAIN: str = ".pinterest.com"
    REQUEST_TIMEOUT: int = int(os.environ.get("REQUEST_TIMEOUT", "30"))

    # Only used when a vault profile carries no user_agent of its own. The real
    # UA always comes from the browser the cookies were born in.
    USER_AGENT_FALLBACK: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # No proxy configuration exists here, deliberately. Phase 1 is one local
    # Chrome on a home IP. When AdsPower and Webshare arrive in phase 2, the
    # proxy is written into the *profile hash* alongside that profile's cookies
    # — per profile and per account, never a global. `Identity.proxy` already
    # reads it from there, so phase 2 changes the writer and not this file.
