# Pinterest Trends — "Moments" (Seasonal moments)

> Source page: https://trends.pinterest.com/  → section **"Moments"** (click **"View moments"**).
> Moments are **seasonal events** (Halloween, Thanksgiving, Christmas, Valentine's Day…).
> The feature helps you spot what's rising early so you can plan for seasonal demand.

Same two-audience split as before:
- **PART A — For the code agent.** The 3 endpoints, params, response shapes.
- **PART B — For the marketer agent.** What the data means and how to use it.

There are **three** endpoints involved:

1. **List** — `moment/available/{REGION}` → the 13 moments with their phase + peak dates.
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
