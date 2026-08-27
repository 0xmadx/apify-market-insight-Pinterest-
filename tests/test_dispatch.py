"""The dispatcher — every operation, driven exactly as a customer drives it.

    .venv/Scripts/python.exe -m tests.test_dispatch

This suite exists because of a bug it would have caught immediately: adding
`interest_ids` to the moments dispatcher also landed it in the keywords
dispatcher, where `KeywordScraper` has no such parameter. The `keywords`
operation raised TypeError on EVERY run — and the other three suites all passed,
because they construct the scrapers directly and never go through
`scraper.run()`. A whole layer had no test.

Two things are proved here that the per-traversal suites cannot:

  1. **Zero-input runs work.** A customer who knows nothing yet and just wants
     "what is trending" must be able to press Run. Every operation has to
     produce records from `{"operation": "<name>"}` alone.
  2. **Every declared input reaches the code.** The Apify form and the
     dispatcher must not drift — 14 working parameters were once invisible in
     the form, including the one that fetches price.
"""
import datetime as _dt
import json
import pathlib
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

from tests.test_full_project import FakeClient as PipelineFake  # noqa: E402
import tests.test_shopping_traversal as shop_mod  # noqa: E402

import src.scraper as sc  # noqa: E402
from src.scraper import OPERATIONS, UnknownOperation, run  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


class EveryFixture(PipelineFake):
    """Serves the keyword/moment/radar fixtures AND the shopping ones."""

    def __init__(self):
        super().__init__()
        self.shop = shop_mod.FakeClient()

    def style_a(self, path, data=None, source_url=None, kind=None):
        if "product_categories" in path or "top_products" in path:
            result = self.shop.style_a(path, data, source_url, kind)
            self.calls.append((path, data or {}))
            return result
        return super().style_a(path, data, source_url, kind)

    @property
    def all_calls(self):
        return self.calls + self.shop.calls

    @property
    def request_count(self):
        """The same counter the real TrendsClient exposes.

        Added because the crawl budget read `all_calls` — which only THIS
        class had. Every offline test passed while the budget counted zero in
        production. A fake that does not expose what the real client exposes
        does not test the real path.
        """
        return len(self.all_calls)


class Ctx:
    def __init__(self, task):
        self.session = None
        self.task = task
        self.cache = None
        self.force_refresh = False
        self.truncated = False


def drive(task):
    """Run the dispatcher against fixtures, returning (records, client)."""
    records, client, _ = drive_ctx(task)
    return records, client


def drive_ctx(task):
    """As drive(), but hands back the Context so run-level flags are visible."""
    client = EveryFixture()
    ctx = Ctx(task)
    original = sc.TrendsClient
    sc.TrendsClient = lambda *a, **k: client
    try:
        return list(run(ctx, task)), client, ctx
    finally:
        sc.TrendsClient = original


