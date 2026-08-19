# Pinterest Trends — "Search trends" (the keyword DASHBOARD)

> Page: `https://trends.pinterest.com/detail/?country=US&terms=<keyword>&dateRange=90D`
> Title: **"Search trends"** · left nav → **Search trends**

**This is the destination every keyword chip on the site links to.** Clicking a keyword
anywhere — the Search queries chips on a product-category page, the "What users are searching
for" chips on a Moment page, "Go to trending searches" — lands you on THIS dashboard, scoped to
that keyword.

```
Shopping category page → "Search queries" chip → ⭐ THIS DASHBOARD
Moment page            → "What users are searching for" chip → ⭐ THIS DASHBOARD
```

- **PART A — code agent:** the 4 endpoints behind it.
- **PART B — marketer agent:** what the dashboard gives you.

> ⚠️ All 4 endpoints here are **plain GET/POST — NOT the ApiResource wrapper.** No
> `resource_response` envelope; the JSON comes back directly. (`X-Pinterest-PWS-Handler:
> trends/index.js` still works as the header.)

---

## PART A — For the CODE agent

### URL → params

| URL param | Becomes |
|-----------|---------|
| `terms=fall halloween decor` | `terms` on every call (URL-encoded) |
| `country=US` | `country` |
| `dateRange=90D` | `days` / `lookback` (`90D`→90, `180D`→180, `365D`→365, `730D`→730) |

Multiple keywords = repeat/comma the `terms` param — that's the "compare" mode.

### A1. `GET /metrics/` — the "Interest over time" chart

```
GET /metrics/?terms=<kw>&country=US&end_date=2026-08-14&days=90
             &aggregation=2&predicted_days=91
             &normalize_against_group=true&shouldMock=false
```
```jsonc
[ { "term": "fall halloween decor",
    "has_prediction": false,                                    // ⚠️ CHECK THIS
    "growth_rates": { "wow_change": -0.2, "mom_change": 1, "yoy_change": null },
    "counts": [ { "date": "2026-05-22", "count": 25, "normalizedCount": 25,
                  "predictedUpperBoundNormalizedCount": null,
                  "predictedLowerBoundNormalizedCount": null }, ... ] } ]
```

> ⚠️ **`has_prediction` is authoritative — and the UI obeys it.** For
> `fall halloween decor` it returned `false`, all `predicted*` fields were `null`, **and the
> "Predict the future" button was absent from the page entirely** (verified: no element
> matching /predict/i in the DOM). For `halloween nails` it returned `true` and the button was
> present. So: **render the forecast toggle only when `has_prediction === true`** — requesting
> `predicted_days: 91` does NOT guarantee a forecast.

### A2. `GET /demographics/` — the Age + Gender charts

```
GET /demographics/?terms=<kw>&country=US&end_date=2026-08-14&days=90
```
```jsonc
{ "term_distributions": {
    "fall halloween decor": {
      "age_distribution":    { "18-24":0.2, "25-34":0.45, "35-44":0.18, "45-49":0.04,
                               "50-54":0.04, "55-64":0.05, "65+":0.05 },
      "gender_distribution": { "female":0.89, "male":0.04, "unspecified":0.09 } } } }
```
Fractions 0–1. UI renders `<5%` for anything under 0.05, and labels the third gender bucket
**"Unspecified + custom"**.

### A3. `GET /related_terms/` — the "Related trends" chips  ⭐ new endpoint

```
GET /related_terms/?requestTerm=<kw>&country=US&endDate=2026-08-14
                   &aggregation=2&lookback=90&shouldMock=false
```
⚠️ Note the param names differ from `/metrics/`: **`requestTerm`** (not `terms`),
**`endDate`** (camelCase, not `end_date`), **`lookback`** (not `days`).

