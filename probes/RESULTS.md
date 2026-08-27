# Endpoint probe — live results

One small call per endpoint in [docs/wire/07-API-REFERENCE.md](../docs/wire/07-API-REFERENCE.md), run against profile `ads_k1fyn0gc` on 2026-08-27 15:10.

`OK` = 200 with a non-empty payload. `EMPTY` = 200 but nothing in it, which is a real answer and not a failure. `FAIL`/`API ERROR` = the endpoint refused.

| § | Endpoint | Style | HTTP | Verdict | ms | Shape returned |
|---|---|---|---|---|---|---|
| 3.1 | latest_available_date — CALL THIS FIRST | B | 200 | **OK** | 0 | `{date: "2026-08-23"}` |
| 3.2 | topics/featured — Trends in the spotlight | A | 200 | **OK** | 264 | `[5x {pins: [6x dict(6 keys)], is_published: bool, name: "Clay Figure Ideas", pct_growth_mom: int, description:` |
| 3.3 | moment/available — Moments list | A | 401 | **API ERROR** | 155 | `Authorization failed.` |
| 3.4 | moment/metrics — Moment chart (only DAILY source) | A | 401 | **API ERROR** | 151 | `Authorization failed.` |
| 3.5 | editorial/content — Editors' Picks | A | 200 | **OK** | 592 | `[6x {title: "Can't Stop Cross-Stitching", is_published: bool, hide_keyword_percentages: bool, interests: [1x "` |
| 3.6 | shopping/product_categories — taxonomy | A | 200 | **OK** | 151 | `{categories: {1002: dict(5 keys), 1003: dict(4 keys), 1008: dict(5 keys), 1009: dict(5 keys), 1010: dict(5 key` |
| 3.7 | product_categories/top — trending categories | A | 200 | **OK** | 230 | `{total_num_product_categories: int, ordered_values: [18x dict(4 keys)]}` |
| 3.8 | product_categories/metrics — sparklines | A | 200 | **OK** | 232 | `{values: [1x dict(3 keys)]}` |
| 3.9 | product_categories/demographics — 3 sections in one | A | 200 | **OK** | 207 | `{product_category_distributions: {1408: dict(3 keys)}}` |
| 3.10 | product_categories/top_products | A | 200 | **OK** | 388 | `{top_products: [34x dict(4 keys)]}` |
| 3.12 | top_trends_filtered — keyword discovery | B | 200 | **OK** | 141 | `{values: [10x dict(9 keys)], endDate: "2026-08-23"}` |
| 3.13 | metrics — keyword chart + forecast | B | 200 | **OK** | 223 | `[1x {growth_rates: dict(3 keys), term: "nails", has_prediction: bool, counts: [66x dict(5 keys)], hasPredictio` |
| 3.14 | demographics — keyword audience | B | 200 | **OK** | 214 | `{term_distributions: {nails: dict(2 keys)}}` |
| 3.15 | related_terms — 5 related keywords | B | 200 | **OK** | 402 | `[5x {term: "family cartoon", counts: [53x int], hasPrediction: bool}]` |
| 3.16 | term_images — Popular Pins (only POST) | B | 200 | **OK** | 103 | `{family: [9x "https://i.pinimg.com/236x/6b/cd/38/6bcd3…"]}` |
| 3.17 | prefix_match — typeahead (case-sensitive) | B | 200 | **OK** | 288 | `[10x {term: "hallow", counts: [52x int]}]` |

## Not probed, with the reason

- **§3.11 top_products/{merchant_id}/{cat_id} and recommendations/{merchant_id}** — Needs a merchant_id from a product catalog this account does not have. The doc records both returning 200 with empty payloads. Probing with a fabricated merchant id would produce an error that says nothing about the endpoint.

- **§3.18 POST /_/graphql/ — moment Age/Gender** — Persisted query; the query body is in no JS bundle, so there is nothing to send. Doc records the REST alternatives as 404/400. Use the §3.12 + §3.14 workaround instead.

## What the raw responses are for

`probes/results/*.json` holds each full response. Diff the keys they actually contain against the keys any parser reads — that single check would have caught the biggest bug in the parent repo at any point in its life.
