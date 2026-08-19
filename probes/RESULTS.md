# Endpoint probe — live results

One small call per endpoint in [docs/07-API-REFERENCE.md](../docs/07-API-REFERENCE.md), run against profile `profile_p5ewxsodn` on 2026-08-18 19:00.

`OK` = 200 with a non-empty payload. `EMPTY` = 200 but nothing in it, which is a real answer and not a failure. `FAIL`/`API ERROR` = the endpoint refused.

| § | Endpoint | Style | HTTP | Verdict | ms | Shape returned |
|---|---|---|---|---|---|---|
| 3.1 | latest_available_date — CALL THIS FIRST | B | 200 | **OK** | 0 | `{date: "2026-08-14"}` |
| 3.2 | topics/featured — Trends in the spotlight | A | 200 | **OK** | 408 | `[5x {time_series: [13x dict(5 keys)], pins: [9x dict(6 keys)], description: "Embroidery gifts are stitching th` |
| 3.3 | moment/available — Moments list | A | 200 | **OK** | 368 | `{moments: [13x "thanksgiving"], peaks: [13x dict(3 keys)], historical_peaks: [13x dict(3 keys)], moment_next_o` |
| 3.4 | moment/metrics — Moment chart (only DAILY source) | A | 200 | **OK** | 456 | `{moments: [1x dict(3 keys)]}` |
| 3.5 | editorial/content — Editors' Picks | A | 200 | **OK** | 519 | `[6x {regions: [3x "US"], keywords: dict(3 keys), interests: [1x "934876475639"], is_published: bool, title: "C` |
| 3.6 | shopping/product_categories — taxonomy | A | 200 | **OK** | 330 | `{categories: {1002: dict(5 keys), 1003: dict(4 keys), 1008: dict(5 keys), 1009: dict(5 keys), 1010: dict(5 key` |
| 3.7 | product_categories/top — trending categories | A | 200 | **OK** | 221 | `{total_num_product_categories: int, ordered_values: [19x dict(4 keys)]}` |
| 3.8 | product_categories/metrics — sparklines | A | 200 | **OK** | 212 | `{values: [1x dict(3 keys)]}` |
| 3.9 | product_categories/demographics — 3 sections in one | A | 200 | **OK** | 184 | `{product_category_distributions: {1408: dict(3 keys)}}` |
| 3.10 | product_categories/top_products | A | 200 | **OK** | 392 | `{top_products: [33x dict(4 keys)]}` |
| 3.12 | top_trends_filtered — keyword discovery | B | 200 | **OK** | 182 | `{values: [10x dict(9 keys)], endDate: "2026-08-14"}` |
| 3.13 | metrics — keyword chart + forecast | B | 200 | **OK** | 182 | `[1x {growth_rates: dict(3 keys), term: "nails", counts: [66x dict(5 keys)], has_prediction: bool, hasPredictio` |
| 3.14 | demographics — keyword audience | B | 200 | **OK** | 167 | `{term_distributions: {nails: dict(2 keys)}}` |
| 3.15 | related_terms — 5 related keywords | B | 200 | **OK** | 501 | `[5x {term: "family icon", counts: [53x int], hasPrediction: bool}]` |
| 3.16 | term_images — Popular Pins (only POST) | B | 200 | **OK** | 98 | `{family: [9x "https://i.pinimg.com/236x/67/d6/0c/67d60…"]}` |
| 3.17 | prefix_match — typeahead (case-sensitive) | B | 200 | **OK** | 531 | `[10x {term: "hallow", counts: [52x int]}]` |

## Not probed, with the reason

- **§3.11 top_products/{merchant_id}/{cat_id} and recommendations/{merchant_id}** — Needs a merchant_id from a product catalog this account does not have. The doc records both returning 200 with empty payloads. Probing with a fabricated merchant id would produce an error that says nothing about the endpoint.

- **§3.18 POST /_/graphql/ — moment Age/Gender** — Persisted query; the query body is in no JS bundle, so there is nothing to send. Doc records the REST alternatives as 404/400. Use the §3.12 + §3.14 workaround instead.

## What the raw responses are for

`probes/results/*.json` holds each full response. Diff the keys they actually contain against the keys any parser reads — that single check would have caught the biggest bug in the parent repo at any point in its life.

## Doc claims checked against the wire

Every claim below was re-derived from the raw responses in `results/`, not taken from the doc.

| Doc claim (§) | On the wire | |
|---|---|---|
| 383 categories, L2=66 / L3=190 / L4=127 (§3.6) | 383 — 66 / 190 / 127 | exact |
| `affinity` is null in every row (§3.12) | null in 10/10 | confirmed |
| `yoy_change` frequently null (§3.13) | null; wow -0.03, mom +0.10 present | confirmed |
| `limit` works, UI sends 20 (§3.7) | asked 20, got 19 — because `total_num_product_categories` **is** 19 for that vertical, not truncation | confirmed |
| §3.13 uses `has_prediction`, §3.15 uses `hasPrediction` | **§3.13 returns BOTH keys**, same value | ⚠️ new |

### The one new fact

`/metrics/` returns `has_prediction` *and* `hasPrediction` in the same object. The doc
describes the two endpoints as differing in spelling, which is only half true.

This matters more than it looks. A parser written against §3.15's camelCase will work
against §3.13 too — until Pinterest drops the duplicate, at which point it reads `None`
and every keyword silently becomes "not forecastable". That is the exact failure that
cost the parent repo seven modules and every empty table it had: **correct data fetched,
`None` read out of it, no error anywhere.** Normalise the spelling at the parser.