```jsonc
[ { "term": "retro halloween decorations",
    "counts": [ 12, 15, 18, ... ],        // 53 raw numbers — the mini sparkline on the chip
    "hasPrediction": true },              // note: camelCase here, snake_case in /metrics/
  ... ]                                    // 5 related terms
```
Returns **5** suggestions. `counts` is a bare number array (no dates) — just enough to draw the
chip's sparkline.

### A4. `POST /term_images/` — "Popular Pins"  ⭐ new endpoint, and it's a POST

The only **POST** in the whole trends surface. Body is JSON:

```jsonc
POST /term_images/
{ "terms": ["retro halloween decorations"],   // array — batch multiple keywords
  "country": "US",
  "limit": 9,
  "requestImageSize": "236x",
  "cacheTtlInSeconds": 86400 }
```
**Response** — a map of keyword → array of image URLs:
```jsonc
{ "retro halloween decorations": [
    "https://i.pinimg.com/236x/5a/e1/2f/5ae12fc549aeff19eadd456c291b2628.jpg",
    ... 9 urls ... ] }
```
> ⚠️ **GET does not work** — every GET variant I tried returned **400 `{}`**. It must be POST
> with a JSON body. `requestImageSize` accepts the standard Pinterest sizes (`236x`, `474x`,
> `736x`, …). Returns image URLs only — no pin IDs, no titles, no merchant.

### A5. Call map

| Section on the dashboard | Endpoint |
|---|---|
| "Interest over time" chart + Date range | `GET /metrics/` (A1) |
| "Predict the future" toggle | client-side; only rendered if `has_prediction` (A1) |
| "Related trends" chips (with `+` to compare) | `GET /related_terms/` (A3) |
| Clicking `+` on a chip | re-calls `/metrics/` + `/demographics/` + `POST /term_images/` for the added term |
| "Demographics" Age + Gender | `GET /demographics/` (A2) |
| **"Popular Pins"** | `POST /term_images/` (A4) |
| Keyword search box / chips with `×` | changes `terms`, refires everything |
| Region dropdown | changes `country` |
| "Create campaign" | hands off to Pinterest Ads |

---

## PART B — For the MARKETER agent

### What this dashboard is

The **keyword-level view**. Everywhere else on Pinterest Trends you're looking at a *topic*, a
*moment*, or a *product category*. Here you look at **one exact search phrase** — and you can
stack several side by side to compare.

### What you get

- **Interest over time** — "How often people are searching for a keyword compared to all
  keyword searches that week, indexed from 0 to 100." So it's **share of search**, not volume.
- **Growth rates** — week-over-week, month-over-month, year-over-year.
- **"Predict the future"** — a forecast with a prediction band. **Not available for every
  keyword** — if the button isn't there, Pinterest has no forecast for that term. Don't read
  its absence as "no demand."
- **Related trends** — 5 adjacent keywords, each with a mini sparkline and a **`+`** to add it
  to the chart and **compare curves directly**. This is the fastest way to pick between near-
  synonyms (e.g. "fall halloween decor" vs "vintage halloween decor" vs "halloween porch").
- **Demographics** — age + gender **for that exact phrase**.
- **Popular Pins** — a visual grid of what's actually being pinned for the keyword. Use it to
  brief creative: you can see the aesthetic, palette and styling that already resonates.
- **Create campaign** — jump straight into Ads with the keyword.

### ⚠️ The one thing to remember

**Keyword demographics are NOT category demographics.** Measured on the same day, same region:

| Age | Category "Seasonal & holiday decorations" | Keyword "fall halloween decor" |
|-----|------------------------------------------|-------------------------------|
| 25-34 | 13% | **45%** |
| 65+ | **32%** | 5% |

Same subject area, opposite audience. The category number reflects who *clicks out to buy in
that whole category*; the keyword number reflects who *searches that phrase*.

**Pull demographics at the level you're actually activating on.** Broad category campaign →
category demographics. Keyword or ad-group targeting → this dashboard's demographics for that
exact term.

### Practical workflow

