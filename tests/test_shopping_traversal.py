"""The Shopping traversal, driven by a fake client over real fixtures.

    .venv/Scripts/python.exe -m tests.test_shopping_traversal

Scenario E2. Asserts on the PLANNED REQUESTS as well as the output, because the
request budget is a product property: a traversal that returns correct records
by making 4x the calls would pass an output-only test and fail in production
against a shared session.
"""
import glob
import json
import sys

sys.path.insert(0, ".")

from src import vocab  # noqa: E402
from src.shopping import ShoppingScraper  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


def load(pattern, style="A"):
    matches = glob.glob(f"probes/results/{pattern}") or \
              glob.glob(f"probes/results/params/{pattern}")
    payload = json.load(open(matches[0], encoding="utf-8"))
    if style == "A":
        return (payload.get("resource_response") or {}).get("data")
    return payload


class FakeClient:
    """Replays captured responses and records every request made."""

    def __init__(self):
        self.calls = []
        self.taxonomy = load("3.6-*.json")
        self.top = load("3.7-*.json")
        self.metrics = load("3.8-*.json")
        self.demographics = load("F-demographics_event=OUTBOUND_CLICK*.json")
        self.products = load("3.10-*.json")

    def bootstrap(self):
        return "2026-08-14"

    def style_a(self, path, data=None, source_url=None, kind=None):
        self.calls.append((path, data or {}))
        data = data or {}
        if path.endswith("product_categories"):
            return self.taxonomy
        if "/top/" in path:
            return self.top
        if "/metrics/" in path:
            return self._metrics_for(data.get("product_category_ids", []))
        if "/demographics/" in path:
            return self._demographics_for(data.get("product_category_ids", []))
        if "top_products" in path:
            return self.products
        raise AssertionError(f"unexpected path {path}")

    # The captured metrics/demographics fixtures are for categories 1356/1408,
    # while the captured top/ fixture is a different vertical returning 1478…
    # Replaying them verbatim would test nothing but a failed join. These
    # re-key the REAL response shapes onto the ids actually requested, so the
    # traversal's join is exercised against genuine structure.
    def _metrics_for(self, ids):
        template = self.metrics["values"][0]
        return {"values": [dict(template, term=str(i)) for i in ids]}

    def _demographics_for(self, ids):
        dists = self.demographics["product_category_distributions"]
        template = next(iter(dists.values()))
        return {"product_category_distributions": {str(i): template for i in ids}}


