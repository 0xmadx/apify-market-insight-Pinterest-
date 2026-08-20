"""Response cache with per-endpoint TTLs.

The opposite of `state.py`. That module exists to *remember* across runs; this one
exists to *forget* — every entry carries an expiry, and nothing is served past it.
Keeping them in separate modules is deliberate: conflating "we already collected
this" with "we called this recently" is how a cache silently becomes a database of
stale numbers.

    cache:{platform}:{kind}:{sha1}   STRING (json), with TTL

**A failed fetch is never cached.** Caching a 403 or a 500 means the next N runs
replay the failure without ever retrying, and the operator sees a dead endpoint
where there was a transient one. Only a response that `session.classify()` calls
`ok` and that actually carries a body is stored.

**TTL is per kind, not global.** Pinterest's weekly trend series does not change
for hours; a search ranking can move in minutes. One TTL for both is either
wastefully short or dangerously long.
"""
import hashlib
import json
import time

import redis

from .config import Config
from .session import classify


class CachedResponse:
    """Duck-types the parts of a curl_cffi response the scraper actually uses."""

    def __init__(self, status_code, text, from_cache, cached_at=None):
        self.status_code = status_code
        self.text = text
        self.from_cache = from_cache
        self.cached_at = cached_at

    def json(self):
        return json.loads(self.text)

    @property
    def age(self):
        return None if self.cached_at is None else time.time() - self.cached_at

    def __repr__(self):
        src = f"cache age={int(self.age)}s" if self.from_cache else "live"
        return f"<Response {self.status_code} {src}>"


class ResponseCache:
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.r = redis.Redis.from_url(self.config.REDIS_URL, decode_responses=True)
        self.hits = 0
        self.coalesced = 0
        self.misses = 0

    def _key(self, kind, url, params, body):
        subject = json.dumps(
            [url, params or {}, body or {}], sort_keys=True, default=str
        )
        digest = hashlib.sha1(subject.encode("utf-8")).hexdigest()
        return f"cache:{self.config.PLATFORM}:{kind}:{digest}"

    def ttl_for(self, kind):
        return self.config.cache_ttls.get(kind, self.config.cache_ttls["default"])

    def get(self, kind, url, params=None, body=None):
        if not self.config.CACHE_ENABLED:
            return None
        raw = self.r.get(self._key(kind, url, params, body))
        if raw is None:
            self.misses += 1
            return None
        try:
            entry = json.loads(raw)
        except ValueError:
            self.misses += 1
            return None
        self.hits += 1
        return CachedResponse(
            status_code=entry["status"],
            text=entry["body"],
            from_cache=True,
            cached_at=entry.get("cached_at"),
        )

    def put(self, kind, url, response, params=None, body=None):
        """Store a response — but only a usable one. Returns whether it stored."""
        if not self.config.CACHE_ENABLED:
            return False
        if response.status_code != 200 or classify(response) != "ok":
            return False
        if not response.text:
            # An empty 200 is not a result; caching it hides the real answer for
            # a whole TTL. Absent is not zero.
            return False

        entry = {
            "status": response.status_code,
            "body": response.text,
            "cached_at": time.time(),
        }
        self.r.setex(
            self._key(kind, url, params, body),
            self.ttl_for(kind),
            json.dumps(entry),
        )
        return True

    # ------------------------------------------------------- singleflight
    #
    # MEASURED without this: 8 clients asking the SAME uncached question made 8
    # upstream requests. Once warm they made 0. So the cache is perfect AFTER
    # the first fill and absent DURING it — a textbook cache stampede, and the
    # binding cost is not Pinterest's patience but IDENTITIES: seven of those
    # eight burned a leased profile to fetch a duplicate.
    #
    # The fix is the standard one: exactly one caller fills a key, everyone
    # else waits for it. `SET NX` because the waiters are separate processes
    # (Apify containers), so an in-process singleflight would not see them.

    def acquire_fill(self, kind, url, params=None, body=None):
        """Claim the right to fetch this key. False means someone else has it.

        The lock TTL is deliberately short: if the winner dies mid-fetch the
        key must become fillable again quickly, and a duplicate fetch is a far
        cheaper failure than a key nothing is allowed to fill.
        """
        if not self.config.CACHE_ENABLED:
            return True
        lock = self._key(kind, url, params, body) + ":fill"
        return bool(self.r.set(lock, "1", nx=True,
                               ex=self.config.FILL_LOCK_TTL))

    def release_fill(self, kind, url, params=None, body=None):
        if not self.config.CACHE_ENABLED:
            return
        self.r.delete(self._key(kind, url, params, body) + ":fill")

    def wait_for_fill(self, kind, url, params=None, body=None):
        """Poll for the winner's answer. None means give up and fetch it too.

        Returning None rather than raising is deliberate: a waiter that times
        out must still be able to serve its customer. A duplicate request is
        the correct outcome when the winner is slow — silence is not.
        """
        if not self.config.CACHE_ENABLED:
            return None
        deadline = time.time() + self.config.FILL_WAIT_TIMEOUT
        while time.time() < deadline:
            time.sleep(self.config.FILL_POLL_INTERVAL)
            hit = self.get(kind, url, params, body)
            if hit is not None:
                self.coalesced += 1
                return hit
        return None

    def clear(self, kind=None):
        """Drop cached responses. `kind=None` drops every kind for this platform."""
        pattern = f"cache:{self.config.PLATFORM}:{kind or '*'}:*"
        dropped = 0
        for key in self.r.scan_iter(match=pattern, count=500):
            self.r.delete(key)
            dropped += 1
        print(f"[cache] cleared {dropped} entr(ies) matching {pattern}")
        return dropped


def cached_get(session, cache: ResponseCache, kind: str, url: str,
               params=None, headers=None, force_refresh=False):
    """GET through the cache. The one call the scraper should use.

        r = cached_get(session, cache, "trends", url, params=…)
        if r.status_code == 200:
            data = r.json()

    `force_refresh` bypasses the read but still writes, so a run can be told to
    ignore what it has without throwing the cache away for everyone.
    """
    if not force_refresh:
        hit = cache.get(kind, url, params)
        if hit is not None:
            return hit

    response = session.get(url, params=params, headers=headers)
    cache.put(kind, url, response, params)
    return CachedResponse(response.status_code, response.text, from_cache=False)
