"""Re-measure how far back each endpoint's `endDate` actually reaches.

Run this when `vocab.HISTORY_LIMIT_DAYS` is suspected stale — Pinterest's
windows move, and one of them (shopping) has an UNDETERMINED shape: we cannot
tell a fixed data-start floor from a rolling window without observations weeks
apart. This probe is how you take the second observation.

    .venv/Scripts/python.exe -m probes.history_caps

Why this exists rather than a test: the failure it guards is not an exception.
Past its window each endpoint answers **HTTP 200 with an empty list** — a run
that looks successful and says "nothing was trending". A wrong cap here is
invisible everywhere else, which is why it gets its own probe and why the
measured numbers are quoted with their dates in `vocab.py`.

Measured 2026-08-19 from a 2026-08-14 baseline:

    discover   -365d  3 terms   |  -400d  0 terms      (silent empty)
    moments    -730d  series    |  -800d  HTTP 500     (a real error)
    shopping   -257d  5 rows    |  -260d  0 rows       (silent empty)

⚠️ The shopping boundary is ragged: 2025-11-28 answers, 2025-11-29 does not,
2025-11-30 answers again. Inside the window there are no gaps (21 consecutive
days in 2026-05 all answered), so it is an edge effect. Take the last
*reliable* day, never the last day that happened to answer.
"""
import datetime as dt

from src import parsers, vocab
from src.session import leased_session
from src.transport import TrendsClient, TrendsAPIError


def _days_back(newest, n):
    return str(newest - dt.timedelta(days=n))


def _discover(client, region, datestr):
    found = parsers.parse_discover(client.style_b(
        "/top_trends_filtered/",
        {"country": region, "endDate": datestr, "trendsPreset": 1,
         "numTermsToReturn": 3, "shouldMock": "false"}, kind=None))
    return len(found["terms"])


def _shopping(client, region, datestr):
    top = parsers.parse_top_categories(client.style_a(
        f"/ads/v4/trends/shopping/product_categories/top/{region}",
        {"event": "OUTBOUND_CLICK", "ranking_method": "GROWTH",
         "end_date": vocab.end_date(datestr),
         "parent_product_categories": vocab.verticals_one_per_call(["1250"]),
         "limit": 5, "order_by": "RELATIVE_VOLUME", "order": "DESC"},
        kind=None))
    return len(top["categories"])


def _moments(client, region, datestr):
    data = parsers.parse_moment_metrics(client.style_a(
        f"/ads/v4/trends/moment/metrics/{region}",
        {"moments": ["halloween"], "end_date": datestr,
         "aggregation_level": "weekly", "lookback_days": 90,
         "predicted_days": 7, "interest_limit": 6,
         "normalize_against_group": False}, kind=None))
    return len((data.get("halloween") or {}).get("series") or [])


PROBES = {"discover": _discover, "shopping": _shopping, "moments": _moments}


def find_boundary(fn, client, region, newest, ceiling=800):
    """Binary-search the last day that still returns rows.

    An exception counts as "no data" the same as an empty list — from the
    customer's side both mean the date is out of range, and the whole point is
    that only one of them is visible.
    """
    def works(n):
        try:
            return fn(client, region, _days_back(newest, n)) > 0
        except TrendsAPIError:
            return False

    if works(ceiling):
        return ceiling, None                      # reaches at least this far
    lo, hi = 1, ceiling
    if not works(lo):
        return None, lo                           # nothing works at all
    while hi - lo > 3:
        mid = (lo + hi) // 2
        print(f"      probing -{mid}d …", flush=True)
        if works(mid):
            lo = mid
        else:
            hi = mid
    return lo, hi


def main(region="US"):
    with leased_session() as (session, identity):
        client = TrendsClient(session)
        newest = dt.date.fromisoformat(client.bootstrap())
        print(f"baseline /latest_available_date/ = {newest}  "
              f"(profile {identity.profile_id})\n")

        print(f"{'endpoint':<10} {'configured':>10} {'measured':>9}  verdict")
        print("-" * 64)
        drifted = []
        for name, fn in PROBES.items():
            last_ok, first_empty = find_boundary(fn, client, region, newest)
            configured = vocab.HISTORY_LIMIT_DAYS[name]
            if last_ok is None:
                verdict = "NO DATA AT ALL — session or params broken"
            elif last_ok >= configured:
                verdict = f"ok (first empty at -{first_empty}d)" if first_empty \
                    else "ok (reaches the probe ceiling)"
            else:
                verdict = (f"⚠️ DRIFT — configured {configured}d now returns a "
                           f"SILENT EMPTY; lower it to {last_ok}")
                drifted.append(name)
            print(f"{name:<10} {configured:>10} {str(last_ok):>9}  {verdict}")

        print()
        if drifted:
            print(f"⚠️ {len(drifted)} cap(s) too permissive: {', '.join(drifted)}.")
            print("   Too permissive is the dangerous direction — it lets a date")
            print("   through that returns 200 + [], which reads as 'nothing was")
            print("   trending'. Update vocab.HISTORY_LIMIT_DAYS and quote today's")
            print("   date in the comment, as the existing entries do.")
            return 1

        print("All caps still hold. If shopping's measured value has grown by")
        print("roughly the number of days since 2026-08-19, its floor is a FIXED")
        print("data-start date, not a rolling window — record that in vocab.py,")
        print("it is currently marked UNDETERMINED.")
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
