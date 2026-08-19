# API REFERENCE

Complete surface. For "what is this and why would I use it", read
[CUSTOMER-GUIDE.md](CUSTOMER-GUIDE.md) first — this page assumes you already
know and want the fields.

The parameter tables are **generated from `.actor/input_schema.json`** — the
same file the Apify form renders and `src/scraper.py` reads. It cannot drift
from the code; `tests/test_dispatch.py` asserts both directions.

---

## Endpoint

One call. Send options, get records.

```
POST https://api.apify.com/v2/acts/<actor-id>/run-sync-get-dataset-items
     ?token=<APIFY_TOKEN>
Content-Type: application/json
```

Locally (`python api_sim.py`), identical except the host:

```
POST http://localhost:8080/v2/acts/pinterest-trends/run-sync-get-dataset-items
```

**Request body** = the input object below. **Response** = a JSON array of
records (Apify), or `{mode, requests, items}` from the local simulator, which
adds `mode` so you can tell LIVE data from replayed fixtures.

Everything is optional. `{}` is a valid request and returns trending shopping
categories.

Async runs, polling and dataset paging are standard Apify platform behaviour —
<https://docs.apify.com/api/v2>. Nothing about this actor is special there.

---

## Input

#### Common to every operation

| Field | Type | Default | Meaning |
|---|---|---|---|
| `operation` | `shopping` \| `keywords` \| `moments` \| `radar` | `shopping` | Which traversal to run. shopping: trending product categories, joined and drilled. keywords: discover/seed/exact keyword research through the full pipeline. moments: seasonal moments with the API's only daily-resolution curves. radar: Pinterest's curated spotlight + Editors' Picks (2 requests, schedule-friendly). |
| `region` | `string` | `US` | Pinterest region code — 32 accepted (US, CA, GB+IE, DE, FR, BR, MX, AU+NZ, JP, KR, SE+DK+FI+NO, …). '+' is literal. Narrower per feature: top_products and editorial serve US/CA/GB+IE only; JP and IN have no seasonal moments at all. |
| `maxRecords` | `integer` | `0` | Hard stop on records pushed. 0 = no limit. |
| `predictedDays` | `integer` | `91` | Days of forecast to request. Max 91 — Pinterest rejects more. Only fills for keywords that have a forecast. |
| `fullRescan` | `boolean` | `false` | Ignore the seen-set: re-emit records already collected in previous runs. |
| `forceRefresh` | `boolean` | `false` | Ignore cached responses: hit Pinterest for every request. |

#### `shopping` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `verticals` | array | — | L1 vertical ids (1181 Fashion, 1250 Home decor, 1042 Beauty, 1148 DIY, 1016 Arts & entertainment, 1500 Wedding, 1315 Media). Leave empty for the three Pinterest's own UI shows (Fashion, Home decor, Beauty) — the cheap default. Pass all seven explicitly to include the four hidden ones, which Pinterest serves but never displays. |
| `drillTopN` | `integer` | `3` | How many categories per vertical get the full drill-down (audience, search queries, products). Each drilled category costs ~2 extra requests. |
| `event` | `OUTBOUND_CLICK` \| `ENGAGEMENT` \| `SAVE` | `OUTBOUND_CLICK` | OUTBOUND_CLICK (purchase intent), ENGAGEMENT (broad attention), SAVE (planning). The audience differs materially per event. |
| `includeProducts` | `boolean` | `true` | Attach the shoppable product list to drilled categories. US, CA and GB+IE only. |
| `enrichTopN` | `integer` | `0` | Per drilled category, fetch real price, stock, free-shipping threshold and the OUTBOUND MERCHANT URL for this many top products. Costs 1 extra request PER PRODUCT (no batch form exists), so start small. 0 = off. |
| `shoppingAges` | array | — | Restrict trending categories to these age bands: 18-24, 25-34, 35-44, 45-49, 50-54, 55-64, 65+, all. Empty = everyone. (Sent as the shopping enum form; the keyword operation uses a different scheme — handled for you.) |
| `shoppingGenders` | array | — | Restrict trending categories: male, female, unspecified. Empty = everyone. |
| `rankingMethod` | `GROWTH` \| `HIGH_VOLUME` \| `VIRAL` | `GROWTH` | How Pinterest ranks the categories. Their own UI only ever sends GROWTH — HIGH_VOLUME and VIRAL are real and unexposed, so this reaches rankings their interface cannot show. |
| `orderBy` | `RELATIVE_VOLUME` \| `TREND_RANK` \| `PCT_CHANGE_MOM` | `RELATIVE_VOLUME` | Which column sorts the table. RELATIVE_VOLUME is the UI default; PCT_CHANGE_MOM surfaces fastest-moving instead of biggest. |

