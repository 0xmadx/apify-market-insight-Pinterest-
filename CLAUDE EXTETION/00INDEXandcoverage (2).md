# Pinterest Trends API — Master Index & Coverage Audit

Entry point for both agents. Lists **every** endpoint the trends.pinterest.com app uses,
what's documented, and what's still missing.

> Inventory method: extracted all `/ads/v4/trends/...` paths from the app's 76 JS bundles +
> captured live network traffic on every page.
>
> ⚠️ **The bundle scan was not exhaustive.** `product_categories/demographics/{region}` (#12)
> did NOT appear in the scan and was only found by clicking into a category detail page.
> Assume other endpoints may still be hiding behind un-clicked UI.

---

## 0. THE TWO CALL STYLES (read this first — it trips people up)

The site uses **two different** ways to call data. Don't mix them.

### Style 1 — "ApiResource wrapper" (all `/ads/v4/trends/...` endpoints)
```
GET https://trends.pinterest.com/resource/ApiResource/get/
    ?source_url=<encoded page path>
    &data=<encoded JSON: {"options":{"url":"<REAL ENDPOINT>","data":{...}},"context":{}}>
    &_=<timestamp>
```
**REQUIRED header** (else 403 `Invalid Resource Request`):
```
X-Pinterest-PWS-Handler: trends/index.js
```
Response is wrapped: `{ "resource_response": { "status", "code", "endpoint_name", "data": ... }, "client_context": {...} }`
→ the payload you want is always **`resource_response.data`**. Ignore `client_context`
(it's the logged-in advertiser/user object).

### Style 2 — plain GET with query params (the keyword/search endpoints)
```
GET https://trends.pinterest.com/metrics/?terms=...&country=US&...
GET https://trends.pinterest.com/demographics/?terms=...&country=US&...
GET https://trends.pinterest.com/top_trends_filtered/?country=US&moments=...
GET https://trends.pinterest.com/latest_available_date/
```
No wrapper, no `resource_response` — the JSON is returned directly.

---

## 1. ⚠️ START HERE EVERY RUN — `/latest_available_date/`

**This was missing from Docs #1 and #2 and it matters.** Almost every endpoint takes an
`end_date` / `endDate` param. Don't hardcode it and don't use "today" — Pinterest's data lags.
Ask the API what the newest available data date is:

```
GET https://trends.pinterest.com/latest_available_date/
→ {"date":"2026-08-14"}
```

**Code agent: call this FIRST, then feed `date` into every other call's `end_date`/`endDate`.**
(The examples in Docs #1/#2 hardcode `2026-08-14` — that was the value on the day of capture.
Replace it with this call's result.)

---

## 2. COMPLETE ENDPOINT INVENTORY

### ✅ DOCUMENTED

| # | Endpoint | Style | Powers | Doc |
|---|----------|-------|--------|-----|
| 1 | `/ads/v4/trends/topics/featured/{region}/{event}` | wrapper | "Trends in the spotlight" (5 trends) | **Doc #1** |
| 2 | `/ads/v4/trends/moment/available/{region}` | wrapper | Moments list / "View moments" table | **Doc #2** |
| 3 | `/ads/v4/trends/moment/metrics/{region}` | wrapper | Moment detail chart + expected peak | **Doc #2** |
| 4 | `/top_trends_filtered/` | plain GET | "What users are searching for" keywords | **Doc #2** |
| 5 | `/metrics/` | plain GET | Keyword "Interest over time" + Predict the future | **Doc #2** |
| 6 | `/demographics/` | plain GET | Age + Gender distribution per keyword | **Doc #2** |
| 7 | `/latest_available_date/` | plain GET | The anchor date for all queries | **this doc, §1** |
| 8 | `/ads/v4/trends/shopping/product_categories` | wrapper | Category taxonomy (383 cats) — needed to resolve IDs→names | **Doc #3 §A3** |
| 9 | `/ads/v4/trends/shopping/product_categories/top/{region}` | wrapper | "Discover trending product categories" table | **Doc #3 §A1** |
| 10 | `/ads/v4/trends/shopping/product_categories/metrics/{region}` | wrapper | Sparklines + detail chart w/ forecast | **Doc #3 §A2, §C1.3** |
| 11 | `/ads/v4/trends/shopping/product_categories/top_products` | wrapper | **Top products on Pinterest** (no path params — works w/o catalog) | **Doc #3 §C1.1** |
| 12 | `/ads/v4/trends/shopping/product_categories/demographics/{region}` | wrapper | ⭐ Age/gender + search queries + 3-metric summary | **Doc #3 §C1.2** |

### ❌ NOT YET DOCUMENTED — remaining work

| # | Endpoint | Style | Powers | Status |
|---|----------|-------|--------|--------|
| 16 | `/related_terms/` | plain GET | "Related trends" chips on the keyword dashboard | **Doc #4 §A3** |
| 17 | `POST /term_images/` | **plain POST** | "Popular Pins" grid on the keyword dashboard | **Doc #4 §A4** |

### ❌ NOT YET DOCUMENTED — remaining work

| # | Endpoint | Style | Powers | Status |
|---|----------|-------|--------|--------|
| 13 | `/ads/v4/trends/editorial/content/{region}` | wrapper | **"Editors' Picks"** section | **TODO** |
| 14 | `…/product_categories/recommendations/{merchant_id}/{region}` | wrapper | Merchant category recs — **empty without a product catalog** | Doc #3 §A5 |
| 15 | `…/product_categories/top_products/{merchant_id}/{cat_id}` | wrapper | Merchant-scoped top products — **returns null without a catalog** | Doc #3 §A5 |

---

## 3. SITE MAP — every section on the site

**Homepage** (`/?country=US`) sections, in order:

| Section | Endpoint(s) | Covered? |
|---------|-------------|----------|
| Trends in the spotlight | #1 | ✅ Doc #1 |
| Moments | #2 (+#3,#4 on detail) | ✅ Doc #2 |
| Shopping trends | #8–#12 | ✅ **Doc #3** (incl. `/shopping/{id}/` detail page) |
| Search trends (keyword dashboard) | #5, #6, #16, #17 | ✅ **Doc #4** |
| **Editors' Picks** | #13 | ❌ **TODO** |
| **Pinterest Predicts 2026** | (likely static/marketing link — needs check) | ❌ **TODO (verify)** |

**Left nav** has 3 destinations: **Trend overview** (`/`), **Shopping trends** (`/shopping/`),
**Search trends** (`/detail/`).

**Standalone pages:**
- `/moments/{slug}` — moment detail ✅ Doc #2
- `/detail/?terms=...` — **Search trends keyword dashboard** ✅ **Doc #4** (supersedes Doc #2 §E)
- `/shopping/` — Shopping trends ❌ **TODO**

---

## 4. SHARED VOCABULARY (applies across all docs)

**Regions** — same codes everywhere (path segment or `country=` param):
```
US, CA, DE, FR, ES, IT, DE+AT+CH, GB+IE, IT+ES+PT+GR+MT, PL+RO+HU+SK+CZ,
SE+DK+FI+NO, NL+BE+LU, AR, BR, CO, MX, MX+AR+CO+CL, AU+NZ, JP, IN, ID, MY,
PH, TH, SA, EG, AE+SA+KW+QA+OM+BH+EG+IQ+DZ, IL+NG+PK+ZA+TR+MA+IN,
CR+DO+EC+GT+PE, CY+CZ+GR+HU+MT+PL+RO+SK, TR, KR
```

**Interest IDs** — same IDs across regions and across endpoints (spotlight, moment_interests,
editorial). Full table in Doc #1. `ALL` and `FASHION` are keywords, not numeric IDs.
> Note: the editorial endpoint returns interest IDs NOT in the homepage dropdown
> (e.g. `948967005229`, `924581335376`, `953061268473` = Entertainment). The dropdown is a
> curated subset — the underlying interest taxonomy is larger.

**Every number is INDEXED, not absolute.** `count`, `normalizedCount`, `normal_counts`,
`searchCount` are all normalized (typically peak = 100). You can compare a series to itself
over time; you can NOT read them as real search/save volumes.

**Growth fields** appear as either `pct_growth_mom` (spotlight) or a
`{wow_change, mom_change, yoy_change}` object (keywords). `yoy_change` is often `null` for
newer terms.

---

## 5. ⚠️ KNOWN UNKNOWN

**Moment page Age/Gender demographics** — the `/moments/{slug}` page's "Who's driving this
moment" widget is fed by a `POST /_/graphql/` call at page load. I captured the rendered
values and confirmed the data model matches `/demographics/` (`age_distribution` +
`gender_distribution`), but **did not capture the raw GraphQL query body**. If the code agent
needs it server-side, the `/demographics/` plain-GET endpoint (#6) returns the same shape and
is the recommended substitute.

---

## 6. SUGGESTED READING ORDER FOR AGENTS

**Code agent:** §0 (call styles) → §1 (`latest_available_date`) → §4 (shared vocabulary) →
then the specific Doc for the section you're implementing.

> ⚠️ **Cross-cutting trap:** there are TWO different `demographics` endpoints with identical
> field names but different subjects — `product_categories/demographics/{region}` (category, #12)
> and `/demographics/` (keyword, #6). They return very different numbers. See Doc #3 §C7.

**Marketer agent:** skip to PART B of each Doc — Doc #1 PART B (spotlight: what's hot now),
Doc #2 PART B (moments: seasonal planning). §4's "everything is indexed" warning is the one
technical note that matters for interpreting any number you see.
