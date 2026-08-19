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

### A6. `l1interests` — all 24 interests ✅ FULLY MAPPED

⚠️ This page offers **24** interests; the homepage spotlight filter offers **16**. The IDs are
the **same taxonomy** — this page simply exposes more of it.

Captured with all 24 selected (param order = alphabetical chip order). **8 of these
independently match the homepage list, which validates the ordering.**

| # | Interest | ID | Also on homepage? |
|---|----------|-----|-------------------|
| 1 | Animals | `925056443165` | ✅ |
| 2 | Architecture | `918105274631` | ✅ |
| 3 | Art | `961238559656` | ✅ |
| 4 | Beauty | `935541271955` | ✅ |
| 5 | Children's Fashion | `903733943146` | — |
| 6 | Design | `902065567321` | — |
| 7 | DIY and Crafts | `934876475639` | ✅ |
| 8 | Education | `922134410098` | ✅ |
| 9 | Electronics | `960887632144` | — |
| 10 | Entertainment | `953061268473` | — |
| 11 | Event Planning | `941870572865` | ✅ |
| 12 | Finance | `913207199297` | — |
| 13 | Food and Drinks | `918530398158` | ✅ |
| 14 | Gardening | `909983286710` | ✅ |
| 15 | Health | `898620064290` | ✅ |
| 16 | Home Decor | `935249274030` | ✅ |
| 17 | Men's Fashion | `924581335376` | — |
| 18 | Parenting | `920236059316` | ✅ |
| 19 | Quotes | `948192800438` | — |
| 20 | Sport | `919812032692` | — |
| 21 | Travel | `908182459161` | ✅ |
| 22 | Vehicles | `918093243960` | — |
| 23 | Wedding | `903260720461` | ✅ |
| 24 | Women's Fashion | `948967005229` | — |

> ✅ **Earlier discrepancy RESOLVED.** A previous draft flagged `948967005229` as possibly
> "Food and Drinks". It is **Women's Fashion**. Food and Drinks is `918530398158` — identical
> to the homepage. This also explains the editorial endpoint's "The Summer of Jorts" trend
> carrying `948967005229` + `924581335376` = **Women's Fashion + Men's Fashion**. ✔

