# Doc #8 — BUILD GUIDE: turning Pinterest Trends into an API

> For **Claude Code**. Doc #7 is the endpoint reference; this is **how the pieces connect** and
> what a client must implement. The site's UI hides the real structure — this doc gives the
> data model behind it.

---

# 1. THE ACTUAL DATA MODEL (not the UI)

Pinterest Trends is **four independent datasets** plus **one shared join table**. The UI mixes
them across pages; the API keeps them separate.

```
                       ┌──────────────────────────┐
                       │  latest_available_date   │  ← call FIRST, feeds every end_date
                       └────────────┬─────────────┘
                                    │
   ┌───────────────┬────────────────┼────────────────┬───────────────────┐
   │               │                │                │                   │
┌──▼──────┐  ┌─────▼──────┐  ┌──────▼───────┐  ┌─────▼────────┐  ┌───────▼────────┐
│ TOPICS  │  │  MOMENTS   │  │   SHOPPING   │  │   KEYWORDS   │  │   EDITORIAL    │
│ (curated│  │ (seasonal  │  │  (commerce)  │  │  (search)    │  │  (hand-written)│
│  trends)│  │  events)   │  │              │  │              │  │                │
└──┬──────┘  └─────┬──────┘  └──────┬───────┘  └─────┬────────┘  └───────┬────────┘
   │               │                │                │                   │
   └───────────────┴────────────────┴────────┬───────┴───────────────────┘
                                             │
                            ALL FOUR emit KEYWORD STRINGS
                                             │
                                   ┌─────────▼──────────┐
                                   │  KEYWORD PIPELINE  │
                                   │ metrics · demo ·   │
                                   │ related · images   │
                                   └────────────────────┘
```

**The key insight:** every dataset is a *different way of arriving at keywords*, and all of them
funnel into the same 4 keyword endpoints. That's why `/detail/` has 5 UI entry points.

