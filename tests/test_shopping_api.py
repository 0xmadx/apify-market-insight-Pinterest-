"""Offline proofs for the shopping API layer — vocab + parsers.

    .venv/Scripts/python.exe -m tests.test_shopping_api

No network. Every assertion runs against the committed probe fixtures in
probes/results/, which are real captured responses. Scenario ids (B2, C1, …)
refer to docs/TEST-SCENARIOS.md so coverage stays auditable.
"""
import glob
import json
import pathlib
import sys

sys.path.insert(0, ".")

from src import parsers, vocab  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    print(f"  {mark}  {name}" + (f"  [{detail}]" if detail and not condition else ""))


def fixture(pattern, style="A"):
    """Load a captured response and unwrap it the way the client does."""
    matches = glob.glob(f"probes/results/{pattern}")
    if not matches:
        matches = glob.glob(f"probes/results/params/{pattern}")
    if not matches:
        return None
    payload = json.load(open(matches[0], encoding="utf-8"))
    if style == "A" and isinstance(payload, dict):
        return (payload.get("resource_response") or {}).get("data")
    return payload


def main():
    print("\nGROUP C — validation refuses before the wire")

    # C1 — one vertical per call
    try:
        vocab.verticals_one_per_call(["1181", "1250"])
        check("C1 two verticals in one top/ call is refused", False)
    except vocab.InvalidParam as exc:
        check("C1 two verticals in one top/ call is refused",
              "normalis" in str(exc) or "crush" in str(exc))
    check("C1 one vertical passes",
          vocab.verticals_one_per_call(["1181"]) == ["1181"])

    # C2 — L1 only
    try:
        vocab.vertical("1311")          # Mascaras, an L3
        check("C2 an L3 id is refused as a vertical", False)
    except vocab.InvalidParam as exc:
        check("C2 an L3 id is refused as a vertical", "L1" in str(exc))

    # C4 — ceilings, named
    for name, over in [("top_limit", 1000), ("predicted_days", 180),
                       ("days", 1095), ("interest_limit", 50),
                       ("num_terms", 500)]:
        try:
            vocab.ceiling(name, over)
            check(f"C4 {name}={over} refused", False)
        except vocab.InvalidParam as exc:
            check(f"C4 {name}={over} refused, cites the max",
                  str(vocab.LIMITS[name]) in str(exc))
    check("C4 a value at the ceiling passes",
          vocab.ceiling("top_limit", 522) == 522)

    # C2b — the guard that would have caught fabricated vertical names.
    # The 3.7 fixture IS vertical 1181; the doc says 1181 has 19 categories.
    # If the id->name map is shuffled, these two facts stop agreeing.
    top_fix = fixture("3.7-*.json")
    if top_fix:
        n = len(parsers.parse_top_categories(top_fix)["categories"])
        # NOT `== EXPECTED_ROWS["1181"]`. On 2026-08-25 the live vertical
        # returned 16 where the doc recorded 19 — Pinterest's catalogue moved,
        # nothing here did. `total_num_product_categories` is the response's
        # OWN count, so comparing the parsed rows against it catches the thing
        # this check is for (a parser silently dropping rows) without asserting
        # a number Pinterest is free to change. EXPECTED_ROWS stays as recorded
        # history; it is no longer a pass/fail gate.
        stated = parsers.parse_top_categories(top_fix)["total_in_vertical"]
        check("C2b the parser surfaces every row the response carried",
              n == stated, f"parsed {n} vs response total {stated}")
        check("C2b 1181 is Fashion, not Home decor (doc #7 §4.3)",
              vocab.VERTICALS["1181"] == "Fashion", vocab.VERTICALS["1181"])
        check("C2b 1250 is Home decor — it returned 9 rows live",
              vocab.VERTICALS["1250"] == "Home decor"
              and vocab.EXPECTED_ROWS["1250"] == 9)
        check("C2b every vertical has an expected row count",
              set(vocab.VERTICALS) == set(vocab.EXPECTED_ROWS))
    try:
        vocab.vertical("1161")   # a real L1 id that returns 0 rows
        check("C2b a known-empty vertical is refused", False)
    except vocab.InvalidParam as exc:
        check("C2b a known-empty vertical is refused with its name",
              "Electronics" in str(exc))

    # C3 — lowercase keywords
    check("C3 keyword is lowercased", vocab.keyword("HALLOW") == "hallow")

    # C5 — region capability
    try:
        vocab.region("DE", capability="top_products")
        check("C5 top_products in DE refused before the wire", False)
    except vocab.InvalidParam:
        check("C5 top_products in DE refused before the wire", True)
    check("C5 top_products in US allowed",
          vocab.region("US", capability="top_products") == "US")

    # C7 — event by meaning, never by label
    check("C7 label 'All' maps to ENGAGEMENT",
          vocab.event("All") == "ENGAGEMENT")
    check("C7 label 'Engagement' also maps to ENGAGEMENT",
          vocab.event("Engagement") == "ENGAGEMENT")
    check("C7 'Pin saves' maps to SAVE", vocab.event("Pin saves") == "SAVE")
    try:
        vocab.event("IMPRESSION")
        check("C7 an accepted-but-500 event is refused", False)
    except vocab.InvalidParam as exc:
        check("C7 an accepted-but-500 event is refused", "500" in str(exc))
    try:
        vocab.event("SAVE", endpoint="top_products")
        check("C7 top_products with SAVE refused (silent empty)", False)
    except vocab.InvalidParam as exc:
        check("C7 top_products with SAVE refused (silent empty)",
              "EMPTY" in str(exc).upper())

    print("\nGROUP B — parsers, against real captured responses")

    # taxonomy
    tax_raw = fixture("3.6-*.json")
    check("fixture 3.6 loaded", tax_raw is not None)
    if tax_raw:
        tax = parsers.parse_taxonomy(tax_raw)
        # 383 on 2026-08-19. Pinterest owns this number and the release
        # gate refreshes the fixture, so assert the shape, not the snapshot.
        check("B2 taxonomy parses the full category tree", len(tax) > 300,
              len(tax))
        levels = {}
        for node in tax.values():
            levels[node["level"]] = levels.get(node["level"], 0) + 1
        check("B2 levels are 66/190/127",
              levels.get(2) == 66 and levels.get(3) == 190 and levels.get(4) == 127,
              str(levels))
        check("B2 every category has a friendly_name",
              all(n["friendly_name"] for n in tax.values()))
        sample = next(n for n in tax.values() if n["parent_id"])
        path = parsers.category_path(tax, sample["id"])
        check("B2 category_path builds a breadcrumb", len(path) >= 2, str(path))

    # trending categories
    top_raw = fixture("3.7-*.json")
    check("fixture 3.7 loaded", top_raw is not None)
    if top_raw:
        top = parsers.parse_top_categories(top_raw)
        check("B2 top/ parses rows", len(top["categories"]) > 0,
              len(top["categories"]))
        # The real claim is that a short list means the vertical IS short, not
        # that we truncated it. Assert the RELATIONSHIP rather than the 19
        # measured on 2026-08-19 — Pinterest owns the count, and the release
        # gate refreshes this fixture.
        check("B2 a short list is the vertical's real size, not truncation",
              len(top["categories"]) == top["total_in_vertical"],
              f'{len(top["categories"])} rows vs total '
              f'{top["total_in_vertical"]}')
        row = top["categories"][0]
        check("B2 row carries a category id", bool(row["category_id"]))
        check("B2 row carries search queries", len(row["search_queries"]) > 0)
        check("B2 summary has all three metrics",
              set(row["summary"]) == {"engagement", "saves", "outbound_clicks"})
        check("B2 always-zero `total` is dropped, not reported as 0",
              "total" not in (row["summary"]["engagement"] or {}))

    # category metrics
    met_raw = fixture("3.8-*.json")
    if met_raw:
        met = parsers.parse_category_metrics(met_raw)
        check("B2 metrics keyed by category id", "1356" in met, str(list(met)))
        node = met["1356"]
        check("B2 metrics carries a series", node["series_points"] > 0)
        check("B3 null growth stays None, never 0",
              node["yoy_change"] is None or isinstance(node["yoy_change"], float))

    # demographics — and the event claim
    print("\nGROUP F — the event/demographics interaction, from 3 captures")
    per_event = {}
    for ev in ("OUTBOUND_CLICK", "ENGAGEMENT", "SAVE"):
        raw = fixture(f"F-demographics_event={ev}*.json")
        if raw:
            per_event[ev] = parsers.parse_category_demographics(raw, ev)["1408"]
    check("all three event captures present", len(per_event) == 3, str(list(per_event)))
    if len(per_event) == 3:
        check("F every record is stamped with its event",
              all(v["event"] == k for k, v in per_event.items()))
        ages = {k: v["age_distribution"]["65+"] for k, v in per_event.items()}
        check("F 65+ really moves with event (0.32 clicks vs 0.19 saves)",
              ages["OUTBOUND_CLICK"] != ages["SAVE"], str(ages))
        queries = {k: tuple(v["search_queries"]) for k, v in per_event.items()}
        check("F search queries are IDENTICAL across events — fetch once",
              len(set(queries.values())) == 1)
        check("F search queries are non-empty",
              len(per_event["SAVE"]["search_queries"]) > 0,
              len(per_event["SAVE"]["search_queries"]))

    # top products
    print("\nGROUP E — products and the bridge off Pinterest")
    prod_raw = fixture("3.10-*.json")
    if prod_raw:
        products = parsers.parse_top_products(prod_raw, region="US")
        # NOT `== 33`. It was 33 when first captured and 35 on 2026-08-19 —
        # the number of shoppable pins in a category is Pinterest's to change,
        # and probes/probe_endpoints.py (the release gate) REFRESHES this
        # fixture, so pinning the count made the documented release sequence
        # fail itself. Assert the invariant: the list parses and is usable.
        check("B2 top_products parses a full product list", len(products) >= 20,
              len(products))
        first = products[0]
        check("B2 product has pin_id and merchant", first["pin_id"] and first["merchant_name"])
        check("B2 pin_url is constructed",
              first["pin_url"] == f"https://www.pinterest.com/pin/{first['pin_id']}/")
        check("B2 image url picked from the size ladder",
              (first["image_url"] or "").startswith("https://i.pinimg.com"))
        check("B3 price/merchant_url are None (not fetched), never 0 or ''",
              first["price"] is None and first["merchant_url"] is None)

    # the double spelling
    print("\nGROUP B1 — the double-spelled forecast flag")
    kw_raw = fixture("3.13-*.json", style="B")
    if kw_raw:
        parsed = parsers.parse_keyword_metrics(kw_raw)
        check("B1 metrics parsed by term", "nails" in parsed)
        check("B1 one canonical has_forecast field",
              parsed["nails"]["has_forecast"] is True)
        # both spellings present in the fixture -> either alone must still work
        snake = [{k: v for k, v in kw_raw[0].items() if k != "hasPrediction"}]
        camel = [{k: v for k, v in kw_raw[0].items() if k != "has_prediction"}]
        check("B1 works with only has_prediction",
              parsers.parse_keyword_metrics(snake)["nails"]["has_forecast"] is True)
        check("B1 works with only hasPrediction",
              parsers.parse_keyword_metrics(camel)["nails"]["has_forecast"] is True)
        neither = [{k: v for k, v in kw_raw[0].items()
                    if k not in ("has_prediction", "hasPrediction")}]
        check("B1 both absent -> None, NOT False",
              parsers.parse_keyword_metrics(neither)["nails"]["has_forecast"] is None)

    # B5 — silent drop
    multi = fixture("G-metrics_10_terms*.json", style="B")
    if multi:
        parsed = parsers.parse_keyword_metrics(multi)
        requested = ["nails", "family", "zzzqqqxyz", "halloween",
                     "christmas ornament", "mom necklace", "felt garland",
                     "qqzzxx99", "backpack name tag", "embroidery ideas"]
        missing = parsers.missing_terms(requested, parsed)
        # The claim under test is "absence is reported, never silently
        # dropped" — not the exact 4/6 split measured on 2026-08-19. Two of
        # the ten are deliberate nonsense (zzzqqqxyz, qqzzxx99) and must
        # always be missing; pinning the whole split would fail the moment
        # Pinterest gained data for a real term, which is not a regression.
        check("B5 dropped terms are reported, not silently lost",
              len(parsed) + len(missing) == len(requested) and len(missing) >= 2,
              f"{len(parsed)} got, {len(missing)} missing")
        check("B5 'halloween' is among the dropped (absence != zero volume)",
              "halloween" in missing)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
