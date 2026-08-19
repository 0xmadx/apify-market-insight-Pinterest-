"""Parameter matrix: test what each param ACTUALLY does, not what the doc claims.

    .venv/Scripts/python.exe -m probes.param_matrix              # everything
    .venv/Scripts/python.exe -m probes.param_matrix --group E    # one group
    .venv/Scripts/python.exe -m probes.param_matrix --list       # what exists

`probe_endpoints.py` answers "does this endpoint still respond". This answers the
next question: **is every documented parameter claim still true?** Each case
declares the doc's claim up front, fires one real request, and reports
MATCH / MISMATCH / NEW against it. A MISMATCH is not a test failure — it is
news, and the doc changes in the same commit (skill: "the wire wins").

Transport is reused from probe_endpoints (curl_cffi + one leased vault identity
for the whole sweep) so this cannot drift from what the real client does.
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, ".")

from probes.probe_endpoints import (BASE, HANDLER, strip_pii, style_a,  # noqa: E402
                                     style_b, unwrap)
from src.session import classify, leased_session  # noqa: E402

OUT = pathlib.Path("probes/results/params")
DELAY = 1.5  # no rate-limit headers exist on this API — back off blindly

CAT = "1408"          # Seasonal & holiday decorations — used across shopping cases
VERTICAL = "1181"     # an L1 vertical that returns data
L3_ID = "1311"        # Mascaras — an L3 id, for the wrong-level case


# --------------------------------------------------------------- result model

class Case:
    """One parameter probe: a claim, a request, and the verdict against it."""

    def __init__(self, group, name, claim, call, judge):
        self.group = group
        self.name = name
        self.claim = claim
        self.call = call        # (session, date) -> response
        self.judge = judge      # (response, data, error) -> (verdict, detail)


def http_and_error(response, style):
    """Both styles' error surface, normalised. Style A hides errors in a 200."""
    try:
        parsed = response.json()
    except Exception:
        return None, response.text[:160]
    data, error = unwrap(parsed, style)
    if style == "B":
        data = parsed
        if isinstance(parsed, dict):
            error = parsed.get("message") or parsed.get("error") or error
    return data, error


def rows_of(data, *path):
    """Dig a list out of a payload, tolerating absence. Absent is not zero."""
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, list) else None


# ------------------------------------------------------------------ the cases

