# Doc #7 — Pinterest Trends: COMPLETE API REFERENCE (endpoint-first)

> **Read this one to build a client.** Docs #1–#6 are organised by UI page; this one is
> organised by **endpoint**, because the API and the UI do not map 1:1.

**Why endpoint-first matters:**
- `/top_trends_filtered/` powers **two unrelated UIs** (Moment keyword chips **and** the whole
  `/search` page) with different params.
- `/detail/` is reachable from **5 different UI entry points**.
- There are **two different `demographics` endpoints** with near-identical names and totally
  different subjects.
- Interest IDs, region codes and category IDs are **shared across endpoints** but were each
  discovered on a different page.

---

# 1. TRANSPORT — two call styles

### Style A — ApiResource wrapper (all `/ads/v4/trends/...`)
```
GET https://trends.pinterest.com/resource/ApiResource/get/
    ?source_url=<url-encoded page path>
    &data=<url-encoded JSON>
    &_=<timestamp>

data = {"options":{"url":"<REAL ENDPOINT>","data":{...}},"context":{}}
```
Response envelope:
```jsonc
{ "resource_response": { "status","code","endpoint_name","http_status","data": <PAYLOAD> },
  "client_context": { /* logged-in advertiser/user — ignore */ } }
```
→ payload is always **`resource_response.data`**. Errors appear as
`resource_response.error.message_detail`.

### Style B — plain GET/POST (keyword + shopping-keyword endpoints)
```
GET  /metrics/ · /demographics/ · /related_terms/ · /prefix_match/
     /top_trends_filtered/ · /latest_available_date/
POST /term_images/
```
No envelope — JSON returned directly.

### REQUIRED HEADER (both styles)
```
X-Pinterest-PWS-Handler: trends/index.js
```
⚠️ **The value must be EXACTLY `trends/index.js`.** Verified — every other value 403s:

| Header value | Result |
|--------------|--------|
| `trends/index.js` | ✅ **200** |
| `www/index.js` | ❌ 403 |
| any other string | ❌ 403 |
| empty string | ❌ 403 |
| header omitted | ❌ 403 |

It is an exact-match allowlist, not a presence check.

### ✅ `source_url` and `_` are OPTIONAL — big client simplification

Verified on Style A: **the `source_url` value is irrelevant and the param can be omitted
entirely.**

| Variant | Result |
|---------|--------|
| `source_url=/` | 200 |
| `source_url=/shopping/` | 200 |
| `source_url=/totally/bogus/path` | 200 |
| `source_url=` (empty) | 200 |
| **`source_url` omitted** | **200** |
| **`_` cachebuster omitted** | **200** |

→ A minimal Style A request is just:
```
GET /resource/ApiResource/get/?data=<encoded>
    with header X-Pinterest-PWS-Handler: trends/index.js
```
`context` is always `{}` in the UI and can stay empty.

### Response headers — no rate-limit signalling

Observed headers include `cache-control: private`, `pragma: no-cache`,
`x-pinterest-rid` (request ID — log it for debugging), `x-envoy-upstream-service-time` (ms).

⚠️ **There are NO `x-ratelimit-*` or `retry-after` headers.** A 429 gives you no budget
information — you must back off blindly.

> ⚠️ **Not verified logged-out.** All testing ran inside an authenticated session with an
> advertiser account. Whether these endpoints work anonymously is **untested**.

---

# 2. ENDPOINT INDEX

| # | Endpoint | Style | Doc |
|---|----------|-------|-----|
| 1 | `/latest_available_date/` | B GET | §3.1 |
| 2 | `/ads/v4/trends/topics/featured/{region}/{event}` | A | §3.2 |
| 3 | `/ads/v4/trends/moment/available/{region}` | A | §3.3 |
| 4 | `/ads/v4/trends/moment/metrics/{region}` | A | §3.4 |
| 5 | `/ads/v4/trends/editorial/content/{region}` | A | §3.5 |
| 6 | `/ads/v4/trends/shopping/product_categories` | A | §3.6 |
| 7 | `/ads/v4/trends/shopping/product_categories/top/{region}` | A | §3.7 |
| 8 | `/ads/v4/trends/shopping/product_categories/metrics/{region}` | A | §3.8 |
| 9 | `/ads/v4/trends/shopping/product_categories/demographics/{region}` | A | §3.9 |
| 10 | `/ads/v4/trends/shopping/product_categories/top_products` | A | §3.10 |
| 11 | `…/product_categories/top_products/{merchant_id}/{cat_id}` | A | §3.11 |
| 12 | `…/product_categories/recommendations/{merchant_id}/{region}` | A | §3.11 |
| 13 | `/top_trends_filtered/` | B GET | §3.12 |
| 14 | `/metrics/` | B GET | §3.13 |
| 15 | `/demographics/` | B GET | §3.14 |
| 16 | `/related_terms/` | B GET | §3.15 |
| 17 | `/term_images/` | **B POST** | §3.16 |
| 18 | `/prefix_match/` | B GET | §3.17 |
| 19 | `POST /_/graphql/` | **C GraphQL** | §3.18 ✅ captured + reproduced — moment Age/Gender |
| 20 | `GET www.pinterest.com/resource/PinResource/get/` | A (www host) | §3.19 ✅ captured — price, outbound URL, merchant |

**No endpoint exists for:** Pinterest Predicts (static in JS bundle), CSV Export (client-side
papaparse), region list, interest list (both hardcoded in bundle).

---

# 3. ENDPOINTS

## 3.1 `GET /latest_available_date/` — CALL THIS FIRST
```
GET /latest_available_date/          →  {"date":"2026-08-14"}
```
No params. **Feed `date` into every other call's `end_date`/`endDate`.** Pinterest's data lags;
never use "today".

## 3.2 `/ads/v4/trends/topics/featured/{region}/{event}` — Trends in the spotlight
```jsonc
{"options":{"url":"/ads/v4/trends/topics/featured/US/SAVE",
 "data":{"interests":["934876475639"],"publish_state":"PUBLISHED"}}}
```
| Param | Values |
|-------|--------|
| `{event}` | **`SAVE` only** — others 400 (`must be one of: SAVE`) |
| `interests` | **exactly one** ID, or omit for cross-interest "All". Multiple → 400. `"FASHION"` → 500 |
| `publish_state` | `PUBLISHED` (default if omitted). **`DRAFT` → 400 "User does not have access to unpublished topics"**. `ALL` → 400 |
| `{region}` | §4.1 |

**Returns 5 trends:** `id, name, description, interests[], pct_growth_mom, related_search_trends[]
(~12), pins[] (~9: id/src/width/height/color), time_series[] (13 weekly, peak=100), is_published`.

The trend **detail view fires no request** — it renders from this same object.

## 3.3 `/ads/v4/trends/moment/available/{region}` — Moments list
`data: {}`. Returns **parallel arrays, zip by index**:
```jsonc
{ "moments":["thanksgiving",…], "phase_labels":["approaching",…],
  "peaks":[{peak_timestamp_millis,takeoff_timestamp_millis,peak_length_in_days}],
  "historical_peaks":[…], "moment_next_occurrence_timestamps":["1795852800000",…] }
```
Timestamps are **epoch ms as strings**.