### Shared join keys
| Key | Used by |
|-----|---------|
| **region code** | every endpoint (but valid sets differ — Doc #7 §4.1) |
| **interest ID** | topics, moments (`moment_interests`), keywords (`l1interests`), editorial |
| **moment slug** | moments, keywords (`moments=`) |
| **category ID** | shopping only (needs taxonomy join for names) |
| **keyword string** | the universal join — connects everything to the keyword pipeline |

---

# 2. CANONICAL CALL CHAINS

### 2.1 Bootstrap (once per session)
```
GET /latest_available_date/            → END_DATE
A   /ads/v4/trends/shopping/product_categories   → taxonomy (cache; 383 rows)
    + hardcode: regions, interest IDs, L1 verticals, moment slugs per region
```

### 2.2 "What's trending right now?" (topics)
```
A  topics/featured/{region}/SAVE {interests:[ID] | omitted}
   → 5 trends, each already containing description, pins, time_series, related_search_trends
   (no follow-up call needed — the detail view is the same object)
```

### 2.3 Seasonal planning (moments)
```
A  moment/available/{region}                     → moments + phase_labels + peak dates
       │ pick phase ∈ {rising, approaching}
A  moment/metrics/{region} {moments:[slug]}      → demand curve + forecast + interest split
B  /top_trends_filtered/?moments=<slug>          → the moment's keywords
       └─► KEYWORD PIPELINE (§2.6)
```

### 2.4 Commerce (shopping)
```
FOR EACH vertical in [1181, 1250, 1042, 1148, 1016, 1500, 1315]:      ← 7, not 3
A  product_categories/top/{region} {parent_product_categories:[v], limit:100}
       │ → category IDs  (join taxonomy for names)
A  product_categories/metrics/{region} {product_category_ids:[…]}      → sparklines
       │
       │ drill into one category:
A  product_categories/demographics/{region} {product_category_ids:[id]}
       │   → age/gender + related_search_trends + Performance's HEADLINE numbers,
       │     all 3 in ONE call — but NOT the Performance chart, see below
A  product_categories/top_products {product_category_id:id, region, event:OUTBOUND_CLICK}
       │   → real products (US/CA/GB+IE only)
       └─► related_search_trends ─► KEYWORD PIPELINE (§2.6)
```
⚠️ One vertical per `top/` call — never combine (normalisation, Doc #7 §3.7).

⚠️ **The full category drill-down page is 3 calls minimum, not the 1 implied above.**
`demographics/` covers the Demographics tab and the Performance headline numbers in one
call, but the Performance **chart itself** is the `metrics/` call already made earlier in
this chain (its `product_category_ids` should include the drilled-into category), and
`top_products` is always separate. See Doc #7 §3.9 for the full three-endpoint table.

### 2.5 Keyword discovery (search)
```
B  /top_trends_filtered/?trendsPreset={1|2|3|4}&country&endDate
      &l1interests&moments&ageBuckets&gender&keywordsToInclude&numTermsToReturn=100
   → up to 100 keywords + wow/mom/yoy + seasonality_score
   (NO prediction flag here — needs §2.6 per term)

   or, from a seed stem:
B  /prefix_match/?query=<lowercase stem>   → 10 keywords + 1yr sparkline each
```

### 2.6 KEYWORD PIPELINE (the convergence point)
```
B  /metrics/?terms=<a,b,c>&days=365&aggregation=2&predicted_days=91
              &normalize_against_group=true&shouldMock=false
   → curve, growth_rates, has_prediction  ← the 🔮 crystal ball
B  /demographics/?terms=<a,b,c>            → age + gender per term
B  /related_terms/?requestTerm=<a>         → 5 siblings + hasPrediction each
P  POST /term_images/ {terms:[a,b,c]}      → Popular Pins image URLs
```

### 2.7 Editorial
```
A  editorial/content/{region}    → 6 curated trends (US/CA/GB+IE only)
       └─► item.keywords[region] ─► KEYWORD PIPELINE (§2.6)
```

---

# 3. MINIMAL CLIENT (pseudo-code)

```python
BASE = "https://trends.pinterest.com"
HEADERS = {"X-Pinterest-PWS-Handler": "trends/index.js"}   # REQUIRED, both styles

def wrapped(path, data=None):
    """Style A — /ads/v4/trends/... endpoints.
       source_url and _ are OPTIONAL and their values are irrelevant (verified)."""
    payload = {"options": {"url": path, "data": data or {}}, "context": {}}
    r = GET(f"{BASE}/resource/ApiResource/get/", headers=HEADERS,
            params={"data": json.dumps(payload)})
    body = r.json()["resource_response"]
    if body.get("error"):
        raise PinterestTrendsError(body["error"].get("message_detail"))
    return body["data"]                      # ← payload is ALWAYS here

def plain(path, **params):
    """Style B — /metrics/, /demographics/, /top_trends_filtered/, ..."""
    return GET(f"{BASE}{path}", headers=HEADERS, params=params).json()

def post_json(path, body):
    return POST(f"{BASE}{path}", headers={**HEADERS,
                "Content-Type": "application/json"}, json=body).json()
```

### Non-negotiable client rules
```python
END_DATE = plain("/latest_available_date/")["date"]   # never "today"

kw = keyword.lower()          # trap 1 — silent empty otherwise
assert aggregation == 2       # only valid value
assert 0 <= predicted_days <= 91
normalize_against_group = True        # when comparing >1 term
shouldMock = False                    # true = fake 2019 data

# multi-term: never trust length/order
got = {row["term"]: row for row in plain("/metrics/", terms=",".join(terms), ...)}
missing = [t for t in terms if t not in got]
```

### Retry / backoff
```python
# /metrics/ returns 429 under load.
for attempt in range(5):
    r = call()
    if r.status_code != 429: break
    sleep(2 ** attempt)
```

---

# 4. NORMALISATION — the #1 correctness bug

Every count in this API is **peak-normalised within its response**, never an absolute volume.

| Endpoint | Field | Scope of the 0–100 scale |
|----------|-------|--------------------------|
| `/metrics/` | `normalizedCount` | per-term if `normalize_against_group=false`; **shared** if `true` |
| `product_categories/top` | `percent_relative_volume` | **the response** — mixing verticals crushes small ones |
| `top_trends_filtered` | `searchCount`, `normalizedCount` | the response |
| `moment/metrics` | `normal_counts` | the response |
| `topics/featured` | `time_series[].count` | the trend itself |

**Rules:**
1. To compare terms → one call, `normalize_against_group=true`.
2. To compare categories → **same vertical**, same call.
3. **Never compare numbers across two different responses.**
4. To compare across scopes, use **percentages** (`percent_growth`, `wow/mom/yoy_change`) — those
   are absolute.

*Evidence:* "family" is `100` alone but `8` beside "nails". Beauty's #1 category is `1.00` alone,
`0.01` beside Fashion.

---

# 5. FORECASTS (`has_prediction`)

```
has_prediction is a property of (keyword, region). It NEVER changes with date params.
  ├─ true  → send predicted_days=91; read predictedUpper/LowerBoundNormalizedCount
  └─ false → no forecast exists. Do NOT retry with other params.
             Rank on growth_rates + seasonality_score instead.
             Check /related_terms/ — siblings may have one (does not propagate either way).
Region: US = true observed; CA/GB+IE/DE/FR/ES = false, including Canada's own top keywords.
        → treat forecasting as US-only.
```
There is **no server-side filter** for "only forecastable keywords" — six candidate param names
were silently ignored. Reproducing the crystal ball costs **1 + N** requests.

---

# 6. SUGGESTED CLIENT SURFACE

```
client.bootstrap()                                  # date + taxonomy + static vocab

# topics
client.spotlight(region, interest=None)

# moments
client.moments(region)                              # list + phases + peaks
client.moment_metrics(region, slug, days=365, predicted_days=91)
client.moment_keywords(region, slug, limit=100)

# shopping
client.trending_categories(region, vertical, event="OUTBOUND_CLICK", limit=100)
client.category_metrics(region, ids, days=180, predicted_days=28)
client.category_profile(region, id, event)          # demographics+keywords+summary (1 call)
client.category_products(region, id)                # US/CA/GB+IE only
client.taxonomy()                                   # cached

# keywords
client.discover(region, preset, **filters)          # top_trends_filtered
client.suggest(stem, region)                        # prefix_match
client.keyword(terms, region, days, forecast=True)  # metrics (+normalize when len>1)
client.keyword_demographics(terms, region)
client.related(term, region)
client.keyword_images(terms, region)

# editorial
client.editors_picks(region)                        # US/CA/GB+IE only
```

**Region-capability matrix to enforce client-side:**
| Capability | Regions |
|------------|---------|
| Most endpoints | all 32 (Doc #7 §4.1) |
| `top_products` | **US, CA, GB+IE** |
| `editorial/content` | **US, CA, GB+IE** (others 200 + empty) |
| Moments | region-specific lists; **JP, IN = none** |
| Forecasts | **US only (observed)** |

---

# 7. VALIDATION CHECKLIST

Before trusting a client, assert:

- [ ] `end_date` comes from `/latest_available_date/`, never `today()`
- [ ] `X-Pinterest-PWS-Handler: trends/index.js` on **every** request — value must match EXACTLY (any other string → 403)
- [ ] `source_url` / `_` omitted (optional; values irrelevant)
- [ ] payload read from `resource_response.data` (Style A) and errors from `resource_response.error`
- [ ] all keyword input lowercased
- [ ] `aggregation=2`, `predicted_days<=91`, `shouldMock=false`
- [ ] `normalize_against_group=true` whenever `len(terms) > 1`
- [ ] shopping `top/` called **once per vertical**, with an **L1** ID
- [ ] category IDs joined to the taxonomy for names; L1 names hardcoded
- [ ] moment slugs validated **against that region's list**, apostrophes stripped
- [ ] empty arrays handled as "no data", not errors
- [ ] 429 backoff on `/metrics/` — **no rate-limit headers exist**, back off blindly
- [ ] `x-pinterest-rid` logged for debugging
- [ ] multi-term responses matched **by term**, not by index

---

# 8. KNOWN LIMITS (document, don't fight)

| Limit | Value |
|-------|-------|
| Granularity | **Weekly everywhere** — EXCEPT `moment/metrics`, the only endpoint supporting `daily`/`monthly` |
| Forecast horizon | **91 days** max |
| Keyword rows | **100** max per `top_trends_filtered` call |
| Typeahead | **10** results, `limit` ignored |
| Related terms | **5** |
| Spotlight | **5** trends |
| Editorial | **6** items |
| Top products | server-decided (33–50), `limit` ignored |
| Trending categories | **40** total across all verticals (US); `limit` ceiling 522 |
| History | **730 days** max window; `/metrics/` `end_date` limited to ~1 yr back (future → 400); `top_trends_filtered` accepts far-past `endDate` |
| Pagination | **none anywhere** — all endpoints use `limit`-style caps |
| Absolute volumes | **never exposed** — `total` is always 0 |

## Not reproducible
- **Moment page Age/Gender** — persisted GraphQL, no REST equivalent (Doc #7 §3.18). Workaround:
  moment keywords → `/demographics/` → aggregate.
- **Merchant endpoints** — need a product catalog.
- **`publish_state=DRAFT`** — permission-gated ("User does not have access to unpublished topics").

## Untested
- **Anonymous access.** All findings come from an authenticated session with an advertiser
  account. Whether any of this works logged-out is **unknown** — verify before designing auth.
- **Rate-limit thresholds.** A 429 was observed on `/metrics/`; limits were not characterised.
