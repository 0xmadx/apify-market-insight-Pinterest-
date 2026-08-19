# Doc #4 — Pinterest Trends: the KEYWORD DASHBOARD (`/detail/`)

> `https://trends.pinterest.com/detail/?country=US&terms=<keyword>&dateRange=90D`
> Page title: **"Search trends"** · left nav → **Search trends**

**Every keyword link on the entire site lands here.** Search-queries chips on a product-category
page, "What users are searching for" chips on a Moment page, rows in the keyword-discovery
table, the crystal ball, "Go to trending searches" — all of them open this page scoped to one
keyword.

```
Shopping category page ─┐
Moment page            ─┤
/search results table  ─┼──►  /detail/?terms=<kw>   ← THIS DOC
Crystal ball 🔮        ─┤
Search bar typeahead   ─┘
```

**Doc #4 vs Doc #6:** this page **profiles a keyword you already have**.
Doc #6 (`/search`) **finds keywords you don't**.

- **PART A** — code agent: the 5 endpoints, params, limits.
- **PART B** — marketer agent: what you get and how to read it.

> ⚠️ Everything here is **plain GET/POST — NOT the ApiResource wrapper.** No `resource_response`
> envelope; JSON comes back directly. Header `X-Pinterest-PWS-Handler: trends/index.js` works.

---

# PART A — For the CODE agent

## A0. URL → params

| URL param | Feeds |
|-----------|-------|
| `terms=fall halloween decor` | `terms` on every call (comma-separate for multiple) |
| `country=US` | `country` |
| `dateRange=90D` | `days` (`90D`→90, `180D`→180, `365D`→365, `730D`→730) / `lookback` |

## A1. The 5 endpoints

| # | Endpoint | Method | Powers |
|---|----------|--------|--------|
| 1 | `/metrics/` | GET | "Interest over time" chart + forecast + growth rates |
| 2 | `/demographics/` | GET | Age + Gender charts |
| 3 | `/related_terms/` | GET | "Related trends" chips |
| 4 | `/term_images/` | **POST** | "Popular Pins" grid |
| 5 | `/prefix_match/` | GET | 🔍 Search-bar typeahead |

---

## A2. `GET /metrics/` — the chart

```
GET /metrics/?terms=nails&country=US&end_date=2026-08-14&days=365
             &aggregation=2&predicted_days=91
             &normalize_against_group=true&shouldMock=false
```

```jsonc
[ { "term": "nails",
    "has_prediction": true,
    "growth_rates": { "wow_change": -0.03, "mom_change": 0.2, "yoy_change": null },
    "counts": [ { "date": "2025-08-15", "count": 55, "normalizedCount": 55,
                  "predictedUpperBoundNormalizedCount": null,     // null on historical points
                  "predictedLowerBoundNormalizedCount": null } ] } ]
```

### Parameter limits (all probed)

| Param | Valid | Notes |
|-------|-------|-------|
| `terms` | 1..N comma-separated | empty → **400**. 10 requested → **9 returned** (silently drops no-data terms) |
| `country` | region codes | invalid → **400** |
| `days` | 90 / 180 / 365 / 730 | drives history length |
| **`aggregation`** | **`2` ONLY** | `0,1,3,4` → **400**. `2` = **weekly (7-day step)**. **No daily data exists.** |
| **`predicted_days`** | **0–91** | `180`/`365` → **400**. Points returned = `predicted_days ÷ 7` |
| `normalize_against_group` | true / false | ⚠️ see A3 |
| **`shouldMock`** | **always `false`** | ⚠️ `true` returns **fake 2019 data with count 0** |
| **`end_date`** | ≤ latest_available_date, ~1 yr back | future → **400**; `2026-01-01` ok, `2025-08-01` → **400** |

> 🚨 **`shouldMock=true` returns mock data** (dates from 2019, all counts 0) with a 200. Never
> set it true, and never copy a URL that has it set.

