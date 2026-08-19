# Pinterest Trends — "Discover trending search keywords" (`/search`)

> Page: `https://trends.pinterest.com/search` · title **"Search trends"**
> Heading: **"Discover trending search keywords"** —
> *"Explore keyword trends on Pinterest. Click the crystal ball for predicted peaks."*

This is the **keyword discovery** page (find keywords you don't know yet). Not to be confused
with `/detail/` (Doc #4), which is the **keyword dashboard** for a keyword you already have.

---

## PART A — For the CODE agent

### A1. ONE endpoint powers the whole page

```
GET https://trends.pinterest.com/top_trends_filtered/?<params>
```
Plain GET, **not** the ApiResource wrapper. Header `X-Pinterest-PWS-Handler: trends/index.js`.

> This is the **same endpoint** used for the Moments keyword chips (Doc #2 §A3) — same shape,
> different params. One endpoint, two very different UIs.

### A2. Full parameter list (captured live)

```
GET /top_trends_filtered/
    ?country=US
    &endDate=2026-08-14
    &trendsPreset=3                    <-- THE TAB (see A3)
    &numTermsToReturn=50
    &lookbackWindow=3                  <-- ⚠️ IGNORED (see A4)
    &ageBuckets=4,5,6,7,8,9,2,3        <-- numeric codes (see A5)
    &gender=1,0,2                      <-- numeric codes (see A5)
    &l1interests=925056443165,918105274631,…   <-- interest IDs (see A6)
    &moments=christmas,easter,halloween,…      <-- moment slugs (see A7)
    &keywordsToInclude=nails           <-- the "Include keyword" box (see A8)
    &rankingMethod=1                   <-- seen on the Moments page variant
    &shouldMock=false
```

**All filter params are optional.** With `country`, `endDate` and `trendsPreset` alone the
endpoint returns results fine.

### A3. ⭐ `trendsPreset` = the four tabs

| UI tab | `trendsPreset` | `lookbackWindow` the UI sends | UI description |
|--------|----------------|-------------------------------|----------------|
| **Top monthly trends** | `1` | 2 | high search volume, last 30 days |
| **Top yearly trends** | `2` | 5 | "high search volume within the last 365 days of your selected end date" |
| **Growing trends** | `3` | 3 | "high growth in search volume within the last 90 days of your selected end date" |
| **Seasonal trends** | `4` | 2 | seasonal peaks |

Probed: **only `1,2,3,4` are valid.** `0`, `5`, `6` → **400**.

Each preset returns genuinely different data:
- `1`/`2` (volume presets) → head terms: *nails, hairstyles, wallpaper, nail ideas…*
- `3` (growing) → emergent terms: *sterling point tv show, sterling point, see more…*
- `4` (seasonal) → *first day of school prayer, senior sunrise captions, august pedicure…*

### A4. 🚨 `lookbackWindow` is IGNORED — `trendsPreset` is the only driver

You suspected monthly-vs-yearly might just be a date/window change. **It isn't** — and the
window param doesn't work at all. Proven by diff:

| Test | Result |
|------|--------|
| preset 1, lookbackWindow `0..6` (7 calls) | **all identical** |
| preset 1 + lb 2 **vs** preset 1 + lb 5 | **identical** ✔ |
| preset 2 + lb 5 **vs** preset 2 + lb 2 | **identical** ✔ |
| preset 1 **vs** preset 2 (same lb) | **different** ✔ |

→ The lookback period is baked into the preset server-side. The UI sends a `lookbackWindow`
that matches the tab, but the value is inert. **Set `trendsPreset` and ignore `lookbackWindow`.**

`endDate` **does** work — it moves the whole window (the UI shows the derived range,
e.g. Growing = `endDate-90d → endDate`, Yearly = `endDate-365d → endDate`).

### A5. Numeric filter codes (decoded by toggling one at a time)

**`ageBuckets`** — ⚠️ **18-24 maps to TWO codes.** That's why 7 UI options send 8 codes:

| UI option | Code(s) |
|-----------|---------|
| **18-24** | **`2,3`** ⚠️ |
| 25-34 | `4` |
| 35-44 | `5` |
| 45-49 | `6` |
| 50-54 | `7` |
| 55-64 | `8` |
| 65+ | `9` |

Verified: only 18-24 → `"2,3"` · only 65+ → `"9"` · 65+ and 25-34 → `"9,4"`.
Codes `0` and `1` are accepted by the API but never sent by the UI. `99` → 400, `abc` → 500.

**`gender`**

| UI option | Code |
|-----------|------|
| Male | `0` |
| Female | `1` |
| Unspecified | `2` |

Verified: Female only → `"1"` · Female+Male → `"1,0"`. Only `0,1,2` valid (`3`, `9` → 400).

### A6. `l1interests` — 24 interests on this page

⚠️ **This page offers 24 interests; the homepage spotlight filter offers 16.** Different lists.

Confirmed IDs (captured in click order — reliable):

| Interest | ID |
|----------|-----|
| Animals | `925056443165` |
| Architecture | `918105274631` |
| Art | `961238559656` |
| Beauty | `935541271955` |
| Children's Fashion | `903733943146` |
| Design | `902065567321` |
| DIY and Crafts | `934876475639` |
| Education | `922134410098` |
| Electronics | `960887632144` |
| Entertainment | `953061268473` |
| Event Planning | `941870572865` |
| Finance | `913207199297` |

Remaining 12 UI options, **IDs not yet captured**: Food and Drinks, Gardening, Health, Home
Decor, Men's Fashion, Parenting, Quotes, Sport, Travel, Vehicles, Wedding, Women's Fashion.

> ⚠️ **Unverified discrepancy to check:** a 13th ID `948967005229` appeared when selecting what
> should have been *Food and Drinks* — but the homepage's Food and Drinks is `918530398158`.
> Either the two pages use different interest taxonomies, or that click mis-registered.
> **Verify by selecting one interest at a time before relying on it.**
> (Note `948967005229` also appeared in the editorial endpoint on a *fashion* trend, which
> suggests it is NOT Food and Drinks.)

To map the rest: select exactly one interest, read `l1interests` from the request.

### A7. `moments` — comma-separated slugs

Same 13 slugs as Doc #2: `christmas, easter, fathers day, halloween, hanukkah,
independence day, memorial day, mothers day, new years eve, st patricks day, summer,
thanksgiving, valentines day`. Filters keywords to those tied to the selected moments.

### A8. `keywordsToInclude` — the "Include keyword" box

Free-text substring filter, applied **server-side**:
- `keywordsToInclude=nails` → every row contains "nails" (spring nails, 4th of july nails…)
- `keywordsToInclude=drill` → **`values: []`**, UI shows *"Oops! Filters are too narrow. Try
  expanding your search."*

So it filters within the preset's universe — it is **not** a general keyword search. For an
arbitrary keyword use `/metrics/` (Doc #4).

### A9. Response

```jsonc
{ "endDate": "2026-08-14",
  "values": [                              // numTermsToReturn (UI uses 50)
    { "term": "end of august nails",
      "searchCount": 100,                  // indexed volume
      "normalizedCount": 100,
      "reverseRank": 25,
      "seasonality_score": 0.43,           // 0–1, how seasonal
      "wow_change": { "value": .., "index": .. },   // Weekly change column
      "mom_change": { "value": .., "index": .. },   // Monthly change column
      "yoy_change": { "value": .., "index": .. },   // Yearly change column
      "affinity": null } ] }
```
UI columns map to: Keywords→`term`, Search volume→`searchCount` bar,
Weekly/Monthly/Yearly change→`wow/mom/yoy_change.value`. Values can be **negative**, and the UI
caps display at **"10,000%+"**.

### A10. Other page behaviour

- **End date** picker → `endDate` (defaults to `/latest_available_date/`).
- **Export** button → CSV download of the current table.
- **"Reset filters"** clears all filter params.
- Each keyword row links to **`/detail/?terms=<kw>`** → the keyword dashboard (Doc #4).
- Region dropdown → `country`.
- ⚠️ **Rate limiting is real:** `/metrics/` returned **429** during heavy probing. Throttle and
  handle 429 with backoff.

---

## PART B — For the MARKETER agent

### What this page is for

**Finding keywords you didn't know to look for.** The `/detail/` dashboard (Doc #4) tells you
about a keyword you already have; this page *generates the list*.

### The four tabs — pick by intent

- **Growing trends** — fastest-rising over the last 90 days. **Get in early.** These are
  emergent, often weird-looking terms that haven't peaked.
- **Seasonal trends** — terms peaking on a seasonal cycle. Best paired with the Moments filter
  for campaign planning.
- **Top monthly trends** — biggest search volume in the last 30 days. Head terms, high
  competition, safe reach.
- **Top yearly trends** — biggest over 365 days. The evergreen backbone of a category.

A term appearing in **Growing** but not **Top monthly** is an opportunity; the reverse is a
mature term.

### The filters

**Interest** (24), **Moments** (13 seasonal events), **Age** (7 bands), **Gender** (3),
**Include keyword** (substring), **End date**, **Region**. Then **Export** for CSV.

**Include keyword** narrows within the trending set — typing `nails` gives every trending
nails-related term. It will return nothing for a term that isn't trending ("drill" → *"Filters
are too narrow"*), which is itself a useful signal.

### Reading the table

Keyword · Search volume (relative bar) · Weekly / Monthly / Yearly change. Changes can be
negative, and huge values display as **"10,000%+"**. A term up weekly *and* monthly but down
yearly is spiking now off a low base; up yearly but down weekly is fading from a high base.

### Workflow

1. Pick the tab that matches your intent (early → Growing; volume → Top monthly/yearly).
2. Filter by Interest, and by Moment if planning seasonally.
3. Set Age/Gender to your target — the keyword list itself changes with them.
4. Optionally type a seed term in **Include keyword**.
5. **Export** the CSV.
6. Click any keyword → `/detail/` for its curve, forecast, demographics and Popular Pins (Doc #4).
