# Pinterest Trends — DOCUMENTATION INDEX

Reverse-engineered from `trends.pinterest.com`. **18 REST endpoints + 2 captured**
(the moment-demographics GraphQL query §3.18 and `PinResource` §3.19), fully mapped.

---

## 🚀 START HERE

| If you are… | Read |
|-------------|------|
| **Building an API client (Claude Code)** | **#7 API-REFERENCE** → **#8 BUILD-GUIDE** |
| A **code agent** implementing one feature | #7 for the endpoint, then the page doc |
| A **marketer agent** | PART B of #1–#6 |
| Resolving a **category ID** | #5 + `pinterest-category-taxonomy.json` |

---

## FILES

| # | File | Contents |
|---|------|----------|
| **0** | `00-INDEX-and-coverage.md` | This file |
| **1** | `01-spotlight-featured-topics.md` | "Trends in the spotlight" (homepage) |
| **2** | `02-moments.md` | Moments: list, detail, keywords |
| **3** | `03-shopping-trends.md` | Shopping trends + category detail pages |
| **4** | `04-search-trends-dashboard.md` | `/detail/` keyword dashboard |
| **5** | `05-shopping-category-taxonomy.md` | Full 383-category tree, 4 levels |
| **6** | `06-search-keywords-discovery.md` | `/search` keyword discovery, 4 presets |
| **7** | **`07-API-REFERENCE.md`** | ⭐ **Endpoint-first reference — every endpoint, params, limits** |
| **8** | **`08-BUILD-GUIDE.md`** | ⭐ **Data model, call chains, client design, checklist** |
| — | `pinterest-category-taxonomy.json` | Machine-readable taxonomy |

> **#1–#6 are organised by UI page. #7–#8 are organised by API.** The API does not map 1:1 to
> the UI — one endpoint can power two unrelated screens, and one screen can hide five endpoints.
> **To build software, use #7 and #8.**

---

## ENDPOINT COVERAGE — 20/20 ✅

| Endpoint | Doc |
|----------|-----|
| `/latest_available_date/` | #7 §3.1 |
| `/ads/v4/trends/topics/featured/{region}/{event}` | #1, #7 §3.2 |
| `/ads/v4/trends/moment/available/{region}` | #2, #7 §3.3 |
| `/ads/v4/trends/moment/metrics/{region}` | #2, #7 §3.4 |
| `/ads/v4/trends/editorial/content/{region}` | #7 §3.5 |
| `/ads/v4/trends/shopping/product_categories` | #5, #7 §3.6 |
| `…/product_categories/top/{region}` | #3, #7 §3.7 |
| `…/product_categories/metrics/{region}` | #3, #7 §3.8 |
| `…/product_categories/demographics/{region}` | #3, #7 §3.9 |
| `…/product_categories/top_products` | #3, #7 §3.10 |
| `…/top_products/{merchant_id}/{cat_id}` | #7 §3.11 (needs catalog) |
| `…/recommendations/{merchant_id}/{region}` | #7 §3.11 (needs catalog) |
| `/top_trends_filtered/` | #2, #6, #7 §3.12 |
| `/metrics/` | #4, #7 §3.13 |
| `/demographics/` | #4, #7 §3.14 |
| `/related_terms/` | #4, #7 §3.15 |
| `POST /term_images/` | #4, #7 §3.16 |
| `/prefix_match/` | #4, #7 §3.17 |
| `POST /_/graphql/` (moment Age/Gender) | #7 §3.18 ✅ captured 2026-08-19 |
| `GET www.pinterest.com/resource/PinResource/get/` | #7 §3.19 ✅ captured 2026-08-19 |

**Confirmed NOT endpoints:** Pinterest Predicts (hardcoded in JS bundle), CSV Export
(client-side papaparse), region list, interest list, spotlight trend detail, "Predict the future"
toggle, "All categories" tab, "Other product categories".

**Captured 2026-08-19 (previously listed here as not reproducible):** moment-page
Age/Gender via the persisted GraphQL query — see #7 §3.18. The same query also answers
**moment × interest**, and Pinterest's ~7-per-moment dropdown is UI curation, not a data
limit: any moment × any interest is queryable.

**Still not reproducible:** merchant endpoints (need a product catalog);
`publish_state=DRAFT` (permission-gated).

**Bonus — product/merchant data (#7 §3.19):** `top_products` returns `pin_id` only, but
`pinterest.com/pin/{pin_id}/` exposes **merchant name, outbound host, price, rating and
shipping** — completing the chain to real product links per trending category.
**Captured 2026-08-19:** `GET www.pinterest.com/resource/PinResource/get/` with
`field_set_key: auth_web_main_pin`. Costs 1 request per pin (no batch form), so
enrichment is opt-in and capped. See #7 §3.19.

**Untested:** anonymous/logged-out access; rate-limit thresholds.

---

## THE 12 TRAPS (full detail in #7 §5)

1. **Case sensitivity** — `keywordsToInclude`, `prefix_match?query=` return 200 + empty on non-lowercase
2. **Response-scoped normalisation** — `normalize_against_group`, `percent_relative_volume`
3. **Dead params** — `lookbackWindow`, `rankingMethod` have zero effect
4. **Unknown params silently ignored**, not rejected
5. **Silent empty ≠ error** — L2/L3 verticals, `SAVE` on top_products, wrong-case keywords
6. **Two `demographics` endpoints** — category vs keyword, opposite answers
7. **`shouldMock=true` returns fake 2019 data** with HTTP 200
8. **`end_date` bounds differ per endpoint**
9. **Everything is peak-indexed**, never absolute volume (`total` is always 0)
10. **429 rate limiting** on `/metrics/`
11. **Multi-term shrinkage** — 10 requested → 9 returned, order not preserved
12. **`affinity` always null**

---

## REGION CAPABILITY MATRIX

| Capability | Regions |
|------------|---------|
| Most endpoints | 32 codes (#7 §4.1) — UI exposes 26 |
| `top_products` | **US, CA, GB+IE only** |
| `editorial/content` | **US, CA, GB+IE only** (others: 200 + empty) |
| Moments | **region-specific lists**; 25 slugs globally; **JP, IN = none** |
| Forecasts (`has_prediction`) | **US only (observed)** |
| Shopping verticals with data | **7 of 14** — UI shows only 3 |