> ⚠️ **`end_date` bounds differ per endpoint.** `/metrics/` rejects **future** dates and dates
> older than ~1 year. `/top_trends_filtered/` (Doc #6) happily accepts far-past dates
> (`2025-10-01` → 200) — that's how you time-travel for seasonal research. Don't assume the
> same window applies everywhere.

## A3. 🚨 `normalize_against_group` — makes comparison valid or meaningless

Max `normalizedCount`, same two keywords, same window:

| Call | family | nails | Comparable? |
|------|--------|-------|-------------|
| `family` alone | 100 | — | — |
| `nails` alone | — | 100 | — |
| `family,nails` + **`false`** | **100** | **100** | ❌ **NO** |
| `family,nails` + **`true`** | **8** | **100** | ✅ **YES** |

- **`false`** → each term normalised to **its own** peak; **every line hits 100**. Two keywords
  of wildly different size look identical.
- **`true`** → all terms share **one** scale. "family" is really **8% the size of "nails"** —
  invisible with `false`.

The UI always sends `true`. **Omitting or falsifying this overstates small keywords by an order
of magnitude.** (Same class of trap as `percent_relative_volume`, Doc #3 §C9.3 — Pinterest
normalises *within the response*.)

## A4. ⭐ The FORECAST — `has_prediction` decision rules

### Rule 1 — fixed per (keyword + region); never flips with date params
Tested `days` 90/180/365/730 × `predicted_days` 0/28/91 on a true keyword and a false keyword:
**the flag never changed** (7/7 each). You cannot unlock a forecast by changing the date range.

### Rule 2 — you must still request it
| `predicted_days` | forecast points |
|------------------|-----------------|
| `0` | **0** ← flag true but no forecast data |
| `28` | 4 |
| `91` | 13 (max, ~3 months) |

### Rule 3 — 🚨 region-dependent; forecasts appear **US-ONLY**
| Keyword | US | CA | GB+IE | DE | FR | ES |
|---------|-----|-----|-------|-----|-----|-----|
| family | **true** | false | false | false | false | — |
| halloween nails | **true** | false | false | false | — | — |
| nails | **true** | false | false | false | false | false |

Stronger test — **Canada's own top 5 trending keywords, queried in CA**: `nails`, `wallpaper`,
`nail ideas`, `outfit ideas`, `hairstyles` → **all `false`**. So it isn't keyword weakness.
**Treat "Predict the future" as US-only** until proven otherwise.

### Rule 4 — no forecast is fine
Fall back to `growth_rates` (WoW/MoM/YoY) + `seasonality_score` (Doc #6 §A9). Note
**`yoy_change` is frequently `null`** — guard it.

### Decision flow
```
GET /metrics/?terms=<kw>&country=<c>&predicted_days=91
   has_prediction?
     ├ true  → usable. predicted_days>0 (use 91). Read predictedUpper/LowerBound…
     └ false → no forecast for this (keyword, region). Do NOT retry with other params.
               Use growth_rates + seasonality_score.
               Check /related_terms/ for a sibling with hasPrediction=true.
               If country ≠ US, expect false as the norm.
```

## A5. `GET /demographics/`

```
GET /demographics/?terms=nails&country=US&end_date=2026-08-14&days=365
```
```jsonc
{ "term_distributions": {
    "nails": { "age_distribution": {"18-24":0.49,"25-34":0.32,"35-44":0.12,
                                    "45-49":0.04,"50-54":0.04,"55-64":0.04,"65+":0.04},
               "gender_distribution": {"female":0.87,"male":0.04,"unspecified":0.09} } } }
```
- Fractions **0–1**. UI renders `<5%` below 0.05 and labels the third bucket
  **"Unspecified + custom"**.
- Multi-term supported; **keys may come back in a different order than requested**.

> ⚠️ **Do not confuse with `product_categories/demographics/{REGION}`** (Doc #3 §C1.2). Same
> field names, different subject — keyword vs product category. They give very different
> numbers for the same theme (Doc #3 §C7).

## A6. `GET /related_terms/`

```
GET /related_terms/?requestTerm=family&country=US&endDate=2026-08-14
                   &aggregation=2&lookback=365&shouldMock=false
```
```jsonc
[ { "term": "family of three", "counts": [12,15,18,…], "hasPrediction": false } ]  // 5 results
```
- Always **5** suggestions. `counts` = bare numbers for the chip sparkline.
- ⚠️ **Naming is inconsistent with `/metrics/`:** `requestTerm` (not `terms`), `endDate` (not
  `end_date`), `lookback` (not `days`), `hasPrediction` (not `has_prediction`).

### Forecastability does NOT propagate — in either direction
| Parent | Parent forecast | Relatives with forecast |
|--------|-----------------|--------------------------|
| `family` | ✅ true | **0 of 5** |
| `family photoshoot` | ❌ false | **2 of 5** (*family picture outfits*, *family pictures*) |

Check every term individually.

## A7. `POST /term_images/` — Popular Pins

The **only POST** in the trends surface. GET variants → **400**.
```jsonc
POST /term_images/
{ "terms": ["family","nails"], "country": "US",
  "limit": 9, "requestImageSize": "236x", "cacheTtlInSeconds": 86400 }
```
```jsonc
{ "family": ["https://i.pinimg.com/236x/…jpg", …9 urls…],
  "nails":  [ … ] }
```
Image URLs only — no pin IDs, no titles, no merchant.

## A8. `GET /prefix_match/` — the search bar (typeahead)

```
GET /prefix_match/?query=hallow&country=US
```
```jsonc
[ { "term": "hallow", "counts": [24,33,36,…] },        // 52 weekly points ≈ 1 year
  { "term": "halloween nails", "counts": [...] }, … ]   // always 10 max
```

| Input | Result |
|-------|--------|
| `query=hallow` | ✅ 10 suggestions |
| **`query=HALLOW`** | ⚠️ **200 + `[]` — CASE SENSITIVE** |
| `query=h` | ✅ 10 |
| `query=` empty | ❌ **500** |
| `query=zzzqqqxx` | 200 `[]` (no match, not an error) |
| `limit=25` | ignored — always 10 |
| `country` omitted | ✅ works; CA/FR give different suggestions |

- Literal query echoed first **if it has data**.
- No `hasPrediction` field here.
- Selecting a suggestion → `/detail/` (on `/detail/` it **adds** a chip rather than replacing).

**Why it matters:** this is the **keyword-discovery primitive** — a stem returns 10 real
keywords **with a year of trend data attached**, in one call. Unlike `keywordsToInclude`
(Doc #6 §G6) which only filters *within the trending set*, `prefix_match` searches the **whole
keyword space**, so it works for non-trending terms.

## A9. Page → endpoint map

| UI element | Source |
|------------|--------|
| Search box | `/prefix_match/` |
| Keyword chips (`×` to remove) | drive `terms` |
| 🔮 crystal ball on chip | `has_prediction` |
| "Interest over time" chart | `/metrics/` |
| "Predict the future" button | **client-side toggle**, zero requests; rendered only if `has_prediction` |
| Date range (`90D/180D/365D/730D`) | `days` / `lookback` |
| "Related trends" `+` | `/related_terms/`; `+` re-fires all 3 with multi `terms` |
| Demographics | `/demographics/` |
| Popular Pins | `POST /term_images/` |
| Region (26 options) | `country` |
| "Create campaign" | → Pinterest Ads |

## A10. With forecast vs without — what the UI drops

| Element | 🔮 has | — hasn't |
|---------|--------|----------|
| Crystal ball on chip | ✅ | ❌ |
| "Predict the future" button | ✅ | ❌ not rendered |
| Dashed predicted median + shaded bounds | ✅ | ❌ |
| **Chart legend** | ✅ | ❌ **removed entirely** |
| Disclaimer text | ✅ | ❌ |
| Chart / Demographics / Popular Pins / Related trends | ✅ | ✅ |

API side is exactly consistent: `predicted*` non-null on **0 of 53** points for a false keyword.

## A11. Operational notes

- **Rate limiting is real** — `/metrics/` returned **429** under heavy probing. Back off.
- **Unknown params are silently ignored**, not rejected (Doc #6 §H3). A typo'd param name fails
  silently rather than erroring.
- Multi-term: **never assume `response.length === terms.length`** or that order is preserved.

---

# PART B — For the MARKETER agent

## What this page is

The **full profile of one keyword**: how often it's searched over time (indexed 0–100 =
*share of search*, not volume), how fast it's moving, optionally where it's heading, who
searches it, what it looks like on Pinterest, and which adjacent keywords to consider instead.

## What you get

- **Interest over time** — *"How often people are searching for a keyword compared to all
  keyword searches that week, indexed 0 to 100."* Share of search, not absolute volume.
- **Growth rates** — week-over-week, month-over-month, year-over-year (YoY often unavailable).
- **🔮 Predict the future** — ~3-month forecast with an uncertainty band. **Not available for
  every keyword, and effectively US-only.** Its absence means "no model output", not "no demand".
- **Related trends** — 5 adjacent keywords with sparklines; `+` overlays them to compare.
- **Demographics** — age + gender **for that exact phrase**.
- **Popular Pins** — what's actually being pinned; use it to brief creative.
- **Create campaign** — hand off to Ads.

## Three things that will mislead you

**1. Comparing keywords without a shared scale.** If each keyword is normalised to its own peak,
they all hit 100 and look equally big. On a proper comparison "family" is **8%** the size of
"nails". Always compare on one scale.

**2. Keyword demographics ≠ category demographics.** Same subject, opposite audience:

| Age | Category *Seasonal & holiday decorations* | Keyword *fall halloween decor* |
|-----|------------------------------------------|-------------------------------|
| 25-34 | 13% | **45%** |
| 65+ | **32%** | 5% |

The category number is who **buys in that category**; the keyword number is who **searches that
phrase**. Pull demographics at the level you're actually activating on.

**3. No crystal ball ≠ bad keyword.** It means Pinterest has no forecast for it (often too new,
too noisy, or non-US). Judge it on momentum instead — and check Related trends, since a sibling
may be forecastable even when your term isn't.

## Workflow

1. Arrive from a chip, the crystal ball, or type a stem in the search box.
2. Read the curve shape + growth rates → is it climbing?
3. If a forecast exists, check the predicted peak for launch timing.
4. Add 2–4 Related trends with `+` → pick the strongest phrasing (on a shared scale).
5. Read Demographics **for that term** → set age/gender targeting.
6. Scan Popular Pins → brief creative to match what already works.
