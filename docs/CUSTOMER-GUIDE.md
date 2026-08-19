# Pinterest Trends — how to use it

Pinterest publishes a Trends site. It shows you *some* of what it knows, through
a filtered interface. This actor asks the same backend the same questions —
without the filter, joined together, and in a form you can put in a spreadsheet.

**You do not need to know anything to start.** Press Run with nothing filled in
and you get what is trending right now.

---

## Try it before you write any code

```bash
.venv/Scripts/python.exe api_sim.py     # → http://localhost:8080
```

A playground opens: pick an operation, set fields, press Run, read the records.
It shows the exact `curl` for whatever you configured, so the moment it looks
right you copy that line into your own code.

It runs the same code the real actor runs. When a Pinterest session is available
it says **LIVE** and fetches fresh data; otherwise it says **DEMO** and replays
captured responses — real shapes and field names, older numbers, and every
record marked `"_demo": true` so you can never mistake one for the other.

---

## The four questions it answers

| Operation | The question | Cost | Records |
|---|---|---|---|
| **`radar`** | What is Pinterest itself featuring right now? | 2 requests | 11 |
| **`keywords`** | What are people searching for, and is it still growing? | ~14 | 10 |
| **`moments`** | When does this season actually start? | ~16 | 13 |
| **`shopping`** | What products are people clicking through to buy? | ~38 | 57 |
| **`crawl`** | Follow the links, the way you would click them | ~17 | 74 |

The first four are complete answers on their own. None requires the others.

**`crawl` is the fifth, and it is different.** The others answer one question
and stop. Pinterest's own site does not: every screen links to the next — a
moment to its keywords, a category's "Search queries" chips to the keyword
page, a keyword to its related terms. `crawl` walks those links for you:

```json
{ "operation": "crawl", "crawlFrom": "shopping", "crawlDepth": 1 }
```

That loads the trending-categories page and then follows every category's
search-query chips into full keyword records — 74 nodes for about 17 requests,
because it batches each level instead of walking node by node. Every record
says how it was reached (`_meta.crawl_path`), and the last record is a summary
telling you whether you got everything or ran out of budget.

---

## The call

One endpoint. Send your options, get your records.

```bash
curl -X POST https://api.apify.com/v2/acts/<actor-id>/run-sync-get-dataset-items \
  -H 'content-type: application/json' \
  -d '{"operation": "shopping", "region": "US"}'
```

Locally, the same call with a different host:

```bash
curl -X POST localhost:8080/v2/acts/pinterest-trends/run-sync-get-dataset-items \
  -H 'content-type: application/json' \
  -d '{"operation": "shopping", "region": "US"}'
```

```python
import requests

records = requests.post(
    "https://api.apify.com/v2/acts/<actor-id>/run-sync-get-dataset-items",
    params={"token": "<your-apify-token>"},
    json={"operation": "keywords", "mode": "exact",
          "queries": ["boho wall art", "macrame wall hanging"]},
).json()

for r in records:
    print(r["term"], r["has_forecast"], r["wow_change"])
```

---

## Walkthroughs

### "I sell home decor. What should I make next?"

```json
{ "operation": "shopping", "verticals": ["1250"], "drillTopN": 3, "enrichTopN": 5 }
```

Each record is one trending category, and carries:

- its **real name and breadcrumb** — `Seasonal & holiday decorations`,
  `Home decor > Seasonal & holiday decorations`. Pinterest's API returns only
  the id `1408`; the name is joined in for you.
- **who is buying** — `65+` at 32%, female at 82%, stamped with the action it
  was measured under (see *Audience shifts by action* below)
- **the 17 search phrases** people use to reach it
- **what they actually buy**, with prices and links straight to the merchant:

```json
"top_products": [
  { "merchant_name": "Amazon.com", "price": "$14.85",
    "merchant_url": "https://www.amazon.com/APPLE-MASCARA-...",
    "pin_url": "https://www.pinterest.com/pin/4607745477126792832/" }
]
```

Pinterest's own Trends interface shows the product and the merchant name. It
never shows the price and never links out. That chain — category → pin →
merchant page — exists on no Pinterest screen.

### "Is this keyword still growing, or did I miss it?"

```json
{ "operation": "keywords", "mode": "exact",
  "queries": ["boho wall art", "macrame wall hanging", "sunset print"] }
```

```json
{ "term": "boho wall art",
  "has_forecast": true,
  "wow_change": -0.03, "mom_change": 0.10, "yoy_change": null,
  "seasonality_score": 0.33,
  "age_distribution": { "18-24": 0.49, "25-34": 0.32, "…": "…" },
  "related": [ { "term": "…", "has_forecast": false } ],
  "series": [ { "date": "2025-08-15", "count": 55 } ] }
```