def build_cases(date):
    cases = []
    A = lambda p, d: (lambda s: style_a(s, p, d))          # noqa: E731
    B = lambda p, q: (lambda s: style_b(s, p, q))          # noqa: E731

    # --- GROUP A: the `event` enum on shopping top/ -------------------------
    # Doc §3.7: validator ALLOWS 7 values but only 3 work; the rest 500.
    for event in ["OUTBOUND_CLICK", "ENGAGEMENT", "SAVE",
                  "IMPRESSION", "CLICK", "LONG_CLICK", "SEARCH"]:
        works = event in ("OUTBOUND_CLICK", "ENGAGEMENT", "SAVE")
        cases.append(Case(
            "A", f"top/ event={event}",
            "200 with rows" if works else "500 (accepted by validator, fails server-side)",
            A(f"/ads/v4/trends/shopping/product_categories/top/US",
              {"event": event, "ranking_method": "GROWTH", "end_date": date,
               "parent_product_categories": [VERTICAL], "limit": 5,
               "order_by": "RELATIVE_VOLUME", "order": "DESC"}),
            lambda r, d, e, works=works: _judge_rows(r, d, e, works,
                                                     "ordered_values")))

    # --- GROUP B: ceilings — the exact number the doc names -----------------
    cases += [
        Case("B", "top/ limit=522 (documented max)", "200",
             A("/ads/v4/trends/shopping/product_categories/top/US",
               {"event": "OUTBOUND_CLICK", "ranking_method": "GROWTH",
                "end_date": date, "parent_product_categories": [VERTICAL],
                "limit": 522, "order_by": "RELATIVE_VOLUME", "order": "DESC"}),
             lambda r, d, e: _judge_ok(r, d, e)),
        Case("B", "top/ limit=1000 (over ceiling)",
             "400 'limit' is too large: 1000 > 522",
             A("/ads/v4/trends/shopping/product_categories/top/US",
               {"event": "OUTBOUND_CLICK", "ranking_method": "GROWTH",
                "end_date": date, "parent_product_categories": [VERTICAL],
                "limit": 1000, "order_by": "RELATIVE_VOLUME", "order": "DESC"}),
             lambda r, d, e: _judge_error(r, d, e, "522")),
        Case("B", "metrics predicted_days=91 (max)", "200",
             B("/metrics/", {"terms": "nails", "country": "US",
                             "end_date": date, "days": 365, "aggregation": 2,
                             "predicted_days": 91, "shouldMock": "false"}),
             lambda r, d, e: _judge_ok(r, d, e)),
        Case("B", "metrics predicted_days=180 (over)", "400 too large: 180 > 91",
             B("/metrics/", {"terms": "nails", "country": "US",
                             "end_date": date, "days": 365, "aggregation": 2,
                             "predicted_days": 180, "shouldMock": "false"}),
             lambda r, d, e: _judge_error(r, d, e, "91")),
        Case("B", "top_trends numTermsToReturn=100 (max)", "200",
             B("/top_trends_filtered/", {"country": "US", "endDate": date,
                                         "trendsPreset": 3,
                                         "numTermsToReturn": 100,
                                         "shouldMock": "false"}),
             lambda r, d, e: _judge_rows(r, d, e, True, "values")),
        Case("B", "top_trends numTermsToReturn=500 (over)", "400",
             B("/top_trends_filtered/", {"country": "US", "endDate": date,
                                         "trendsPreset": 3,
                                         "numTermsToReturn": 500,
                                         "shouldMock": "false"}),
             lambda r, d, e: _judge_error(r, d, e, None)),
        Case("B", "moment/metrics interest_limit=24 (max)", "200",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "weekly", "lookback_days": 365,
                "predicted_days": 91, "interest_limit": 24}),
             lambda r, d, e: _judge_ok(r, d, e)),
        Case("B", "moment/metrics interest_limit=50 (over)",
             "400 too large: 50 > 24",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "weekly", "lookback_days": 365,
                "predicted_days": 91, "interest_limit": 50}),
             lambda r, d, e: _judge_error(r, d, e, "24")),
        Case("B", "moment/metrics lookback_days=1095 (over 730)",
             "400 too large: 1095 > 730",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "weekly", "lookback_days": 1095,
                "predicted_days": 0, "interest_limit": 6}),
             lambda r, d, e: _judge_error(r, d, e, "730")),
    ]

    # --- GROUP C: enums that must be exact ---------------------------------
    cases += [
        Case("C", "metrics aggregation=2 (only valid)", "200",
             B("/metrics/", {"terms": "nails", "country": "US",
                             "end_date": date, "days": 365, "aggregation": 2,
                             "shouldMock": "false"}),
             lambda r, d, e: _judge_ok(r, d, e)),
        Case("C", "metrics aggregation=1", "400",
             B("/metrics/", {"terms": "nails", "country": "US",
                             "end_date": date, "days": 365, "aggregation": 1,
                             "shouldMock": "false"}),
             lambda r, d, e: _judge_error(r, d, e, None)),
        Case("C", "moment/metrics aggregation_level=daily",
             "200 — THE ONLY daily source in the API (~456 pts vs 66 weekly)",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "daily", "lookback_days": 365,
                "predicted_days": 91, "interest_limit": 6}),
             lambda r, d, e: _judge_daily(r, d, e)),
        Case("C", "moment/metrics aggregation_level=hourly", "400",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "hourly", "lookback_days": 90,
                "predicted_days": 0, "interest_limit": 6}),
             lambda r, d, e: _judge_error(r, d, e, None)),
        Case("C", "moment/metrics monthly + predicted_days=91",
             "400 — 91 does not evenly divide into monthly",
             A("/ads/v4/trends/moment/metrics/US",
               {"moments": ["halloween"], "end_date": date,
                "aggregation_level": "monthly", "lookback_days": 730,
                "predicted_days": 91, "interest_limit": 6}),
             lambda r, d, e: _judge_error(r, d, e, None)),
        Case("C", "top_trends trendsPreset=5 (invalid)", "400 — only 1-4",
             B("/top_trends_filtered/", {"country": "US", "endDate": date,
                                         "trendsPreset": 5,
                                         "numTermsToReturn": 10,
                                         "shouldMock": "false"}),
             lambda r, d, e: _judge_error(r, d, e, None)),
    ]

    # --- GROUP D: case sensitivity (silent empties) ------------------------
    cases += [
        Case("D", "prefix_match query=hallow (lowercase)", "200 with 10 rows",
             B("/prefix_match/", {"query": "hallow", "country": "US"}),
             lambda r, d, e: _judge_listlen(r, d, e, expect_nonempty=True)),
        Case("D", "prefix_match query=HALLOW (uppercase)",
             "200 + EMPTY — case-sensitive, silent",
             B("/prefix_match/", {"query": "HALLOW", "country": "US"}),
             lambda r, d, e: _judge_listlen(r, d, e, expect_nonempty=False)),
        Case("D", "prefix_match query=zzzqqq (no match)", "200 + empty, not an error",
             B("/prefix_match/", {"query": "zzzqqq", "country": "US"}),
             lambda r, d, e: _judge_listlen(r, d, e, expect_nonempty=False)),
    ]

    # --- GROUP E: silent empties that are NOT errors -----------------------
    cases += [
        Case("E", "top_products event=OUTBOUND_CLICK", "200 with products",
             A("/ads/v4/trends/shopping/product_categories/top_products",
               {"product_category_id": CAT, "region": "US",
                "event": "OUTBOUND_CLICK"}),
             lambda r, d, e: _judge_rows(r, d, e, True, "top_products")),
        Case("E", "top_products event=SAVE",
             "200 + [] — WRONG PARAM, silent (not an error)",
             A("/ads/v4/trends/shopping/product_categories/top_products",
               {"product_category_id": CAT, "region": "US", "event": "SAVE"}),
             lambda r, d, e: _judge_rows(r, d, e, False, "top_products")),
        Case("E", "top/ with an L3 id (wrong level)",
             "200 + 0 rows — WRONG LEVEL, silent",
             A("/ads/v4/trends/shopping/product_categories/top/US",
               {"event": "OUTBOUND_CLICK", "ranking_method": "GROWTH",
                "end_date": date, "parent_product_categories": [L3_ID],
                "limit": 20, "order_by": "RELATIVE_VOLUME", "order": "DESC"}),
             lambda r, d, e: _judge_rows(r, d, e, False, "ordered_values")),
        Case("E", "editorial region=FR",
             "200 + 0 items — REGION UNSUPPORTED, silent",
             A("/ads/v4/trends/editorial/content/FR", {}),
             lambda r, d, e: _judge_listlen(r, d, e, expect_nonempty=False)),
        Case("E", "editorial region=US (control)", "200 with 6 items",
             A("/ads/v4/trends/editorial/content/US", {}),
             lambda r, d, e: _judge_listlen(r, d, e, expect_nonempty=True)),
    ]

    # --- GROUP H: shouldMock — fake data behind a 200 ----------------------
    cases += [
        Case("H", "metrics shouldMock=true",
             "200 with FAKE 2019 data — never use",
             B("/metrics/", {"terms": "nails", "country": "US",
                             "end_date": date, "days": 365, "aggregation": 2,
                             "shouldMock": "true"}),
             lambda r, d, e: _judge_mock(r, d, e)),
    ]

    return cases