`phase_labels` → UI: `rising`→Rising · `approaching`→Approaching · `cooldown`→Cooling ·
`off_season`/`ended`→Frozen.

> ⚠️ **Moment lists are REGION-SPECIFIC** — see §4.4. US has 13; the global universe is **25**.
> JP and IN return **0**.

## 3.4 `/ads/v4/trends/moment/metrics/{region}` — Moment detail chart
```jsonc
{"moments":["halloween"],"end_date":"2026-08-14","aggregation_level":"weekly",
 "lookback_days":365,"predicted_days":91,"interest_limit":6,"normalize_against_group":false}
```
| Param | Valid | Notes |
|-------|-------|-------|
| `moments` | array of slugs | **multi supported** — 2 slugs → 2 objects returned |
| **`aggregation_level`** | **`daily` \| `weekly` \| `monthly`** | ⭐ **DAILY WORKS HERE** — 456 points vs 66 weekly. Case-insensitive (`DAILY` ok). `hourly` → 400 |
| `lookback_days` | ≤ **730** | 90→26pts, 365→66, 730→118 (weekly). 1095 → 400 `too large: 1095 > 730` |
| `predicted_days` | 0–**91** | 180/365 → 400 `too large: 180 > 91` |
| `interest_limit` | 0–**24** | 0 → no `moment_interests`; 50 → 400 `too large: 50 > 24` |
| `normalize_against_group` | bool | accepted; no visible effect on single-moment calls |

> ⚠️ **`monthly` + `predicted_days=91` → 400** (`91 predicted days do not evenly divide into
> monthly agg`). Predicted days must divide evenly into the aggregation unit.

> ⭐ **This is the ONLY endpoint in the whole API that exposes daily granularity.** Every other
> time series is weekly. If you need day-level resolution, it must come from here.

Returns `data.moments[]`: `name`, `moment.daily_values[]` (`timestamp`, `normal_counts`,
`predicted_normalized_lower/upper_bound_count`), `moment.peaks[]`, and `moment_interests{}`
keyed by interest ID (each with its own `daily_values`).

## 3.5 `/ads/v4/trends/editorial/content/{region}` — Editors' Picks
`data: {}`. Returns **6** curated editorial trends.
```jsonc
[{ "id","title":"Can't Stop Cross-Stitching","body":"<paragraph>",
   "trend_type":"search", "interests":["934876475639"],
   "regions":["US","GB+IE","CA"],
   "keywords":{"US":["embroidery ideas",…5],"GB+IE":[…],"CA":[…]},   // per-region keywords
   "pins":[{src,color,width,height,id,vertical_offset}],              // 6 pins
   "start_date":"2026-08-01","end_date":"","is_published":true,
   "hide_keyword_percentages":false,"is_ready_for_translation":true }]
```
> ⚠️ **Only US / CA / GB+IE return data.** `FR` → 200 with **0 items**. `trend_type` observed
> only as `"search"`. Keywords are nested **per region** inside each item — pick your region's
> array, don't assume a flat list.

UI: homepage **"Editors' Picks"** — card per item with title, "Popular in {interest}", and
keyword chips showing growth (e.g. `embroidery ideas +40%` `+4`).

## 3.6 `/ads/v4/trends/shopping/product_categories` — Category taxonomy
`data: {}`. Returns **383** categories keyed by ID:
```jsonc
{"categories":{"1311":{"friendly_name":"Mascaras","level":3,
  "parent_product_category_id":"1306","children":[],"l2_product_category_ids":["1168"]}}}
```
Levels: **L2=66, L3=190, L4=127**. ⚠️ **L1 verticals are NOT included** — hardcode them (§4.3).
Call once, cache; every shopping endpoint returns IDs only, never names.

## 3.7 `…/product_categories/top/{region}` — Trending categories table
```jsonc
{"event":"OUTBOUND_CLICK","ranking_method":"GROWTH","end_date":"2026-08-14",
 "age_bucket":["AGE_18_24",…],"gender":["MALE","FEMALE","UNSPECIFIED"],
 "parent_product_categories":["1181"],"limit":100,"order_by":"RELATIVE_VOLUME","order":"DESC"}
```
| Param | Values |
|-------|--------|
| `event` | validator allows `IMPRESSION,ENGAGEMENT,CLICK,LONG_CLICK,OUTBOUND_CLICK,SAVE,SEARCH` but **only `OUTBOUND_CLICK`, `ENGAGEMENT`, `SAVE` work** — the rest 500 |
| `ranking_method` | `HIGH_VOLUME`, `GROWTH`, `VIRAL` |
| `order_by` | `TREND_RANK`, `RELATIVE_VOLUME`, `PCT_CHANGE_MOM` |
| `order` | `DESC`/`ASC` |
| `age_bucket` | `AGE_18_24,AGE_25_34,AGE_35_44,AGE_45_49,AGE_50_54,AGE_55_64,AGE_65_PLUS,AGE_ALL` |
| `gender` | `MALE,FEMALE,UNSPECIFIED` |
| `parent_product_categories` | **L1 vertical IDs only** — L2/L3 return **200 with 0 rows (silent)** |
| `limit` | max **522** (1000 → 400 `too large: 1000 > 522`). UI sends 20; use 100+ |
| `order` | `DESC` / `ASC` (`bogus` → 400) |

Returns `{total_num_product_categories, ordered_values:[{product_category (ID only),
parent_product_categories[], related_search_trends[] (~24), summary:{engagement,saves,
outbound_clicks}{percent_growth,percent_relative_volume,total,lookback}}]}`

🚨 **Query ONE vertical per call.** `percent_relative_volume` is normalised *within the
response*: Beauty's top category is `1.00` alone but `0.01` when Fashion is in the same call.
Only 7 of 14 verticals return data (§4.3).

## 3.8 `…/product_categories/metrics/{region}` — Category sparklines / detail chart
```jsonc
{"product_category_ids":["1356",…],"event":"ENGAGEMENT","end_date":"2026-08-14",
 "days":60,"predicted_days":0,"age_bucket":[],"gender":[]}
```
Returns `{values:[{term (=category ID), growth_rates:{wow,mom,yoy}, daily_values:[{date,count,
normalized_predicted_lower_bound,normalized_predicted_upper_bound}]}]}`.
Table uses `days:60, predicted_days:0`; detail page uses `days:180, predicted_days:28`.
**Limits:** `days` ≤ **730** (1095 → 400); `predicted_days` ≤ **91** (180 → 400).

> **This is the ONLY source of the actual sparkline/chart series** (`daily_values`, despite
> the name — weekly-stepped, see §4). §3.9's `summaries` block gives you the *headline*
> growth numbers for the same "Performance" UI section (percent_growth snapshot) — it does
> **not** give you the chart. Building the Performance tab needs both calls; §3.9 alone is
> enough for the Demographics tab and the Search-queries chips.