`has_forecast: true` means Pinterest will project this term forward 91 days.
`false` is a **final answer**, not a retry — the record says so in
`forecast_note`, and tells you to rank on growth and seasonality instead.

Type your terms however you like. Casing is fixed for you — uppercase silently
returns nothing on this API, which is the kind of trap that costs an afternoon.

### "When do I launch the Christmas range?"

```json
{ "operation": "moments", "aggregation": "daily" }
```

Every seasonal moment with its **phase** (`rising`, `approaching`, `cooldown`,
`off_season`, `ended`), its **takeoff and peak dates**, and — for the live ones
— a demand curve at **daily** resolution. Nothing else in this API resolves
below weekly.

```json
{ "slug": "thanksgiving", "phase": "approaching",
  "next_peak": { "takeoff_at": 1761868800000, "peak_at": 1764288000000,
                 "peak_length_days": 28 },
  "audience": { "age_distribution": {…}, "gender_distribution": {…} },
  "_meta": { "audience_basis": "measured" } }
```

Add `"interestIds": ["918530398158"]` and you get that moment's audience broken
down by interest — including combinations Pinterest's own dropdown does not
offer. Halloween × Food and Drinks is not in their menu and returns a full,
valid, very different audience (18-24 at 0.19 against 0.43 unfiltered).

### "What was trending last October?" — asking about the past

Every operation takes `endDate`. Leave it out for the newest data; set it to
look back:

```json
{ "operation": "keywords", "mode": "discover", "endDate": "2025-10-15" }
```

Measured, same query, different dates:

| `endDate` | what was growing |
|---|---|
| *(omitted — now)* | sterling point tv show · end of august nails |
| `2025-12-01` | christmas nails · thanksgiving outfit · thanksgiving recipes |
| `2025-10-15` | fall nails · halloween nails · fall outfits |

That is how you plan a season: look at what took off this time last year, and
you have your calendar.

**Three things about dates.**

**How far back you can go depends on the operation** — and they are very
different. Measured, not assumed:

| Operation | Reaches back |
|---|---|
| `moments` | ~730 days |
| `keywords` | ~365 days |
| `shopping` | **~257 days** |

Past its limit an endpoint does not fail — it answers *successfully with
nothing*, which reads as "nothing was trending that week". That is worse than
an error, so out-of-range dates are refused up front with the reason.

**Pinterest snaps your date to its own week boundary** — ask for `2026-02-15`
and the data is for `2026-02-13`.

**`endDate` and `dateRange` are different controls.** Pinterest's interface has
both, and they answer different questions:

| Control | Question |
|---|---|
| `endDate` | **Which date** am I looking at? |
| `dateRange` | **How much history** does the chart show — 3 months, 6 months, 1 year, 2 years? |

They combine: `{"endDate": "2025-10-15", "dateRange": "past_1_year"}` is "the
year of history ending last October". `dateRange` is one input for all three
operations, because Pinterest shows that same dropdown on the keyword page, the
product-category page and the moment view.

Their limits are independent, which surprises people. Shopping's `endDate` only
reaches ~257 days back, but its **chart** still covers a full 2 years from
whichever end date you pick. A short end-date window is not a short chart.

**Asking about the past turns the forecast off.** On `keywords` and `shopping`,
Pinterest refuses to project forward from a historical date — reasonably, since
that period has already happened. So those runs return the real history with no
forecast, and `_meta.forecast_suppressed` says exactly that. We spell it out
because a blank forecast would otherwise read as "Pinterest has no forecast for
this term", which is a different and wrong claim. `moments` is unaffected.

**Every record carries both dates, plus which one you are looking at.**
`_meta.end_date_requested` is always your ask. `_meta.end_date_basis` tells you
what `_meta.end_date` actually is: `echoed` means Pinterest returned the date it
used, so you can see the snap (only `keywords` does this); `requested` means the
endpoint returns no date at all, so `end_date` is your own ask passed through
and any snapping is invisible. We would rather say that than let you read a
number as confirmed when it is not.

### "Give me everything around this, not one report" — the crawl

The other four operations answer one question and stop. Pinterest's own site
does not: a moment links to its keywords, a category's *Search queries* chips
link to the keyword page, a keyword links to its related terms. `crawl` walks
those links for you.

```json
{ "operation": "crawl", "crawlFrom": "shopping", "crawlDepth": 1 }
```

That loads the trending-categories page, then follows **every category's
search-query chips** into full keyword records — about 74 nodes for 17
requests. Start somewhere else by changing one field:

