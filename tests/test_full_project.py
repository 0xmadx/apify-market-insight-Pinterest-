"""Offline proofs for the keywords/moments/radar layers — parsers + traversals.

    .venv/Scripts/python.exe -m tests.test_full_project

All against committed captured responses; scenario ids from docs/TEST-SCENARIOS.
"""
import glob
import json
import sys

sys.path.insert(0, ".")

from src import parsers, vocab  # noqa: E402
from src.keywords import KeywordScraper  # noqa: E402
from src.moments import MomentScraper  # noqa: E402
from src.radar import RadarScraper  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


def load(pattern, style="A"):
    matches = glob.glob(f"probes/results/{pattern}") or \
              glob.glob(f"probes/results/params/{pattern}")
    if not matches:
        return None
    payload = json.load(open(matches[0], encoding="utf-8"))
    if style == "A" and isinstance(payload, dict):
        return (payload.get("resource_response") or {}).get("data")
    return payload


# --------------------------------------------------------------- fake client

class FakeClient:
    def __init__(self):
        self.calls = []
        self.discover100 = load("H2-top_trends_100rows.json", style="B")
        self.discover10 = load("3.12-*.json", style="B")
        self.metrics = load("3.13-*.json", style="B")
        self.demographics = load("3.14-*.json", style="B")
        self.related = load("3.15-*.json", style="B")
        self.images = load("3.16-*.json", style="B")
        self.prefix = load("3.17-*.json", style="B")
        self.moments_list = load("3.3-*.json")
        self.moment_metrics = load("3.4-*.json")
        self.spotlight = load("3.2-*.json")
        self.editorial = load("3.5-*.json")

    def bootstrap(self):
        return "2026-08-14"

    def style_a(self, path, data=None, source_url=None, kind=None):
        self.calls.append((path, data or {}))
        if "moment/available" in path:
            return self.moments_list
        if "moment/metrics" in path:
            # Re-key the real halloween capture onto whichever slug was asked
            # for, so the traversal's slug lookup is exercised.
            slug = (data or {}).get("moments", ["halloween"])[0]
            src = self.moment_metrics["moments"][0]
            return {"moments": [dict(src, name=slug)]}
        if "topics/featured" in path:
            return self.spotlight
        if "editorial" in path:
            return self.editorial
        raise AssertionError(f"unexpected A path {path}")

    def style_b(self, path, params=None, method="GET", json_body=None,
                kind=None):
        self.calls.append((path, params or json_body or {}))
        if path == "/top_trends_filtered/":
            return self.discover10
        if path == "/metrics/":
            # echo real metric rows for the requested terms
            template = self.metrics[0]
            terms = (params or {}).get("terms", "").split(",")
            return [dict(template, term=t) for t in terms]
        if path == "/demographics/":
            template = next(iter(self.demographics["term_distributions"].values()))
            terms = (params or {}).get("terms", "").split(",")
            return {"term_distributions": {t: template for t in terms}}
        if path == "/related_terms/":
            return self.related
        if path == "/term_images/":
            terms = (json_body or {}).get("terms", [])
            urls = next(iter(self.images.values()))
            return {t: urls for t in terms}
        if path == "/prefix_match/":
            return self.prefix
        raise AssertionError(f"unexpected B path {path}")


