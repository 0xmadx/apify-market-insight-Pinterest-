# Pinterest Trends — "Trends in the Spotlight" (Featured Topics)

> Source page: https://trends.pinterest.com/  → section **"Trends in the spotlight"**
> This section shows a ranked list of 5 trending topics. The UI lets you change the
> **Region** (top-right of page) and the **Interest** (dropdown inside the section).
> Everything on this page is one call to the endpoint below.

This document has two halves:
- **PART A — For the code agent.** Exact endpoint, how to call it, params, response shape.
- **PART B — For the marketer agent.** What each field *means* and what you actually receive.

---

## PART A — For the CODE agent

### The one endpoint behind this section

The page never calls the API directly. It goes through Pinterest's generic resource
proxy. You always hit the SAME wrapper URL and change what's *inside* it:

```
GET https://trends.pinterest.com/resource/ApiResource/get/
    ?source_url=<url-encoded page path>
    &data=<url-encoded JSON payload>
    &_=<timestamp / cache-buster>
```

The real API call lives inside the `data` JSON:

```jsonc
{
  "options": {
    "url": "/ads/v4/trends/topics/featured/{REGION}/SAVE",   // <-- the actual endpoint
    "data": {
      "interests": ["934876475639"],   // one interest ID, OR omit for cross-interest ("All")
      "publish_state": "PUBLISHED"
    }
  },
  "context": {}
}
```

So there are really two things to fill in:
1. `{REGION}` in the path (e.g. `US`, `CA`, `GB+IE`).
2. The `data` object: which `interests` + `publish_state`.

### Required header (IMPORTANT)

The request MUST include this header or you get **403 Forbidden**:

```
X-Pinterest-PWS-Handler: trends/index.js
```

- No cookies / auth token needed for the request to *succeed* (it runs against the
  logged-in advertiser context of the browser session, but the call itself only needs
  the header above). Send it same-origin from trends.pinterest.com.
- `X-Requested-With: XMLHttpRequest` alone is NOT enough → 403.
- With NO special headers → 403.
- With `X-Pinterest-PWS-Handler: trends/index.js` → **200 OK**. ✅

### Path parameter: REGION

Put the region code directly in the path: `/ads/v4/trends/topics/featured/US/SAVE`.
`+` is allowed literally in the path (e.g. `GB+IE`). Valid region codes:

```
US, CA, DE, FR, ES, IT,
DE+AT+CH, GB+IE, IT+ES+PT+GR+MT, PL+RO+HU+SK+CZ, SE+DK+FI+NO, NL+BE+LU,
AR, BR, CO, MX, MX+AR+CO+CL,
AU+NZ, JP, IN, ID, MY, PH, TH,
SA, EG, AE+SA+KW+QA+OM+BH+EG+IQ+DZ, IL+NG+PK+ZA+TR+MA+IN,
CR+DO+EC+GT+PE, CY+CZ+GR+HU+MT+PL+RO+SK, TR, KR
```

> Note: the full list above comes from the API error message (all regions the endpoint
> accepts). The Region dropdown in the UI only exposes a subset (26 options). An invalid
> region returns 400 with this list.

### The `/SAVE` at the end (event type)

The last path segment is an **event type**. Only `SAVE` is accepted for this endpoint:

```
The event parameter must be one of: SAVE
```

`SEARCH`, `CLICK`, `IMPRESSION`, `VIEW`, `ALL` → all return **400**.
So this endpoint is hardcoded to trends based on **Pin saves** (which is exactly what the
UI says: "trending based on Pin saves, a powerful signal of intent").

### Body param: `interests`

- **One numeric interest ID** → trends for that interest (e.g. `["934876475639"]` = DIY and Crafts).
- **Omit `interests` entirely** (or send `[]`) → the **"All"** view: top cross-interest trends.
- **Multiple IDs** → **400** `Multiple interest parameters are not supported, with the
  exception of requesting all fashion L1 interests`.
- **`"FASHION"`** (the Fashion category) is a special string keyword, NOT a numeric ID.
  Passing `["FASHION"]` here returned **500**. Fashion is handled by a different path
  (the "all fashion L1 interests" special case referenced in the error) — treat Fashion
  as a separate case, not a normal interest ID.

### Body param: `publish_state`

- `PUBLISHED` — what the live site uses. (also accepts `DRAFT`.)
- Omitting it still returns 200 with published data.
- `"ALL"` / other values → **400** `'publish_state' must be one of: PUBLISHED, DRAFT`.

### All valid Interest IDs (US dropdown)

| ID | Interest |
|----|----------|
| `ALL` | All (omit `interests` in the body to get this) |
| `925056443165` | Animals |
| `918105274631` | Architecture |
| `961238559656` | Art |
| `935541271955` | Beauty |
| `934876475639` | DIY and Crafts |
| `922134410098` | Education |
| `941870572865` | Event Planning |
| `FASHION` | Fashion (⚠️ keyword, special handling — see above) |
| `918530398158` | Food and Drinks |
| `909983286710` | Gardening |
| `898620064290` | Health |
| `935249274030` | Home Decor |
| `920236059316` | Parenting |
| `908182459161` | Travel |
| `903260720461` | Wedding |

> To get the interest list programmatically for a region, the values live in the page's
> Interest `<select>`. IDs appear stable across regions (same numeric IDs).

### Response shape

```jsonc
{
  "resource_response": {
    "status": "success",
    "code": 0,
    "endpoint_name": "get_featured_topics_handler",
    "http_status": 200,
    "data": [ /* array of 5 TREND objects — see below */ ]
  },
  "client_context": { /* logged-in advertiser/user context — ignore for trends */ }
}
```