def main():
    print("\nZERO-INPUT RUNS — 'just tell me what's trending'")
    for op in OPERATIONS:
        try:
            records, client = drive({"operation": op})
            check(f"{op}: runs with no customer input at all",
                  len(records) > 0, f"{len(records)} records")
        except Exception as exc:
            check(f"{op}: runs with no customer input at all", False,
                  f"{type(exc).__name__}: {exc}")

    print("\ndefaults are sane, not accidental")
    recs, _ = drive({})
    check("no `operation` at all defaults to shopping",
          bool(recs) and "category_id" in recs[0].data)
    try:
        list(run(Ctx({"operation": "nope"}), {"operation": "nope"}))
        check("an unknown operation is refused by name", False)
    except UnknownOperation as exc:
        check("an unknown operation is refused, listing the real ones",
              all(op in str(exc) for op in OPERATIONS))

    print("\nCUSTOMER INPUT actually reaches the wire")
    recs, c = drive({"operation": "keywords", "mode": "exact",
                     "queries": ["Boho Wall Art", "Macrame"],
                     "days": 180, "includeRelated": False,
                     "includeImages": False})
    sent = next(v for p, v in c.all_calls if p == "/metrics/")
    check("their exact terms are used", len(recs) == 2)
    check("terms lowercased for the wire (uppercase silently returns nothing)",
          sent["terms"] == "boho wall art,macrame", sent["terms"])
    check("their history window is honoured", sent["days"] == 180)
    check("group-normalised so their terms are comparable to each other",
          sent["normalize_against_group"] == "true")

    _, c2 = drive({"operation": "keywords", "mode": "discover",
                   "preset": 4, "numTerms": 50,
                   "keywordsToInclude": ["Wall Art"], "interests": ["935249274030"],
                   "moments": ["christmas"], "ageBuckets": ["25-34"],
                   "genders": ["female"], "includeRelated": False,
                   "includeImages": False})
    d = next(v for p, v in c2.all_calls if p == "/top_trends_filtered/")
    check("niche filter lowercased and passed", d["keywordsToInclude"] == "wall art")
    check("their preset + count honoured",
          d["trendsPreset"] == 4 and d["numTermsToReturn"] == 50)
    check("age maps to the KEYWORD numeric scheme, not the shopping enum",
          d["ageBuckets"] == "4", d["ageBuckets"])
    check("gender likewise", d["gender"] == "1", d["gender"])
    check("moment filter passed", d["moments"] == "christmas")

    _, c3 = drive({"operation": "shopping", "verticals": ["1042"],
                   "drillTopN": 1, "event": "SAVE"})
    top = next(v for p, v in c3.all_calls if "/top/" in p)
    check("their vertical, one per call", top["parent_product_categories"] == ["1042"])
    check("their ranking event honoured", top["event"] == "SAVE")

    _, c4 = drive({"operation": "moments", "aggregation": "weekly",
                   "predictedDays": 28, "interestIds": ["918530398158"]})
    gq = [v for p, v in c4.all_calls if p == "/_/graphql/"]
    check("moments still wires the interest matrix through the dispatcher",
          any(v["category"] == "MOMENT_INTEREST" for v in gq))
    met = next(v for p, v in c4.all_calls if "moment/metrics" in p)
    check("their granularity honoured", met["aggregation_level"] == "weekly")

    print("\nCORNERS - things the docs record and a first pass missed")
    from src import vocab
    check("all 32 API regions accepted, not the 10 on the doc's first line",
          len(vocab.REGIONS) == 32, len(vocab.REGIONS))
    for r in ("BR", "JP", "AU+NZ", "KR", "SE+DK+FI+NO"):
        check(f"region {r} is accepted", vocab.region(r) == r)
    check("18-24 expands to BOTH codes (7 UI options send 8 codes)",
          vocab.AGE_CODES_KEYWORD["18-24"] == [2, 3])
    _, ca = drive({"operation": "keywords", "mode": "discover",
                   "ageBuckets": ["18-24", "65+"], "genders": ["female"],
                   "moments": ["Father's Day"], "includeRelated": False,
                   "includeImages": False})
    da = next(v for p_, v in ca.all_calls if p_ == "/top_trends_filtered/")
    check("...and reaches the wire as 2,3 - narrowing it is silent otherwise",
          da["ageBuckets"] == "2,3,9", da["ageBuckets"])
    check("moment slug normalised: apostrophe stripped, spaces kept, lowered",
          da["moments"] == "fathers day", da["moments"])
    check("non-ASCII slugs survive intact (IT carnevale)",
          vocab.moment_slug("Carnevale Martedi Grasso") == "carnevale martedi grasso")
    try:
        vocab.region("JP", capability="moments")
        check("JP/IN moments refused with the reason", False)
    except vocab.InvalidParam as exc:
        check("JP/IN moments refused - 0 moments is real, not an outage",
              "no seasonal moments" in str(exc))
    check("inert params never sent (lookbackWindow, rankingMethod)",
          "lookbackWindow" not in da and "rankingMethod" not in da)

    print("\nC6 - one customer input, TWO wire schemes")
    _, cs = drive({"operation": "shopping", "verticals": ["1042"], "drillTopN": 0,
                   "shoppingAges": ["25-34", "65+"], "shoppingGenders": ["female"],
                   "rankingMethod": "VIRAL", "orderBy": "PCT_CHANGE_MOM"})
    ts = next(v for p_, v in cs.all_calls if "/top/" in p_)
    check("C6 shopping age uses the ENUM form, not the numeric codes",
          ts["age_bucket"] == ["AGE_25_34", "AGE_65_PLUS"], ts["age_bucket"])
    check("C6 shopping gender likewise", ts["gender"] == ["FEMALE"], ts["gender"])
    check("same band, two wire forms: keywords numeric vs shopping enum",
          "AGE_" in ts["age_bucket"][0] and da["ageBuckets"][0].isdigit())
    check("ranking_method reaches the wire (UI only ever sends GROWTH)",
          ts["ranking_method"] == "VIRAL")
    check("order_by reaches the wire", ts["order_by"] == "PCT_CHANGE_MOM")
    try:
        from src.shopping import ShoppingScraper as _SS
        _SS(None, ranking_method="NOPE")
        check("an invalid ranking_method is refused", False)
    except vocab.InvalidParam:
        check("an invalid ranking_method is refused before the wire", True)
    try:
        vocab.age_buckets_shopping(["25 to 34"])
        check("an unknown age band is refused", False)
    except vocab.InvalidParam:
        check("an unknown age band is refused, listing the valid ones", True)

    print("\nTIME TRAVEL - asking about a PAST date")
    from src import vocab as _v
    _, ct = drive({"operation": "keywords", "mode": "discover",
                   "endDate": "2025-10-01", "includeRelated": False,
                   "includeImages": False})
    disc = next(v for p_, v in ct.all_calls if p_ == "/top_trends_filtered/")
    check("customer endDate reaches discovery (the seasonal time machine)",
          disc["endDate"] == "2025-10-01", disc["endDate"])
    _, cs2 = drive({"operation": "shopping", "verticals": ["1042"],
                    "drillTopN": 0, "endDate": "2026-06-01", "chartDays": 60})
    ts2 = next(v for p_, v in cs2.all_calls if "/top/" in p_)
    check("shopping honours a past endDate", ts2["end_date"] == "2026-06-01")
    m2 = next(v for p_, v in cs2.all_calls if "/metrics/" in p_)
    check("chartDays is no longer hardcoded at 180", m2["days"] == 60, m2["days"])
    recs, _ = drive({"operation": "radar", "endDate": "2026-07-01"})
    check("the record's _meta.end_date reports the date ASKED for",
          recs[0].data["_meta"]["end_date"] == "2026-07-01")
    check("omitting endDate still uses Pinterest's newest settled date",
          drive({"operation": "radar"})[0][0].data["_meta"]["end_date"] == "2026-08-14")
    for bad, ep, why in [("2026-12-01", "discover", "after the newest settled date"),
                         ("2024-01-01", "metrics", "past the ~1 year limit"),
                         ("01/10/2025", "discover", "not YYYY-MM-DD")]:
        try:
            _v.history_date(bad, "2026-08-14", endpoint=ep)
            check(f"endDate {bad} refused ({why})", False)
        except _v.InvalidParam:
            check(f"endDate {bad} refused - {why}", True)
    # Measured 2026-08-19, correcting doc S3.12's "far-past" claim: -365d
    # returns terms, -400d returns 200 with an EMPTY LIST. A silent empty reads
    # as "nothing was trending", so the cap is enforced rather than discovered.
    check("history reaches ~365d everywhere - discovery is NOT unbounded",
          _v.HISTORY_LIMIT_DAYS["discover"] == 365
          and _v.HISTORY_LIMIT_DAYS["metrics"] == 365)
    check("-365d is allowed",
          _v.history_date("2025-08-14", "2026-08-14", endpoint="discover")
          == "2025-08-14")
    try:
        _v.history_date("2025-07-09", "2026-08-14", endpoint="discover")
        check("-400d refused BEFORE the wire (it would answer 200 + empty)", False)
    except _v.InvalidParam as exc:
        check("-400d refused before the wire, naming the silent-empty risk",
              "EMPTY LIST" in str(exc) or "empty" in str(exc).lower())
    _, csnap = drive({"operation": "keywords", "mode": "discover",
                      "endDate": "2026-08-01", "includeRelated": False,
                      "includeImages": False})
    rsnap = [r for r in drive({"operation": "keywords", "mode": "discover",
                               "endDate": "2026-08-01", "includeRelated": False,
                               "includeImages": False})[0]]
    if rsnap:
        m = rsnap[0].data["_meta"]
        check("records carry BOTH the date asked for and the one Pinterest used",
              m.get("end_date_requested") == "2026-08-01" and "end_date" in m)

    print("\nthe form and the code cannot drift")
    schema = json.loads(pathlib.Path(".actor/input_schema.json")
                        .read_text(encoding="utf-8"))["properties"]
    reads = set()
    for f in ("src/scraper.py", "src/context.py", "src/main.py"):
        reads |= set(re.findall(r'task\.get\("([a-zA-Z]+)"',
                                pathlib.Path(f).read_text(encoding="utf-8")))
    check("every input the code accepts is offered in the form",
          not (reads - set(schema)), sorted(reads - set(schema)))
    check("every input the form offers is read by the code",
          not (set(schema) - reads), sorted(set(schema) - reads))

    print("\nH - history caps and date provenance (measured 2026-08-19)")
    # The shopping cap was 730 for the project's whole life. It was never
    # probed; it was copied from the `days`/`lookback_days` ceiling, which is a
    # different parameter. Measured: -257d returns rows, -260d returns HTTP 200
    # with an EMPTY LIST. The old value waved ~470 days of silent empties
    # through, and an empty shopping run reads as "nothing is trending here".
    check("shopping history cap is the measured 257, not the inherited 730",
          _v.HISTORY_LIMIT_DAYS["shopping"] == 257,
          _v.HISTORY_LIMIT_DAYS["shopping"])
    check("moments cap 730 is now measured (-730d ok, -800d -> HTTP 500)",
          _v.HISTORY_LIMIT_DAYS["moments"] == 730)
    try:
        _v.history_date("2025-07-10", "2026-08-14", endpoint="shopping")
        check("shopping -400d refused before the wire", False)
    except _v.InvalidParam as exc:
        check("shopping -400d refused before the wire, with the reason",
              "empty" in str(exc).lower(), str(exc)[:70])
    # ...but the same date is fine for moments. The caps genuinely DIFFER;
    # one constant standing in for all four endpoints is what caused this.
    check("moments still reaches -400d (the caps genuinely differ)",
          _v.history_date("2025-07-10", "2026-08-14", endpoint="moments")
          == "2025-07-10")

    # Only discovery echoes the date it used. Shopping and moment/metrics
    # return no date anywhere in the response, so reporting their end_date as
    # "Pinterest's date" was a claim we could not support.
    check("only discovery echoes its end_date back",
          _v.ECHOES_END_DATE["discover"] and not _v.ECHOES_END_DATE["shopping"]
          and not _v.ECHOES_END_DATE["moments"])
    for op in OPERATIONS:
        recs, _c = drive({"operation": op, "endDate": "2026-06-10"})
        if not recs:
            continue
        m = recs[0].data["_meta"]
        check(f"{op}: _meta carries end_date_requested (the guide promises it)",
              m.get("end_date_requested") == "2026-06-10",
              m.get("end_date_requested"))
        check(f"{op}: _meta says whether the date was echoed or only requested",
              m.get("end_date_basis") in ("echoed", "requested"),
              m.get("end_date_basis"))

    # A forecast running forward from a PAST endDate is HTTP 500 on the keyword
    # and shopping chart endpoints. This was a LIVE BUG, not a missing feature:
    # `endDate` shipped and crashed both operations for every historical date,
    # because the only thing verified at the time was the discovery call.
    check("keyword metrics cannot forecast from the past",
          _v.FORECAST_FROM_PAST["metrics"] is False)
    check("shopping metrics cannot forecast from the past",
          _v.FORECAST_FROM_PAST["shopping"] is False)
    check("moment metrics CAN — measured, so it keeps its forecast",
          _v.FORECAST_FROM_PAST["moments"] is True)
    days, note = _v.forecast_days(91, "2026-08-14", "2026-08-14",
                                  endpoint="metrics")
    check("no endDate -> forecast untouched", days == 91 and note is None)
    days, note = _v.forecast_days(91, "2026-08-10", "2026-08-14",
                                  endpoint="metrics")
    check("inside the current week -> forecast still requested", days == 91)
    days, note = _v.forecast_days(91, "2025-10-15", "2026-08-14",
                                  endpoint="metrics")
    check("past endDate -> forecast dropped rather than a 500", days == 0)
    check("...and the drop is EXPLAINED, not silent (absent != no forecast)",
          bool(note) and "500" in note, (note or "")[:60])
    days, note = _v.forecast_days(91, "2025-10-15", "2026-08-14",
                                  endpoint="moments")
    check("moments keeps its forecast at the same past date",
          days == 91 and note is None)


    print("\nI - the Date range dropdown, and the moment regions")
    # Pinterest shows ONE "Date range" control on the keyword page, the
    # product-category page and the moment view. We exposed it as three
    # differently-named inputs, so "past 1 year" meant knowing which operation
    # renamed it. `dateRange` is that one control.
    for label, expected in [("past_3_months", 90), ("past_6_months", 180),
                            ("past_1_year", 365), ("past_2_years", 730)]:
        check(f"dateRange {label} -> {expected} days",
              _v.date_range(label, 0) == expected)
    check("the on-screen wording is accepted verbatim",
          _v.date_range("Past 2 years", 0) == 730)
    check("a raw day count still works (per-operation inputs unaffected)",
          _v.date_range(365, 0) == 365)
    try:
        _v.date_range(2000, 0)
        check("over the ceiling is refused before the wire", False)
    except _v.InvalidParam as exc:
        # The message must name the CEILING, not the dropdown - 2000 is a
        # perfectly well-formed day count, it is just past 730.
        check("over the ceiling is refused, naming the real limit",
              "730" in str(exc) and "dropdown" not in str(exc), str(exc)[:70])
    try:
        _v.date_range("last week", 180)
        check("a bad range is refused", False)
    except _v.InvalidParam as exc:
        check("a bad range is refused, listing the real options",
              "past_1_year" in str(exc))
    # It has to reach all three traversals, not just the one it was written on.
    # Asserted on what is SENT, not on the record: the fake client replays a
    # fixed fixture, so a series length here would prove nothing either way.
    for op, extra, path, key in [
            ("keywords", {"mode": "exact", "queries": ["mascara"],
                          "includeRelated": False, "includeImages": False},
             "/metrics/", "days"),
            ("shopping", {"verticals": ["1042"], "drillTopN": 0,
                          "includeProducts": False},
             "product_categories/metrics", "days"),
            ("moments", {"aggregation": "weekly"},
             "moment/metrics", "lookback_days")]:
        task = {"operation": op, "dateRange": "past_2_years"}
        task.update(extra)
        _recs, c = drive(task)
        sent = [v for pth, v in c.all_calls if path in pth]
        check(f"{op}: dateRange reaches the wire as {key}=730",
              bool(sent) and sent[0].get(key) == 730,
              sent[0].get(key) if sent else "endpoint never called")

    # The per-operation input is the more specific instruction and must win.
    _recs, c = drive({"operation": "keywords", "mode": "exact",
                      "queries": ["mascara"], "dateRange": "past_2_years",
                      "days": 90, "includeRelated": False,
                      "includeImages": False})
    sent = next(v for pth, v in c.all_calls if pth == "/metrics/")
    check("an explicit days= overrides dateRange", sent["days"] == 90,
          sent["days"])

    # 13 regions ran, returned nothing, and said nothing.
    check("every region with zero moments is declared (15, measured)",
          len(_v.REGIONS_WITHOUT_MOMENTS) == 15,
          len(_v.REGIONS_WITHOUT_MOMENTS))
    check("...and the 17 that DO have moments account for the rest",
          len(_v.MOMENT_COUNTS) + len(_v.REGIONS_WITHOUT_MOMENTS)
          == len(_v.REGIONS))
    for region in ("KR", "TR", "TH", "PH", "SA"):
        try:
            _v.region(region, capability="moments")
            check(f"{region} moments refused rather than silently empty", False)
        except _v.InvalidParam:
            check(f"{region} moments refused rather than silently empty", True)
    check("US and AU+NZ still allowed (17 regions do have moments)",
          _v.region("US", capability="moments") == "US"
          and _v.region("AU+NZ", capability="moments") == "AU+NZ")

    # "When do I launch" is the whole point of this operation, and the answer
    # shipped as `1795824000000`. The epoch value stays — it sorts and diffs —
    # but a customer must not have to convert the one field they opened the
    # record to read. NOTE: the doc-drift guard does NOT cover these, because
    # it only walks top-level and _meta keys; nested fields need real asserts.
    mrec, _c = drive({"operation": "moments", "drill": False})
    peaks = [r.data.get("next_peak") for r in mrec if r.data.get("next_peak")]
    # The promise is PAIRING — wherever an epoch is emitted, a readable date is
    # emitted beside it — NOT that every moment has every field. This used to
    # require `takeoff_at` on all of them, and broke on 2026-08-25 when
    # `valentines day` came back with `takeoff_at: None`. Pinterest simply has
    # no takeoff for that moment; the parser reporting None is the codebase's
    # own "absent is not zero" rule working correctly, so the test was wrong,
    # not the code. Demanding a field Pinterest may omit would have pushed
    # someone toward fabricating one.
    check("a moment's peak carries a readable date beside the epoch",
          bool(peaks) and all(p.get("peak_date") for p in peaks), len(peaks))
    check("...and takeoff is paired too WHEN Pinterest supplies one",
          all(bool(p.get("takeoff_date")) == bool(p.get("takeoff_at"))
              for p in peaks),
          [(p.get("takeoff_at"), p.get("takeoff_date")) for p in peaks
           if bool(p.get("takeoff_date")) != bool(p.get("takeoff_at"))])
    check("...formatted YYYY-MM-DD",
          all(len(p["peak_date"]) == 10 and p["peak_date"][4] == "-"
              for p in peaks), peaks[0].get("peak_date") if peaks else None)
    check("...and the epoch and the date describe the same instant",
          all(_dt.datetime.fromtimestamp(p["peak_at"] / 1000, _dt.timezone.utc)
              .date().isoformat() == p["peak_date"] for p in peaks))
    check("next_occurrence gets the same treatment",
          all(r.data.get("next_occurrence_date") for r in mrec
              if r.data.get("next_occurrence_at")))

    # Our records carry the wire value; Pinterest's screen shows another word.
    check("cooldown reads 'Cooling' on Pinterest's own screen",
          _v.PHASE_LABELS["cooldown"] == "Cooling")
    check("off_season and ended BOTH read 'Frozen'",
          _v.PHASE_LABELS["off_season"] == "Frozen"
          and _v.PHASE_LABELS["ended"] == "Frozen")
    mrecs, _c = drive({"operation": "moments", "drill": False})
    check("every moment carries both the wire phase and the UI label",
          all(r.data.get("phase") and r.data.get("phase_label")
              for r in mrecs), len(mrecs))
    check("an unknown phase passes through instead of being guessed",
          _v.PHASE_LABELS.get("peaking", "peaking") == "peaking")


    print("\nJ - the crawl: following Pinterest's own navigation")
    from src.crawl import Crawler, ENTRY_POINTS
    check("every entry point is one of Pinterest's own pages",
          set(ENTRY_POINTS) == {"overview", "shopping", "search", "moments"})
    try:
        drive({"operation": "crawl", "crawlFrom": "nowhere"})
        check("an unknown entry page is refused", False)
    except _v.InvalidParam as exc:
        check("an unknown entry page is refused, listing the real ones",
              all(e in str(exc) for e in ENTRY_POINTS))

    # depth 0 is the entry page and nothing else - the cheapest useful crawl.
    recs0, c0 = drive({"operation": "crawl", "crawlFrom": "shopping",
                       "crawlDepth": 0, "maxRequests": 60})
    kinds0 = {r.data["_meta"]["crawl_node_kind"] for r in recs0} - {"summary"}
    check("depth 0 stays on the entry page", kinds0 == {"category"}, kinds0)

    # depth 1 follows the "Search queries" chips a user would click.
    recs1, c1 = drive({"operation": "crawl", "crawlFrom": "shopping",
                       "crawlDepth": 1, "maxRequests": 60})
    kinds1 = {r.data["_meta"]["crawl_node_kind"] for r in recs1} - {"summary"}
    check("depth 1 follows the category's search-query chips into keywords",
          kinds1 == {"category", "keyword"}, kinds1)
    check("...and that is strictly more than depth 0 found",
          len(recs1) > len(recs0), f"{len(recs0)} -> {len(recs1)}")

    # The bug this test exists for: depth 2 once returned depth 1's result
    # exactly, because the level was enriched WITHOUT the `related` edges the
    # next level needed. It reported success and stopped.
    deep = {}
    for d in (1, 2):
        recs, _c = drive({"operation": "crawl", "crawlFrom": "overview",
                          "crawlDepth": d, "maxRequests": 200})
        deep[d] = max((r.data["_meta"]["crawl_depth"] for r in recs),
                      default=0)
    check("crawlDepth=2 actually reaches depth 2 (it silently did not)",
          deep[2] > deep[1], deep)

    # Cost must scale with DEPTH, not with node count - the whole design.
    check("a 74-node crawl costs tens of requests, not hundreds",
          len(c1.all_calls) < len(recs1),
          f"{len(recs1)} nodes / {len(c1.all_calls)} requests")

    # A crawl is the one operation that can run away. The budget caps what is
    # FOLLOWED; the entry page always loads in full, because half an entry page
    # is a wrong answer rather than a cheap one.
    recsb, cb = drive({"operation": "crawl", "crawlFrom": "overview",
                       "crawlDepth": 3, "maxRequests": 8})
    summary = recsb[-1].data
    check("the last record of a crawl is always its summary",
          summary.get("crawl_summary") is True)
    check("an impossible budget follows NOTHING (entry page only)",
          summary["nodes_by_kind"].get("keyword") is None,
          summary["nodes_by_kind"])
    # This is the point of the summary: a streamed record emitted early cannot
    # know the crawl was cut short later, so truncation has to be reported
    # somewhere written at the end.
    check("...and the summary SAYS the dataset is partial",
          summary["truncated"] is True)
    check("...and says how much was left unfollowed",
          summary["edges_unfollowed"] > 0, summary["edges_unfollowed"])
    check("the entry page's floor cost is reported, not hidden",
          summary["entry_cost"] > summary["request_budget"],
          f"floor {summary['entry_cost']} vs budget {summary['request_budget']}")
    # A budget that IS enough must not claim truncation.
    recsok, _c = drive({"operation": "crawl", "crawlFrom": "shopping",
                        "crawlDepth": 1, "maxRequests": 200})
    check("a crawl that completed does NOT report truncation",
          recsok[-1].data["truncated"] is False)

    # A crawl still honours the seed page's own filters. These were hardcoded,
    # so `verticals: ["1042"]` was accepted and silently ignored.
    recsv, _c = drive({"operation": "crawl", "crawlFrom": "shopping",
                       "crawlDepth": 0, "verticals": ["1042"],
                       "maxRequests": 60})
    vids = {r.data.get("vertical_id") for r in recsv
            if r.data["_meta"]["crawl_node_kind"] == "category"}
    check("a crawl honours `verticals` instead of silently ignoring it",
          vids == {"1042"}, vids)

    # Every node has to explain why it is in the dataset.
    for r in recs1[:-1]:
        m = r.data["_meta"]
        if not (m.get("crawl_path") and m.get("crawl_entry")
                and m.get("crawl_node_kind") is not None):
            check("every crawled node carries its navigation trail", False,
                  m.get("crawl_path"))
            break
    else:
        check("every crawled node carries its navigation trail", True)
    check("the trail names the hops taken",
          any(" > " in r.data["_meta"]["crawl_path"] for r in recs1[:-1]))

    # Mixed-kind dataset: ids must not collide across kinds.
    ids = [r.id for r in recs1]
    check("node ids are namespaced by kind (a keyword != a category)",
          all(i and ":" in i for i in ids) and len(set(ids)) == len(ids),
          f"{len(ids)} ids, {len(set(ids))} unique")


    print("\nK - a run must never outlive its lease")
    # THE ONLY RULE THAT PROTECTS THE ACCOUNTS THEMSELVES. The vault leases a
    # profile with SET NX and a 900s expiry so a crashed run cannot hold one
    # forever — but that same expiry means a run LONGER than the TTL loses its
    # lease mid-flight, and the vault then hands the same Pinterest session to
    # another run. Two runs, one account, two IPs. `maxRequests` had NO maximum
    # at all, so it was reachable straight from customer input.
    check("K1 a request budget cap exists at all",
          isinstance(_v.MAX_REQUESTS_PER_RUN, int))
    check("K2 ...and it leaves margin below the lease TTL",
          _v.MAX_REQUESTS_PER_RUN * 2 <= 900,
          f"{_v.MAX_REQUESTS_PER_RUN} x 2s/request vs 900s")
    check("K3 an ordinary budget passes untouched",
          _v.request_budget(60) == 60 and _v.request_budget(400) == 400)
    try:
        _v.request_budget(5000)
        check("K4 a budget that could outlive the lease is REFUSED", False)
    except _v.InvalidParam as exc:
        # Refused, not silently clamped: a customer who asked for 5000 and
        # quietly got 400 would read the short result as "Pinterest had no
        # more" — the wrong answer wearing the right shape.
        check("K4 a budget that could outlive the lease is REFUSED", True)
        check("K5 ...and the message explains the ACCOUNT risk, not just a limit",
              "two runs on one session" in str(exc) or "another run" in str(exc),
              str(exc)[:70])
    for bad in (0, -5, "abc", None):
        try:
            _v.request_budget(bad)
            check(f"K6 {bad!r} is refused", False)
        except _v.InvalidParam:
            check(f"K6 {bad!r} is refused", True)
    # And it must be enforced on the path a customer actually reaches.
    try:
        drive({"operation": "crawl", "maxRequests": 5000})
        check("K7 the cap is enforced through the dispatcher", False)
    except _v.InvalidParam:
        check("K7 the cap is enforced through the dispatcher", True)
    schema_max = schema["maxRequests"].get("maximum")
    check("K8 the form advertises the same cap the code enforces",
          schema_max == _v.MAX_REQUESTS_PER_RUN, schema_max)


    print("\nthe reference and the form cannot drift either")
    api_md = pathlib.Path("docs/API.md").read_text(encoding="utf-8")
    undocumented = [k for k in schema if "`" + k + "`" not in api_md]
    check("every input the form offers is documented in docs/API.md",
          not undocumented, undocumented)

    # Same guard pointing the other way. Every field we EMIT has to appear in
    # its operation's output table — an undocumented field is one a customer
    # never reads, so the work that produced it was wasted.
    out = api_md[api_md.index("## Output"):]
    sections = {}
    for chunk in re.split(r"\n### ", out)[1:]:
        sections[chunk.split("\n", 1)[0].strip().split(" ")[0].strip("`")] = chunk
    meta_section = sections.get("_meta", "")
    for op in OPERATIONS:
        recs, _c = drive({"operation": op})
        emitted, emitted_meta = set(), set()
        for r in recs:
            emitted |= {k for k in r.data if k not in ("_meta", "_demo")}
            emitted_meta |= set(r.data.get("_meta") or {})
        # `crawl` deliberately RE-EMITS the other operations' record shapes,
        # so its fields are documented in their own sections. Checking it
        # against the whole Output chapter still catches a genuinely
        # undocumented field, without demanding 38 duplicated rows.
        body = out if op == "crawl" else sections.get(op, "")
        missing = sorted(k for k in emitted if "`" + k + "`" not in body)
        check(f"{op}: every emitted field is in its output table", not missing,
              missing)
        missing_meta = sorted(k for k in emitted_meta
                              if "`" + k + "`" not in meta_section)
        check(f"{op}: every _meta key is in the _meta table", not missing_meta,
              missing_meta)


    print("\ncost of a zero-input run (customers pay per request)")
    for op in OPERATIONS:
        recs, c = drive({"operation": op})
        n = len(c.all_calls)
        print(f"       {op:<10} {len(recs):>4} records / ~{n:>2} requests")
        check(f"{op}: default run stays under 100 requests", n < 100, n)

    print("\nF4 — a capped run says whether it is a slice or the whole answer")
    for op in OPERATIONS:
        full, _ = drive({"operation": op})
        if len(full) < 2:
            continue                      # nothing to cut; F4 says nothing here

        cut, _, ctx = drive_ctx({"operation": op, "maxRecords": 1})
        check(f"{op}: maxRecords=1 yields exactly 1 record", len(cut) == 1, len(cut))
        # The point of F4. Without this the customer cannot tell a cut answer
        # from "Pinterest has nothing" — the failure mode this repo is built
        # against. A count alone never carries that; a flag does.
        check(f"{op}: a cut run is FLAGGED as truncated", ctx.truncated is True)

        exact, _, ctx_exact = drive_ctx({"operation": op, "maxRecords": len(full)})
        # The other half, and the reason for peeking rather than counting:
        # a cap that happens to equal the total is a COMPLETE answer. Flagging
        # it would cry wolf on every fully-served capped run.
        check(f"{op}: maxRecords == total is NOT called truncated",
              len(exact) == len(full) and ctx_exact.truncated is False,
              f"{len(exact)}/{len(full)} truncated={ctx_exact.truncated}")

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
