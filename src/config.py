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
    # How often a waiting run re-checks for a free profile. Was 5s, which
    # made sense with one profile and long runs and wastes half the pool with
    # several profiles and short ones: a freed profile sat idle for up to 5
    # seconds before anyone noticed. Measured, 100 clients on 4 profiles:
    #
    #     WAIT_INTERVAL=5s -> 39/100 served
    #     WAIT_INTERVAL=1s -> 60/100 served
    #
    # +50% throughput from one number. Not lower than 1s: below that the poll
    # itself becomes Redis load, and the win is already banked.
    WAIT_INTERVAL: int = int(os.environ.get("VAULT_WAIT_INTERVAL", "1"))

    # curl_cffi TLS fingerprint. This has to stay plausible against the UA the
    # vault hands back — a Chrome 124 user agent over a Chrome 99 TLS handshake
    # is a mismatch that costs nothing to avoid.
    IMPERSONATE: str = os.environ.get("IMPERSONATE", "chrome124")

    COOKIE_DOMAIN: str = ".pinterest.com"
    REQUEST_TIMEOUT: int = int(os.environ.get("REQUEST_TIMEOUT", "30"))

    # Cookies that must be present for a profile to be logged in at all.
    # Verified against the live vault 2026-08-18: both profiles carry `_auth` and
    # `_pinterest_sess`. A jar without them is a signed-out browser — it has
    # cookies, so the "has cookies" check passes, and every request then goes out
    # anonymous. That failure reads as a site change, not as a dead session.
    REQUIRED_COOKIES: str = os.environ.get(
        "REQUIRED_COOKIES", "_auth,_pinterest_sess")

    # A profile with no proxy exits from whatever host the run happens to be on.
    # That is coherent on a laptop where the browser and the scraper share one
    # IP, and it is a MIXED POOL the moment any other profile has a proxy: the
    # same customer's requests then come from a datacentre IP one run and a
    # residential one the next, and one Pinterest account's cookies get replayed
    # from an address they were never born behind.
    #
    # Measured 2026-08-20: 6 profiles in the live pool, 5 AdsPower ones on their
    # own Webshare exit IPs and one Chrome-extension profile with none. Roughly
    # one run in six went out from the operator's home IP, and nothing said so.
    #
    # Default ON. It costs pool size — that pool drops from 6 usable to 5 — and
    # that is the cheaper mistake: a smaller pool delays a run, an unproxied
    # identity risks the account. Set REQUIRE_PROXY=0 for local development on a
    # machine that has no proxies at all, where every profile is unproxied and
    # the pool is therefore not mixed.
    REQUIRE_PROXY: bool = os.environ.get("REQUIRE_PROXY", "1") not in (
        "0", "false", "False", "no", "")

    # ---- not pulling old data -------------------------------------------

    # How long before an already-collected record is worth re-reading. Pinterest
    # metrics move, so identity-only dedup would freeze a live number into a
    # one-time snapshot. 7 days reads as "the counts have probably shifted".
    SEEN_TTL: int = int(os.environ.get("SEEN_TTL", str(7 * 86400)))

    # Singleflight. Short on purpose: the winner dying must not block a key for
    # long, and a duplicate fetch is cheaper than a key nobody may fill.
    FILL_LOCK_TTL: int = int(os.environ.get("FILL_LOCK_TTL", "45"))
    # A waiter that times out fetches it itself rather than failing the
    # customer — see wait_for_fill.
    FILL_WAIT_TIMEOUT: float = float(os.environ.get("FILL_WAIT_TIMEOUT", "30"))
    FILL_POLL_INTERVAL: float = float(os.environ.get("FILL_POLL_INTERVAL", "0.25"))
    # Coarse bound so an abandoned scope's seen-set cannot grow forever.
    SEEN_KEY_TTL: int = int(os.environ.get("SEEN_KEY_TTL", str(90 * 86400)))

    CACHE_ENABLED: bool = os.environ.get("CACHE_ENABLED", "true").lower() in (
        "1", "true", "yes", "t")
    # Per-endpoint-kind TTLs, "kind=seconds" comma separated. A single global TTL
    # is either wastefully short for a weekly trend series or dangerously long
    # for a search ranking.
    # `bootstrap` is /latest_available_date/ — the one call EVERY run makes.
    # It was uncached, so even a fully-cached run had to lease an identity just
    # to ask "what is your newest date?". 300s collapses 100 concurrent runs
    # into one such request while still noticing new data within five minutes;
    # Pinterest settles roughly daily, so five minutes costs nothing.
    #
    # `typeahead` is /prefix_match/ — a customer typing a query, which is the
    # most common way a keyword tool gets used. It sat on the 15-minute
    # `search` TTL, which is far too short: autocomplete suggestions are the
    # slowest-moving thing this API returns ("macrame" -> ideas / plant hanger
    # / bracelet does not change in a quarter of an hour).
    #
    # ⚠️ It is also the ONE endpoint whose request carries no end_date, so its
    # cache key does NOT change when Pinterest publishes new data. Every other
    # key self-invalidates that way; this one has only the TTL. 6h is chosen to
    # match `trends` for that reason — long enough to make repeated queries
    # free, short enough that a day's new data is never more than a few hours
    # away. It is a judgement, not a measurement: suggestion stability over a
    # full day was not tested.
    #
    # It must stay SHORT for a second reason: every other cache key contains
    # end_date, so this value is what makes the rest of the cache expire. Cache
    # it for hours and the whole cache goes stale together.
    CACHE_TTLS: str = os.environ.get(
        "CACHE_TTLS",
        "default=3600,trends=21600,search=900,detail=43200,bootstrap=300,"
        "typeahead=21600")

    @property
    def cache_ttls(self) -> dict:
        out = {"default": 3600}
        for pair in self.CACHE_TTLS.split(","):
            kind, _, seconds = pair.partition("=")
            try:
                out[kind.strip()] = int(seconds)
            except ValueError:
                continue
        return out

    @property
    def required_cookies(self) -> list:
        return [c.strip() for c in self.REQUIRED_COOKIES.split(",") if c.strip()]

    # Only used when a vault profile carries no user_agent of its own. The real
    # UA always comes from the browser the cookies were born in.
    USER_AGENT_FALLBACK: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # No GLOBAL proxy setting exists here, deliberately, and none ever should.
    # `adspower/sync_cookies.py` writes each profile's proxy into that profile's
    # own hash, next to its cookies — per profile and per account. A global
    # would put two profiles behind one exit IP, which defeats the separation
    # the proxies are for. `Identity.proxy` reads it from the hash, so the
    # writer can change without this file changing.
