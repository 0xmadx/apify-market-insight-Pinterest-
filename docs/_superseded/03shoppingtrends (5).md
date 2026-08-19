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

**Why separate — measured proof.** `limit:20` is applied to the *merged* result, so combining
verticals silently truncates the smaller ones. Same params, only the vertical changed:

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
