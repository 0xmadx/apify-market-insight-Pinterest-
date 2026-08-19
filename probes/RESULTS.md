# Endpoint probe — live results

One small call per endpoint in [docs/wire/07-API-REFERENCE.md](../docs/wire/07-API-REFERENCE.md), run against profile `profile_ldu6ypke8` on 2026-08-19 14:07.

`OK` = 200 with a non-empty payload. `EMPTY` = 200 but nothing in it, which is a real answer and not a failure. `FAIL`/`API ERROR` = the endpoint refused.

| § | Endpoint | Style | HTTP | Verdict | ms | Shape returned |
|---|---|---|---|---|---|---|
| 3.1 | latest_available_date — CALL THIS FIRST | B | 200 | **OK** | 0 | `{date: "2026-08-14"}` |
| 3.2 | topics/featured — Trends in the spotlight | A | 200 | **OK** | 545 | `[5x {id: "2542595742484332416", pct_growth_mom: int, interests: [1x "934876475639"], related_search_trends: [1` |
| 3.3 | moment/available — Moments list | A | 200 | **OK** | 702 | `{moments: [13x "thanksgiving"], peaks: [13x dict(3 keys)], historical_peaks: [13x dict(3 keys)], moment_next_o` |
| 3.4 | moment/metrics — Moment chart (only DAILY source) | A | 200 | **OK** | 619 | `{moments: [1x dict(3 keys)]}` |
| 3.5 | editorial/content — Editors' Picks | A | 200 | **OK** | 678 | `[6x {is_ready_for_translation: bool, pins: [6x dict(6 keys)], id: "eaa3b7c5-6930-4c66-aa59-8b81b8ec5b9c", regi` |
| 3.6 | shopping/product_categories — taxonomy | A | 200 | **OK** | 498 | `{categories: {1002: dict(5 keys), 1003: dict(4 keys), 1008: dict(5 keys), 1009: dict(5 keys), 1010: dict(5 key` |
| 3.7 | product_categories/top — trending categories | A | 200 | **OK** | 504 | `{ordered_values: [19x dict(4 keys)], total_num_product_categories: int}` |
| 3.8 | product_categories/metrics — sparklines | A | 200 | **OK** | 296 | `{values: [1x dict(3 keys)]}` |
| 3.9 | product_categories/demographics — 3 sections in one | A | 200 | **OK** | 414 | `{product_category_distributions: {1408: dict(3 keys)}}` |
| 3.10 | product_categories/top_products | A | 200 | **OK** | 536 | `{top_products: [35x dict(4 keys)]}` |
| 3.12 | top_trends_filtered — keyword discovery | B | 200 | **OK** | 296 | `{values: [10x dict(9 keys)], endDate: "2026-08-14"}` |
| 3.13 | metrics — keyword chart + forecast | B | 200 | **OK** | 347 | `[1x {counts: [66x dict(5 keys)], has_prediction: bool, term: "nails", growth_rates: dict(3 keys), hasPredictio` |
| 3.14 | demographics — keyword audience | B | 200 | **OK** | 299 | `{term_distributions: {nails: dict(2 keys)}}` |
| 3.15 | related_terms — 5 related keywords | B | 200 | **OK** | 493 | `[5x {term: "family of three", counts: [53x int], hasPrediction: bool}]` |
| 3.16 | term_images — Popular Pins (only POST) | B | 200 | **OK** | 1421 | `{family: [9x "https://i.pinimg.com/236x/ac/b6/16/acb61…"]}` |
| 3.17 | prefix_match — typeahead (case-sensitive) | B | 200 | **OK** | 425 | `[10x {term: "hallow", counts: [52x int]}]` |

## Not probed, with the reason

- **§3.11 top_products/{merchant_id}/{cat_id} and recommendations/{merchant_id}** — Needs a merchant_id from a product catalog this account does not have. The doc records both returning 200 with empty payloads. Probing with a fabricated merchant id would produce an error that says nothing about the endpoint.

- **§3.18 POST /_/graphql/ — moment Age/Gender** — Persisted query; the query body is in no JS bundle, so there is nothing to send. Doc records the REST alternatives as 404/400. Use the §3.12 + §3.14 workaround instead.

## What the raw responses are for

`probes/results/*.json` holds each full response. Diff the keys they actually contain against the keys any parser reads — that single check would have caught the biggest bug in the parent repo at any point in its life.
