"""What the scraper is handed. One object instead of five arguments.

    def run(ctx, task):
        r = ctx.get("trends", url, params={...})     # cached, TTL by kind
        if ctx.seen("pins", pin_id):                 # skip work before fetching
            continue
        yield Record(scope="pins", id=pin_id, data={...})

The two "don't pull old data" layers sit here side by side and do different jobs:

  ctx.get(...)   avoids the REQUEST — a repeat call inside the kind's TTL is
                 served from Redis. Never serves a failed or empty response.
  ctx.seen(...)  avoids the WORK and the duplicate dataset row — asks whether a
                 record was already collected and has not changed since.

Use `ctx.seen` before an expensive follow-up fetch; the caching layer cannot know
that a detail page is pointless to load. The final dedup happens in main.py
regardless, so forgetting it costs requests, never correctness.
"""
from .cache import ResponseCache, cached_get
from .config import Config
from .state import RunState, fingerprint


class Context:
    def __init__(self, session, identity, task, config: Config = None):
        self.config = config or Config()
        self.session = session
        self.identity = identity
        self.task = task or {}
        self.cache = ResponseCache(self.config)
        self.state = RunState(self.config)

        # Run-level overrides from the actor input. `forceRefresh` ignores cached
        # responses; `fullRescan` ignores the seen-set. Both still WRITE, so a
        # one-off full run does not leave the next run with nothing to skip.
        self.force_refresh = bool(self.task.get("forceRefresh", False))
        self.full_rescan = bool(self.task.get("fullRescan", False))

    # ------------------------------------------------------------- fetching

    def get(self, kind: str, url: str, params=None, headers=None):
        """Cached GET. `kind` picks the TTL — see Config.CACHE_TTLS."""
        return cached_get(
            self.session, self.cache, kind, url,
            params=params, headers=headers,
            force_refresh=self.force_refresh,
        )

    def raw(self, method: str, url: str, **kwargs):
        """Uncached request, for anything that must never be replayed (POSTs,
        cursor pagination, anything whose response is only valid once)."""
        return self.session.request(method, url, **kwargs)

    # ---------------------------------------------------------- incremental

    def seen(self, scope: str, record_id, record: dict = None, fields=None) -> bool:
        """True if this was already collected and has not changed.

        Inverted from `RunState.is_new` on purpose: at the call site the useful
        question is "can I skip this?", and `if ctx.seen(...): continue` reads
        the way the code behaves.
        """
        if self.full_rescan:
            return False
        fp = fingerprint(record, fields) if record is not None else None
        return not self.state.is_new(scope, record_id, fp, fields)

    def watermark(self, scope: str):
        """Where the last run got to, or None on a first run."""
        return None if self.full_rescan else self.state.get_watermark(scope)

    def set_watermark(self, scope: str, value):
        """Move the cursor forward. Only after the data behind it has landed."""
        self.state.set_watermark(scope, value)

    # ------------------------------------------------------------- summary

    def stats(self) -> dict:
        return {
            "profile": self.identity.profile_id,
            "cache_hits": self.cache.hits,
            "cache_misses": self.cache.misses,
            "force_refresh": self.force_refresh,
            "full_rescan": self.full_rescan,
        }