#### `keywords` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `mode` | `discover` \| `seed` \| `exact` | `discover` | discover: Pinterest's trending set with filters. seed: typeahead expansion of a stem (whole keyword space). exact: enrich your own term list. |
| `queries` | array | — | exact mode: the terms to enrich. seed mode: the first entry is the stem. Lowercase (enforced - uppercase silently returns nothing). |
| `preset` | `integer` | `3` | 1 top monthly, 2 top yearly, 3 growing, 4 seasonal. |
| `numTerms` | `integer` | `100` | Keywords to pull from discovery before enrichment. Max 100 (Pinterest's ceiling). |
| `maxTerms` | `integer` | `0` | Cap on terms sent through the pipeline. 0 = all discovered (up to 100). |
| `keywordsToInclude` | array | — | Only keep discovered keywords containing one of these. OR logic, substring match, lowercase enforced (uppercase silently returns nothing). This is how you scope discovery to YOUR niche. |
| `interests` | array | — | Interest ids to scope discovery to (24 exist; see docs 07 4.2). Empty = all interests. |
| `moments` | array | — | Only keywords tied to these seasonal moments. Type them however Pinterest displays them — "Father's Day" is normalised to the wire form (lowercase, spaces kept, apostrophes stripped) automatically; the raw form is a 400. Slugs are REGION-SPECIFIC: 25 exist globally, 13 in the US. |
| `ageBuckets` | array | — | Restrict discovery to age bands: 18-24, 25-34, 35-44, 45-49, 50-54, 55-64, 65+. (18-24 maps to two internal codes; handled for you.) |
| `genders` | array | — | Restrict discovery: male, female, unspecified. |
| `days` | `integer` | `365` | Days of history per keyword chart. 90 / 180 / 365 / 730. |
| `includeRelated` | `boolean` | `false` | 5 siblings per term, each with its own forecast flag. Costs one request PER TERM (no batch form exists). |
| `includeImages` | `boolean` | `true` | 9 popular-pin image urls per term. One batched POST for the whole run. |

#### `moments` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `aggregation` | `daily` \| `weekly` \| `monthly` | `daily` | daily is unique to moments - nothing else in the API resolves below weekly. |
| `phases` | array | — | Which phases get curves + keywords + audience. Default: rising, approaching. All moments are emitted either way; cooled ones cost no extra requests. |
| `drill` | `boolean` | `true` | Off = list every moment with its phase and peak dates only, no per-moment calls. Cheapest possible run. |
| `includeAudience` | `boolean` | `true` | Age + gender for each drilled moment — the same figures Pinterest's own "Who's driving this moment" chart draws (measured). If their persisted query rotates, this falls back to an aggregate of the moment's keyword audiences and the record says so in _meta.audience_basis. |
| `interestIds` | array | — | Interest ids to break each drilled moment's audience down by. Pinterest's own UI offers only ~7 per moment, but ANY moment x ANY interest works - these are audiences their interface cannot show. Costs one request PER CELL, so keep the list short. Empty = off. |
| `lookbackDays` | `integer` | `365` | Days of moment history. Max 730. |

#### `radar` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `interest` | `string` | — | One interest id to scope the spotlight, or empty for all. |
| `includeSpotlight` | `boolean` | `true` | Pinterest's 5 curated homepage trends. |
| `includeEditorial` | `boolean` | `true` | 6 hand-written editorial trends with campaign windows. US, CA and GB+IE only. |


### Constraints enforced before the request leaves

Several of these fail **silently** on Pinterest's side, so they are refused
here instead, with the measured reason in the message.

| Rule | Why it exists |
|---|---|
| keywords are lowercased for you | uppercase returns HTTP 200 with an empty list — a silent nothing |
| moment names are normalised | `Father's Day` → `fathers day`; the raw form is a 400 |
| one vertical per request | `percent_relative_volume` is normalised *within* a response, so combining verticals crushes the smaller one |
| `numTerms` ≤ 100 · `predictedDays` ≤ 91 · `days`/`lookbackDays` ≤ 730 | 400 past those, and those endpoints return **no error message** |
| `shoppingAges` takes band names (`25-34`) | shopping wants `AGE_25_34`, keyword discovery wants `4` — same input, two wire forms, mapped for you |
| `top_products` and editorial: US, CA, GB+IE only | other regions return 200 with nothing |
| moments: not JP or IN | those regions have zero seasonal moments |

---

## Output

One record per row. Every record carries `_meta`.

### `shopping` — one trending product category

| Field | Type | Notes |
|---|---|---|
| `category_id` | string | Pinterest's id, e.g. `1311` |
| `category_name` | string | joined from the taxonomy — the API alone returns ids only |
| `category_level` | number | 2, 3 or 4 |
| `category_path` | array | breadcrumb: `["Beauty","Makeup","Eye makeup","Mascaras"]` |
| `vertical_id` · `vertical_name` | string | the L1 it belongs to |
| `rank_in_vertical` | number | 1-based, **within this response only** |
| `total_in_vertical` | number | the vertical's real size — not a truncation signal |
| `summary` | object | `engagement` / `saves` / `outbound_clicks`, each `{percent_growth, percent_relative_volume, lookback}` |
| `growth` | object | `wow_change`, `mom_change`, `yoy_change` — **null means unmeasured, not flat** |
| `chart` | array | `{date, count, predicted_lower, predicted_upper}` |
| `drilled` | boolean | whether the deep fields below were fetched |
| `age_distribution` | object | 7 bands as fractions. `null` when not drilled |
| `gender_distribution` | object | `male` / `female` / `unspecified` |
| `search_queries` | array | phrases people use to reach this category |
| `top_products` | array | below |

`top_products[]`:

| Field | Notes |
|---|---|
| `pin_id` · `pin_url` | the permalink is always present |
| `merchant_name` · `title` | free with the category call |
| `image_url` · `image_sizes` | largest url; any other size is the same hash with the path segment swapped |
| `price` · `price_value` · `currency` | **only when `enrichTopN` covered this product**; otherwise `null` = *not fetched*, never 0 |
| `merchant_url` | the real outbound link, tracking params intact |
| `merchant_domain` · `in_stock` · `free_shipping_over` | same condition |
| `enriched` | true when the extra request was made |

### `keywords` — one search term

| Field | Type | Notes |
|---|---|---|
| `term` | string | as sent (lowercased) |
| `status` | string | `ok`, or `no_data` when Pinterest returned nothing for it |
| `has_forecast` | boolean or null | the 🔮 flag. `null` = the field was absent, not `false` |
| `forecast_note` | string or null | present when `has_forecast` is false — says rank on growth instead, and **do not retry** |
| `wow_change` · `mom_change` · `yoy_change` | number or null | fractions. `yoy` is frequently null |
| `seasonality_score` · `search_count` | number | discovery mode only |
| `series` | array | `{date, count, normalized, predicted_lower, predicted_upper}` |
| `age_distribution` · `gender_distribution` | object | per term |
| `related` | array | 5 siblings, each with its own `has_forecast`. Only with `includeRelated` |
| `pin_images` | array | 9 urls. Only with `includeImages` |

### `moments` — one seasonal moment

| Field | Type | Notes |
|---|---|---|
| `slug` | string | wire form, e.g. `fathers day` |
| `phase` | string | `rising`, `approaching`, `cooldown`, `off_season`, `ended` |
| `actionable` | boolean | true for `rising` / `approaching` |
| `next_peak` · `last_peak` | object | `{takeoff_at, peak_at, peak_length_days}`, epoch ms |
| `next_occurrence_at` | number | epoch ms |
| `drilled` | boolean | gated on phase — cooled moments cost no extra requests |
| `series` · `series_points` | array · number | the demand curve. **Daily resolution here and nowhere else in this API** |
| `interest_split` | object | keyed by interest id, each with its own series |
| `keywords` | array | the moment's terms |
| `audience` | object | `{age_distribution, gender_distribution}` |
| `audience_by_interest` | object or null | keyed by interest id. Only with `interestIds` — **includes combinations Pinterest's own UI does not offer** |

### `radar` — one curated trend

| Field | Type | Notes |
|---|---|---|
| `source` | string | `spotlight` or `editorial` |
| `curated_by` | string | always `pinterest` — editorial choice, not organic ranking |
| `id` · `name` · `description` | string | |
| `pct_growth_mom` | number | spotlight only |
| `keywords` | array | editorial: **this region's list only** |
| `campaign_start` · `campaign_end` | string or null | editorial only — when the push began |
| `series` | array | normalised to the trend itself; never compare across trends |
| `pins` | array | `{pin_id, pin_url, image_url, color, width, height}` |

### `_meta` — on every record

| Field | Meaning |
|---|---|
| `end_date` | **Pinterest's settled date, not the run date.** Data lags ~4 days |
| `normalization_scope` | what the relative numbers are relative to. **Different scope ⇒ not comparable** |
| `basis` · `audience_basis` · `chart_basis` · `series_basis` | `measured`, `derived`, `curated`, or `null` (not fetched) |
| `audience_event` | which action the audience was measured under — the same category has a very different audience under `OUTBOUND_CLICK` vs `SAVE` |
| `audience_note` | why a derived value is derived, when it is |
| `absolute_volume` | always `null`, with `absolute_volume_note` explaining that Pinterest never publishes them |

---

## Errors

| Response | Meaning | What to do |
|---|---|---|
| Run fails, *"No usable Pinterest session in the vault."* | no live session backs the actor | not your input — retry later. The actor **fails rather than returning an empty dataset**, because empty would read as "Pinterest has nothing" |
| `InvalidParam: …` | refused before the wire, with the measured reason | fix the input; the message names the valid values |
| Fewer records than requested | Pinterest silently dropped terms it has no data for | absence is not a verdict — measured: 10 keywords requested, 4 returned |
| Empty result for a region | that feature is narrower than the rest | see the region rules above |
| Identical output to last run | the seen-set is working | set `fullRescan: true` to re-emit everything |

---

## Cost

Requests are the currency, and batching keeps them low — 50 categories or 20
keywords is usually **one** request.

| Call | Requests | Records |
|---|---|---|
| `{"operation":"radar"}` | 2 | 11 |
| `{"operation":"keywords"}` | 14 | 10 |
| `{"operation":"moments"}` | 16 | 13 |
| `{"operation":"shopping"}` | 38 | 57 |
| `{"operation":"shopping","verticals":["1042"],"drillTopN":1,"enrichTopN":2}` | 8 | 6 |

Three things have **no batch form** and are billed per item — the only places
cost grows linearly with your request:

- `enrichTopN` — 1 request **per product** (price + merchant link)
- `includeRelated` — 1 request **per keyword**
- each drilled category's product list

---

## Stability

This sits on a reverse-engineered wire with no contract. Two moving parts:

- **`queryHash`** (moment demographics) is a persisted-query hash and rotates
  when Pinterest redeploys. When it does, moment audiences fall back to
  `audience_basis: "derived"` rather than vanishing — the record tells you which
  you got.
- **Endpoint drift** is checked by `probes/probe_endpoints.py` before each
  release; 16/16 must answer or the release stops.