def main():
    print("\nE2 — the shopping traversal")
    client = FakeClient()
    scraper = ShoppingScraper(client, region="US", drill_top_n=2, log=lambda *a: None)
    records = list(scraper.run(verticals=["1181"]))

    # NOT `== 19`. That number is Pinterest's, and on 2026-08-25 vertical 1181
    # returned 16 where it had returned 19 — the fixture's own
    # `total_num_product_categories` moved with it. The invariant that actually
    # matters is that the traversal emits one record per category the response
    # carried, i.e. drops none; a hardcoded count tests Pinterest's catalogue
    # instead, and fails on a day nothing in this repo changed.
    expected = len(client.top["ordered_values"])
    check("E2 yields one record per trending category, dropping none",
          len(records) == expected, f"{len(records)} vs {expected}")

    paths = [c[0] for c in client.calls]
    check("E2 taxonomy fetched exactly once",
          sum(1 for p in paths if p.endswith("product_categories")) == 1)
    check("E2 top/ called once for the one vertical",
          sum(1 for p in paths if "/top/" in p) == 1)
    check("E2 metrics batched into ONE call, not one per category",
          sum(1 for p in paths if "/metrics/" in p) == 1)
    check("E2 demographics batched into ONE call",
          sum(1 for p in paths if "/demographics/" in p) == 1)
    check("E2 top_products called once per drilled category only",
          sum(1 for p in paths if "top_products" in p) == 2)
    check("E2 total budget is 4+N, not 1+4N", len(client.calls) == 6, len(client.calls))

    print("\nrequest shapes")
    top_call = next(d for p, d in client.calls if "/top/" in p)
    check("C1 top/ sent exactly one vertical",
          len(top_call["parent_product_categories"]) == 1)
    check("A4 end_date came from bootstrap, not today()",
          top_call["end_date"] == "2026-08-14")
    met_call = next(d for p, d in client.calls if "/metrics/" in p)
    check("E2 metrics asked for EVERY category in one array, not a subset",
          len(met_call["product_category_ids"]) == expected,
          f"{len(met_call['product_category_ids'])} vs {expected}")
    # The default was Pinterest's own 180 (their detail page draws its dashed
    # band from 180 + a 28-day forecast). Deliberately changed to 730: one year
    # shows each season exactly ONCE, which cannot distinguish a seasonal
    # pattern from a one-off — and on the keyword endpoint 730 is the only
    # window that returns yoy_change at all. It costs the same single request.
    check("E2 chart asks for 2 years by default, with the 28d forecast kept",
          met_call["days"] == 730 and met_call["predicted_days"] == 28,
          f'days={met_call["days"]} predicted={met_call["predicted_days"]}')
    # 180 must still be reachable for anyone matching Pinterest's own view.
    from src.shopping import ShoppingScraper as _SS
    import inspect as _i
    check("E2 ...and Pinterest's 180 is still available on request",
          "chartDays=180" in _i.getsource(_SS) or "chart_days" in
          _i.signature(_SS.__init__).parameters)
    demo_call = next(d for p, d in client.calls if "/demographics/" in p)
    check("E2 demographics asked only for the drilled 2",
          len(demo_call["product_category_ids"]) == 2)
    prod_call = next(d for p, d in client.calls if "top_products" in p)
    check("C7 top_products forced to OUTBOUND_CLICK",
          prod_call["event"] == "OUTBOUND_CLICK")

    print("\nrecord contents")
    first = records[0]
    check("E2 category id joined to a human name", bool(first["category_name"]))
    check("E2 breadcrumb path built from the taxonomy",
          isinstance(first["category_path"], list) and len(first["category_path"]) >= 2,
          str(first["category_path"]))
    check("E2 vertical name resolved from doc §4.3, not guessed",
          first["vertical_name"] == "Fashion", first["vertical_name"])
    check("E2 rank is present and 1-based", first["rank_in_vertical"] == 1)
    check("E2 chart series attached", bool(first["chart"]))
    check("E2 search queries present even when NOT drilled",
          bool(records[5]["search_queries"]) and records[5]["drilled"] is False)

    drilled = [r for r in records if r["drilled"]]
    check("E2 exactly 2 records drilled", len(drilled) == 2)
    check("E2 drilled record has an audience", bool(drilled[0]["age_distribution"]))
    # Lower bound, not an equality — see the note in test_shopping_api.py.
    check("E2 drilled record has products",
          len(drilled[0]["top_products"]) >= 20,
          len(drilled[0]["top_products"]))
    product = drilled[0]["top_products"][0]
    check("E2 product carries a usable pin permalink",
          (product["pin_url"] or "").startswith("https://www.pinterest.com/pin/"))
    check("E2 product merchant present", bool(product["merchant_name"]))

    print("\nD5/provenance — honesty of the numbers")
    meta = drilled[0]["_meta"]
    check("D5 normalization scope stamped",
          meta["normalization_scope"] == "top:US:1181:2026-08-14")
    check("D5 two verticals would carry different scopes",
          meta["normalization_scope"] != "top:US:1250:2026-08-14")
    check("C7 audience is stamped with the event it was measured under",
          meta["audience_event"] == "OUTBOUND_CLICK")
    check("D4 audience basis is 'measured' when drilled",
          meta["audience_basis"] == "measured")
    check("B3 undrilled record has audience None, not 0",
          records[5]["age_distribution"] is None
          and records[5]["_meta"]["audience_basis"] is None)
    check("B3 absolute volume declared absent, with the reason",
          meta["absolute_volume"] is None and "never exposes" in meta["absolute_volume_note"])
    check("A4 end_date is Pinterest's date, on every record",
          meta["end_date"] == "2026-08-14")

    print("\nrefusals before the wire")
    try:
        ShoppingScraper(client, region="DE", drill_top_n=1, log=lambda *a: None) \
            ._products("1408")
        ok = True
    except Exception:
        ok = False
    check("C5 products in an unsupported region degrade to [], not a crash", ok)
    try:
        ShoppingScraper(client, event="IMPRESSION")
        check("C7 constructing with a broken event is refused", False)
    except vocab.InvalidParam:
        check("C7 constructing with a broken event is refused", True)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
