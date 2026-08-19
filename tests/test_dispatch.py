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


class Ctx:
    def __init__(self, task):
        self.session = None
        self.task = task
        self.cache = None
        self.force_refresh = False


def drive(task):
    """Run the dispatcher against fixtures, returning (records, client)."""
    client = EveryFixture()
    original = sc.TrendsClient
    sc.TrendsClient = lambda *a, **k: client
    try:
        return list(run(Ctx(task), task)), client
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
        body = sections.get(op, "")
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

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