**The homepage's 16-item list is a subset**, plus the homepage-only pseudo-values `ALL` and
`FASHION` (Doc #1). This page splits Fashion into **Children's / Men's / Women's Fashion** and
adds Design, Electronics, Entertainment, Finance, Quotes, Sport, Vehicles.

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

---
---

# ADDENDUM — Filters verified across ALL 4 presets + the Moments filter

## D1. ✅ Every filter works on every tab

The same filter set applies to **Growing / Seasonal / Top monthly / Top yearly** — the tab is
just `trendsPreset`, and filters compose with all four. Verified live (`numTermsToReturn=8`):

| Filter | preset 1 (monthly) | preset 3 (growing) | preset 4 (seasonal) |
|--------|--------------------|--------------------|---------------------|
| *(none)* | nails, hairstyles, wallpaper | sterling point tv show, see more | first day of school prayer, senior sunrise captions |
| Beauty `935541271955` | hairstyles, back to school nails | end of august nails, cry baby makeup | august pedicure colors, fall transition nails |
| Food & Drinks `918530398158` | banana bread recipe, zucchini bread | kids lunch box meals, snacks for school | back to school breakfast for kids, hatch chili recipes |
| Age 65+ `ageBuckets=9` | **good morning, good morning images, good night** | end of summer nails ideas | end of summer nails |
| Male `gender=0` | **wallpaper, spiderman, pose reference** | rosemary walten pfp, needy | first day of school sign, senior car decorating ideas |

> The demographic filters change results **completely**, not marginally. Age 65+ on the monthly
> tab returns *"good morning / good morning images / good night"* — a totally different
> Pinterest from the unfiltered *"nails / hairstyles / wallpaper"*. Male returns
> *"spiderman / pose reference"*. **Never present unfiltered keyword lists as "what people
> search" — they are dominated by the majority demographic.**

## D2. The `moments` filter

Comma-separated **slugs**. Works on all 4 presets:

| Preset | `moments=halloween` → top terms |
|--------|--------------------------------|
| 1 Top monthly | couples halloween costumes, halloween nails, halloween costumes |
| 2 Top yearly | halloween nails, pumpkin carving ideas, pumpkin |
| 3 Growing | spider man halloween costume, genie halloween costumes, unique… |
| 4 Seasonal | toddler halloween costumes boy, creative halloween makeup |

### The 13 valid slugs — all verified 200 ✅
```
christmas · easter · fathers day · halloween · hanukkah · independence day ·
memorial day · mothers day · new years eve · st patricks day · summer ·
thanksgiving · valentines day
```

### ⚠️ Slug format is strict — no apostrophes

| Value | Result |
|-------|--------|
| `valentines day` | ✅ 200 |
| `valentine's day` | ❌ **400** |
| `fathers day` / `mothers day` / `st patricks day` / `new years eve` | ✅ 200 (no apostrophes) |
| `bogusmoment` | ❌ 400 |

The UI *displays* "Valentine's Day", "Father's Day", "New Year's Eve" **with** apostrophes but
sends them **without**. Lowercase, spaces preserved, apostrophes stripped. An invalid slug is a
hard 400, not an empty result.

### Combining moments + interests = AND

```
moments=halloween & l1interests=935541271955 (Beauty)
→ witchy nails, coraline nails, summerween nails, unicorn cost…
```
Beautifully specific — this is the most targeted query the endpoint supports. Multiple moments
(`moments=halloween,christmas`) are accepted and return a combined ranking.

> Filtering by a moment **shrinks results a lot** (often 5–8 rows even with
> `numTermsToReturn=8`+). Expect small result sets and don't treat that as an error.

## D3. `numTermsToReturn` — max is 100

| Value | Returned |
|-------|----------|
| omitted | **50** (default) |
| 10 | 10 |
| 50 | 50 |
| **100** | **100** ✅ |
| 500 | ❌ **400** |

→ Use `numTermsToReturn=100` to get double what the UI shows. **100 is the hard ceiling.**

## D4. Final parameter reference

| Param | Required | Values |
|-------|----------|--------|
| `country` | yes | region codes (Doc #0 §4) |
| `endDate` | yes | `YYYY-MM-DD` — from `/latest_available_date/` |
| `trendsPreset` | yes | `1` monthly · `2` yearly · `3` growing · `4` seasonal (only 1–4) |
| `numTermsToReturn` | no | 1–**100** (default 50) |
| `l1interests` | no | comma-separated interest IDs (§A6, all 24 mapped) |
| `moments` | no | comma-separated slugs (§D2, 13 valid, no apostrophes) |
| `ageBuckets` | no | `2,3`=18-24 · `4`=25-34 · `5`=35-44 · `6`=45-49 · `7`=50-54 · `8`=55-64 · `9`=65+ |
| `gender` | no | `0`=Male · `1`=Female · `2`=Unspecified |
| `keywordsToInclude` | no | substring filter within the preset's universe |
| `lookbackWindow` | no | ⚠️ **IGNORED — no effect** (§A4) |
| `shouldMock` | no | `false` |

**All filters are AND-combined** and every one works on every preset.

## D5. Marketer note

The filter matrix is the real power here: **4 presets × 24 interests × 13 moments × 7 age bands
× 3 genders**. A query like *Seasonal + Halloween + Beauty + 25-34 + Female* returns exactly the
keywords that audience is searching for that moment — far more actionable than the default list.

Start broad (preset + interest), then add moment, then demographics. And always sanity-check the
demographic cut: the unfiltered list reflects Pinterest's majority audience, not yours.

---
---

# ADDENDUM 2 — The `moments` filter in depth

## E1. UI label → slug (all 13, captured with all selected)

| UI label | slug sent |
|----------|-----------|
| Christmas | `christmas` |
| Easter | `easter` |
| **Father's Day** | `fathers day` |
| Halloween | `halloween` |
| Hanukkah | `hanukkah` |
| Independence Day | `independence day` |
| Memorial Day | `memorial day` |
| **Mother's Day** | `mothers day` |
| **New Year's Eve** | `new years eve` |
| **St Patrick's Day** | `st patricks day` |
| Summer | `summer` |
| Thanksgiving | `thanksgiving` |
| **Valentine's Day** | `valentines day` |

Rule: **lowercase, spaces kept, apostrophes stripped.** Sending the apostrophe form → **400**.
This is the **same 13-moment set** returned by `moment/available/{REGION}` (Doc #2) — consistent
across the product.

## E2. 🚨 THE BIG ONE — result count depends on the moment's SEASON, and the presets split in two

Swept all 13 moments on **preset 4 (Seasonal)**, `numTermsToReturn=100`, `endDate=2026-08-14`
(mid-August):

| Moment | Rows | Its phase (Doc #2) |
|--------|------|--------------------|
| **halloween** | **100** (capped) | Rising |
| **thanksgiving** | **91** | Approaching |
| christmas | 34 | Approaching |
| summer | 34 | Cooling |
| hanukkah | 18 | Approaching |
| st patricks day | 13 | Ended |
| mothers day | 9 | Ended |
| new years eve | 8 | Approaching |
| memorial day | 4 | Ended |
| valentines day | 4 | Off-season |
| fathers day | 1 | Ended |
| independence day | 1 | Ended |
| **easter** | **0** | Ended |

**The row count tracks how seasonally live the moment is.** Halloween (Rising in August) caps
out at 100; Easter (long past) returns **nothing**.

### The presets behave completely differently here

Same moment, all four presets:

| Moment | `1` monthly | `2` yearly | `3` growing | `4` seasonal |
|--------|-------------|------------|-------------|--------------|
| halloween | 100 | 100 | 100 | 100 |
| christmas | 98 | 99 | **18** | **34** |
| **easter** | **100** | **100** | **0** | **0** |

→ **Volume presets (`1`, `2`) always return data**, for any moment, any time of year — they
read 30/365-day historical volume, which exists year-round.
→ **Growth/Seasonal presets (`3`, `4`) only return data when the moment is live relative to
`endDate`.** Easter in August is empty on both.

## E3. ✅ The fix: `endDate` is the time machine

Easter, preset 4 (Seasonal), just changing the date:

| `endDate` | Rows | Top terms |
|-----------|------|-----------|
| `2026-08-14` (August) | **0** | — |
| `2026-03-20` (Easter season) | **100** | spring nails, april nails, happy spring images |

**To research an out-of-season moment, move `endDate` into that moment's season.** This is how
you plan a campaign months ahead:

```
# Planning Easter 2027 while it's August 2026:
GET /top_trends_filtered/?country=US&endDate=2026-03-20&trendsPreset=4
    &moments=easter&numTermsToReturn=100
→ 100 rows of real Easter keywords
```

> This partly confirms the "just change the date" intuition — but precisely: the **tab** is
> `trendsPreset` (dates don't switch monthly↔yearly), while **`endDate` controls which season
> you're looking at**, and it's mandatory for growth/seasonal views of out-of-season moments.

## E4. ⚠️ The moment filter is SEASONALLY FUZZY, not literal

It returns keywords from the moment's **time window**, not keywords containing the moment name:

| `moments=` | Actual top terms |
|------------|------------------|
| `fathers day` | **grandparents day crafts** |
| `independence day` | **labor day nails** |
| `memorial day` | labor day outfits, labor day nails, weekend dinner ideas |
| `mothers day` | **first day of school quotes**, first day of kindergarten ideas |
| `hanukkah` | december nails, gingerbread man, december aesthetic |
| `st patricks day` | dr seuss, green cat eye nails |
| `valentines day` | tiffany valentine, jennifer tilly 90s, heart art |

Treat `moments` as **"keywords trending around this time of year"**, not "keywords about this
holiday". `hanukkah` → generic December terms; `st patricks day` → green/Dr-Seuss (March).
Pair it with `keywordsToInclude` if you need literal matches.

## E5. Recipe summary

| Goal | Query |
|------|-------|
| What's hot for Halloween right now | `trendsPreset=4&moments=halloween` + current `endDate` |
| Rising Halloween terms (get in early) | `trendsPreset=3&moments=halloween` |
| Evergreen Halloween terms | `trendsPreset=2&moments=halloween` |
| Plan Easter from August | `trendsPreset=4&moments=easter&endDate=2026-03-20` |
| Halloween × Beauty × 25-34 Female | `trendsPreset=4&moments=halloween&l1interests=935541271955&ageBuckets=4&gender=1` |

**Marketer takeaway:** an empty result for a moment is **not** an error and **not** "no demand"
— it means that moment isn't in season for your `endDate`. Move the date to its season, or
switch to a volume preset (`1`/`2`) to see its all-year baseline.