| `crawlFrom` | Starts at | Then follows |
|---|---|---|
| `overview` | spotlight + editorial + every moment | their keywords |
| `shopping` | trending product categories | each category's search-query chips |
| `search` | trending search keywords | each keyword's related terms |
| `moments` | every seasonal moment | each moment's keywords |

**The entry page keeps its own filters.** Crawling from `shopping` still takes
`verticals`, `drillTopN`, `enrichTopN` and `event`:

```json
{ "operation": "crawl", "crawlFrom": "shopping",
  "verticals": ["1042"], "crawlDepth": 1 }
```

is "Beauty only, then follow its search-query chips" — 14 nodes, 7 requests.

**Why it is not slow.** It walks a whole level at once rather than node by
node, and the keyword endpoints accept batches — so cost grows with `crawlDepth`,
not with how many things it finds. Node-by-node would be roughly 975 requests
for the same result.

**Every record says how you got there.** `_meta.crawl_path` reads
`shopping > keyword`, so a dataset mixing categories, moments and keywords
still explains itself.

**Read the last record.** Every crawl ends with a summary:

```json
{ "crawl_summary": true, "nodes_total": 74,
  "nodes_by_kind": { "category": 19, "keyword": 50, "moment": 13 },
  "requests_spent": 17, "request_budget": 60, "entry_cost": 13,
  "truncated": false, "edges_unfollowed": 0 }
```

`truncated: true` means **your dataset is partial** — the budget stopped it.
Raise `maxRequests` or lower `crawlDepth`. Records stream as they are found, so
a record written early cannot know the crawl was cut short later; this last one
is where that fact lives. Check it before you trust a crawl to be complete.

One thing to know about `maxRequests`: it caps what the crawl **follows**. The
entry page always loads in full, because half a page is a wrong answer rather
than a cheap one. `entry_cost` in the summary tells you that floor.

### "Just tell me what's hot" — zero input

```json
{ "operation": "radar" }
```

Two requests. Pinterest's own curated picks, plus its editorial features with
the date each campaign started.

---

## Reading the numbers honestly

Four things about this data that will mislead you if nobody says them out loud.

**There are no absolute volumes. Anywhere.** Pinterest does not publish them, so
neither do we. Every count is *relative to the response that carried it*. Each
record names its scope in `_meta.normalization_scope`; two records with
different scopes are **not comparable**. This is why you will never see a
"search volume" figure here — inventing one would be the easiest way to make
this product feel better and be wrong.

**Absent is never zero.** A field Pinterest did not return comes back `null`,
not `0`. A category with `"wow_change": null` is *unmeasured*, not flat. A
product with `"price": null` was *not fetched* (turn up `enrichTopN`), not free.

**The data is about 4 days behind.** Not a delay on our side — that is when
Pinterest settles it. Every record carries `_meta.end_date` with the real date,
so you compare against that rather than today.

**Audience shifts by action, a lot.** The same category has a materially
different audience depending on whether you measure clicks or saves:

| | 25-34 | 65+ |
|---|---|---|
| `OUTBOUND_CLICK` | 13% | **32%** |
| `SAVE` | **23%** | 19% |

Younger people *save* (planning); older people *click through* (buying). So
"the audience for this category" is not a well-formed question — every
demographic record states the `event` it was measured under, and you pick the
one matching your intent.

---

## What it costs you

Requests are the currency. Batching keeps them low: asking about 50 categories
or 20 keywords is usually **one** request, not 50.

The exceptions have no batch form and are billed per item:

- `enrichTopN` — **1 request per product** for price and merchant link
- `includeRelated` — **1 request per keyword** for its 5 siblings
- each drilled category's product list

Start small on those two. `drillTopN: 1, enrichTopN: 2` answers most questions
for about six requests.

---

## When something looks wrong

**"No usable Pinterest session"** — the actor deliberately fails instead of
returning an empty dataset, because empty would read as *"Pinterest has
nothing"*, which would be a lie. Nothing is wrong with your input.

**Fewer keywords than you asked for** — Pinterest silently drops terms it has no
data for. Measured: 10 requested, 4 returned, and `halloween` was among the
dropped. Absence is not a verdict on the term.

**Empty result for a region** — several features are narrower than the rest.
`top_products` and editorial serve **US / CA / GB+IE** only. Seasonal moments
exist in **17 of the 32 regions**; the other 15 (including JP, IN, KR, TR, TH,
PH, MY, ID, SA, EG and several groupings) have none at all. Counts differ where
they do exist — US and AU+NZ have 13, France 11, Mexico 8 — because seasonal
calendars differ by market. All of these are refused up front with the reason
rather than returning a confusing empty run.

**Everything looks identical to last run** — that is the point. Records are only
emitted when they are new or their numbers moved. Set `"fullRescan": true` to
re-emit everything.