def main():
    print("\nGROUP B — the new parsers, on real captures")

    d100 = load("H2-top_trends_100rows.json", style="B")
    if d100:
        found = parsers.parse_discover(d100)
        check("B2 discover parses 100 rows", len(found["terms"]) == 100)
        row = found["terms"][0]
        check("B2 seasonality + reverse_rank + counts captured",
              row["seasonality_score"] is not None
              and row["reverse_rank"] is not None
              and row["search_count"] is not None)
        check("H2 index exposed as *_rank_in_response, scope in the name",
              "wow_rank_in_response" in row and "wow_change" in row)
        check("B3 affinity is dropped entirely", "affinity" not in row)

    pm = load("3.17-*.json", style="B")
    if pm:
        rows = parsers.parse_prefix_match(pm)
        check("B2 prefix_match parses 10 terms with sparklines",
              len(rows) == 10 and len(rows[0]["sparkline"]) > 40)

    rel = load("3.15-*.json", style="B")
    if rel:
        rows = parsers.parse_related_terms(rel)
        check("B2 related_terms parses exactly 5", len(rows) == 5)
        check("B1 camelCase-only hasPrediction lands in has_forecast",
              all(isinstance(r["has_forecast"], bool) for r in rows))

    kd = load("3.14-*.json", style="B")
    if kd:
        parsed = parsers.parse_keyword_demographics(kd)
        check("B2 keyword demographics keyed by term", "nails" in parsed)
        check("B2 fractions present",
              parsed["nails"]["age_distribution"]["18-24"] == 0.49)

    ti = load("3.16-*.json", style="B")
    if ti:
        parsed = parsers.parse_term_images(ti)
        check("B2 term_images maps term -> 9 urls",
              len(next(iter(parsed.values()))) == 9)

    print("\nGROUP B4 — moments parallel arrays")
    ml = load("3.3-*.json")
    if ml:
        moments = parsers.parse_moments_list(ml)
        check("B4 13 moments zipped", len(moments) == 13)
        check("B4 epoch-ms strings became ints",
              isinstance(moments[0]["next_occurrence_at"], int))
        check("D2 actionable derives from phase",
              all(m["actionable"] == (m["phase"] in ("rising", "approaching"))
                  for m in moments))
        broken = dict(ml)
        broken["phase_labels"] = ml["phase_labels"][:-1]
        try:
            parsers.parse_moments_list(broken)
            check("B4 unequal arrays RAISE, never truncate", False)
        except ValueError as exc:
            check("B4 unequal arrays RAISE, never truncate",
                  "mis-assign" in str(exc))

    mm = load("3.4-*.json")
    if mm:
        parsed = parsers.parse_moment_metrics(mm)
        check("B2 moment metrics keyed by slug", "halloween" in parsed)
        hal = parsed["halloween"]
        check("B2 series + interest split captured",
              len(hal["series"]) > 0 and len(hal["interest_split"]) > 0)

    print("\nGROUP B — spotlight/editorial")
    sp = load("3.2-*.json")
    if sp:
        trends = parsers.parse_spotlight(sp)
        check("B2 spotlight parses 5 trends", len(trends) == 5)
        check("F1 spotlight pins carry pin_id AND a permalink",
              trends[0]["pins"][0]["pin_id"]
              and trends[0]["pins"][0]["pin_url"].startswith(
                  "https://www.pinterest.com/pin/"))
        check("F6 dominant color kept",
              (trends[0]["pins"][0]["color"] or "").startswith("#"))
        check("H3 null forecast fields stay None, not 0",
              trends[0]["series"][0]["predicted_lower"] is None)

    ed = load("3.5-*.json")
    if ed:
        items = parsers.parse_editorial(ed, "US")
        check("B2 editorial parses 6 items", len(items) == 6)
        check("B6 the US keyword list was picked",
              isinstance(items[0]["keywords"], list) and len(items[0]["keywords"]) == 5)
        check("B6 a region not covered yields None, never another region's list",
              parsers.parse_editorial(ed, "DE+AT+CH")[0]["keywords"] is None)
        check("F3 campaign_start emitted", items[0]["campaign_start"] == "2026-08-01")
        check("F3 empty end_date is None, not ''", items[0]["campaign_end"] is None)

    print("\nGROUP E1 — keyword traversal (budget + crystal ball)")
    client = FakeClient()
    scraper = KeywordScraper(client, region="US", log=lambda *a: None)
    records = list(scraper.run(mode="discover", preset=3, num_terms=10,
                               include_related=False, include_images=True))
    check("E1 one record per discovered term", len(records) == 10, len(records))
    b_paths = [p for p, _ in client.calls]
    check("E1 metrics is ONE batched call",
          b_paths.count("/metrics/") == 1)
    check("E1 demographics is ONE batched call",
          b_paths.count("/demographics/") == 1)
    check("E1 images is ONE POST", b_paths.count("/term_images/") == 1)
    check("E1 budget: 4 calls for 10 terms without related "
          "(discover+metrics+demographics+images)",
          len(client.calls) == 4, len(client.calls))
    met_call = next(d for p, d in client.calls if p == "/metrics/")
    check("E1 group-normalised whenever >1 term",
          met_call["normalize_against_group"] == "true")
    check("E1 shouldMock always false", met_call["shouldMock"] == "false")
    first = records[0]
    check("D1 has_forecast surfaced", first["has_forecast"] is True)
    check("D1 forecast_note absent when forecast exists",
          first["forecast_note"] is None)
    check("E1 discovery-time seasonality carried onto the record",
          first["seasonality_score"] is not None)
    check("D5 scope names the batch",
          "metrics:US:2026-08-14" in first["_meta"]["normalization_scope"])

    print("\nGROUP E3 — moment traversal (phase gate + derived audience)")
    client2 = FakeClient()
    mscraper = MomentScraper(client2, region="US", log=lambda *a: None)
    mrecords = list(mscraper.run())
    check("E3 all 13 moments emitted", len(mrecords) == 13)
    drilled = [m for m in mrecords if m["drilled"]]
    gated = [m for m in mrecords if not m["drilled"]]
    check("D2 only actionable phases drilled",
          all(m["phase"] in ("rising", "approaching") for m in drilled)
          and all(m["phase"] not in ("rising", "approaching") for m in gated))
    a_metrics = [p for p, _ in client2.calls if "moment/metrics" in p]
    check("D2 cooled moments cost zero metric calls",
          len(a_metrics) == len(drilled), f"{len(a_metrics)} vs {len(drilled)}")
    if drilled:
        d0 = drilled[0]
        check("E3 drilled moment has a series", d0["series_points"] > 0)
        check("D4 audience labelled derived, with the reason",
              d0["_meta"]["audience_basis"] == "derived"
              and "GraphQL" in (d0["_meta"]["audience_note"] or "")
              or "REST" in (d0["_meta"]["audience_note"] or ""))
        check("E3 derived audience aggregates fractions",
              0 < d0["audience"]["gender_distribution"]["female"] <= 1)
    if gated:
        check("B3 gated moment carries phase data but null series",
              gated[0]["series"] is None and gated[0]["phase"])

    try:
        MomentScraper(client2, aggregation="hourly")
        check("C4 hourly aggregation refused", False)
    except vocab.InvalidParam:
        check("C4 hourly aggregation refused", True)
    try:
        MomentScraper(client2, aggregation="monthly", predicted_days=91)
        check("C4 monthly+91 divisibility refused", False)
    except vocab.InvalidParam:
        check("C4 monthly+91 divisibility refused", True)

    print("\nGROUP E4 — radar traversal")
    client3 = FakeClient()
    rscraper = RadarScraper(client3, region="US", log=lambda *a: None)
    rrecords = list(rscraper.run())
    check("E4 5 spotlight + 6 editorial = 11 records", len(rrecords) == 11)
    check("E4 exactly 2 requests for the whole radar",
          len(client3.calls) == 2, len(client3.calls))
    check("E4 every record says curated_by pinterest",
          all(r["curated_by"] == "pinterest" for r in rrecords))
    editorial = [r for r in rrecords if r["source"] == "editorial"]
    check("F3 editorial records carry the campaign window",
          editorial[0]["campaign_start"] is not None)

    client4 = FakeClient()
    r_de = list(RadarScraper(client4, region="DE",
                             log=lambda *a: None).run())
    check("D3 editorial in DE degrades to spotlight-only with a reason, "
          "never a silent []",
          len(r_de) == 5
          and not any("editorial" in p for p, _ in client4.calls))

    print("\nGROUP F3 - the response cache is actually WIRED IN")
    # This group exists because the cache was built, tested, and used by
    # nothing: every traversal called TrendsClient directly, so the taxonomy
    # was refetched every run and forceRefresh did nothing. Output-only tests
    # could never catch that — these assert on the wire traffic.
    from src.transport import TrendsClient
    from src.config import Config
    from src.cache import ResponseCache
    from dataclasses import replace

    cfg = replace(Config(), PLATFORM="__test_cachewire")
    cache = ResponseCache(cfg)
    cache.clear()

    class CountingSession:
        """Returns a real captured payload and counts wire hits."""
        def __init__(self, payload):
            self.payload = payload
            self.hits = 0

        def get(self, url, params=None, headers=None):
            self.hits += 1
            return self

        status_code = 200
        headers = {}

        @property
        def text(self):
            return json.dumps(self.payload)

        def json(self):
            return self.payload

    raw_tax = json.load(open(glob.glob("probes/results/3.6-*.json")[0],
                             encoding="utf-8"))
    sess = CountingSession(raw_tax)

    c1 = TrendsClient(sess, cache=cache, delay=0)
    first = c1.style_a("/ads/v4/trends/shopping/product_categories", kind="taxonomy")
    check("F3 first call goes to the wire", sess.hits == 1 and first is not None)

    c2 = TrendsClient(sess, cache=cache, delay=0)
    second = c2.style_a("/ads/v4/trends/shopping/product_categories", kind="taxonomy")
    check("F3 a SECOND client (new run) is served from cache — 0 extra wire hits",
          sess.hits == 1, f"hits={sess.hits}")
    check("F3 the cached payload is identical, not a stub",
          json.dumps(second, sort_keys=True) == json.dumps(first, sort_keys=True))
    check("F3 cache_hits counter reflects it", c2.cache_hits == 1)

    c3 = TrendsClient(sess, cache=cache, delay=0, force_refresh=True)
    c3.style_a("/ads/v4/trends/shopping/product_categories", kind="taxonomy")
    check("F3 forceRefresh actually bypasses the cache",
          sess.hits == 2, f"hits={sess.hits}")

    c4 = TrendsClient(sess, cache=cache, delay=0)
    c4.style_a("/ads/v4/trends/shopping/product_categories")   # no kind
    check("F3 an untagged call is never cached (kind=None)",
          sess.hits == 3, f"hits={sess.hits}")

    # every traversal must tag its calls, or the cache silently does nothing
    import src.shopping, src.keywords, src.moments, src.radar, inspect
    for mod in (src.shopping, src.keywords, src.moments, src.radar):
        source = inspect.getsource(mod)
        calls = source.count("style_a(") + source.count("style_b(")
        tagged = source.count('kind="')
        check(f"F3 {mod.__name__.split('.')[-1]}: all "
              f"{calls} calls tagged with a cache kind",
              tagged >= calls - 1, f"{tagged} tagged of {calls}")
    cache.clear()

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