# ----------------------------------------------------------------- judgements

def _judge_ok(r, d, e):
    if r.status_code == 200 and not e:
        return "MATCH", "200"
    return "MISMATCH", f"{r.status_code} {e or ''}"[:120]


def _judge_error(r, d, e, must_contain):
    if r.status_code == 200 and not e:
        return "MISMATCH", "expected an error, got 200 — the ceiling may have moved"
    detail = str(e or r.status_code)[:120]
    if must_contain and must_contain not in str(e or ""):
        return "NEW", f"errored, but not with '{must_contain}': {detail}"
    return "MATCH", detail


def _judge_rows(r, d, e, expect_rows, key):
    rows = rows_of(d, key) if isinstance(d, dict) else None
    if e or r.status_code != 200:
        return ("MATCH" if not expect_rows else "MISMATCH",
                f"{r.status_code} {str(e)[:100]}")
    n = len(rows) if rows is not None else None
    if expect_rows:
        return ("MATCH", f"200, {n} rows") if n else ("MISMATCH", f"200 but {n} rows")
    return ("MATCH", f"200, {n} rows (silent empty confirmed)") if not n else \
           ("MISMATCH", f"200 with {n} rows — expected empty")


def _judge_listlen(r, d, e, expect_nonempty):
    items = d if isinstance(d, list) else None
    if items is None:
        return "MISMATCH", f"not a list: {type(d).__name__} {str(e)[:80]}"
    n = len(items)
    if expect_nonempty:
        return ("MATCH", f"{n} items") if n else ("MISMATCH", "empty")
    return ("MATCH", f"{n} items (silent empty confirmed)") if not n else \
           ("MISMATCH", f"{n} items — expected empty")


