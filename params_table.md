
### Common to every operation

| Field | Type | Default | Meaning |
|---|---|---|---|
| `operation` | `shopping` \| `keywords` \| `moments` \| `radar` | `shopping` | Which traversal to run. shopping: trending product categories, joined and drilled. keywords: discover/seed/exact keyword research through the full pipeline. moments: seasonal moments with the API's only daily-resolution curves. radar: Pinterest's curated spotlight + Editors' Picks (2 requests, schedule-friendly). |
| `region` | `string` | `US` | Pinterest region code — 32 accepted (US, CA, GB+IE, DE, FR, BR, MX, AU+NZ, JP, KR, SE+DK+FI+NO, …). '+' is literal. Narrower per feature: top_products and editorial serve US/CA/GB+IE only; JP and IN have no seasonal moments at all. |
| `maxRecords` | `integer` | `0` | Hard stop on records pushed. 0 = no limit. |
| `predictedDays` | `integer` | `91` | Days of forecast to request. Max 91 — Pinterest rejects more. Only fills for keywords that have a forecast. |
| `fullRescan` | `boolean` | `false` | Ignore the seen-set: re-emit records already collected in previous runs. |
| `forceRefresh` | `boolean` | `false` | Ignore cached responses: hit Pinterest for every request. |

### `shopping` only

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

### `keywords` only

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

### `moments` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `aggregation` | `daily` \| `weekly` \| `monthly` | `daily` | daily is unique to moments - nothing else in the API resolves below weekly. |
| `phases` | array | — | Which phases get curves + keywords + audience. Default: rising, approaching. All moments are emitted either way; cooled ones cost no extra requests. |
| `drill` | `boolean` | `true` | Off = list every moment with its phase and peak dates only, no per-moment calls. Cheapest possible run. |
| `includeAudience` | `boolean` | `true` | Aggregate the moment's keyword audiences (labelled derived - Pinterest's own moment chart is not reachable via REST). |
| `interestIds` | array | — | Interest ids to break each drilled moment's audience down by. Pinterest's own UI offers only ~7 per moment, but ANY moment x ANY interest works - these are audiences their interface cannot show. Costs one request PER CELL, so keep the list short. Empty = off. |
| `lookbackDays` | `integer` | `365` | Days of moment history. Max 730. |

### `radar` only

| Field | Type | Default | Meaning |
|---|---|---|---|
| `interest` | `string` | — | One interest id to scope the spotlight, or empty for all. |
| `includeSpotlight` | `boolean` | `true` | Pinterest's 5 curated homepage trends. |
| `includeEditorial` | `boolean` | `true` | 6 hand-written editorial trends with campaign windows. US, CA and GB+IE only. |