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
