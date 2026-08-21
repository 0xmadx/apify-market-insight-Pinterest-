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

    print("\nPER-ITEM CACHE - overlapping customers share work")
    frag = ResponseCache(replace(base, PLATFORM="__frag_unit"))
    frag.clear()
    grp = {"region": "US", "end_date": "2026-08-14", "days": 365}

    check("an unfetched item is a miss",
          frag.get_item("detail", grp, "macrame") is None)
    frag.put_item("detail", grp, "macrame", {"18-24": 0.5})
    check("...and a hit after one customer fetched it",
          frag.get_item("detail", grp, "macrame") == {"18-24": 0.5})

    # The group is everything that makes two identical terms different answers.
    # Leaving any of it out serves a US answer to a DE request.
    check("the same term in another REGION is a miss",
          frag.get_item("detail", {**grp, "region": "DE"}, "macrame") is None)
    check("the same term on another DATE is a miss",
          frag.get_item("detail", {**grp, "end_date": "2026-08-21"}, "macrame") is None)
    check("the same term over another WINDOW is a miss",
          frag.get_item("detail", {**grp, "days": 90}, "macrame") is None)

    # Absent is not zero: a cached None would hide a term Pinterest simply has
    # no data for yet, permanently.
    check("None is never stored", not frag.put_item("detail", grp, "x", None))
    check("...so it stays a miss and gets retried",
          frag.get_item("detail", grp, "x") is None)
    frag.clear()

    # THE MEASUREMENT THAT DECIDED THIS. /metrics/ sends
    # normalize_against_group=true, so a term's counts are scaled against the
    # OTHER terms in the same request. Live, same term, same dates:
    #     'eye makeup' beside 'eyelashes'                     -> [30, 32, 36]
    #     'eye makeup' beside 'nails','hairstyles','wallpaper'-> [ 1,  1,  2]
    # Caching that per term would serve a 36 next to a fresh 1. Demographics
    # are per-term percentages and measured IDENTICAL across both groups.
    import inspect
    from src.keywords import KeywordScraper
    msrc = inspect.getsource(KeywordScraper._metrics)
    dsrc = inspect.getsource(KeywordScraper._demographics)
    check("_metrics carries the group-relative warning",
          "NEVER cache this per term" in msrc and "GROUP-RELATIVE" in msrc)
    check("_metrics does NOT use the per-item cache",
          "get_item" not in msrc and "put_item" not in msrc)
    check("_demographics DOES use it, and says it was measured safe",
          "get_item" in dsrc and "MEASURED SAFE" in dsrc)

    print("\nGROUP T — one customer must not empty another customer's dataset")
    # FOUND 2026-08-20 in the container rehearsal, which reported
    # `pushed 0 · skipped 11 already held` on a second run. The seen-set key was
    # `seen:{platform}:{scope}` with no tenant in it, and on Apify every
    # customer shares one Redis — so the second customer to ask a question got
    # an EMPTY DATASET and paid for it, for up to SEEN_TTL (7 days).
    #
    # Not a wrong number. A missing one, which is worse here: a wrong number
    # gets argued with, an empty dataset just reads as "this actor is broken".
    alice = RunState(replace(base, PLATFORM=TEST_PLATFORM, DEDUP_SCOPE="alice"))
    bob = RunState(replace(base, PLATFORM=TEST_PLATFORM, DEDUP_SCOPE="bob"))
    for who in (alice, bob):
        who.reset("radar")

    rec = {"id": "spot-1", "growth": 12}
    check("T1 a record is new to the first customer",
          alice.is_new("radar", "spot-1", fingerprint(rec)))
    alice.mark_seen("radar", [rec], id_key="id")
    check("T2 ...and not new to that same customer again",
          not alice.is_new("radar", "spot-1", fingerprint(rec)))
    check("T3 ...but STILL new to a different customer",
          bob.is_new("radar", "spot-1", fingerprint(rec)))

    # Watermarks carry the same risk: a shared cursor would make one customer's
    # progress skip another customer's history.
    alice.set_watermark("radar", "2026-08-20")
    check("T4 watermarks are per customer too",
          alice.get_watermark("radar") == "2026-08-20"
          and bob.get_watermark("radar") is None)

    # Resetting one customer must not clear another's.
    bob.mark_seen("radar", [rec], id_key="id")
    alice.reset("radar")
    check("T5 one customer's reset leaves the other's state intact",
          alice.is_new("radar", "spot-1", fingerprint(rec))
          and not bob.is_new("radar", "spot-1", fingerprint(rec)))

    # The cache is the OPPOSITE and must stay that way: sharing an answer is
    # the economics of the whole product. Only the delivery receipt is private.
    shared_a = ResponseCache(replace(base, PLATFORM=TEST_PLATFORM,
                                     DEDUP_SCOPE="alice"))
    shared_b = ResponseCache(replace(base, PLATFORM=TEST_PLATFORM,
                                     DEDUP_SCOPE="bob"))
    check("T6 the response cache key does NOT split by customer",
          shared_a._key("trends", "https://x/y", {"a": 1}, None)
          == shared_b._key("trends", "https://x/y", {"a": 1}, None))

    check("T7 an unidentified tenant gets its own bucket, not a shared one",
          Config().DEDUP_SCOPE == "local")
    bob.reset("radar")

    print("\nGROUP F — the fill lock must not survive a failed request")
    # FOUND BY probes/stress.py, 2026-08-20. `_store()` releases the fill lock,
    # and its own docstring claimed that happens "ALWAYS, hit or miss". It does
    # not: every call site is
    #
    #     response = self._request(...)      # can raise
    #     payload  = self._json(response)    # can raise
    #     self._store(...)                   # only reached on success
    #
    # so ANY failure leaves the lock set for its full 45s TTL. Every other
    # client asking the same question then sits out the 30s wait_for_fill
    # timeout and fetches anyway. One failing request becomes a 30s latency
    # spike plus N duplicate requests — aimed at an endpoint that is already
    # failing, which is the worst possible moment to retry in a crowd.
    # NOT `from src.cache import ResponseCache` here: a local import of a name
    # already imported at module level makes it local for the WHOLE function,
    # and every earlier use in main() becomes an UnboundLocalError.
    from src.transport import TrendsClient, TrendsAPIError  # noqa: E402

    F_PLATFORM = "__test_fill"
    fcfg = replace(Config(), PLATFORM=F_PLATFORM)
    try:
        fcache = ResponseCache(fcfg)
        fcache.r.ping()
    except Exception as exc:
        print(f"  cannot reach redis for the fill-lock test: {exc}")
        fcache = None

    if fcache is not None:
        class Boom:
            """A response that is a 200 but whose body will not parse — the
            exact shape that makes _json raise after _request succeeded."""
            status_code = 200
            text = "<html>not json</html>"

            def json(self):
                raise ValueError("not json")

        class BoomSession:
            def get(self, *a, **k):
                return Boom()

            def post(self, *a, **k):
                return Boom()

        # THROUGH THE PUBLIC METHOD, not the internals. An earlier version of
        # this test drove _cached/_request/_json/_store by hand, which
        # reproduced the bug but could never prove the fix: the try/except that
        # releases the lock lives in style_a/style_b/graphql/pin_resource, and
        # calling past them skips exactly the code under test.
        from src.transport import BASE  # noqa: E402
        path = "/fill-lock/"
        params = {"q": "1"}
        fcache.clear()
        key = fcache._key("trends", BASE + path, params, None)
        fcache.r.delete(key + ":fill")

        client = TrendsClient(BoomSession(), cache=fcache, delay=0)
        raised = False
        try:
            client.style_b(path, params, kind="trends")
        except TrendsAPIError:
            raised = True

        check("F1 a body that will not parse does raise", raised)
        still_locked = bool(fcache.r.exists(key + ":fill"))
        if still_locked:
            print(f"        (lock still set, ttl "
                  f"{fcache.r.ttl(key + ':fill')}s — every other client waits "
                  f"{fcfg.FILL_WAIT_TIMEOUT}s then fetches anyway)")
        check("F2 ...and the fill lock is NOT left behind for other clients",
              not still_locked)

        # The same must hold when the transport layer itself raises before a
        # response exists at all — a dead proxy, a timeout, a reset.
        class DeadSession:
            def get(self, *a, **k):
                raise OSError("connection reset")

            def post(self, *a, **k):
                raise OSError("connection reset")

        fcache.r.delete(key + ":fill")
        try:
            TrendsClient(DeadSession(), cache=fcache, delay=0).style_b(
                path, params, kind="trends")
        except Exception:
            pass
        check("F3 a transport failure does not strand the lock either",
              not fcache.r.exists(key + ":fill"))

        # Style A takes a different route to the same cache, so it needs its
        # own proof rather than an assumption that one wrapper covers all four.
        a_url = f"{BASE}/resource/ApiResource/get/"
        import json as _json_mod
        a_params = {"data": _json_mod.dumps(
            {"options": {"url": "/ads/v4/trends/x", "data": {}}, "context": {}},
            separators=(",", ":"))}
        a_key = fcache._key("trends", a_url, a_params, None)
        fcache.r.delete(a_key + ":fill")
        try:
            TrendsClient(BoomSession(), cache=fcache, delay=0).style_a(
                "/ads/v4/trends/x", kind="trends")
        except Exception:
            pass
        check("F3b style_a releases its lock on failure too",
              not fcache.r.exists(a_key + ":fill"))

        # And the docstring must stop claiming a guarantee it does not give.
        import inspect as _inspect
        store_src = _inspect.getsource(TrendsClient._store)
        check("F4 _store no longer claims 'ALWAYS' without qualification",
              "hit or miss" not in store_src or "raise" in store_src.lower())

        fcache.clear()
        for k in list(fcache.r.scan_iter(f"*{F_PLATFORM}*")):
            fcache.r.delete(k)


    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
