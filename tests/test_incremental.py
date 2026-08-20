"""Proofs for the "do not pull old data" rules.

Runs against the real Redis under a throwaway platform namespace, so it never
touches the live pinterest keys. Run it with:

    .venv/Scripts/python.exe -m tests.test_incremental
"""
import sys
import time
from dataclasses import replace

sys.path.insert(0, ".")

from src.cache import ResponseCache  # noqa: E402
from src.config import Config  # noqa: E402
from src.state import RunState, fingerprint  # noqa: E402

TEST_PLATFORM = "__test_incremental"
checks = []


def check(name, condition):
    checks.append((name, bool(condition)))
    print(("  PASS  " if condition else "  FAIL  ") + name)


class FakeResponse:
    def __init__(self, status_code=200, text='{"ok":1}'):
        self.status_code = status_code
        self.text = text


def main():
    base = Config()
    cfg = replace(base, PLATFORM=TEST_PLATFORM, SEEN_TTL=2)
    state = RunState(cfg)
    cache = ResponseCache(cfg)

    # clean slate
    state.reset("pins")
    cache.clear()

    print("\nincremental / seen-set")
    pin = {"id": "p1", "saves": 10, "title": "a"}

    check("a record never seen is new",
          state.is_new("pins", "p1", fingerprint(pin)))

    state.mark_seen("pins", [pin], id_key="id")
    check("after marking, the same record is not new",
          not state.is_new("pins", "p1", fingerprint(pin)))

    moved = {"id": "p1", "saves": 42, "title": "a"}
    check("a record whose metric moved IS new again",
          state.is_new("pins", "p1", fingerprint(moved)))

    # fields= narrows what "changed" means. Mark and check must use the SAME
    # fields, which is what Record.fields guarantees at every call site.
    METRICS = ("id", "saves")
    state.reset("pins")
    state.mark_seen("pins", [pin], id_key="id", fields=METRICS)
    reordered = {"id": "p1", "saves": 10, "title": "ZZZ", "noise": [3, 2, 1]}
    check("a change OUTSIDE `fields` does not count as new",
          not state.is_new("pins", "p1", fingerprint(reordered, METRICS), METRICS))
    bumped = {"id": "p1", "saves": 11, "title": "a"}
    check("a change INSIDE `fields` does count as new",
          state.is_new("pins", "p1", fingerprint(bumped, METRICS), METRICS))

    print("\nchanging `fields` is detected, not silent")
    check("a different fields signature forces a re-collect",
          state.is_new("pins", "p1", fingerprint(pin), None))

    state.reset("pins")
    state.mark_seen("pins", [pin], id_key="id")

    print("\nseen entries expire so slow metrics still refresh")
    time.sleep(2.2)  # SEEN_TTL=2 in this config
    check("past SEEN_TTL the same unchanged record is new again",
          state.is_new("pins", "p1", fingerprint(pin)))

    print("\nrecords with no id are never silently dropped")
    unidentified = [{"saves": 1}, {"saves": 2}]
    kept = list(state.filter_new("pins", unidentified, id_key="id"))
    check("both id-less records pass through", len(kept) == 2)
    check("marking id-less records writes nothing",
          state.mark_seen("pins", unidentified, id_key="id") == 0)

    print("\nresponse cache")
    url = "https://example.invalid/resource"
    check("a cold read is a miss", cache.get("search", url) is None)

    stored = cache.put("search", url, FakeResponse())
    check("a usable 200 is stored", stored)
    hit = cache.get("search", url)
    check("the next read is served from cache",
          hit is not None and hit.from_cache and hit.json() == {"ok": 1})

    check("a 403 is NOT cached",
          not cache.put("search", url + "/a", FakeResponse(403, "denied")))
    check("a 500 is NOT cached",
          not cache.put("search", url + "/b", FakeResponse(500, "boom")))
    check("an empty 200 is NOT cached",
          not cache.put("search", url + "/c", FakeResponse(200, "")))
    check("nothing was stored for the failures",
          cache.get("search", url + "/a") is None
          and cache.get("search", url + "/b") is None
          and cache.get("search", url + "/c") is None)

    print("\nTTLs differ per endpoint kind")
    check("trends outlives search", cache.ttl_for("trends") > cache.ttl_for("search"))
    check("an unknown kind falls back to default",
          cache.ttl_for("nonesuch") == cfg.cache_ttls["default"])

    print("\nwatermark")
    check("no watermark on a first run", state.get_watermark("pins") is None)
    state.set_watermark("pins", "2026-08-18")
    check("watermark round-trips", state.get_watermark("pins") == "2026-08-18")

    # tidy up
    state.reset("pins")
    cache.clear()

    print("\nSINGLEFLIGHT - one fill per key, not one per client")
    import json as _json, threading, time as _t
    from src.transport import TrendsClient

    sf = ResponseCache(replace(base, PLATFORM="__sf_unit"))
    sf.clear()

    class _R:
        status_code = 200
        text = '{"date": "2026-08-14"}'
        def json(self): return _json.loads(self.text)

    wire = {"n": 0}
    wlock = threading.Lock()

    class _Slow:
        def get(self, url, params=None, headers=None):
            with wlock: wire["n"] += 1
            _t.sleep(0.8)
            return _R()
        def post(self, *a, **k): raise AssertionError("no POSTs here")

    def _client():
        TrendsClient(_Slow(), cache=sf).style_b("/x/", {"q": "same"}, kind="trends")

    # THE BUG THIS GUARDS: measured before singleflight, 8 clients asking one
    # uncached question made 8 requests and tied up 8 leased identities. The
    # cache was perfect after the first fill and absent during it.
    ts = [threading.Thread(target=_client) for _ in range(6)]
    [x.start() for x in ts]; [x.join() for x in ts]
    check("6 clients on one cold key make ONE upstream request", wire["n"] == 1)

    # A warm key must not take the lock path at all.
    wire["n"] = 0
    _client()
    check("a warm key costs no request at all", wire["n"] == 0)

    # The lock must be RELEASED after a fill, or every later waiter sits out
    # the full timeout before fetching anyway.
    sf.clear()
    check("the fill lock is released after storing",
          sf.acquire_fill("trends", "https://x/y", {"a": 1}))
    sf.release_fill("trends", "https://x/y", {"a": 1})
    check("...so the next caller can claim it",
          sf.acquire_fill("trends", "https://x/y", {"a": 1}))
    check("...and a second claim while held is refused",
          not sf.acquire_fill("trends", "https://x/y", {"a": 1}))
    sf.release_fill("trends", "https://x/y", {"a": 1})

    # A waiter whose winner never delivers must FETCH, not fail: a duplicate
    # request is the right outcome, silence is not.
    slow_cfg = replace(base, PLATFORM="__sf_unit", FILL_WAIT_TIMEOUT=1.0)
    sf2 = ResponseCache(slow_cfg); sf2.clear()
    sf2.acquire_fill("trends", "https://x/z", None)      # winner that never returns
    wire["n"] = 0
    TrendsClient(_Slow(), cache=sf2).style_b("/z/", None, kind="trends")
    check("a waiter whose winner never delivers fetches rather than failing",
          wire["n"] == 1)
    sf.clear(); sf2.clear()

    print("\nLAZY LEASING - an identity only when the wire is touched")
    from src.lazy import LazySession

    lz = LazySession(base)
    # THE POINT: capacity is profiles/miss_rate, not profiles. A run served
    # entirely from cache must occupy nothing at all.
    check("nothing is leased until a request happens", not lz.acquired)
    check("...and closing an unused session is a no-op, not an error",
          lz.close() is None)
    check("a run that never fetched reports no identity", lz.identity is None)

    # stats() must not explode on a cache-only run, and must not name a profile
    # that was never used.
    from src.context import Context
    ctx = Context(lz, None, {}, base)
    check("stats() on a cache-only run says so instead of crashing",
          ctx.stats()["profile"] == "cache-only")

    # The bootstrap was uncached, so EVERY run leased an identity just to ask
    # "what is your newest date?" — which defeated lazy leasing entirely.
    check("the bootstrap call is cached", "bootstrap" in base.cache_ttls)
    check("...but briefly: it is what expires every other key",
          base.cache_ttls["bootstrap"] <= 900)
    check("...much shorter than the trends TTL it gates",
          base.cache_ttls["bootstrap"] < base.cache_ttls["trends"])

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