**Date-range control on the detail page** sends `90D` / `180D` / `365D` / `730D` ("Past 3
months / 6 months / 1 year / 2 years") → becomes `days` here. Detail page defaults to
`days:180, predicted_days:28` (the table uses `days:60, predicted_days:0`) — the
`predicted_days:28` is what produces the dashed "Predicted median" + shaded bounds.

## 3.9 `…/product_categories/demographics/{region}` — ⭐ 3 sections in one call
```jsonc
{"product_category_ids":["1408"],"event":"OUTBOUND_CLICK","end_date":"2026-08-14"}
```
```jsonc
{"product_category_distributions":{"1408":{
  "demographics":[{"age_distribution":{"18-24":0.08,…,"65+":0.32},
                   "gender_distribution":{"male":0.07,"female":0.82,"unspecified":0.11}}],
  "related_search_trends":[…17 terms…],
  "summaries":[{"engagement":{percent_growth,…},"saves":{…},"outbound_clicks":{…}}] }}}
```
Fractions **0–1**. `product_category_ids` is an array → batch. Powers the Demographics charts,
the "Key metric changes" header, **and** the "Search queries" chips.

⚠️ **`event` changes demographics but NOT `related_search_trends`** (identical across all 3
events — verified). Don't re-request keywords per event.

⚠️ **Different endpoint from §3.14** despite the name.

> **Three UI sections, three data sources — do not assume one call covers the drill-down
> page.** All three sit under the same category id, all three take `event` + `end_date`, but
> they answer different questions:
> | UI section | Endpoint | What it returns here |
> |---|---|---|
> | **Demographics** (age/gender charts, Search-queries chips) | §3.9 (this one) | everything, in one call |
> | **Performance** (the "Key metric changes" headline numbers) | §3.9's `summaries` block | comes free with the call above |
> | **Performance** (the actual sparkline/chart) | §3.8 `metrics/` | a **separate** call — §3.9 has no time series |
> | **Top products on Pinterest** | §3.10 `top_products` | a **separate** call, `event` must be `OUTBOUND_CLICK` or it silently returns `[]` |
>
> A full category drill-down page therefore costs **3 calls minimum**
> (§3.9 + §3.8 + §3.10), not one.

### ⚠️ The "Engagement" filter on the detail page — three traps (detail in doc #3 §C4)

**1. The dropdown appears TWICE, independently.** Both the Performance chart (§3.8) and the
Demographics block (§3.9) have their own "Engagement" dropdown. They are not linked — the
page can be showing demographics for one `event` and the chart for another. A client
modelling one page-level `event` will silently mismatch the UI.

**2. The labels do NOT match the shopping table's labels — never map by label.**

| Dropdown label (detail page) | `event` value sent |
|---|---|
| **All** | `ENGAGEMENT` |
| **Outbound clicks** (default here) | `OUTBOUND_CLICK` |
| **Pin saves** | `SAVE` |

The shopping table (§3.7) calls the same `ENGAGEMENT` value **"Engagement"**; here it is
**"All"**. Same enum, different wording, same page family.

**3. `event` changes demographics a LOT, and `related_search_trends` not at all.**
**Independently re-measured 2026-08-19** by `probes/param_matrix.py --group F` on category
`1408`, all three events:

| `event` | 25-34 | 65+ | female | keywords |
|---|---|---|---|---|
| `OUTBOUND_CLICK` | 0.13 | **0.32** | 0.82 | 17 |
| `ENGAGEMENT` | 0.17 | 0.27 | 0.83 | 17 |
| `SAVE` | **0.23** | 0.19 | 0.85 | 17 |

The keyword lists were **byte-identical across all three** (verified by set comparison), while
65+ nearly halves from clicks to saves and 25-34 nearly doubles. So: request the keywords once,
but **never cache demographics across events** — and label which `event` any audience figure
came from, because "the audience for this category" is not a well-formed fact without it.

## 3.10 `…/product_categories/top_products` — Top products (the one that works)
**No path params.** `{"product_category_id":"1408","region":"US","event":"OUTBOUND_CLICK"}`
```jsonc
{"top_products":[{"pin_id","merchant_name","title",
  "images":{"75x75":{url,width,height},"236x":…,"345x":…,"474x":…,"564x":…,"736x":…,"1200x":…}}]}
```
| Constraint | |
|---|---|
| `event` | **`OUTBOUND_CLICK` only** — `SAVE`/`ENGAGEMENT` return **200 with `[]`** (not an error) |
| `region` | **`US`, `CA`, `GB+IE` ONLY** — others 400 |
| `limit` | **ignored** (server decides: 33 US, 50 CA for the same category) |
| `event` omitted | 500 |

Build the pin URL yourself: `https://www.pinterest.com/pin/{pin_id}/`

## 3.11 Merchant-scoped (require a product catalog — unusable without one)
- `…/top_products/{merchant_id}/{product_category_id}` — body `{region,event,limit}` → `data: null`
- `…/recommendations/{merchant_id}/{region}` — body `{}` → `{total_num_product_categories:289, ordered_values:[]}`

Both return 200 with empty payloads on a catalog-less account. **Not reproducible here.**

## 3.12 `GET /top_trends_filtered/` — keyword discovery ⭐ TWO different callers
```
GET /top_trends_filtered/?country=US&endDate=2026-08-14&trendsPreset=3
    &numTermsToReturn=100&l1interests=…&moments=…&ageBuckets=…&gender=…
    &keywordsToInclude=…&lookbackWindow=3&rankingMethod=1&shouldMock=false
```
| Param | Values |
|-------|--------|
| `trendsPreset` | **`1` Top monthly · `2` Top yearly · `3` Growing · `4` Seasonal.** Only 1–4; 0/5/6 → 400 |
| `numTermsToReturn` | 1–**100** (default 50). 500 → 400 |
| `l1interests` | comma IDs (§4.2) |
| `moments` | comma slugs (§4.4) — invalid slug → **400** |
| `ageBuckets` | `2,3`=18-24 · `4`=25-34 · `5`=35-44 · `6`=45-49 · `7`=50-54 · `8`=55-64 · `9`=65+ |
| `gender` | `0`=Male · `1`=Female · `2`=Unspecified |
| `keywordsToInclude` | comma list, **OR** logic, **substring**, **lowercase only** |
| **`lookbackWindow`** | ⚠️ **INERT — no effect** (7 values → identical results) |
| **`rankingMethod`** | ⚠️ **INERT — no effect** (even `"abc"` returns 200 + same rows) |
| `endDate` | accepts **far-past** dates (unlike §3.13) — this is the seasonal time machine |

Returns `{endDate, values:[{term, searchCount, normalizedCount, reverseRank,
seasonality_score, wow_change:{value,index}, mom_change, yoy_change, affinity}]}`

⚠️ **`affinity` is `null` in 100/100 rows** — never populated; ignore it.

⭐ **`wow/mom/yoy_change.index` decoded (probed 2026-08-19, 100 rows):** each growth metric
returns `{value, index}`. `value` is the growth fraction; `index` is the **1..N rank of that
value within this response** — 100 rows produced indexes 1..100, one each, monotone with
value (index 100 = highest growth in the set). It is NOT a stable strength score: it is
response-scoped like every other relative number, and fully derivable by sorting `value`.
Parse it as `rank_in_response` or drop it — never compare it across responses.
⚠️ **No prediction flag here** — the crystal ball requires an extra `/metrics/` call per term.

**Caller A — Moment page chips:** `?country&moments=halloween&endDate&lookbackWindow=2&rankingMethod=1&trendsPreset=1&numTermsToReturn=25`
**Caller B — `/search` page:** all filters above.

## 3.13 `GET /metrics/` — keyword chart + forecast
```
GET /metrics/?terms=nails&country=US&end_date=2026-08-14&days=365
    &aggregation=2&predicted_days=91&normalize_against_group=true&shouldMock=false
```
| Param | Values |
|-------|--------|
| `terms` | 1..N comma-separated. **Silently drops terms with no data — and the drop is far heavier than one.** Measured 2026-08-19: 10 terms → **4 returned**. Dropped included `halloween` and `christmas ornament`, which plainly have volume — so the filter is not simply "unknown term". Never infer a term is dead from its absence here; match by term and mark the rest `no_data`, not `0`. |
| `days` | 90/180/365/730 |
| **`aggregation`** | **`2` ONLY** (weekly, 7-day step). 0/1/3/4 and string values → 400. ⚠️ No daily for KEYWORDS — but **moments DO support daily**, see §3.4 |
| **`predicted_days`** | **0–91**. Points = `predicted_days÷7`. 180/365 → 400 |
| **`normalize_against_group`** | 🚨 `true` = shared scale (comparable). `false` = each term self-normalised to 100 (**not comparable**) |
| **`shouldMock`** | ⚠️ **`true` returns FAKE 2019 data, count 0, HTTP 200.** Always `false` |
| `end_date` | ⚠️ **future → 400**; older than ~1 yr → 400 (`2026-01-01` ok, `2025-08-01` → 400) |

> ⚠️ **Style B errors carry NO message.** `predicted_days=180` returns **HTTP 400 with an
> empty body (`[]`)** — verified 2026-08-19. Unlike Style A, which puts a readable reason in
> `resource_response.error.message_detail` (e.g. `'limit' is too large: 1000 > 522`), the
> plain endpoints tell you only the status code. **A client cannot learn the ceiling from the
> response here** — which is exactly why the ceilings must be enforced client-side before the
> wire (§validation).

Returns `[{term, has_prediction, growth_rates:{wow_change,mom_change,yoy_change},
counts:[{date,count,normalizedCount,predictedUpper/LowerBoundNormalizedCount}]}]`

> ⚠️ **`/metrics/` returns BOTH spellings: `has_prediction` AND `hasPrediction`**, with the
> same value (verified on the wire 2026-08-18, `terms=nails&country=US`). §3.15 returns only
> the camelCase `hasPrediction`. Read whichever you like here, but a parser written against
> §3.15's camelCase will *silently* work on §3.13 and then break the day Pinterest drops the
> duplicate. Pick one spelling and normalise at the parser.

**`has_prediction`** = the 🔮 crystal ball. Fixed per **(keyword, region)** — never changes with
date params. **Appears US-only** (CA/GB+IE/DE/FR/ES all false, incl. Canada's own top keywords).
`yoy_change` is frequently `null`.

## 3.14 `GET /demographics/` — keyword demographics
```
GET /demographics/?terms=nails&country=US&end_date=2026-08-14&days=365
```
```jsonc
{"term_distributions":{"nails":{"age_distribution":{"18-24":0.49,…},
                                "gender_distribution":{"female":0.87,"male":0.04,"unspecified":0.09}}}}
```
Multi-term supported; **key order may differ from request order**.
⚠️ **Different endpoint and different subject from §3.9.** Keyword ≠ product category — same
theme can give opposite audiences (25-34: 13% category vs 45% keyword).

## 3.15 `GET /related_terms/` — 5 related keywords
```
GET /related_terms/?requestTerm=family&country=US&endDate=2026-08-14
    &aggregation=2&lookback=365&shouldMock=false
```
⚠️ **Param names differ from §3.13:** `requestTerm` (not `terms`), `endDate` (not `end_date`),
`lookback` (not `days`), and response uses `hasPrediction` (not `has_prediction`).

Returns 5: `[{term, counts:[…bare numbers…], hasPrediction}]`.
**Forecastability does not propagate** — a forecast parent can have 0/5 forecastable relatives
and vice-versa.

## 3.16 `POST /term_images/` — Popular Pins (**the only POST**)
```jsonc
POST /term_images/
{"terms":["family","nails"],"country":"US","limit":9,
 "requestImageSize":"236x","cacheTtlInSeconds":86400}
→ {"family":["https://i.pinimg.com/236x/…jpg", …9], "nails":[…]}
```
GET variants → **400**. Image URLs only — no pin IDs, titles or merchants.

## 3.17 `GET /prefix_match/` — keyword typeahead ⭐ discovery primitive
```
GET /prefix_match/?query=hallow&country=US
→ [{"term":"hallow","counts":[…52 weekly points…]}, …]   // always 10 max
```
| Input | Result |
|-------|--------|
| `query=HALLOW` | ⚠️ **200 + `[]` — case-sensitive** |
| `query=` empty | ❌ **500** |
| `query=zzzqqq` | 200 `[]` (no match, not an error) |
| `limit=25` | ignored — always 10 |
| `country` | optional; different suggestions per region |

Searches the **whole keyword space** (unlike `keywordsToInclude`, which only filters the
trending set) — works for non-trending terms. No `hasPrediction` field.

## 3.18 `POST /_/graphql/` — moment page Age/Gender ✅ **CAPTURED AND REPRODUCED** (2026-08-19)

The `/moments/{slug}` page's **"Who's driving this moment"** Age+Gender charts are the one
dataset with no reachable REST endpoint.

### What was proven

A **complete cold-load capture** of a moment page (41 requests) shows only these
trends-related calls:
```
GET  /latest_available_date/
A    /ads/v4/trends/moment/available/{region}
A    /ads/v4/trends/moment/metrics/{region}
GET  /top_trends_filtered/?...&moments=<slug>
POST /_/graphql/            ← the only unaccounted-for call
```
`moment/metrics` was re-inspected in full: **zero** matches for `age`, `gender`, `female`, or
`distribution` anywhere in its 64 KB response. None of the other calls carry demographics
either. **By elimination, the GraphQL POST is the source.**

> ⚠️ **Investigation history — kept so this isn't re-litigated with worse information.** An
> earlier pass tentatively reversed "not reproducible" after seeing the generic `/_/graphql/`
> endpoint also fire on `/shopping` (which has no demographics chart) and called that
> "decisive". **That reasoning does not hold** — one shared/generic endpoint can carry a
> different payload per page, so seeing it fire elsewhere says nothing about what it returns
> on a moment page. You cannot identify what this call does by its URL alone. Status is
> "not reproducible" (no REST equivalent), not "impossible to ever capture" (see below).

### One POST, not several — confirmed 2026-08-19

A cold navigation to `/moments/halloween/?country=US` with capture armed **beforehand**
produced **exactly one** POST to `/_/graphql/`, status 200. So on this page there is no
disambiguation problem at all: that single request carries the moment payload, age/gender
included. (The `/shopping` sighting noted above is a different page, hence a different
payload — which is the point.)

### Why the body has not been captured yet — corrected diagnosis

An earlier version of this section said a hook must be installed "before the page's own
JavaScript runs". **That is not the mechanism, and the difference is what unblocks it:**

- The POST fires **once, during hydration**. Everything after is served from **Apollo's
  in-memory JS cache**.
- Therefore DevTools' **"Disable cache" does nothing** — the cache hiding the request is
  not the HTTP cache.
- A hard reload *does* re-fire it, but a reload also **wipes any console patch**. That
  catch-22 — not a timing mistake — is what the five attempts below actually hit.

**The route through:** install an in-page `fetch`/XHR interceptor on an already-loaded
page, then force the SPA router to re-fire the query **without a reload** (switch the
country dropdown, or navigate to another moment and back). Tooling and step-by-step:
[`probes/captures/`](../../probes/captures/README.md).

⚠️ Also recorded there: the `claude-in-chrome` network tool reports **url/method/status
only, never request payloads**, so "Copy as cURL" is not available through it. An in-page
interceptor is the only route to the body.

| Attempt | Result |
|---------|--------|
| Client-side SPA navigation with hook pre-armed | GraphQL did not re-fire (Apollo cache) |
| Scroll to lazy-load the chart | chart rendered, **0 requests** |
| Relay globals (`__PWS_RELAY_SSR_REQUESTS__`) | already consumed / empty |
| Server-rendered HTML | no `age_distribution` in 118 KB of SSR HTML |
| React fiber traversal from the chart node | no demographic props found |

### REST alternatives — all rejected
- `/ads/v4/trends/moment/demographics/{region}` → **404 API method not found**
- `/demographics/?moments=…` → **400**
- `moment/metrics` with `include_demographics` → silently ignored

### ✅ THE CAPTURE — reproduced through a vault session

```
POST https://trends.pinterest.com/_/graphql/
X-Pinterest-GraphQL-Name: useGetMomentDemographicsAdsQuery
X-Pinterest-PWS-Handler: trends/moments/[momentId].js     ← NOT trends/index.js
X-CSRFToken: <csrftoken cookie, echoed>                    ← session-bound
Content-Type: application/json
```
```jsonc
{"queryHash":"85bfe810f1f9a895ec901e57dcbb9b193bfade5c8504299d645ca89053b31a50",
 "variables":{"terms":["halloween"],"region":"US","endDate":"2026-08-14",
              "event":null,"category":"MOMENT"}}
```
```jsonc
{"data":{"trendsDemographicsRead":{"items":[{
  "ageDistribution":[{"key":"18-24","value":0.43},…],        // an ARRAY
  "genderDistribution":{"male":0.05,"female":0.87,…}}]}}}    // an OBJECT
```

Four things here are **not guessable**, and each would have been wrong:

| | |
|---|---|
| **Handler** | `trends/moments/[momentId].js`, page-specific. The global `trends/index.js` does not serve this query. |
| **`queryHash`, not `doc_id`** | and there is **no `operationName` in the body at all** — it lives in the `X-Pinterest-GraphQL-Name` header. Grepping bodies for an operation name finds nothing. |
| **Two different shapes** | age is an array of `{key,value}`; gender is a flat object. `parse_moment_demographics` flattens age to the same dict the REST endpoints return, so consumers see one shape. |
| **Buckets are not decades** | `45-49` and `50-54` are split. |

⚠️ **The fractions do NOT reliably sum to 1** — rounded to 2dp, small buckets round up off a
0.04 floor. Halloween sums to **1.07**; christmas/thanksgiving/hanukkah to 1.00; gender to
1.01 on thanksgiving. Passed through unnormalised: rescaling would invent precision
Pinterest never published.

⚠️ **`queryHash` is a deploy artefact and WILL rotate.** A rotated hash returns HTTP 200
with no `data` — which a naive client reports as "this moment has no audience". Transport
raises `StaleQueryHash` for exactly that case, and `moments.py` degrades to the derived
workaround below rather than emitting nothing. When it rotates, re-capture
(`probes/captures/README.md`); never guess.

✅ **`endDate` resolved:** the capture's `2026-08-14` was five days behind the capture date,
and it is confirmed to be `/latest_available_date/`'s value — the settled data date, not a
client clock read. Bootstrap already supplies it.

Verified live 2026-08-19 through a leased vault session: halloween and christmas both
replayed **byte-identical** to the browser capture, and the moments traversal now emits
`_meta.audience_basis: "measured"`.

### The workaround, now the FALLBACK (derived, not measured)
```
1. GET /top_trends_filtered/?country={r}&moments=<slug>&trendsPreset=1&numTermsToReturn=25
2. GET /demographics/?terms=<those terms>&country={r}&end_date=…&days=90
3. Aggregate the per-term age/gender distributions
```
This approximates moment-level demographics from its constituent keywords. **Label it
`derived`** (see ARCHITECTURE.md §2.3, scenario D4) — it will not match Pinterest's own chart
exactly.

### ⭐ The interest filter — SAME query, two variables that move together

Pinterest's "Filter by relevant interest" dropdown is not a separate query. It
reuses the identical `queryHash`; **two variables change as a pair**:

```jsonc
{"queryHash":"85bfe810…","variables":{
  "terms":["halloween:925056443165"],      // "<moment>:<interestId>", colon-joined
  "region":"US","endDate":"2026-08-14","event":null,
  "category":"MOMENT_INTEREST"}}           // NOT "MOMENT"
```

Move one without the other and you get **HTTP 200 with `items: []`** — no error.
`vocab.moment_terms()` returns both or neither so no call site can desynchronise
them.

**The dropdown is UI CURATION, not a data constraint.** Each moment offers ~7
"relevant" interests; that list is cosmetic. halloween × **Food and Drinks**
(`918530398158`) is *not* offered and returns a full valid distribution:

| halloween × interest | 18-24 | female |
|---|---|---|
| *(no filter)* | 0.43 | 0.87 |
| DIY and Crafts | 0.23 | 0.86 |
| Entertainment | 0.51 | 0.84 |
| **Food and Drinks — not in the UI** | **0.19** | 0.86 |

So the real surface is **any moment × any interest** — 13 × 24 in the US, where
the UI shows ~7 per moment. It generalises across moments (`christmas:961238559656`
works too). ⚠️ Pinterest may curate the list because un-offered pairs are thin;
cells are labelled `offered_in_ui: null` rather than claimed equivalent.

### Failure modes — two of three return HTTP 200

| input | result |
|---|---|
| unknown interest id (`halloween:111111111111`) | **200, `items: []`** |
| terms/category out of sync | **200, `items: []`** |
| malformed variables (`event:"x"`, bad `category` enum) | `{"data":null,"errors":[{"message":"CLIENT GRAPHQL ERROR"}]}` |
| **an UNKNOWN variable added** (`interests:[…]`, `interestId:…`) | **silently ignored — response byte-identical to baseline** |

That last row is the trap: unknown variables against a persisted query do not
error, they vanish. A plausible-looking response is not evidence the variable
worked — diff against a baseline. (Recorded because it was guessed wrong first:
`interests`, `interestId` and `category:"<id>"` were all tried before the
colon-join was found.)

### To re-capture when the hash rotates

Capture the live request from browser DevTools: Network → filter `graphql` → right-click the
POST → **Copy as cURL**. The `operationName`/query hash + `variables` are sufficient to
replay it. **Browser automation cannot do this** — it can't hook before the page's own
scripts run, which is exactly what all five attempts above ran into.

---

## 3.19 PRODUCT / MERCHANT DATA — `www.pinterest.com/pin/{pin_id}/`

⭐ Not part of the Trends API, but it completes the shopping chain. `top_products` (§3.10)
returns `pin_id` but **no merchant URL or price**. Both are available from the pin page.

### What a pin page exposes
For pin `4607745477126792832` (the #1 top-product for *Seasonal & holiday decorations*):

| Field | Value |
|-------|-------|
| Merchant | **Oriental Trading** |
| Outbound host | **`www.orientaltrading.com`** |
| Product title | 97" Halloween Manor Archway Halloween Prop |
| **Price** | **$380.99** |
| Rating | 2.0 (1 review) |
| Shipping | Free shipping with $25+ |
| CTA | "Visit site" |

### The outbound-link endpoint — a dead end for READING data, keep for reference
```jsonc
// via the ApiResource wrapper, on www.pinterest.com
{"options":{"url":"/v3/offsite/",
 "data":{"check_only":true,"client_tracking_params":"…","pin_id":"…","url":"<MERCHANT URL>"}}}
```
⚠️ **The merchant `url` is an INPUT to this call, not an output** — the client already has it
from the pin object before this fires. `/v3/offsite/` is a click-tracking/validation hop, not
a lookup, and it fires **on interaction**, not on page load. **This is NOT the endpoint that
returns merchant name, price, or the outbound URL** — do not build against it expecting a
merchant record back.

> ⚠️ Different host (`www.pinterest.com`, not `trends.pinterest.com`) → different PWS handler
> (`www/index.js`) and a cross-origin boundary. Treat as a separate client.

### ✅ CAPTURED 2026-08-19 — `GET www.pinterest.com/resource/PinResource/get/`

```
GET https://www.pinterest.com/resource/PinResource/get/
  ?source_url=/pin/<pinId>/
  &data={"options":{"id":"<pinId>","field_set_key":"auth_web_main_pin",…},"context":{}}
X-Pinterest-PWS-Handler: www/pin/[id].js      ← page-specific, like the moment page
```

**GET, not POST**, and it travels as XHR — everything is in the query string, there is no
body. `field_set_key: "auth_web_main_pin"` is the load-bearing option: it is what pulls in
the `rich_summary` subtree where commerce data lives.

Root is `resource_response.data`:

| field | path | value |
|---|---|---|
| **outbound URL** | `.link` | full merchant URL **including their `utm_*` / `cm_mmc` tracking** |
| merchant domain | `.link_domain.id` | `www.orientaltrading.com` |
| merchant name | `.closeup_attribution.full_name` | `Oriental Trading` |
| merchant handle | `.closeup_attribution.username` | `orientaltrading` |
| product name | `.rich_summary.display_name` | |
| **price** | `.rich_summary.products[0].offer_summary.price` | `"$380.99"` |
| price numeric | `…offer_summary.price_val` | `380.99` |
| currency | `…offer_summary.currency` | `USD` |
| stock | `…offer_summary.in_stock` / `.availability` | |
| free shipping | `…shipping_info.free_shipping_price` / `_value` | `"$25"` / `25` |

⚠️ `rich_metadata` mirrors `rich_summary` field-for-field in the captured response. Read
**one** — reading whichever happens to be populated is how two sources silently disagree.

⚠️ **Tracking params in `.link` are kept verbatim.** Stripping them changes where the click
is attributed, which is not ours to decide.

### ✅ Verified from code 2026-08-19

Replayed through a leased vault session on Linux — not just captured in a
browser. Beauty → Mascaras returned Amazon.com **$14.85**, Thrive Causemetics
**$46.80**, Target **$8.00**, each with its full outbound merchant URL. The
`www` host accepted the reproducible headers; `X-Pinterest-Platform-BID` and
`X-APP-VERSION` (the two opaque ids we refused to fabricate) proved unnecessary.

### The cost, and why enrichment is off by default

There is **no batch form**: this is `1 + N` requests for an N-row strip, and a drilled
category returns up to 33 products. Enriching every product of every category across seven
verticals would be hundreds of requests against a session shared by every customer. So
`ShoppingScraper(enrich_top_n=0)` is the default, the cap is **per category**, and a failure
leaves the fields `None` rather than taking the category down.

`noCache: true` appears in the observed options — whether these are cacheable per pin is
worth measuring before any wide sweep.

### Q1 answered: the trends surface does not have this, at all

Checked directly rather than assumed. On `/shopping/1408/?country=US`, "Explore top
products" lists 33 rows with an external-link icon each — and:

- **210 anchors on the page, zero external.** The icon is an `<a>` to
  `https://www.pinterest.com/pin/<id>/`, not to the merchant.
- The panel **fires no request**; it renders the `top_products` payload already fetched.
- That payload has exactly four fields: `pin_id`, `merchant_name`, `title`, `images`. No
  `link`, no price, no domain. `merchant_name` is a display string, not a URL.

So there is nothing to grep for on the trends side. Resolving each `pin_id` individually is
the only route.

**Chain once the read-endpoint is captured:** `product_categories/top` → category →
`top_products` → `pin_id` → the pin's resource call → merchant + price + outbound URL. This
would yield **real, linkable product pages per trending category** — something the Trends UI
never shows.

---

# 4. SHARED VOCABULARY

## 4.1 Regions (path segment or `country=`)
```
US, CA, DE, FR, ES, IT, DE+AT+CH, GB+IE, IT+ES+PT+GR+MT, PL+RO+HU+SK+CZ,
SE+DK+FI+NO, NL+BE+LU, AR, BR, CO, MX, MX+AR+CO+CL, AU+NZ, JP, IN, ID, MY,
PH, TH, SA, EG, AE+SA+KW+QA+OM+BH+EG+IQ+DZ, IL+NG+PK+ZA+TR+MA+IN,
CR+DO+EC+GT+PE, CY+CZ+GR+HU+MT+PL+RO+SK, TR, KR
```
`+` is literal in paths. UI exposes 26; API accepts 32. **Narrower per endpoint:**
`top_products` = US/CA/GB+IE only; `editorial` returns data only for US/CA/GB+IE.

## 4.2 Interest IDs (24 — `l1interests`, `interests`)
| Interest | ID | | Interest | ID |
|---|---|---|---|---|
| Animals | `925056443165` | | Gardening | `909983286710` |
| Architecture | `918105274631` | | Health | `898620064290` |
| Art | `961238559656` | | Home Decor | `935249274030` |
| Beauty | `935541271955` | | Men's Fashion | `924581335376` |
| Children's Fashion | `903733943146` | | Parenting | `920236059316` |
| Design | `902065567321` | | Quotes | `948192800438` |
| DIY and Crafts | `934876475639` | | Sport | `919812032692` |
| Education | `922134410098` | | Travel | `908182459161` |
| Electronics | `960887632144` | | Vehicles | `918093243960` |
| Entertainment | `953061268473` | | Wedding | `903260720461` |
| Event Planning | `941870572865` | | Women's Fashion | `948967005229` |
| Finance | `913207199297` | | Food and Drinks | `918530398158` |

Spotlight (§3.2) exposes a 16-item subset + pseudo-values `ALL` and `FASHION`.
Editorial returns IDs outside this list — the underlying taxonomy is larger.

## 4.3 Shopping verticals (L1 — hardcode; API never returns them)
| ID | Vertical | Trend data? |
|----|----------|-------------|
| `1181` | Fashion | ✅ 19 rows |
| `1250` | Home decor | ✅ 9 |
| `1042` | Beauty | ✅ 6 |
| `1148` | DIY | ✅ 3 (hidden from UI) |
| `1016` | Arts & entertainment | ✅ 2 (hidden) |
| `1500` | Wedding | ✅ 2 (hidden) |
| `1315` | Media | ✅ 1 (hidden) |
| `1161` `1007` `1194` `1241` `1436` `1481` `1489` | Electronics, Animals & pet supplies, Food & beverages, Hardware, Sporting goods, Toys & games, Vehicles & parts | ❌ 0 rows |

**The UI exposes only 3; the API serves 7.** Full 383-category tree → Doc #5 / `pinterest-category-taxonomy.json`.

## 4.4 Moment slugs — ⚠️ REGION-SPECIFIC (25 total, not 13)
| Region | n | Slugs |
|--------|---|-------|
| US | 13 | christmas, easter, fathers day, halloween, hanukkah, independence day, memorial day, mothers day, new years eve, st patricks day, summer, thanksgiving, valentines day |
| CA | 12 | + **superbowl, canada day, diwali, lunar new year** − easter/hanukkah/independence day/memorial day/st patricks day |
| GB+IE | 11 | + **prom, lunar new year** |
| FR | 11 | + **mardi gras, ramadan** |
| DE | 12 | + **oktoberfest, karneval, spring, ramadan** |
| ES | 8 | core set only |
| IT | 9 | + **carnevale martedì grasso** ⚠️ non-ASCII — URL-encode |
| BR | 9 | + **carnaval** |
| MX | 8 | core set only |
| AU+NZ | 13 | + **diwali, lunar new year, mardi gras, ramadan** |
| **JP, IN** | **0** | **no moments at all** |

Format: **lowercase, spaces kept, apostrophes stripped** (`Father's Day` → `fathers day`).
Wrong form → **400**.

## 4.5 Age / gender codes (two different schemes!)
| | Shopping (§3.7/3.8) | Keyword (§3.12) |
|---|---|---|
| 18-24 | `AGE_18_24` | **`2,3`** (two codes) |
| 25-34 | `AGE_25_34` | `4` |
| 35-44 | `AGE_35_44` | `5` |
| 45-49 | `AGE_45_49` | `6` |
| 50-54 | `AGE_50_54` | `7` |
| 55-64 | `AGE_55_64` | `8` |
| 65+ | `AGE_65_PLUS` | `9` |
| all | `AGE_ALL` | omit |
| Male / Female / Unspecified | `MALE`/`FEMALE`/`UNSPECIFIED` | `0`/`1`/`2` |

---

# 5. GLOBAL TRAPS

| # | Trap | Detail |
|---|------|--------|
| 1 | **Silent case sensitivity** | `keywordsToInclude` and `prefix_match?query=` return **200 + empty** on any non-lowercase input. Always `.toLowerCase()` |
| 2 | **Response-scoped normalisation** | `normalize_against_group` (§3.13) and `percent_relative_volume` (§3.7) are normalised *within the response*. Wrong flag → small items overstated 10× |
| 3 | **Dead params** | `lookbackWindow` and `rankingMethod` (§3.12) have **zero effect**, even with garbage values |
| 4 | **Unknown params ignored** | Typo'd param names are silently dropped, not rejected |
| 5 | **Silent empty ≠ error** | L2/L3 in `parent_product_categories`; `SAVE` on `top_products`; wrong-case keywords — all 200 with empty payloads |
| 6 | **Two `demographics` endpoints** | §3.9 (category) vs §3.14 (keyword) — same field names, opposite answers |
| 7 | **`shouldMock=true` = fake data** | 2019 dates, all zeros, HTTP 200 |
| 8 | **`end_date` bounds vary** | `/metrics/` rejects future + >1yr past; `/top_trends_filtered/` accepts far-past |
| 9 | **Everything is indexed** | `count`, `searchCount`, `normalizedCount`, `normal_counts` are peak-normalised, **not volumes**. `total` is 0 |
| 10 | **Rate limiting** | `/metrics/` returned **429** under load. Back off; batch `terms` |
| 11 | **Multi-term shrinkage** | 10 terms → 9 returned; order not preserved. Never assume `len(resp)==len(terms)` |
| 12 | **`affinity` always null** | 0/100 rows populated |

---

# 6. NOT AN API (don't go looking)

| Feature | Reality |
|---------|---------|
| **Pinterest Predicts 2026** | **Hardcoded in `/webapp/trends/index-*.mjs`** with localized strings + static webp images. 21 items, client-side pagination. **No endpoint.** |
| **Export (CSV)** | **Client-side** via bundled `vendor-papaparse`. Clicking fires no request. Rebuild from the JSON yourself. |
| **Region list / Interest list** | Hardcoded `<select>` options in the bundle. No endpoint. |
| **Trend detail (spotlight)** | No request — rendered from §3.2's response. |
| **"Predict the future" toggle** | Client-side show/hide of data already in §3.13. |
| **"All categories" tab** | Rendered from cached §3.6 taxonomy. |
| **"Other product categories"** | Taxonomy siblings, computed client-side. |

---

# 7. NEGATIVE RESULTS — endpoints that do NOT exist

Probed by sibling-pattern guessing against the known-good control
(`topics/featured/US/SAVE` → 200). **All returned `404 API method not found`:**

```
/ads/v4/trends/topics/{region}                      404
/ads/v4/trends/topics/metrics/{region}              404
/ads/v4/trends/topics/demographics/{region}         404
/ads/v4/trends/keywords/{region}                    404
/ads/v4/trends/search/{region}                      404
/ads/v4/trends/interests                            404
/ads/v4/trends/interests/{region}                   404
/ads/v4/trends/regions                              404
/ads/v4/trends/shopping/product_categories/related/{region}   404
/ads/v4/trends/shopping/brands/{region}             404
/ads/v4/trends/moment/keywords/{region}             404
/ads/v4/trends/moment/demographics/{region}         404
/ads/v4/trends/editorial/content   (no region)      405 Method not allowed
```

**Conclusions:**
- There is **no endpoint for the interest list or region list** — hardcode them (§4.1, §4.2).
- There is **no moment-level demographics endpoint** (§3.18).
- There is **no topic-level metrics or demographics endpoint** — the spotlight response is
  self-contained.
- The `/ads/v4/trends/` namespace is **exactly the 12 endpoints in §2**, nothing hidden.

> Method note: `editorial/content` without a region returns **405**, not 404 — the route exists
> but requires the `{region}` segment.

---

# 8. ROUTE MAP (UI URLs, for deep-linking)

Only **5 routes** exist:

| Route | Purpose | Params |
|-------|---------|--------|
| `/` | Trends overview (spotlight, moments, shopping, editorial, predicts) | `?country=US&topicInterestIds=<interestId>` |
| `/shopping` | Shopping trends table | `?country=US` |
| `/shopping/{category_id}/` | Product-category detail | `?country=US` |
| `/search` | Keyword discovery | `?country=US` |
| `/detail/` | Keyword dashboard | `?country=US&terms=<kw>&dateRange=90D` |
| `/moments/{slug}/` | Moment detail | `?country=US` |

⚠️ **Moment slugs are URL-encoded in paths** — spaces become `%20`:
`/moments/new%20years%20eve`, `/moments/st%20patricks%20day`, `/moments/valentines%20day`.

External hand-offs: `ads.pinterest.com/automated/ads/create/` ("Create campaign"),
`ads.pinterest.com/advertiser/{id}/media_planner/plan` ("Create media plan").

---

# 9. UNIVERSAL LIMITS (verified via explicit API error messages)

The API returns precise ceilings — these are not guesses:

| Limit | Value | Error text |
|-------|-------|------------|
| Forecast horizon | **91 days** | `'predicted_days' is too large: 180 > 91` |
| History window | **730 days** | `'lookback_days' is too large: 1095 > 730` / `'days' is too large: 1095 > 730` |
| Shopping `top/` rows | **522** | `'limit' is too large: 1000 > 522` |
| Moment interests | **24** | `'interest_limit' is too large: 50 > 24` |
| Keyword rows | **100** | 500 → 400 |
| Typeahead | **10** | `limit` ignored |

**Granularity:** weekly everywhere **except** `moment/metrics`, which uniquely supports
`daily` / `weekly` / `monthly` (§3.4).

---

# 10. NAV → ENDPOINT MAP (what each sidebar destination actually loads)

The left sidebar has exactly **3 parent destinations**. Captured from a cold load of each:

## 10.1 🏠 "Trend overview" → `/` — **11 endpoints** (the heaviest page)

```
B  /latest_available_date/                                    ← bootstrap
A  /ads/v4/trends/topics/featured/US/SAVE                     → "Trends in the spotlight"
A  /ads/v4/trends/moment/available/US                         → "Moments"
A  /ads/v4/trends/shopping/product_categories                 → taxonomy (for names)
A  /ads/v4/trends/shopping/product_categories/top/US          → "Shopping trends"
A  /ads/v4/trends/shopping/product_categories/metrics/US      → shopping sparklines
A  /ads/v4/trends/shopping/product_categories/recommendations/{merchant}/US   → (empty w/o catalog)
B  /top_trends_filtered/                                      → "Search trends" preview ⭐
B  /metrics/                                                  → preview sparklines
B  /term_images/  (POST)                                      → preview thumbnails
A  /ads/v4/trends/editorial/content/US                        → "Editors' Picks"
```

> ⭐ **The homepage contains a "Search trends" PREVIEW section** (a keyword table ending in
> *"View the full list ›"*) that is not part of the `/search` page. It uses the same
> `/top_trends_filtered/` + `/metrics/` + `/term_images/` trio. Easy to miss when mapping by UI.

**Pinterest Predicts 2026** appears on this page but fires **nothing** — static bundle data (§6).

## 10.2 🛒 "Shopping trends" → `/shopping` — **5 endpoints**

```
B  /latest_available_date/
A  /ads/v4/trends/shopping/product_categories                 → taxonomy
A  /ads/v4/trends/shopping/product_categories/top/US          → the table
A  /ads/v4/trends/shopping/product_categories/metrics/US      → sparklines
A  …/product_categories/recommendations/{merchant}/US         → (empty w/o catalog)
```
Child route `/shopping/{category_id}/` additionally fires
`…/demographics/{region}` + `…/top_products` (+ a merchant-scoped `top_products` that returns null).

## 10.3 🔍 "Search trends" → `/search` — **5 endpoints**

```
B  /latest_available_date/
A  /ads/v4/trends/moment/available/US        ← ⚠️ populates the MOMENTS FILTER dropdown
B  /top_trends_filtered/                     → the keyword table
B  /metrics/                                 → row sparklines
B  /term_images/  (POST)                     → row thumbnails
```

> ⚠️ **Dependency worth knowing:** `/search` calls `moment/available/{region}` **not** to show
> moments, but to populate its **Moments filter options**. This is why the valid `moments=`
> slugs on `/top_trends_filtered/` are region-specific (§4.4) — the filter list is fetched
> per-region at page load.

## 10.4 Cross-route summary

| Endpoint | Overview | Shopping | Search |
|----------|:--------:|:--------:|:------:|
| `latest_available_date` | ✅ | ✅ | ✅ |
| `topics/featured` | ✅ | — | — |
| `moment/available` | ✅ | — | ✅ *(filter)* |
| `product_categories` (taxonomy) | ✅ | ✅ | — |
| `product_categories/top` | ✅ | ✅ | — |
| `product_categories/metrics` | ✅ | ✅ | — |
| `product_categories/recommendations` | ✅ | ✅ | — |
| `editorial/content` | ✅ | — | — |
| `top_trends_filtered` | ✅ *(preview)* | — | ✅ |
| `metrics` | ✅ | — | ✅ |
| `term_images` | ✅ | — | ✅ |

**Only `/latest_available_date/` is universal.** Everything else is route-specific — which is
why a UI-first reading of this API misleads: the *overview* page alone touches 6 of the 7
datasets.

**Endpoints reachable ONLY by interaction (never on a parent route load):**
`product_categories/demographics`, `product_categories/top_products`, `moment/metrics`,
`demographics`, `related_terms`, `prefix_match`.