Each of the 5 **TREND objects**:

```jsonc
{
  "id": "2542595742484332416",
  "name": "Embroidery Gifts",
  "description": "Embroidery gifts are ...",       // marketing blurb about the trend
  "interests": ["934876475639"],                    // which interest(s) this belongs to
  "is_published": true,
  "pct_growth_mom": 25,                             // month-over-month growth %, see Part B
  "related_search_trends": [                        // related search terms people use
    "how to embroider by hand", "embroidery projects", ...
  ],
  "pins": [                                         // representative example Pins (images)
    {
      "id": "...",
      "src": "https://i.pinimg.com/236x/...jpg",    // image URL
      "width": 236, "height": 472,
      "color": "#85703d",                           // dominant color (hex)
      "vertical_offset": null
    }
    // ~9 pins per trend
  ],
  "time_series": [                                  // ~13 weekly points, the sparkline data
    { "date": "2026-05-22", "count": 3,
      "normalized_count": null,
      "normalized_predicted_lower_bound": null,
      "normalized_predicted_upper_bound": null },
    ...
    { "date": "2026-08-14", "count": 100, ... }
  ]
}
```

### Error behavior (quick reference)

| Cause | Status | Message |
|-------|--------|---------|
| Missing `X-Pinterest-PWS-Handler` header | 403 | Invalid Resource Request |
| Bad region | 400 | `The region parameter must be one of: ...` |
| Event ≠ SAVE | 400 | `The event parameter must be one of: SAVE` |
| Multiple interest IDs | 400 | `Multiple interest parameters are not supported...` |
| `interests: ["FASHION"]` | 500 | server error (Fashion needs special path) |
| Bad `publish_state` | 400 | `'publish_state' must be one of: PUBLISHED, DRAFT` |

---

## PART B — For the MARKETER agent

### What this data is

This is Pinterest's **"Trends in the Spotlight"** — a hand-picked, ranked list of **5
topics that are trending right now**, based on **how often people SAVE Pins** about them.
A save is a strong intent signal on Pinterest (someone is planning to actually do/buy the
thing), so this is closer to "what people intend to do soon" than "what people are just
looking at."

You get 5 trends per query. You control two dials:
- **Region** — the market (US, Canada, GB+IE, France, Brazil, Korea, etc.).
- **Interest** — the category (DIY and Crafts, Food and Drinks, Home Decor, Beauty…),
  or "All" for the top trends across every category.

### What you actually receive for each of the 5 trends

- **`name`** — the trend, in plain words. e.g. *"Embroidery Gifts"*, *"Kids Denim Jackets"*,
  *"Pumpkin Patch Outfits"*. This is your headline / content angle.

- **`pct_growth_mom`** — **month-over-month growth**, as a percentage. This is the "↑ 2,500%
  MoM" number you see in the UI. Higher = accelerating faster right now. Use it to rank how
  urgent/hot a trend is. (Note: the UI shows values like 2,500% / 900% / 100%; the raw field
  is the same number.)

- **`interests`** — which category the trend sits in. In the "All" view this tells you
  which category a cross-category trend actually came from (e.g. "Kids Denim Jackets" shows
  up under *DIY and Crafts AND Fashion*).

- **`description`** — a ready-made marketing blurb describing the trend and *why it's
  resonating*. Good raw material for captions, briefs, or positioning. (It's Pinterest's
  own copy — treat as a starting point, not final copy.)

- **`related_search_trends`** — the actual **search terms** people type around this trend
  (e.g. "easy embroidery for beginners", "embroidery gift ideas"). This is gold for:
  keyword targeting, SEO, ad copy, hashtags, and understanding the sub-angles of a trend.

- **`pins`** — a handful (~9) of **example Pins/images** that represent the trend, each with
  an image URL (`src`), size, and dominant `color`. Use these to *see* what the trend looks
  like visually — the aesthetic, the styling, the palette — before you brief creative.

- **`time_series`** — ~13 weekly data points (`date` + `count`) showing how the trend rose
  over the last ~3 months. This is the sparkline. `count` is a **relative popularity index
  scaled to 100 at its peak** (e.g. starts at 3, ends at 100) — so read it as a *shape*
  (is it still climbing? plateauing? spiking?), not as an absolute number of saves.

### How a marketer would typically use it

1. Pick your **market** (region) and **category** (interest).
2. Read the 5 trend `name`s + `pct_growth_mom` → shortlist which trends to act on.
3. Pull `related_search_trends` → build your keyword / hashtag / ad-copy list.
4. Look at `pins` → understand the visual style to match in creative.
5. Check `time_series` shape → decide if you're early (still climbing = act now) or late
   (already peaked/declining).
6. Use `description` as a first-draft angle for the brief.

### Important caveats for the marketer agent

- Only **5 trends** come back per query — it's a curated spotlight, not a full list. To
  cover more ground, query multiple **interests** and/or **regions** and combine.
- Signal is **Pin SAVES only** — it reflects *intent/planning*, which skews toward
  seasonal, DIY, planning, and purchase-consideration behavior.
- `count` in `time_series` is **normalized (peak = 100)**, so you can compare a trend's
  own shape over time, but you can NOT compare absolute `count` between two different
  trends as if they were real volumes.
- **Fashion** behaves differently from other categories (special handling) — expect it to
  need a separate query path, not the normal interest-ID call.
- Numbers refresh over time (weekly cadence in the series), so treat any pulled snapshot
  as dated — re-pull for current values.