def _judge_daily(r, d, e):
    vals = None
    if isinstance(d, dict) and d.get("moments"):
        vals = (d["moments"][0].get("moment") or {}).get("daily_values")
    if not vals:
        return "MISMATCH", f"no daily_values ({r.status_code} {str(e)[:80]})"
    n = len(vals)
    return ("MATCH" if n > 300 else "NEW"), f"{n} points (weekly baseline is ~66)"


def _judge_mock(r, d, e):
    if r.status_code != 200:
        return "NEW", f"{r.status_code} — mock now rejected rather than faked"
    counts = (d[0].get("counts") if isinstance(d, list) and d else None) or []
    first = counts[0].get("date") if counts else None
    return ("MATCH" if first and first.startswith("2019") else "NEW"), \
           f"200, first date {first} (doc says fake 2019 data)"


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", help="run only this group (A/B/C/D/E/H)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for c in build_cases("2026-08-14"):
            print(f"  {c.group}  {c.name}")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    with leased_session() as (session, identity):
        print(f"profile: {identity.profile_id}\n")

        r = style_b(session, "/latest_available_date/")
        date = r.json().get("date")
        print(f"end_date = {date}  (never today() — data lags)\n")
        if not date:
            print("no date — cannot build cases")
            return 1

        cases = [c for c in build_cases(date)
                 if not args.group or c.group == args.group.upper()]
        print(f"{len(cases)} cases\n")

        current = None
        for case in cases:
            if case.group != current:
                current = case.group
                print(f"--- GROUP {current} ---")
            style = "A" if "ApiResource" in str(case.call) or True else "B"
            try:
                response = case.call(session)
            except Exception as exc:
                results.append((case, "ERROR", f"{type(exc).__name__}: {exc}"))
                print(f"  ERROR     {case.name}")
                time.sleep(DELAY)
                continue

            # style is decided by what came back, not by guessing
            raw = None
            try:
                raw = response.json()
            except Exception:
                pass
            style = "A" if isinstance(raw, dict) and "resource_response" in raw else "B"
            data, error = http_and_error(response, style)

            verdict, detail = case.judge(response, data, error)
            results.append((case, verdict, detail))
            print(f"  {verdict:<9} {case.name}")
            if verdict != "MATCH":
                print(f"      claim : {case.claim}")
                print(f"      actual: {detail}")

            slug = case.name.replace("/", "_").replace(" ", "_")[:70]
            if raw is not None:
                (OUT / f"{case.group}-{slug}.json").write_text(
                    json.dumps(strip_pii(raw), indent=2)[:200000], encoding="utf-8")
            time.sleep(DELAY)

    _write_report(results, identity)
    counts = {}
    for _, v, _ in results:
        counts[v] = counts.get(v, 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


def _write_report(results, identity):
    lines = [
        "# Parameter matrix — what the params actually do",
        "",
        f"Live, profile `{identity.profile_id}`, "
        f"{time.strftime('%Y-%m-%d %H:%M')}. One request per case.",
        "",
        "**MATCH** = the wire agrees with the doc. **MISMATCH** = the doc is now "
        "wrong — fix it in the commit that found this. **NEW** = it errored/behaved "
        "as expected but differently in detail; read before trusting.",
        "",
        "| Group | Case | Doc claim | Verdict | Actual |",
        "|---|---|---|---|---|",
    ]
    for case, verdict, detail in results:
        lines.append(
            f"| {case.group} | {case.name} | {case.claim} | **{verdict}** | "
            f"`{str(detail)[:90]}` |")
    lines += ["", "Raw responses: `probes/results/params/*.json`.", ""]
    pathlib.Path("probes/PARAM-MATRIX.md").write_text("\n".join(lines),
                                                      encoding="utf-8")
    print("\nwrote probes/PARAM-MATRIX.md")


if __name__ == "__main__":
    sys.exit(main())
