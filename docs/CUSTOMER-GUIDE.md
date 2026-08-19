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

Each is a complete answer on its own. None requires the others.

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

**Two things about dates.** History reaches **about 365 days** and no further —
past that Pinterest answers with an empty list rather than an error, so we
refuse the request and tell you why instead of handing you a silent nothing.
And Pinterest **snaps your date to its own week boundary** — ask for
`2026-02-15` and the data is for `2026-02-13`. Records carry both:
`_meta.end_date` is what Pinterest used, `_meta.end_date_requested` is what you
asked for.

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

**Empty result for a region** — several features are narrower than the rest:
`top_products` and editorial serve **US / CA / GB+IE** only, and **JP and IN
have no seasonal moments at all**. Those are refused up front with the reason
rather than returning a confusing empty run.

**Everything looks identical to last run** — that is the point. Records are only
emitted when they are new or their numbers moved. Set `"fullRescan": true` to
re-emit everything.
