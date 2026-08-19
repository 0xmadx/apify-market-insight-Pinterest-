# Pinterest Trends — "Shopping trends" / Discover trending product categories

> Page: https://trends.pinterest.com/shopping/  (left nav → **Shopping trends**)
> Heading: **"Discover trending product categories"** —
> "Explore how product categories are trending across Pinterest. Trending categories are
> updated weekly to keep it fresh."

- **PART A — code agent:** endpoints, full payloads, every enum value.
- **PART B — marketer agent:** what the data means.

> ⚠️ **RULE: QUERY EACH VERTICAL SEPARATELY. NEVER COMBINE THEM.** See §A4 — this is not a
> style preference, combining actively loses data.

---

## PART A — For the CODE agent

All endpoints below use the **ApiResource wrapper** + header
`X-Pinterest-PWS-Handler: trends/index.js` (see Doc #0 §0).
Get `end_date` from `/latest_available_date/` first (Doc #0 §1).

### A1. The ranked list — `/ads/v4/trends/shopping/product_categories/top/{REGION}`

This is the main table. **Full payload** (captured live from the UI):

```jsonc
{ "options": {
  "url": "/ads/v4/trends/shopping/product_categories/top/US",
  "data": {
    "event": "OUTBOUND_CLICK",              // <-- the "Ranked by:" dropdown
    "ranking_method": "GROWTH",
    "end_date": "2026-08-14",
    "age_bucket": ["AGE_18_24","AGE_25_34","AGE_35_44",
                   "AGE_45_49","AGE_50_54","AGE_55_64"],
    "gender": ["MALE","FEMALE","UNSPECIFIED"],
    "parent_product_categories": ["1181"],   // <-- the "Top vertical" filter. ONE ID. See §A4.
    "limit": 20,
    "order_by": "RELATIVE_VOLUME",
    "order": "DESC"
  }
}, "context": {} }
```

#### `event` — the "Ranked by:" dropdown  ⭐ (what you asked me to test)

The dropdown exposes exactly **3** options:

| UI label | `event` value | UI description |
|----------|---------------|----------------|
| **Outbound clicks** (default) | `OUTBOUND_CLICK` | "Engagement from outbound clicks" |
| **Engagement** | `ENGAGEMENT` | "Total engagement from clicks and saves" |
| **Pin saves** | `SAVE` | "Engagement from Pin saves" |

The API's validator accepts a **wider** enum than the UI offers:
```
'event' must be one of: IMPRESSION, ENGAGEMENT, CLICK, LONG_CLICK, OUTBOUND_CLICK, SAVE, SEARCH
```
**But only the 3 UI values actually work.** Verified by probe:
`OUTBOUND_CLICK` → 200 · `ENGAGEMENT` → 200 · `SAVE` → 200 ·
`IMPRESSION` → **500** · `CLICK` → **500** · `LONG_CLICK` → **500** · `SEARCH` → **500**.
→ Code agent: treat the enum as **`OUTBOUND_CLICK | ENGAGEMENT | SAVE`** only.

Changing `event` also relabels the table columns: "Outbound clicks growth / volume" →
"Engagement growth / volume" → "Pin saves growth / volume".

#### Every other enum (probed against the validator)

| Param | Accepted values | Notes |
|-------|-----------------|-------|
| `ranking_method` | `HIGH_VOLUME`, `GROWTH`, `VIRAL` | UI uses `GROWTH`; the other two are not exposed in the UI |
| `order_by` | `TREND_RANK`, `RELATIVE_VOLUME`, `PCT_CHANGE_MOM` | UI uses `RELATIVE_VOLUME`; column-header arrows switch this |
| `order` | `DESC` / `ASC` | sort direction |
| `age_bucket` | `AGE_18_24`, `AGE_25_34`, `AGE_35_44`, `AGE_45_49`, `AGE_50_54`, `AGE_55_64`, `AGE_65_PLUS`, `AGE_ALL` | array. ⚠️ UI's "Age (6)" chip set omits `AGE_65_PLUS` |
| `gender` | `UNSPECIFIED`, `FEMALE`, `MALE` | array |
| `limit` | integer | UI uses 20 |
| `parent_product_categories` | array of vertical IDs | **pass exactly one — see §A4** |
| `{REGION}` | path segment | same region codes as Doc #0 §4 |

#### Response — `resource_response.data`

```jsonc
{
  "total_num_product_categories": 289,
  "ordered_values": [
    {
      "product_category": "1311",                    // ID ONLY — no name. Join via §A3.
      "parent_product_categories": ["1306","1168"],  // its ancestor chain
      "related_search_trends": [ "eyelashes", "eye makeup", ... ],   // ~24 search terms
      "summary": {
        "engagement":      { "percent_growth": 0.82, "percent_relative_volume": 0.8,  "total": 0, "lookback": 2 },
        "saves":           { "percent_growth": -0.13,"percent_relative_volume": 0.25, "total": 0, "lookback": 2 },
        "outbound_clicks": { "percent_growth": 1.23, "percent_relative_volume": 1,    "total": 0, "lookback": 2 }
      }
    }
  ]
}
```

> **Note:** `summary` always returns **all three** metric families regardless of which `event`
> you ranked by. So one call gives you engagement + saves + outbound clicks for each category.
> `percent_growth` → the "↑44% MoM" column. `percent_relative_volume` → the volume bar (0–1).
> `total` is 0 for this account (absolute totals appear gated).

### A2. The sparkline data — `/ads/v4/trends/shopping/product_categories/metrics/{REGION}`

Fired right after A1, using the IDs A1 returned. Powers the "Trend" mini-chart column.

```jsonc
{ "options": {
  "url": "/ads/v4/trends/shopping/product_categories/metrics/US",
  "data": {
    "product_category_ids": ["1356","1108","1350", ...],   // the 20 IDs from A1
    "event": "ENGAGEMENT",         // must match A1's event
    "end_date": "2026-08-14",
    "days": 60,                    // lookback window
    "predicted_days": 0,           // >0 to request a forecast
    "age_bucket": ["AGE_18_24", ...],
    "gender": []                   // note: UI sends [] here even when A1 sends all three
  }
}, "context": {} }
```

**Response:**
```jsonc
{ "values": [
    { "term": "<category id>",
      "growth_rates": { "wow_change": .., "mom_change": .., "yoy_change": .. },
      "daily_values": [ { "date": "...", "count": 12,
                          "normalized_predicted_lower_bound": null,
                          "normalized_predicted_upper_bound": null }, ... ]   // 9 points
    } ] }
```

### A3. The category taxonomy — `/ads/v4/trends/shopping/product_categories`

**You need this** — A1/A2 return category **IDs only**, never names. Call once, cache, join.

Payload: `{"options":{"url":"/ads/v4/trends/shopping/product_categories","data":{}},"context":{}}`

```jsonc
{ "categories": {
    "1311": { "friendly_name": "Mascaras",
              "level": 3,
              "parent_product_category_id": "1306",
              "children": [],
              "l2_product_category_ids": ["1168"] }, ... } }
```
- **383 categories**, keyed by ID. Levels present: **L2 = 66, L3 = 190, L4 = 127**.
- ⚠️ **L1 (the verticals) are NOT in this map.** `friendly_name` lookup on `1181`/`1250`/`1042`
  returns undefined — hardcode those three names (§A4).
- This is also what powers the **"All categories"** tab (an A–Z browse list). That tab fires
  **no new request** — it renders from this cached taxonomy.

### A4. ⚠️ THE VERTICALS — QUERY EACH ONE SEPARATELY

`parent_product_categories` is the "Top vertical" filter. The UI ships all three at once
(`["1181","1250","1042"]`) — **do not copy that.**

| Vertical | ID | L2 children | Identify by |
|----------|-----|------------|-------------|
| **Fashion** | `1181` | 9 | Accessories, Bags & luggage, Clothing, Costumes & accessories, Jewelry & watches… |
| **Home decor** | `1250` | 14 | Bathroom accessories, Bedding, Furniture, Household appliances… |
| **Beauty** | `1042` | 9 | Bath & body, Fragrance, Hair, Makeup, Nails, Skincare… |

> ⚠️ **CORRECTION (see §C9):** an earlier version of this doc said combining is capped at 20.
> That was the `limit:20` the UI sends — raising `limit` DOES return all rows. The real reason
> to keep verticals separate is **relative-volume normalization**, proven in §C9.3.

**Why separate — measured proof.** With the UI's `limit:20`, combining verticals truncates the
smaller ones. Same params, only the vertical changed:

| Vertical (alone) | `OUTBOUND_CLICK` | `ENGAGEMENT` | `SAVE` |
|------------------|------------------|--------------|--------|
| Fashion (1181) | 17 rows | 17 rows | 5 rows |
| Home decor (1250) | 9 rows | 9 rows | 5 rows |
| Beauty (1042) | 6 rows | 7 rows | 2 rows |
| **All three combined** | **20 (capped)** | **20 (capped)** | — |

17 + 9 + 6 = **32 available rows**, but the combined call returns **20**. Fashion dominates
the merged ranking and Beauty gets squeezed out. Beauty's top category (`1311` Mascaras) is
**not visible at all** in the combined SAVE view.

**Correct usage — 3 separate calls, one per vertical:**
```
top/US  { event:"OUTBOUND_CLICK", parent_product_categories:["1181"], ... }   // Fashion
top/US  { event:"OUTBOUND_CLICK", parent_product_categories:["1250"], ... }   // Home decor
top/US  { event:"OUTBOUND_CLICK", parent_product_categories:["1042"], ... }   // Beauty
```
Keep the three result sets **separate** downstream too — each vertical's `percent_relative_volume`
is normalized within its own response, so a "1.0" in Beauty is NOT comparable to a "1.0" in Fashion.

> There are **14** L1 verticals in the taxonomy (also incl. Electronics `1161`, DIY/craft `1148`,
> Food `1194`, Media `1315`, Animals & pet supplies, Arts & entertainment, …), and the
> "All categories" tab shows chips for all of them. But **only Fashion, Home decor and Beauty
> have trend data** and are selectable in the "Top vertical" filter.

### A5. Merchant endpoints (require a product catalog)

| Endpoint | Payload | Result on a catalog-less account |
|----------|---------|-----------------------------------|
| `…/product_categories/recommendations/{merchant_id}/{REGION}` | `{}` | 200, `{total_num_product_categories: 289, ordered_values: []}` — **empty** |
| `…/product_categories/top_products/{merchant_id}/{product_category_id}` | `{"region":"US"}` ⚠️ region goes in the **body**, not the path | 200, `data: null` — **empty** |

Both need a merchant with an active catalog. `top_products` without `region` → 400
`The region None must be one of: US, CA, GB+IE` (note: narrower region support than other endpoints).

### A6. Click/interaction map

| Action | Fires |
|--------|-------|
| Load `/shopping/` | `product_categories` (taxonomy) + `top/{REGION}` + `metrics/{REGION}` |
| Change "Ranked by" | `top/` + `metrics/` re-fire with the new `event` |
| Change Top vertical / Age / Gender | `top/` + `metrics/` re-fire |
| **"All categories" tab** | **nothing** — renders from cached taxonomy |
| Grid ⇄ List view toggle | nothing — client-side |
| "Reset filters" | restores defaults, re-fires |

---

## PART B — For the MARKETER agent

### What this is

A weekly-refreshed ranking of **product categories** (not keywords, not topics) that are
trending on Pinterest — the commerce view. Where "Trends in the spotlight" tells you what
people are *into*, this tells you **what kinds of products they're actually engaging with and
clicking through to buy.**

### The "Ranked by" dropdown — pick the right intent signal

This is the most important control. It changes what "trending" means:

- **Outbound clicks** — people clicked *off Pinterest to the retailer*. **Closest to purchase
  intent.** Use for performance/conversion planning.
- **Engagement** — clicks + saves combined. The broadest "attention" measure. Use for reach.
- **Pin saves** — people bookmarked it for later. **Planning/consideration intent**, earlier in
  the funnel and often earlier in the season. Use for spotting things before they convert.

A category ranking high on *saves* but not yet on *outbound clicks* is a **leading indicator** —
demand is forming but hasn't converted yet. That gap is the opportunity.

### What you get per category

- **Rank** and a **thumbnail**.
- **Growth** (e.g. "↑44% MoM") — momentum vs last month.
- **Volume bar** — relative size *within that vertical*.
- **Trend sparkline** — the shape over the last ~60 days.
- **Related search trends** — ~24 actual search terms for that category. Same keyword goldmine
  as elsewhere; feeds directly into copy and targeting.
- All three metrics (engagement / saves / outbound clicks) come back for every category, so you
  can compare intent stages for the same product category side by side.

### Filters

- **Top vertical** — Fashion, Home decor, Beauty (only these three have trend data).
- **Age** — 18-24 through 65+.
- **Gender** — Female / Male / Unspecified.
- **Region** — same market list as the rest of the site.

### ⚠️ Read one vertical at a time

Ask for **one vertical per view**. With all three on at once the list is capped at 20 rows and
Fashion crowds out the others — in testing, Beauty's top category didn't appear at all in the
combined view. Look at Fashion, then Home decor, then Beauty, as three separate lists.

Also: the volume bar is relative **within its own vertical**. A full bar in Beauty and a full bar
in Fashion do not mean the same amount of activity. Never rank across verticals on that number.

### Caveats

- Growth percentages are real percentages, but volume is **relative/indexed**, not absolute
  units — absolute `total` comes back as 0 on non-catalog accounts.
- "Updated weekly" — don't expect day-level freshness; anchor to `latest_available_date`.
- The merchant-specific views (recommended categories, top products) need a connected product
  catalog and return empty without one.

---
---

# PART C — Clicking a category → the PRODUCT CATEGORY detail page

Clicking a row in the Trending categories table (e.g. **Pants**) navigates to
**`/shopping/{product_category_id}/`** — a full page titled **"Product category"**.
(Pants = `1356`. The ID in the URL is the `product_category` value from the A1 response.)

> ⚠️ Unlike the spotlight and moments list, **this page DOES fetch new data** — 4 calls.
> It is NOT rendered from the table response.

## C1. The 4 calls it fires

All use the ApiResource wrapper + `X-Pinterest-PWS-Handler: trends/index.js`.

### 1. Top products (the real one) — `/ads/v4/trends/shopping/product_categories/top_products`
⚠️ **Note: NO path parameters on this variant.** Everything goes in the body.
```jsonc
{ "options": { "url": "/ads/v4/trends/shopping/product_categories/top_products",
  "data": { "product_category_id": "1408", "region": "US", "event": "OUTBOUND_CLICK" } } }
```
**Response** — real shoppable products (33 returned in testing):
```jsonc
{ "top_products": [
    { "pin_id": "4607745477126792832",
      "merchant_name": "Oriental Trading",
      "title": "97\" Halloween Manor Archway Halloween Prop",
      "images": {                                  // 7 sizes, all with url/width/height
        "75x75": {...}, "236x": {...}, "345x": {...},
        "474x": {...}, "564x": {...}, "736x": {...}, "1200x": {...} } }, ... ] }
```
> This corrects Doc §A5. The **merchant-scoped** variant
> `top_products/{merchant_id}/{category_id}` (body `{region, event, limit:50}`) is ALSO fired
> by the page but returns `null` without a product catalog. **Use the no-path-param variant
> above — it works without a catalog and returns the products you see on screen.**

### 2. Demographics — `/ads/v4/trends/shopping/product_categories/demographics/{REGION}`
⭐ **New endpoint — was not in the Doc #0 inventory.** This is the richest one on the page.
```jsonc
{ "options": { "url": "/ads/v4/trends/shopping/product_categories/demographics/US",
  "data": { "product_category_ids": ["1408"], "event": "OUTBOUND_CLICK",
            "end_date": "2026-08-14" } } }
```
**Response:**
```jsonc
{ "product_category_distributions": {
    "1408": {
      "demographics": [ {
        "gender_distribution": { "male": 0.07, "female": 0.82, "unspecified": 0.11 },
        "age_distribution":    { "18-24": 0.08, "25-34": 0.13, "35-44": 0.13, "45-49": 0.07,
                                 "50-54": 0.08, "55-64": 0.22, "65+": 0.32 } } ],
      "related_search_trends": [ "fall halloween decor", "halloween decor", ... ],  // 17 terms
      "summaries": [ { "engagement":      { "percent_growth": 0.71, "total": 0, "lookback": 2, "percent_relative_volume": null },
                       "saves":           { "percent_growth": 0.63, ... },
                       "outbound_clicks": { "percent_growth": 0.84, ... } } ]
    } } }
```
- Values are **fractions (0–1)**, not percentages — `0.82` renders as "82%".
- `product_category_ids` is an **array** → you can batch several categories in one call.
- `summaries` here drives the header's "Key metric changes in the past 30 days".

### 3. Chart data — `/ads/v4/trends/shopping/product_categories/metrics/{REGION}`
Same endpoint as A2, but scoped to the one category and a longer window:
```jsonc
{ "product_category_ids": ["1408"], "event": "OUTBOUND_CLICK",
  "end_date": "2026-08-14", "days": 180, "predicted_days": 28 }
```
`days: 180` (vs 60 on the table) and `predicted_days: 28` → this is what produces the
**forecast** (dashed "Predicted median" + shaded "Prediction bounds") on the detail chart.

### 4. `top_products/{merchant_id}/{product_category_id}` — merchant-scoped, returns `null` without a catalog.

## C2. Page layout → data source map

| Section on screen | Source |
|---|---|
| Title + breadcrumb ("Pants" · Fashion > Clothing) | taxonomy (A3) — walk `parent_product_category_id` up |
| "Key metric changes in the past 30 days" — Outbound clicks ↑10% · Engagement ↑10% · Pin saves ↑5% | `demographics` → `summaries[0]` (all 3 metrics) |
| **"Top products on Pinterest"** ("Products based on outbound clicks") + "Explore top products" + "Visit Pin" | `top_products` (call 1) |
| **"Performance" → "Relative interest over time"** (indexed 0–100, Historical / Predicted median / Prediction bounds) | `metrics` (call 3) |
| **"Demographics"** — Age bars + Gender donut ("Pinners who engaged … in the past 3 months") | `demographics` → `demographics[0]` |
| **"Related to {category}" → "Search queries"** + Copy keywords | `demographics` → `related_search_trends` |
| **"Other product categories"** ("People interested in X are also interested in…") | **taxonomy siblings** — same `parent_product_category_id`. No extra call. |
| Date range dropdown | changes `days` on call 3 |
| Engagement dropdown (on chart AND on Demographics, independently) | changes `event` — refires the relevant call |

## C3. For the MARKETER agent — what you get by clicking a category

Clicking a category is where the commerce picture gets concrete. You go from "Pants is
trending" to:

- **The actual products people are clicking on** — real Pins with merchant names (eBay,
  Walmart, Amazon, Target, Oriental Trading…), titles and images. This is competitive
  intelligence: you can see exactly which retailers and which product styles are winning the
  clicks in your category right now.
- **All three intent metrics side by side** for the past 30 days (outbound clicks, engagement,
  Pin saves) — so you can see whether a category is converting or just being admired.
- **A 6-month demand curve with a 28-day forecast** — longer history and a real prediction,
  vs the tiny sparkline in the table.
- **Who's buying it** — age and gender split of people who *engaged with this category*.
  This is per-category, and it varies a lot: Seasonal & holiday decorations skews 82% female
  and heavily **55-64 (22%) / 65+ (32%)**, which is a very different buyer from a fashion
  category. Don't assume the site-wide Pinterest demographic.
- **Search queries** for the category (with Copy keywords) — the keyword list to target.
- **Other product categories** the same audience likes — adjacent categories to expand into.

Note the **Engagement dropdown appears twice** (once on the chart, once on Demographics) and
they work independently — so you can view the demand curve by outbound clicks while looking at
demographics by saves. Make sure you know which metric each chart is currently showing.

---

## C4. The "Engagement" filter + "Search queries" chips — verified behaviour

### C4a. The "Engagement" dropdown (appears twice on the page)

Both the **Performance chart** and the **Demographics** block have their own dropdown labelled
**"Engagement"**. Its options map to the SAME `event` enum used everywhere else — but note the
**labels differ from the shopping table's "Ranked by" dropdown**:

| Dropdown label | `event` value sent |
|----------------|--------------------|
| **All** | `ENGAGEMENT` |
| **Outbound clicks** (default on this page) | `OUTBOUND_CLICK` |
| **Pin saves** | `SAVE` |

> ⚠️ On the table it's "Engagement / Outbound clicks / Pin saves"; here the same
> `ENGAGEMENT` value is labelled **"All"**. Same enum, different wording. Don't map by label.

**Date range** dropdown (Performance chart) sends: `90D`, `180D`, `365D`, `730D`
("Past 3 months / 6 months / 1 year / 2 years") → becomes `days` on the `metrics` call.

**Caching:** changing the Engagement dropdown fires the `demographics` call **once per event
value**, then caches. Switching back to an already-loaded value fires **no request**
(verified: switching to a previously-selected event produced zero network calls).

### C4b. ⭐ What the Engagement filter DOES and DOESN'T change

I called `demographics/US` for the same category (`1408`) with all three events and diffed:

**❌ It does NOT change `related_search_trends` (the "Search queries" chips).**
All three events returned the **identical 17 keywords in identical order**. The chips under
"People engaging with this product category commonly search for:" are the **same regardless of
the filter**. Don't re-request them per event — one call is enough.

**✅ It DOES change the demographics — significantly.** Same category, only `event` changed:

| Age band | `ENGAGEMENT` (All) | `OUTBOUND_CLICK` | `SAVE` |
|----------|--------------------|------------------|--------|
| 18-24 | 9% | 8% | 10% |
| **25-34** | 17% | **13%** | **23%** |
| 35-44 | 14% | 13% | 17% |
| 45-49 | 7% | 7% | 7% |
| 50-54 | 8% | 8% | 8% |
| 55-64 | 20% | 22% | 18% |
| **65+** | 27% | **32%** | **19%** |
| Female | 83% | 82% | 85% |

→ **The same product category has a materially different audience depending on the action.**
For "Seasonal & holiday decorations": the 65+ group is **32% of outbound clicks but only 19%
of saves**, while 25-34 is **23% of saves but only 13% of clicks**.

**Marketer takeaway:** younger Pinners **save** (planning, inspiration); older Pinners
**click out** (buying now). If you pick your audience off the default "Outbound clicks" view
you will target older than the people actually engaging with the category. Always check the
demographic under all three filters before setting age targeting — and match the filter to
your campaign objective (awareness/saves vs conversion/clicks).

### C4c. Clicking a "Search queries" chip

Every chip is a link to the **Search trends** keyword page (same page documented in
Doc #2 §E):

```
/detail/?country=US&terms=halloween decor&dateRange=90D
```

| Param | Value |
|-------|-------|
| `country` | current region |
| `terms` | the keyword text |
| `dateRange` | `90D` (also `180D`, `365D`, `730D`) |

That page then fires `GET /metrics/` and `GET /demographics/` for the keyword (Doc #2 §E1–E2),
and offers **"Predict the future"** (forecast toggle) and **"Related trends"** (multi-keyword
compare).

**"Copy keywords"** button copies the whole chip list to the clipboard — the API equivalent is
just `related_search_trends` from the `demographics` call.

> **Full click path:** shopping table → `/shopping/{category_id}/` → Search queries chip →
> `/detail/?terms=…` → keyword forecast + keyword demographics.

---

## C5. "Top products on Pinterest" — YES, fully pullable ⭐

**Short answer: yes, the agent can pull this — all of it, in one call, with no product catalog
and no scraping.** This is the most commercially valuable data on the site.

### Clicking "Explore top products"

Opens a panel: **"Pinterest top products in {category}"** —
*"View products that are trending across Pinterest based on outbound clicks over the last 30
days"* — showing **"Showing 33 Pins"** in a Preview / Merchant / Product name table with an
external-link icon per row.

**It fires NO new request.** The panel renders the same `top_products` response already
fetched for the strip. The strip shows the first ~10; the panel shows the full list.
→ **One call gives you the complete list.** Don't try to paginate the UI.

### The call (repeat of §C1.1 — this is the one that works)

```jsonc
{ "options": { "url": "/ads/v4/trends/shopping/product_categories/top_products",
  "data": { "product_category_id": "1408", "region": "US", "event": "OUTBOUND_CLICK" } } }
```

### Verified constraints (probed)

| Test | Result |
|------|--------|
| `event: "OUTBOUND_CLICK"` | ✅ 200, 33 products |
| `event: "SAVE"` | ⚠️ 200 but **0 products** |
| `event: "ENGAGEMENT"` | ⚠️ 200 but **0 products** |
| `event` omitted | ❌ 500 |
| `limit: 100` | ignored — still 33 |
| `region: "CA"` | ✅ 200, **50 products** |
| `region: "FR"` | ❌ 400 `The region FR must be one of: US, CA, GB+IE` |

**Key limits for the code agent:**
1. **`OUTBOUND_CLICK` is the only usable `event`.** The other two return an empty array, not an
   error — so guard for `top_products: []` rather than trusting a 200.
2. **Only 3 regions: `US`, `CA`, `GB+IE`.** Much narrower than every other endpoint (~32 regions).
3. **`limit` is ignored** on this variant. The server decides the count and it varies by
   region/category (33 for US/1408, 50 for CA/1408). Take what you get.
4. `event` is **required** — omitting it 500s.

### Building the product link

The response gives `pin_id`, not a URL. Construct it:
```
https://www.pinterest.com/pin/{pin_id}/
```
(Verified against the panel's link hrefs, e.g. `/pin/4607745477126792832/`.)

### What you get per product

`pin_id` · `merchant_name` · `title` · `images` (7 sizes: 75x75, 236x, 345x, 474x, 564x, 736x,
1200x — each with `url`/`width`/`height`).

Real merchants seen for one category: Oriental Trading, Amazon, Target, Poshmark, Hobby Lobby,
The Home Depot, Walmart, Sam's Club, Michaels Stores.

### For the MARKETER agent

This is **competitive intelligence**: the actual products winning outbound clicks in a category
over the last 30 days, with the retailer named. Use it to see which merchants own a category,
what product styles/price-tiers are converting, and how your assortment compares. Note it is
**click-based only** — there is no "top products by saves" view, so this reflects
buying behaviour, not inspiration.

---

## C6. "Search queries" — yes, covered (see §C4)

Confirming, since it's easy to miss: the **"Search queries"** block
("People engaging with this product category commonly search for:") is **fully pullable** and
is documented in **§C4b/§C4c** above. Recap:

- **Source:** `related_search_trends` inside the **`demographics/{REGION}`** response
  (§C1.2) — NOT a separate endpoint. If you already called `demographics` for the age/gender
  charts, you already have the keywords; no second request needed.
- **17 terms** for this category.
- **The Engagement filter does NOT change them** — identical across `ENGAGEMENT` /
  `OUTBOUND_CLICK` / `SAVE` (verified by diff). Only the demographics change.
- **"Copy keywords"** button = that same array.
- **Each chip links to** `/detail/?country=US&terms=<keyword>&dateRange=90D` → the Search
  trends keyword page (forecast + keyword-level demographics, Doc #2 §E).

> So one `demographics` call returns **three** of the page's sections at once:
> the age/gender charts, the "Key metric changes" header numbers (`summaries`), and the
> Search queries chips (`related_search_trends`).

---

## C7. ⭐ Following a "Search queries" chip — and why it matters

Taking the exact chip from the page:

```html
<a href="https://trends.pinterest.com/detail/?country=US&terms=fall%20halloween%20decor&dateRange=90D">
   fall halloween decor </a>
```

`dateRange=90D` on the URL → becomes `days=90` on the API calls. That page then fires the two
plain-GET keyword endpoints (Doc #2 §E1–E2). Called directly for this keyword:

```
GET /metrics/?terms=fall halloween decor&country=US&end_date=2026-08-14
             &days=90&aggregation=2&predicted_days=91&normalize_against_group=true&shouldMock=false
GET /demographics/?terms=fall halloween decor&country=US&end_date=2026-08-14&days=90
```

**`/metrics/` returned:**
```jsonc
{ "term": "fall halloween decor",
  "has_prediction": false,                     // ⚠️ see note below
  "growth_rates": { "wow_change": -0.2, "mom_change": 1, "yoy_change": null },
  "counts": [ { "date":"2026-05-22", "count":25, "normalizedCount":25, ... },   // 13 weekly points
              { "date":"2026-08-14", "count":84, "normalizedCount":84, ... } ] }
```
> ⚠️ **`has_prediction: false`** even though `predicted_days: 91` was requested — all
> `predicted*` fields came back `null`. **Not every keyword has a forecast.** The code agent
> must check `has_prediction` before trying to render "Predict the future"; don't assume the
> forecast band exists.

### 🚨 The important finding: keyword demographics ≠ category demographics

Same page, same region, same date — but the **category** and one of **its own search queries**
describe completely different people:

| Age band | **Category** "Seasonal & holiday decorations" (outbound clicks) | **Keyword** "fall halloween decor" |
|----------|------------------------------------------------|-------------------------------------|
| 18-24 | 8% | **20%** |
| **25-34** | **13%** | **45%** |
| 35-44 | 13% | 18% |
| 45-49 | 7% | 4% |
| 50-54 | 8% | 4% |
| 55-64 | 22% | 5% |
| **65+** | **32%** | **5%** |
| Female | 82% | 89% |

The category skews **old** (65+ = 32%). One of the very keywords listed *under that category*
skews **young** (25-34 = 45%, and 65+ collapses to 5%).

**Why:** they measure different things. The category demographic = who **clicks out to buy**
products in that category. The keyword demographic = who **searches that phrase**. A category
is an aggregate of many audiences; an individual query isolates one of them.

**Code agent:** these are two different endpoints with two different meanings —
`product_categories/demographics/{REGION}` (category, wrapper style) vs `/demographics/`
(keyword, plain GET). Same field names (`age_distribution`, `gender_distribution`), same 0–1
fractions, **completely different subject**. Never merge or substitute them.

**Marketer agent:** do NOT set audience targeting from the category demographic and then run
creative built around a specific keyword — you'll target 65+ for a phrase that 45% of 25-34s
are searching. Pick the level you're actually activating on: broad category campaign → category
demographics; keyword/ad-group level → pull `/demographics/` for that exact term.

### The chain, end to end

```
Shopping table  →  /shopping/{category_id}/     (category: products, curve, demographics, keywords)
                →  Search queries chip
                →  /detail/?terms=<kw>&dateRange=90D   (keyword: own curve, own forecast, OWN demographics)
```
Each hop narrows the audience — and the numbers change at every hop. Always re-pull
demographics at the level you're acting on.

---

## C8. Category DEPTH — L2 vs L3 vs L4 behaviour (clicking "Bath & body")

Clicking **Bath & body** from the "All categories" browse list goes to **`/shopping/1030/`** —
`1030` is an **L2** (main category), whereas Pants (`1356`) and Seasonal & holiday decorations
(`1408`) are **L3**. Verified: **every level behaves identically on the detail endpoints.**

### Same page, same 4 calls, any level

| | **L2** `1030` Bath & body | **L3** `1356` Pants | **L4** `1064` Body moisturizers / `1311` Mascaras |
|---|---|---|---|
| `demographics/US` | ✅ 200 | ✅ 200 | ✅ 200 |
| `top_products` | ✅ 32 products | ✅ 39 products | ✅ 50 products |
| `metrics/US` | ✅ 26 points | ✅ 30 points | ✅ |
| `related_search_trends` | 25 keywords | 25 keywords | 25 keywords |
| Forecast present? | ❌ **no** | ✅ **yes** | — |
| Breadcrumb shown | `Beauty` | `Fashion > Clothing` | — |

→ **The code agent can request ANY category ID at ANY level** on `demographics`,
`top_products` and `metrics`. No special handling for depth.

### 🚨 BUT: `parent_product_categories` on `top/` accepts **L1 ONLY** — and fails SILENTLY

| `parent_product_categories` value | Level | Result |
|-----------------------------------|-------|--------|
| `["1042"]` (Beauty) | **L1** | ✅ 200, **6 rows** |
| `["1030"]` (Bath & body) | L2 | ⚠️ **200, 0 rows** |
| `["1356"]` (Pants) | L3 | ⚠️ **200, 0 rows** |

**This returns HTTP 200 with an empty `ordered_values` array — not a 400, not an error
message.** An agent that passes an L2/L3 ID here will silently get "no trending categories"
and may wrongly conclude the category has no data.

> **Rule: `top/` takes L1 verticals only** (`1181` Fashion, `1250` Home decor, `1042` Beauty).
> Everything else — `demographics`, `top_products`, `metrics`, and the `/shopping/{id}/` page —
> takes **any** level. Always validate the level against the taxonomy (Doc #5) before calling.

### Breadcrumb construction

The header breadcrumb is the **ancestor chain excluding the category itself**:
- L2 `1030` Bath & body → parent `1042` → **"Beauty"** (just the vertical)
- L3 `1356` Pants → `1104` Clothing → `1181` Fashion → **"Fashion > Clothing"**

Walk `parent_product_category_id` up until the ID is absent from `categories` — that final ID
is the L1 vertical, whose name must come from the hardcoded table (Doc #5).

### "Other product categories" = SIBLINGS at the same level

On the L2 page for Bath & body it showed: **Hair, Skincare, Beauty supplements, Teeth
whitening, Fragrance, Makeup** — all L2 siblings under Beauty (`1042`). On the L3 page for
Seasonal & holiday decorations it showed L3 siblings under Home accessories (`1249`).
Same rule at every level, computed client-side from the taxonomy — **no API call**.

### ⚠️ Two data-shape reminders confirmed here

1. **Growth can be NEGATIVE.** Bath & body showed **Pin saves ↓8%** (`saves.percent_growth:
   -0.08`) while outbound clicks were **↑26%**. Don't assume `percent_growth >= 0`, and don't
   render a "↑" unconditionally — the three metrics can move in opposite directions for the
   same category.
2. **Forecast availability is per-category.** Bath & body returned **no** prediction points
   even with `predicted_days: 28`; Pants did. Same as the keyword-level `has_prediction` issue
   (Doc #4 §A1) — check whether `normalized_predicted_upper_bound` is non-null before drawing
   a forecast band.

### Marketer note

Depth = specificity, and the numbers change with it. `Bath & body` (L2) is a broad view;
`Body moisturizers` (L4) is a precise one. Because **all three metrics are returned at every
level**, you can drill from vertical → main → sub → sub-sub and watch where growth actually
concentrates — a flat L2 can hide a fast-growing L4 inside it. Note also that a category can be
**up on clicks and down on saves at the same time** (Bath & body: clicks +26%, saves −8%),
which usually means demand is converting now rather than being planned for later.

---

## C9. ⭐⭐ THE INJECTION TEST — unlocking verticals the UI hides

**The test:** the "Top vertical" filter only offers Fashion / Home decor / Beauty. What happens
if you inject a vertical ID the UI never lets you pick — e.g. Electronics `1161` — straight into
`parent_product_categories`?

**Result: the API is NOT restricted to the 3 UI verticals.** Four hidden verticals return real
trending data. Others return empty. Full sweep of all 14 L1 IDs (`limit:20`, US, OUTBOUND_CLICK):

| L1 ID | Vertical | In UI filter? | Rows returned |
|-------|----------|---------------|---------------|
| `1181` | Fashion | ✅ yes | **19** |
| `1250` | Home decor | ✅ yes | **9** |
| `1042` | Beauty | ✅ yes | **6** |
| `1148` | **DIY** | ❌ **no** | **3** ⭐ |
| `1016` | **Arts & entertainment** | ❌ **no** | **2** ⭐ |
| `1500` | **Wedding** | ❌ **no** | **2** ⭐ |
| `1315` | **Media** | ❌ **no** | **1** ⭐ |
| `1161` | Electronics | ❌ no | 0 |
| `1007` | Animals & pet supplies | ❌ no | 0 |
| `1194` | Food & beverages | ❌ no | 0 |
| `1241` | Hardware | ❌ no | 0 |
| `1436` | Sporting goods | ❌ no | 0 |
| `1481` | Toys & games | ❌ no | 0 |
| `1489` | Vehicles & parts | ❌ no | 0 |

> **Electronics specifically returns 0** — so the trick doesn't unlock *everything*. But
> **7 verticals have data, not 3.** The UI shows you fewer than half.

### C9.1 The hidden data is real, and some of it is the most interesting on the site

| Vertical | Category | ID | Outbound-clicks growth |
|----------|----------|-----|------------------------|
| DIY | **Drills & screwdrivers** | `1155` | **+367% MoM** 🔥 |
| DIY | Tools | `1477` | +20% |
| DIY | Woodworking plans | `1518` | +10% |
| Arts & entertainment | **Hobbies & creative arts** | `1248` | **+181%** |
| Arts & entertainment | Wedding ceremony decor | `1502` | +10% |
| Wedding | Groom & groomsmen suits | `1219` | +16% |
| Wedding | Wedding ceremony decor | `1502` | +10% |
| Media | DVDs & videos | `1159` | +32% |

**+367% on Drills & screwdrivers is the single fastest-growing category found anywhere on the
site — and it is completely invisible in the UI.**

### C9.2 Hidden categories are FULLY drillable

`1155` Drills & screwdrivers behaves like any normal category:
- `demographics` → 200, **25 keywords** ("toolbox", "antique hand tools", "vintage hand tools",
  "gadgets", "cool tools")
- `top_products` → **49 products** (The Home Depot, Walmart, SANRICO)
- Gender: **34% male / 58% female** — versus the 82–89% female typical of the UI-exposed
  categories. Hiding these verticals hides Pinterest's entire male-skewing audience.

So the hidden verticals are not half-built — they're complete. The gate is UI-only.

### C9.3 🚨 PROOF that verticals must be queried separately

Injecting also let me settle *why*. `percent_relative_volume` is **normalized within each
response**, not globally. Same Beauty categories, two calls, `limit:100` both times:

| Category | ID | **Beauty alone** | **Beauty + Fashion + Home decor** |
|----------|-----|------------------|-----------------------------------|
| Mascaras | `1311` | **1.00** | **0.01** |
| Facial cleansers | `1178` | **0.70** | **0.01** |
| Beauty supplements | `1043` | **0.45** | **0.00** |
| Nail care | `1321` | 0.38 | 0.00 |
| Makeup tools | `1309` | 0.31 | 0.00 |
| Skincare masks & peels | `1421` | 0.12 | 0.00 |

**Beauty's #1 category collapses from 1.00 to 0.01 when Fashion is in the same call.** The
volume bar is relative to the largest row *in that response*, so mixing verticals flattens the
smaller ones to zero. **This is the real reason to query one vertical per call** — not the
`limit`.

### C9.4 `limit` corrections

`limit` **does** work on `top/` (unlike `top_products`, where it is ignored — §C5):

| Verticals | `limit` | Rows |
|-----------|---------|------|
| 3 UI verticals | 20 | 20 *(truncated)* |
| 3 UI verticals | 100 | **34** = 19+9+6 ✓ |
| Fashion alone | 100 | 19 |
| All 14 | 100 | **40** |
| All 14 | 500 | **40** *(ceiling — 40 is the true universe)* |

→ Across every vertical and every level there are only **40 trending categories** in US right
now. The default view shows 20 of them, drawn from 3 verticals.

### C9.5 What this means

**Code agent:**
- Sweep all 14 L1 IDs, not the 3 the UI offers — 4 extra verticals have data.
- Still **one vertical per call** (§C9.3), then merge client-side. If you must compare across
  verticals, compare `percent_growth` (an absolute %) — never `percent_relative_volume`.
- Use `limit: 100`; the UI's 20 truncates.
- Expect empty results for the other 7 verticals and handle `ordered_values: []` as normal.

**Marketer:**
- There is trending data for **DIY, Arts & crafts, Wedding and Media** that no one using the
  Pinterest Trends UI can see. That includes the fastest-growing category on the platform
  (Drills & screwdrivers, +367%) and a genuinely male-skewing audience (34% male) that the
  three default verticals completely hide.
