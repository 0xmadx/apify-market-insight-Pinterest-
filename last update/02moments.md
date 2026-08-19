# Pinterest Trends — "Moments" (Seasonal moments)

> Source page: https://trends.pinterest.com/  → section **"Moments"** (click **"View moments"**).
> Moments are **seasonal events** (Halloween, Thanksgiving, Christmas, Valentine's Day…).
> The feature helps you spot what's rising early so you can plan for seasonal demand.

Same two-audience split as before:
- **PART A — For the code agent.** The 3 endpoints, params, response shapes.
- **PART B — For the marketer agent.** What the data means and how to use it.

There are **three** endpoints involved:

1. **List** — `moment/available/{REGION}` → that region's moments with their phase + peak dates.

> 🚨 **CORRECTION (Doc #7 §4.4): moment lists are REGION-SPECIFIC.** US has 13; CA 12 (adds
> `superbowl`, `canada day`, `diwali`, `lunar new year`), DE 12 (adds `oktoberfest`, `karneval`,
> `spring`), FR 11 (adds `mardi gras`), IT 9 (adds `carnevale martedì grasso` — non-ASCII),
> BR 9 (adds `carnaval`), GB+IE 11 (adds `prom`), AU+NZ 13, ES 8, MX 8.
> **25 distinct slugs globally. JP and IN return ZERO.** Never hardcode the US list.
2. **Metrics** — `moment/metrics/{REGION}` → one moment's demand curve + audience breakdown.
3. **Keywords** — `/top_trends_filtered/` → the search terms driving that moment.

> All the `/ads/...` endpoints go through the SAME `ApiResource/get` wrapper and REQUIRE the
> header `X-Pinterest-PWS-Handler: trends/index.js` (see Doc #1). `/top_trends_filtered/` is
> different — it's a plain GET, see below.

---

## PART A — For the CODE agent

### 1) Moments LIST — `/ads/v4/trends/moment/available/{REGION}`

This powers the "View moments" panel (the table with Preview / Status / Name / Peak).
It is fetched **once at page load**; opening the panel does NOT re-fetch.

**Call (through the ApiResource wrapper):**
```jsonc
{ "options": { "url": "/ads/v4/trends/moment/available/US", "data": {} }, "context": {} }
```
- `data` is empty `{}`. Region is in the path (same region codes as Doc #1).
- Header `X-Pinterest-PWS-Handler: trends/index.js` required.

**Response — `resource_response.data`** is a set of **parallel arrays**, all indexed the
same way (index `i` = the same moment across every array). 13 moments in US:

```jsonc
{
  "moments": ["thanksgiving", "halloween", "christmas", ...],        // moment slug/id
  "phase_labels": ["approaching", "rising", "approaching", ...],     // lifecycle phase
  "peaks": [                                                         // the UPCOMING/current peak
    { "peak_timestamp_millis": "1792713600000",
      "takeoff_timestamp_millis": "1785456000000",
      "peak_length_in_days": 98 }, ...
  ],
  "historical_peaks": [ { ...same shape... }, ... ],                 // last year's peak
  "moment_next_occurrence_timestamps": ["1795852800000", ...]       // next calendar date of event
}
```

> ⚠️ Note the arrays are **positional**, not objects. To use it, zip by index:
> `moment[i]` has phase `phase_labels[i]`, peak `peaks[i]`, next date
> `moment_next_occurrence_timestamps[i]`. Timestamps are **epoch milliseconds as strings**.

**`phase_labels` values** (raw API → what the UI shows):

| Raw `phase_labels` | UI label | Meaning |
|--------------------|----------|---------|
| `rising` | Rising | Taking off now — act now |
| `approaching` | Approaching | Coming up, before takeoff |
| `cooldown` | Cooling | Just past peak, declining |
| `off_season` | Frozen | Long dormant, will return next cycle |
| `ended` | Frozen | This year's occurrence is over |

> The UI buckets these into two groups: **"Approaching and Rising"** (rising + approaching =
> act now) and **"Peaking, Cooling, and Frozen"** (cooldown + off_season + ended = review/learn).

### 2) Moment METRICS (detail) — `/ads/v4/trends/moment/metrics/{REGION}`

Fires when you **click a moment** (navigates to `/moments/{slug}`). Powers the big demand
chart + the "audiences driving this moment" breakdown.

**Call:**
```jsonc
{ "options": {
    "url": "/ads/v4/trends/moment/metrics/US",
    "data": {
      "moments": ["halloween"],          // the moment slug from the list
      "end_date": "2026-08-14",          // anchor date (YYYY-MM-DD)
      "aggregation_level": "weekly",
      "lookback_days": 365,              // how far back the history goes
      "predicted_days": 91,             // how far forward to forecast
      "interest_limit": 6,              // # of top interests to break out
      "normalize_against_group": false
    }
  }, "context": {} }
```

**Response — `resource_response.data.moments[0]`:**
```jsonc
{
  "name": "halloween",
  "moment": {
    "peaks": [                                    // the highlighted "Expected peak" marker
      { "takeoff_timestamp_millis": "1785456000000",
        "peak_timestamp_millis": "1792713600000",
        "peak_length_in_days": 98 }
    ],
    "daily_values": [                             // THE MAIN CHART line (history + forecast)
      { "timestamp": 1794528000000,
        "normal_counts": 6,                       // historical/actual index value (0–100 scale)
        "predicted_normalized_lower_bound_count": 4,   // forecast band (null for historical points)
        "predicted_normalized_upper_bound_count": 7 },
      ...                                          // ~66 points
    ]
  },
  "moment_interests": {                           // "audiences driving this moment" breakdown
    "925056443165": { "peaks": [], "daily_values": [ {same shape}, ... ] },   // per interest ID
    "941870572865": { ... },
    ...                                            // up to interest_limit entries
  }
}
```

- `daily_values`: this is the chart. `normal_counts` = actual/historical (the solid line,
  indexed 0–100). `predicted_normalized_lower/upper_bound_count` = the forecast prediction
  band (the dashed median + shaded bounds); these are `null` on historical points.
- `moment_interests`: keyed by **interest ID** (same IDs as Doc #1's interest list) — each is
  that interest's own demand curve, i.e. which audiences/categories drive this moment.

### 3) Moment KEYWORDS — `/top_trends_filtered/`  (⚠️ different call style)

Fires on the moment detail page. Powers **"What audiences are searching for"** (the keyword
chips + "Copy keywords"). This is a **plain GET with query params — NOT the ApiResource
wrapper:**

```
GET https://trends.pinterest.com/top_trends_filtered/
    ?country=US
    &moments=halloween          // the moment slug
    &endDate=2026-08-14
    &lookbackWindow=2
    &rankingMethod=1
    &trendsPreset=1
    &numTermsToReturn=25
```

**Response:**
```jsonc
{
  "endDate": "2026-08-14",
  "values": [                                    // 25 search terms, ranked
    {
      "term": "couples halloween costumes",
      "searchCount": 100,                        // relative search volume (indexed, peak=100)
      "normalizedCount": 100,
      "reverseRank": 25,
      "seasonality_score": 0.4379618,            // 0–1, how seasonal the term is
      "mom_change": { "value": 2,   "index": 24 },   // month-over-month change
      "wow_change": { "value": 0.07,"index": 12 },   // week-over-week change
      "yoy_change": { "value": 25,  "index": 24 },   // year-over-year change
      "affinity": null
    }, ...
  ]
}
```

### Click behavior summary

| Action | What fires |
|--------|-----------|
| Page load | `moment/available/{REGION}` (the list) — once |
| Open "View moments" panel | nothing new (renders from the list already loaded) |
| Click a moment row | navigate to `/moments/{slug}` → `moment/metrics/{REGION}` + `/top_trends_filtered/` |

---

## PART B — For the MARKETER agent

### What Moments are

Moments are **big seasonal events** — Halloween, Thanksgiving, Christmas, Valentine's Day,
Mother's Day, Summer, etc. Pinterest tracks the seasonal demand curve for each one so you can
**plan campaigns ahead of the peak** instead of reacting late. The whole point: "spot what's
rising so you can act early and plan for seasonal demand."

### The moments list — what you get

For each of the ~13 moments (in your chosen region) you get:

- **name** — the event (e.g. "halloween", "thanksgiving").
- **phase** — where the event is in its cycle right now. This is the single most useful field:
  - **Rising** → it's taking off *now* — launch today.
  - **Approaching** → coming up soon — start planning.
  - **Cooling** → just past peak — winding down.
  - **Frozen** → dormant / already happened this year — use it to *learn* for next cycle.
- **peak date** — the week demand is expected to peak (e.g. "Week of Oct 23, 2026").
- **takeoff date** — when demand starts climbing (your "launch by" signal).
- **next occurrence** — the calendar date of the event itself.
- **historical peak** — last year's peak, so you can compare timing year over year.

Rule of thumb: **act on Rising + Approaching**, **study Frozen/Cooling** to prep next year.

### The moment detail — what you get when you open one

- **Demand curve over time** — historical search demand (indexed 0–100) PLUS a **forecast**
  with a prediction band and an "Expected peak" marker. This tells you *when* to be live and
  how much runway you have before the peak.
- **"Audiences driving this moment"** — a breakdown by **interest/category** showing which
  audiences are fueling the seasonal demand. Use this to target the right interests.
- **"What audiences are searching for"** — the top ~25 **search terms** for the moment, each
  with **month-over-month, week-over-week, and year-over-year change** and a relative search
  volume. This is your keyword list for SEO, ad copy, hashtags — there's even a "Copy keywords"
  button in the UI. The `seasonality_score` (0–1) tells you how seasonal each term is.
- The header also shows editorial signals (e.g. "Rising", "↑11% WoW", "3 in 4 U.S. consumers
  plan to celebrate Halloween", "Launch today") — these are Pinterest's editorial/marketing
  framing around the moment. (Sourced separately from the raw metrics; treat as guidance copy.)

### How a marketer would use it

1. Open the moments list for your region → filter to **Rising / Approaching**.
2. For a chosen moment, read the **takeoff and peak dates** → set your launch timeline.
3. Look at the **demand curve + forecast** → confirm you're early (still climbing).
4. Use **"audiences driving this moment"** → pick which interests to target.
5. Pull the **search terms** (with MoM/WoW/YoY change) → build keywords + ad copy; prioritize
   terms that are rising fastest.

### Caveats

- All the count/volume numbers are **indexed (0–100 / peak-normalized)**, not raw volumes — read
  them as *shape and momentum*, not absolute search counts.
- The list is **regional** — moments and their timing differ by region (Region dropdown).
- Some header copy ("3 in 4 consumers…", "Launch today") is **editorial framing**, not a raw
  metric — use it as a prompt, verify with the actual curve.
- The forecast (prediction band) is a model estimate for the next ~91 days — treat as directional.

---
---

# ADDENDUM — Deeper dive: the "View moments" table, the detail sections, and clicking a keyword

This addendum expands three things with what was verified by clicking through the UI.

## C) The "View moments" table (the panel)

Clicking **"View moments"** opens a panel titled **"Moments on Pinterest"** — "Explore moments
in the next 6 months or review moments from the past." It renders entirely from the
`moment/available/{REGION}` list (no new call). It is split into **two status groups**:

**Group 1 — "Approaching and Rising"** ("Seasonal opportunities you can still act on now")
- Contains moments whose `phase_labels` = `rising` or `approaching`.
- These are the **actionable** ones — the event hasn't peaked yet.

**Group 2 — "Peaking, Cooling, and Frozen"** ("Seasonal opportunities you can review and learn from")
- Contains moments whose `phase_labels` = `cooldown` (Cooling), `off_season` / `ended` (Frozen).
- These are **past/dormant** — for learning and planning next cycle, not acting now.

Each row shows four columns, all derived from the list arrays (zipped by index):

| Column | Source field |
|--------|--------------|
| **Preview** (thumbnail) | image associated with the moment (from editorial/pins) |
| **Status** (Rising / Approaching / Cooling / Frozen) | `phase_labels[i]` (mapped — see Part A table) |
| **Name** (e.g. "Halloween 2026") | `moments[i]` + the year from `moment_next_occurrence_timestamps[i]` |
| **Peak** (e.g. "Week of Oct 23, 2026") | `peaks[i].peak_timestamp_millis` (rounded to the week) |

> So one call (`moment/available/{REGION}`) gives you the whole table, both groups, all columns.
> The grouping is just a filter on `phase_labels`.

## D) The moment DETAIL page — the three sections

Clicking a moment row (or a "More opportunities" card) opens `/moments/{slug}`. It has three
content blocks, each fed as follows:

### D1. The demand chart ("...demand is rising" / "Expected search demand, indexed 0–100")
- **Source:** `moment/metrics/{REGION}` → `moments[0].moment.daily_values`.
- Shows three series (see the chart legend): **Historical volume** (`normal_counts`),
  **Predicted median** (dashed), **Prediction bounds** (shaded) — the last two from
  `predicted_normalized_lower/upper_bound_count`.
- The **"Expected peak" marker** (e.g. "Oct 23, 2026") = `moments[0].moment.peaks[0].peak_timestamp_millis`.
- **Date range** dropdown (Past 1 year, etc.) changes `lookback_days` / `end_date`.

### D2. "Who's driving this moment"  ⚠️ CORRECTION — this is AGE + GENDER demographics
Earlier this doc said this block was the interest breakdown. On screen it actually renders
**two demographic charts**: an **Age** bar chart (18-24, 25-34, 35-44, 45-49, 50-54, 55-64,
65+) and a **Gender** donut (Female / Male / Unspecified). Example (Halloween US):
Age 18-24=43%, 25-34=33%, 35-44=15%…; Gender 87% Female, 5% Male, 8% Unspecified.

- **Data model:** age distribution + gender distribution (percentages that sum to ~100).
  This is the SAME structure the `/demographics/` endpoint returns (documented in section E2).
- **Source on the moment page:** fetched at page load via Pinterest's GraphQL endpoint
  (`POST /_/graphql/`). It is cached, so switching moments client-side does not refetch it.
  (The raw GraphQL body wasn't captured, but the on-screen data is identical in shape to the
  `/demographics/` GET response — `age_distribution` + `gender_distribution`.)
- Note: the `moment/metrics` response ALSO contains a `moment_interests` object (per-interest
  demand curves). That's a separate interest-level breakdown; the visible "Who's driving this
  moment" widget is the age/gender demographics.

### D3. "What users are searching for"
- **Source:** `/top_trends_filtered/` (plain GET — see Part A #3).
- Renders the top search terms as **clickable chips** + a **"Copy keywords"** button + "View more".
- **Each chip is a link** → clicking it opens the keyword detail page (section E).

## E) Clicking a KEYWORD chip → the "Search trends" detail page ("Predict the future")

Clicking a keyword chip navigates to **`/detail/?...&terms=<keyword>`**, titled **"Search trends"**.
This is the same keyword-explorer the "Go to trending searches" links point to. It fires
**two plain-GET endpoints** (NOT the ApiResource wrapper):

### E1. Keyword metrics — `GET /metrics/`
```
GET /metrics/
    ?terms=halloween nails        // one or more keywords (comma/repeat for multiple)
    &country=US
    &end_date=2026-08-14
    &days=90                       // lookback window in days
    &aggregation=2                 // 2 = weekly
    &predicted_days=91             // >0 = include forecast; 0 = history only
    &normalize_against_group=true  // true when comparing multiple terms
    &shouldMock=false
```
**Response** — an array, one object per term:
```jsonc
[{
  "term": "halloween nails",
  "has_prediction": true,
  "growth_rates": { "wow_change": 0.3, "mom_change": 1, "yoy_change": null },
  "counts": [                                     // the line chart, ~13 hist + ~13 predicted
    { "date": "2026-05-22", "count": 0, "normalizedCount": 0,
      "predictedUpperBoundNormalizedCount": null,      // null on historical points
      "predictedLowerBoundNormalizedCount": null },
    ...
    { "date": "2026-11-13", "count": 0, "normalizedCount": 0,
      "predictedUpperBoundNormalizedCount": 1,          // populated on forecast points
      "predictedLowerBoundNormalizedCount": 0 }
  ]
}]
```

### E2. Keyword demographics — `GET /demographics/`
```
GET /demographics/?terms=halloween nails&country=US&end_date=2026-08-14&days=90
```
**Response** — age + gender distribution per term (this is the same age/gender data model as
the moment page's "Who's driving this moment"):
```jsonc
{
  "term_distributions": {
    "halloween nails": {
      "age_distribution":    { "18-24": .., "25-34": .., "35-44": .., "45-49": ..,
                               "50-54": .., "55-64": .., "65+": .. },
      "gender_distribution": { "female": .., "male": .., "unspecified": .. }
    }
  }
}
```

### The page's features (for the marketer agent)

- **"Interest over time" chart** — "How often people are searching for a keyword compared to
  all keyword searches that week, indexed 0–100." Legend: Historical volume, Predicted estimate,
  Predicted bounds.
- **"Predict the future"** button — toggles the **forecast** (the dashed predicted median +
  shaded prediction bounds) on the chart. The forecast data is already in the `/metrics/`
  response (`predicted*` fields when `predicted_days>0`); the button is a client-side show/hide,
  not a new request. The UI disclaims: predictions are estimates from historical data, not
  guaranteed.
- **"Related trends"** — suggested related keywords (each with a mini sparkline and a **"+"**).
  Clicking **+** adds that keyword to the chart to **compare multiple terms** on the same axes
  (this re-calls `/metrics/` with multiple `terms` and `normalize_against_group=true`).
- **Search box** — you can type any keyword to explore it directly (chips show active keywords).
- **Demographics** section — the Age + Gender charts from `/demographics/`.
- **Date range** dropdown — changes `days` / `end_date`.
- **"Create campaign"** button (top right) — hands off to Pinterest Ads.

### Click-path summary (Moments → keyword)

```
Home → "View moments"            → panel from moment/available (no new call)
     → click a moment row        → /moments/{slug}  → moment/metrics + top_trends_filtered + graphql(demographics)
     → click a keyword chip       → /detail/?terms=… → /metrics/ + /demographics/  ("Search trends" page)
     → "Predict the future"       → client-side toggle of the forecast (no new call)
     → "Related trends" +         → /metrics/ with multiple terms (compare mode)
```