1. Land here from a category or moment keyword chip (or type a keyword in the search box).
2. Check the curve shape + growth rates → is it climbing?
3. If "Predict the future" is available, check the forecast for runway.
4. Add 2–4 **Related trends** with `+` → compare and pick the strongest phrasing.
5. Read **Demographics for that term** → set age/gender targeting.
6. Scan **Popular Pins** → brief the creative to match what's already working.

---
---

# ADDENDUM — Full page analysis (re-audited on a keyword WITH a prediction)

Re-analysed `/detail/?terms=family` — a keyword whose `has_prediction` is `true`, so the whole
forecast UI is live. This adds what the first pass missed.

## Z1. Page anatomy

| Section | Source |
|---------|--------|
| Keyword search box + chips (each with `×`) | drives `terms` on every call |
| Chip **🔮 crystal-ball icon** | `has_prediction: true` (Doc #6 §H) |
| **"Interest over time"** chart | `GET /metrics/` |
| **"Predict the future"** button | client-side toggle (only rendered if `has_prediction`) |
| **Date range** dropdown | `90D` / `180D` / `365D` / `730D` → `days` |
| **"Related trends"** chips with `+` | `GET /related_terms/` |
| **Demographics** (Age + Gender) | `GET /demographics/` |
| **Popular Pins** | `POST /term_images/` |
| Region dropdown | `country` (26 UI options) |
| **"Create campaign"** | hands off to Pinterest Ads |

## Z2. "Predict the future" is a pure client-side toggle — CONFIRMED

Clicking it **fires zero data requests** (only `ApiSResource` analytics pings) and simply
shows/hides the dashed *Predicted median* + shaded *Prediction bounds*. Toggling it off ends the
chart at the last historical point. The forecast data is already in the `/metrics/` response.

## Z3. Multi-keyword compare

Clicking `+` on a Related-trends chip re-fires **all three** data endpoints with a
**comma-separated `terms`**:
```
GET /metrics/?terms=family,family of three&…
GET /demographics/?terms=family,family of three&…
POST /term_images/  {"terms":["family","family of three"],…}
```

**Capacity:** 1, 2, 4, 6 terms → all returned. Asking for **10 returned 9** — the endpoint
silently drops terms it has no data for rather than erroring. **Always check which terms came
back**; don't assume `response.length === terms.length` or that order is preserved
(`/demographics/` returned keys in the order `nails, family` for the request `family,nails`).

`/demographics/` returns one entry per term, keyed by the term string:
```jsonc
{ "term_distributions": {
    "family": { "age_distribution": {"18-24":0.43,"25-34":0.37,…}, "gender_distribution": {…} },
    "nails":  { "age_distribution": {"18-24":0.49,"25-34":0.32,…}, "gender_distribution": {…} } } }
```

## Z4. 🚨 `normalize_against_group` — the parameter that makes comparison valid

This is the most important flag on this page and it is easy to get wrong.

Max `normalizedCount` for the same two keywords, same window:

| Call | family | nails | Comparable? |
|------|--------|-------|-------------|
| `family` alone | **100** | — | — |
| `nails` alone | — | **100** | — |
| `family,nails` + `normalize_against_group=false` | **100** | **100** | ❌ **NO** |
| `family,nails` + `normalize_against_group=true` | **8** | **100** | ✅ **YES** |

- **`false`** → every term is normalised to **its own** peak, so **every line hits 100**. Two
  keywords of wildly different size look identical. Comparing them is meaningless.
- **`true`** → all terms share **one** scale, the biggest term hits 100. Here **"family" is only
  8% the size of "nails"** — a fact completely invisible with `false`.

**The UI always sends `true` for the compare view.** A code agent that omits or falsifies this
flag will produce charts that overstate small keywords by an order of magnitude.

> Same trap as `percent_relative_volume` in Shopping trends (Doc #3 §C9.3): Pinterest normalises
> *within the response*, so what you ask for in one call determines the scale you get back.

## Z5. `/related_terms/` — 5 suggestions, and they carry `hasPrediction`

```
GET /related_terms/?requestTerm=family&country=US&endDate=2026-08-14
                   &aggregation=2&lookback=365&shouldMock=false
```
Returned 5: *family of three · family cartoon · family icon · happy family aesthetic · picture*,
each with `counts[]` (bare numbers for the chip sparkline) and **`hasPrediction`** — all `false`
here, even though the parent term "family" has `has_prediction: true`. So **relatedness does not
imply forecastability**; check per term.

⚠️ Param naming is inconsistent with `/metrics/`: `requestTerm` (not `terms`), `endDate` (not
`end_date`), `lookback` (not `days`), and `hasPrediction` camelCase (not `has_prediction`).

## Z6. Region & date range

- Region select: **26 options**, same codes as the rest of the site (`US`, `GB+IE`, `IT+ES+PT+GR+MT`…).
- Date range: `90D` / `180D` / `365D` / `730D` — mapped to `days` on `/metrics/` and
  `/demographics/`, and `lookback` on `/related_terms/`. Default on arrival from a crystal ball
  is **`365D`**.

## Z7. Marketer summary for this page

This is where a single keyword gets fully profiled: its 0–100 share-of-search curve, an optional
~3-month forecast with uncertainty bands, who searches it (age + gender), what it actually looks
like on Pinterest (Popular Pins), and 5 adjacent keywords you can overlay to pick the best
phrasing.

The one discipline that matters: **when comparing keywords, make sure they're on a shared
scale.** Otherwise every keyword looks equally big — and "family" looks like "nails" when it's
really 8% of it.

---

## Z8. 🔮 vs — : the page WITH a forecast vs WITHOUT

Compared `family` (`has_prediction: true`) against `family photoshoot`
(`has_prediction: false`), same region, same date range.

### What disappears when there's no forecast

| Element | 🔮 `family` | — `family photoshoot` |
|---------|-------------|------------------------|
| Crystal-ball icon on the keyword chip | ✅ present | ❌ absent |
| **"Predict the future" button** | ✅ present | ❌ **not rendered at all** |
| Dashed **Predicted median** on chart | ✅ | ❌ |
| Shaded **Prediction bounds** | ✅ | ❌ |
| **Chart legend** (Historical volume / Predicted median / Prediction bounds) | ✅ | ❌ **entire legend removed** |
| Disclaimer *"Predictive trend ranges are estimates…"* | ✅ | ❌ |
| Interest over time chart | ✅ | ✅ (history only) |
| Demographics | ✅ | ✅ |
| Popular Pins | ✅ | ✅ |
| Related trends | ✅ | ✅ |

The no-forecast page is not degraded — it just has **no forward-looking UI at all**. Even the
legend vanishes, because there's only one series to label.

### API side — exactly consistent

```jsonc
// family photoshoot
{ "term": "family photoshoot",
  "has_prediction": false,
  "counts": [ …53 points… ],                       // all historical
  // EVERY point: predictedUpperBoundNormalizedCount = null
  //              predictedLowerBoundNormalizedCount = null
  "growth_rates": { "wow_change": -0.09, "mom_change": 0.01, "yoy_change": null } }
```
Verified: `predicted*` non-null on **0 of 53** points. Requesting `predicted_days=91` changed
nothing.

> Also note `yoy_change: null` here — year-over-year is frequently null. Guard for it.

### ⭐ The actionable part: related terms can be forecastable when the parent isn't

`family photoshoot` has **no** forecast, but `/related_terms/` for it returns:

| Related term | `hasPrediction` | Crystal ball shown |
|--------------|-----------------|--------------------|
| family of 3 poses | false | — |
| **family picture outfits** | **true** | 🔮 |
| family pictures with teenagers | false | — |
| family of 4 photos | false | — |
| **family pictures** | **true** | 🔮 |

**2 of its 5 relatives are forecastable even though it isn't.** And the reverse also happens:
`family` **has** a forecast while **all 5** of its relatives do not (§Z5).

→ **Forecastability does not propagate in either direction.** It's per-term and must be checked
per term.

**Marketer takeaway:** if the keyword you care about has no crystal ball, don't give up on
forecasting the theme — scan its Related trends for a sibling that *does* have one
(here, "family pictures" instead of "family photoshoot") and plan your timing off that.

**Code agent:** the crystal ball is rendered from `hasPrediction` in `/related_terms/` for chips,
and from `has_prediction` in `/metrics/` for the main keyword. Both must be read; neither can be
inferred from the other.

---

## Z9. 🔍 The SEARCH BAR — `GET /prefix_match/` (typeahead)

The **"Search for a keyword"** box (present on both `/search` and `/detail/`) is an
**autocomplete/typeahead**, and it has its own endpoint — the last undocumented one on the
keyword side.

### The call

```
GET https://trends.pinterest.com/prefix_match/?query=hallow&country=US
```
Plain GET. Fires on each keystroke (debounced).

### Response — suggestions WITH sparkline data

```jsonc
[ { "term": "hallow",
    "counts": [24,33,36,45,42,54,71,73,76,73,100,21,10,…] },   // 52 weekly points (1 year)
  { "term": "halloween nails",
    "counts": [12,14,21,28,33,48,79,100,95,73,36,2,1,0,…] },
  { "term": "halloween costume ideas", "counts": [...] },
  …10 total ]
```

- **Always 10 results max.** A `limit` param is **ignored**.
- `counts` is a bare number array (52 points ≈ 1 year weekly, peak-normalised to 100) — this is
  what draws the **mini sparkline next to each suggestion** in the dropdown. Same shape as
  `/related_terms/`, but **no `hasPrediction` field here**.
- The literal query string is echoed as the first result **if it has data** (`hallow` appeared
  above `halloween nails`).

### Behaviour probed

| Input | Result |
|-------|--------|
| `query=hallow&country=US` | ✅ 200, 10 suggestions |
| **`query=HALLOW`** | ⚠️ **200, `[]` — CASE SENSITIVE** |
| `query=h` (single char) | ✅ 200, 10 (*hairstyles, hair, hairstyle ideas*) |
| `query=` (empty) | ❌ **500** |
| `query=halloween na` (with space) | ✅ 200 (*halloween nails, halloween nail designs*) |
| `query=zzzqqqxx` | 200, `[]` (no match — not an error) |
| `country` omitted | ✅ 200 (defaults, US-like results) |
| `country=CA` / `country=FR` | ✅ 200, **different suggestions per region** |

> 🚨 **Case-sensitive again.** `HALLOW` returns an empty array with a 200 — the same silent
> failure as `keywordsToInclude` (Doc #6 §G4). **Lowercase all user input before sending.**
> And guard against an **empty query → 500**; debounce so you never fire on an empty box.

### Selecting a suggestion

Clicking a suggestion navigates to **`/detail/`** with that keyword as an active chip and fires
the usual trio — `/metrics/`, `/demographics/`, `POST /term_images/`. On `/detail/` the box
**adds** the keyword as another chip (multi-keyword compare, §Z3) rather than replacing.

### Why this endpoint is useful to an agent

`/prefix_match/` is the **keyword discovery primitive**: give it a stem, get 10 real Pinterest
keywords back **with a year of trend data already attached** — no follow-up call needed just to
see the shape. It is the cheapest way to:
- expand a seed term into real, actually-searched variants,
- check whether a phrasing exists on Pinterest at all (empty array = nobody searches it),
- get a sparkline for ranking candidates before spending `/metrics/` calls on them.

Unlike `keywordsToInclude` (Doc #6 §G6), which only filters *within the trending set*,
`prefix_match` searches the **whole keyword space** — so it works for terms that aren't trending.

---

## Z10. ⭐ CAN the prediction option be used? — the decision rules

Your question: *some keywords have the prediction option and some don't — for one that has it,
can it actually be used?* Answered by testing every variable.

### Rule 1 — `has_prediction` is FIXED per (keyword + region). It never flips with date params.

Tested `family` (has) and `family photoshoot` (hasn't) across every date variable:

| Variable | Values tested | Did `has_prediction` change? |
|----------|---------------|------------------------------|
| `days` | 90, 180, 365, 730 | ❌ **No** — same answer every time |
| `predicted_days` | 0, 28, 91 | ❌ **No** |
| `aggregation` | weekly | ❌ No |

`family photoshoot` = `false` in all 7 combinations. `family` = `true` in all 7.
→ **You cannot unlock a forecast by changing the date range or asking for more predicted days.**

### Rule 2 — ✅ BUT you must request it: `predicted_days` controls whether forecast points come back

For a keyword that *does* have a prediction, the flag alone isn't enough:

| `predicted_days` | `has_prediction` | forecast points returned |
|------------------|------------------|--------------------------|
| `0` | true | **0** ← flag true but NO forecast data |
| `28` | true | **4** (28 ÷ 7 = 4 weekly points) |
| `91` | true | **13** (91 ÷ 7 = 13) |
| `180` | — | ❌ **400** |
| `365` | — | ❌ **400** |

- **`predicted_days` max is 91** (~3 months). Anything higher is a hard 400.
- Points returned = `predicted_days / 7` at weekly aggregation.
- **`predicted_days=0` returns no forecast even when `has_prediction` is true** — a very easy
  way to conclude "no forecast" incorrectly. The UI sends **91**.

### Rule 3 — 🚨 `has_prediction` is REGION-DEPENDENT, and forecasts appear to be US-ONLY

Same keyword, different `country`:

| Keyword | US | CA | GB+IE | DE | FR | ES |
|---------|-----|-----|-------|-----|-----|-----|
| family | **true** | false | false | false | false | — |
| halloween nails | **true** | false | false | false | — | — |
| nails | **true** | false | false | false | false | false |
| family photoshoot | false | false | false | false | — | — |

Stronger check — I pulled **Canada's own top 5 trending keywords** and tested them **in CA**:

| CA top keyword | `has_prediction` in CA |
|----------------|------------------------|
| nails | false |
| wallpaper | false |
| nail ideas | false |
| outfit ideas | false |
| hairstyles | false |

**All false.** So it isn't "these keywords are weak in Canada" — **no forecast was found for any
non-US region tested** (CA, GB+IE, DE, FR, ES).

> **Treat "Predict the future" as a US-only feature until proven otherwise.** If your agent
> targets a non-US market, expect no crystal balls and no forecast band anywhere, and don't
> report that as missing data.

### Rule 4 — no prediction is fine, and here's what to use instead

A keyword without a forecast is **not** low quality — Pinterest simply has no model output for
it. You still get everything else: the full historical curve, `growth_rates`
(`wow_change` / `mom_change` / `yoy_change`), demographics, Popular Pins and Related trends.

Rank non-forecast keywords on **WoW / MoM / YoY momentum + `seasonality_score`**
(from `/top_trends_filtered/`, Doc #6 §A9) instead of a predicted peak.
And remember (§Z8) a non-forecast keyword often has **forecastable relatives** — check
`/related_terms/`.

### Decision flowchart for the code agent

```
GET /metrics/?terms=<kw>&country=<c>&predicted_days=91
        │
   has_prediction?
        ├── true  → forecast IS usable.
        │           Ensure predicted_days > 0 (use 91, the max).
        │           Read predictedUpper/LowerBoundNormalizedCount.
        │           Show the crystal ball / "Predict the future" UI.
        │
        └── false → no forecast exists for this (keyword, region). Do NOT retry with
                    other days/predicted_days — the answer will not change.
                    Fall back to growth_rates + seasonality_score.
                    Optionally check /related_terms/ for a sibling with hasPrediction=true.
                    If country ≠ US, expect false as the norm.
```
