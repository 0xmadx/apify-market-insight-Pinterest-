# API REFERENCE

Complete surface. For "what is this and why would I use it", read
[CUSTOMER-GUIDE.md](CUSTOMER-GUIDE.md) first — this page assumes you already
know and want the fields.

This page is **checked against the code, not written alongside it.**
`tests/test_dispatch.py` fails the build if `.actor/input_schema.json` offers an
input this page does not document, if the code reads one the form does not
offer, or if any operation emits a field — including any `_meta` key — that is
missing from its output table below. All four directions, every run.

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
| `endDate` | `string` (YYYY-MM-DD) | *newest* | **Ask about the past.** Omit for the newest settled data; set it to see what was trending on that date — `2025-10-15` returns fall nails, halloween nails, fall outfits. History reaches a **different distance per operation** and out-of-range dates are refused before the wire (see *History limits* below). Pinterest snaps the date to its own week boundary. **Forecasts are dropped** for past dates on `keywords` and `shopping` (the endpoints 500 rather than forecast from history); `_meta.forecast_suppressed` says so on the record. |
| `dateRange` | `past_3_months` \| `past_6_months` \| `past_1_year` \| `past_2_years` | *per operation* | **Pinterest's own "Date range" dropdown** — how much HISTORY the chart covers. This is a *window length*; `endDate` is *which date you are looking at*. They compose. Pinterest shows this same control on the keyword page, the product-category page and the moment view, so one input drives all three here. The per-operation inputs below (`days`, `chartDays`, `lookbackDays`) still work and **override** this when you set them explicitly. |
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
| `chartDays` | `integer` | `180` | Days of history in each category's Performance chart. 90 / 180 / 365 / 730. 180 with a 28-day forecast is what draws the dashed prediction band on Pinterest's own detail page. |
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

#### `crawl` only

The other four operations answer one question and stop. `crawl` follows
Pinterest's own navigation — the links that are clickable on its screens and
were, until now, dead ends in our output.

```
Trends overview ──▶ a spotlight trend ──▶ "commonly search for:" ──▶ keyword
                └─▶ Moments ──▶ a moment ──▶ its keywords ──────────▶ keyword
Shopping page   ──▶ a category ──▶ "Search queries" chips ──────────▶ keyword
                                └─▶ Top products ───────────────────▶ pin
Keyword page    ──▶ "Related trends" ──▶ another keyword ──▶ … recursive
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `crawlFrom` | `overview` \| `shopping` \| `search` \| `moments` | `overview` | Which Pinterest page to start from, loaded the way the UI loads it by default. overview = spotlight + editorial + every moment; shopping = Discover trending product categories; search = Discover trending search keywords. |
| `crawlDepth` | `integer` 0–3 | `1` | 0 = the entry page only. 1 = also follow its keyword links. 2 = also follow those keywords' related terms. |
| `maxRequests` | `integer` | `60` | Budget for what the crawl **follows**. See the cost note below — the entry page is a floor this cannot reduce. |
| `maxNodesPerLevel` | `integer` | `50` | Cap on keywords followed per level, so a wide entry page (13 moments × 25 keywords) does not become a level of 325. |
| `verticals` · `drillTopN` · `enrichTopN` · `event` | | | The `shopping` inputs above apply when `crawlFrom` is `shopping` — a crawl still honours the entry page's own filters. |
| `relatedFanout` | `integer` | `10` | How many keywords per level also get their 5 related siblings — the edges the *next* level walks. The crawl's only per-item cost (1 request each, no batch form exists), and only spent when `crawlDepth` is high enough to use it. |

**Why this is cheap.** The crawl is breadth-first and **batched per level**, not
depth-first per node: one `/metrics/` call answers for a whole level. Cost
scales with *depth*, not with how many nodes it finds.

| | requests |
|---|---|
| depth-first, per node (13 moments × 25 keywords × 3 calls) | ~975 |
| breadth-first, per level (what this does) | **~17 for 74 nodes** |

**The budget caps following, not the entry page.** The entry page always loads
in full — half of it would be a wrong answer, not a cheaper one. If the floor
exceeds your budget the crawl loads the page, follows nothing, and says so.

**Every crawl ends with a `crawl_summary` record.** Records stream as they are
found, so one emitted early cannot know the crawl was cut short later. The
summary is written at the end and carries what actually happened:

```json
{ "crawl_summary": true, "entry": "overview",
  "depth_requested": 3, "depth_reached": 0,
  "nodes_total": 24, "nodes_by_kind": {"moment": 13, "trend": 11},
  "requests_spent": 13, "request_budget": 8, "entry_cost": 13,
  "truncated": true, "edges_unfollowed": 140 }
