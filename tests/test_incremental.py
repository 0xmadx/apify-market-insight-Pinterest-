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

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