```

`truncated: true` means **the dataset is partial** — raise `maxRequests` or
lower `crawlDepth`. Without this record a truncated crawl is indistinguishable
from a complete one.

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

### The two time controls — they are different

Pinterest's interface has two, and mixing them up is the most common way to
get an answer to a question you did not ask:

| | Control | Question it answers |
|---|---|---|
| **`endDate`** | *End date* picker | **Which date** am I looking at? Move it to see what was trending last October |
| **`dateRange`** | *Date range* dropdown | **How much history** does the chart show? 3 months / 6 months / 1 year / 2 years |

They compose: `{"endDate": "2025-10-15", "dateRange": "past_1_year"}` is "the
year of history ending last October".

Their limits are also independent. Shopping's `endDate` only reaches ~257 days
back, but its **chart** reaches a full 2 years from whatever end date you pick
— measured 109 points at `past_2_years`. A short `endDate` window does not mean
a short chart.

### History limits — how far back `endDate` reaches

Measured 2026-08-19 by binary search, per operation. **They are not the same**,
and the differences are large:

| Operation | Reaches back | At the boundary |
|---|---|---|
| `shopping` | **~257 days** | −257d returns rows, −260d returns **HTTP 200 with an empty list** |
| `keywords` | **~365 days** | −365d returns terms, −400d returns **200 + empty list** |
| `moments` | **~730 days** | −730d returns a full series, −800d returns HTTP 500 |
| `radar` | n/a | curated, always current |

Past the limit these endpoints do not error — they answer *successfully* with
nothing, which reads as "nothing was trending that week". That is a plausible
wrong answer rather than a visible failure, so out-of-range dates are **refused
here**, with the measured reason in the message.

Two caveats worth knowing:

- **The shopping boundary is ragged.** `2025-11-28` returns rows, `2025-11-29`
  returns none, `2025-11-30` returns rows again. Well inside the window there
  are no gaps (21 consecutive days sampled, all answered). So a date near the
  floor can still come back empty; move a few days later.
- **Whether shopping's floor is fixed or rolling is undetermined** — one
  observation cannot tell a data-start date from a rolling window. The cap is
  enforced as a day count deliberately: if the floor turns out to be fixed, the
  cap drifts toward *refusing* dates that would have worked, which is visible.
  The other choice drifts toward silent empties, which is not.

---

## Output

One record per row. Every record carries `_meta`.

### `shopping` — one trending product category

| Field | Type | Notes |
|---|---|---|
| `region` | string | the region this record was measured in — echoed back so a multi-region collection stays separable |
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
| `region` | string | the region this record was measured in |
| `term` | string | as sent (lowercased) |
| `status` | string | `ok`, or `no_data` when Pinterest returned nothing for it |
| `has_forecast` | boolean or null | the 🔮 flag. `null` = the field was absent, not `false` |
| `forecast_note` | string or null | present when `has_forecast` is false — says rank on growth instead, and **do not retry** |
| `wow_change` · `mom_change` · `yoy_change` | number or null | fractions. **`yoy_change` needs `days: 730`** — measured, it is `null` at 180 and 365 and populated only at 730, which is why 730 is now the default. An earlier version of this page called `yoy` "frequently null" as though that were a fact about Pinterest; it was a fact about how much history we asked for |
| `seasonality_score` · `search_count` | number | discovery mode only |
| `series` | array | `{date, count, normalized, predicted_lower, predicted_upper}` |
| `age_distribution` · `gender_distribution` | object | per term |
| `related` | array | 5 siblings, each with its own `has_forecast`. Only with `includeRelated` |
| `pin_images` | array | 9 urls. Only with `includeImages` |

### `moments` — one seasonal moment

| Field | Type | Notes |
|---|---|---|
| `region` | string | the region this record was measured in |
| `phase_label` | string | The word **Pinterest's own screen** shows for `phase`: `cooldown` → `Cooling`, and both `off_season` and `ended` → `Frozen`. `phase` stays the authoritative wire value; this is here so you can reconcile against the Trends UI. An unrecognised phase passes through unchanged rather than being bucketed |
| `slug` | string | wire form, e.g. `fathers day` |
| `phase` | string | `rising`, `approaching`, `cooldown`, `off_season`, `ended` |
| `actionable` | boolean | true for `rising` / `approaching` |
| `next_peak` · `last_peak` | object | `{takeoff_at, takeoff_date, peak_at, peak_date, peak_length_days}`. `*_at` are epoch ms (they sort and diff cleanly); **`*_date` are `YYYY-MM-DD` in UTC** — the same instant, readable. Measured: Thanksgiving 2026 takes off `2026-10-31` and peaks `2026-11-28`, matching the "Week of Nov 28, 2026" on Pinterest's own screen |
| `next_occurrence_at` · `next_occurrence_date` | | when the moment next comes round — epoch ms and `YYYY-MM-DD` |
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
| `region` | string | the region this record was measured in |
| `source` | string | `spotlight` or `editorial` |
| `curated_by` | string | always `pinterest` — editorial choice, not organic ranking |
| `id` · `name` · `description` | string | |
| `pct_growth_mom` | number | spotlight only |
| `interests` | array | the interest ids this trend sits under — join to the same 24 ids `interests` and `interestIds` take |
| `keywords` | array | editorial: **this region's list only** |
| `campaign_start` · `campaign_end` | string or null | editorial only — when the push began |
| `series` | array | normalised to the trend itself; never compare across trends |
| `pins` | array | `{pin_id, pin_url, image_url, color, width, height}` |

### `crawl` — a mixed graph

A crawl re-emits the record shapes above — a `moment` record is the same
`moment` record, a `keyword` record the same `keyword` record — so read the
relevant section for the body fields. What a crawl adds is **where the node
came from**, in `_meta`, plus one terminal record.

| Field | Type | Notes |
|---|---|---|
| `crawl_summary` | boolean | Present and `true` only on the terminal record |
| `entry` · `depth_requested` · `depth_reached` | | summary: what was asked and what was reached |
| `nodes_total` · `nodes_by_kind` | | summary: how many nodes, split by kind |
| `requests_spent` · `request_budget` · `entry_cost` | | summary: what it cost, and the entry page's floor |
| `truncated` | boolean | summary: **`true` means the dataset is partial** |
| `edges_unfollowed` | integer | summary: links left unwalked when it stopped |

Node ids in the dataset are namespaced by kind (`keyword:mascara`,
`category:1311`) because one crawl mixes categories, moments, trends and
keywords, and a keyword must not collide with a category of the same name.

### `_meta` — on every record

| Field | Meaning |
|---|---|
| `end_date` | The settled date this record describes, **not the run date** — data lags ~4 days. Read it together with `end_date_basis` below |
| `end_date_requested` | The date you asked for. On every record, so you can always see the ask next to the answer |
| `end_date_basis` | `echoed` = Pinterest returned the date it used, so `end_date` is *its* answer and may differ from your ask (it snaps to a week boundary: asked `2026-02-15` → answered `2026-02-13`). `keywords` is the only operation that does this. `requested` = the endpoint returns **no date at all** (measured: shopping and moment/metrics contain no date anywhere in the response), so `end_date` is your ask passed through and any snapping is invisible to us. We do not claim it is Pinterest's |
| `normalization_scope` | what the relative numbers are relative to. **Different scope ⇒ not comparable** |
| `basis` · `audience_basis` · `chart_basis` · `series_basis` | `measured`, `derived`, `curated`, or `null` (not fetched) |
| `audience_event` | which action the audience was measured under — the same category has a very different audience under `OUTBOUND_CLICK` vs `SAVE` |
| `audience_note` | why a derived value is derived, when it is |
| `event` | which action the trending ranking itself was measured under (shopping) |
| `mode` | which keyword mode produced the record — `discover`, `seed` or `exact` |
| `aggregation` | the resolution of a moment's series: `daily`, `weekly`, `monthly` |
| `chart_days` · `predicted_days` | the history and forecast windows actually requested |
| `forecast_suppressed` | `null` normally. When you pass a past `endDate`, a string explaining that the **forecast was not requested** — the keyword and shopping chart endpoints return HTTP 500 for any forecast running forward from a historical date. The history is unaffected. This is stated rather than left blank, because a missing forecast otherwise reads as "Pinterest has no forecast for this term", which is a different claim. `moments` is unaffected and keeps its forecast |
| `forecast_region_note` | forecasts have only ever been observed in `US` |
| `regions_covered` | which regions an editorial item ran in — its keywords are this region's list only |
| `note` | how to read this record's series without over-reading it |
| `crawl_entry` · `crawl_depth` | which page the crawl started from, and how many hops out this node is |
| `crawl_path` | **how this node was reached**, e.g. `shopping > keyword`. Without it a crawl dataset is a pile of records with no explanation of why any of them is there |
| `crawl_node_kind` | `category`, `keyword`, `moment`, `trend` or `summary` |
| `crawl_requests_spent` | requests used at the moment this record was emitted |
| `crawl_truncated` | set when the budget had already stopped the crawl. Read the terminal `crawl_summary` record for the authoritative answer — an early record cannot know |
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
