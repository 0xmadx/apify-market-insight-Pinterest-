# Pinterest Trends — keywords, shopping and seasonal moments

Pinterest publishes its Trends data through a filtered dashboard. This actor
asks the same backend the same questions — unfiltered, joined together, and in
a shape that goes straight into a spreadsheet or a database.

**Press Run with nothing filled in** and you get what is trending now.

## The five questions it answers

| Operation | The question it answers | Requests | Records |
|---|---|---|---|
| `radar` | What is Pinterest itself featuring right now? | 2 | 11 |
| `keywords` | What are people searching for, and is it still growing? | ~14 | 10 |
| `moments` | When does this season actually start? | ~16 | 13 |
| `shopping` | What products are people clicking through to buy? | ~38 | 57 |
| `crawl` | Follow Pinterest's links the way you would click them | ~17 | 74 |

The first four are complete answers on their own — none needs the others.

## What you actually get

A real record, not an illustration:

```json
{
  "category_name": "Mascaras",
  "category_path": ["Beauty", "Makeup", "Eye makeup", "Mascaras"],
  "growth": { "mom_change": 0.25, "wow_change": null, "yoy_change": null },
  "age_distribution":    { "18-24": 0.28, "25-34": 0.29, "35-44": 0.14, "65+": 0.09 },
  "gender_distribution": { "female": 0.88, "male": 0.04, "unspecified": 0.08 },
  "search_queries": ["eyelashes", "eyelash growth", "eye makeup", "…25 total"],
  "top_products": [
    {
      "title": "APPLE MASCARA SUPER LASH - AVOCADO (BLACK)",
      "merchant_name": "Amazon.com",
      "price": "$14.85", "price_value": 14.85, "currency": "USD",
      "in_stock": true,
      "merchant_url": "https://www.amazon.com/…",
      "pin_url": "https://www.pinterest.com/pin/4597682773444952192/"
    }
  ]
}
```

Up to **50 products per category**, each with its pin, merchant and image. Set
`enrichTopN` and the top N also carry **price, stock and the outbound merchant
link** — the chain from category to pin to the merchant's own page.

## Output format

Every record above is one item in the run's dataset — no parsing required.
Apify converts that dataset for you on download, so pick whatever your tools
already read: **JSON**, **CSV**, **Excel (XLSX)**, **XML**, or **RSS**, from
the Console's Export button or the API (`?format=xml`, `?format=csv`, …).

## Quick start

```json
{ "operation": "radar", "region": "US" }
```

Then try:

```json
{ "operation": "shopping", "verticals": ["1042"], "drillTopN": 3, "enrichTopN": 5 }
```

```json
{ "operation": "crawl", "crawlFrom": "shopping", "crawlDepth": 1 }
```

`crawl` is the one that is different. The others answer a question and stop;
`crawl` walks the links between them — a category's search-query chips into
full keyword records, 74 nodes for about 17 requests, because it batches each
level instead of walking node by node. Every record says how it was reached in
`_meta.crawl_path`.

## Using the API

Press Run once in the Console and Apify generates ready-to-paste code for
this exact call (Python, JS, curl) under the **API** tab — that's the
fastest way to integrate. The shape of it:

```bash
curl "https://api.apify.com/v2/acts/0xdevers~pinterest-vault-scraper/run-sync-get-dataset-items?token=YOUR_API_TOKEN" \
  -X POST -H "Content-Type: application/json" \
  -d '{ "operation": "radar", "region": "US" }'
```

Get `YOUR_API_TOKEN` from Console → Settings → Integrations. This returns the
finished dataset directly. A `crawl` run, or a heavier `shopping`/`keywords`
run, can take longer than `run-sync`'s timeout — for those, POST to
`/v2/acts/.../runs` instead and poll the run, or set `maxRecords` to keep one
call inside the sync window.

## Reading the numbers honestly

This is where most Pinterest data goes wrong, so the actor is explicit about it:

- **There are no absolute search volumes. Anywhere.** Pinterest does not expose
  them. Every count is normalised against the peak **inside its own response**,
  so a `count` of 100 in one record and 100 in another are not the same
  quantity. Compare with the percentage fields (`mom_change`, `wow_change`,
  `yoy_change`), never raw counts across records.
- **`null` is not zero.** A missing number means Pinterest did not return one.
  The actor never substitutes a `0`, because a fabricated zero is worse than an
  honest gap.
- **The data lags about 4 days.** Pinterest's own dashboard does too.
- **Every record carries `_meta`** saying which date was used, what the numbers
  are relative to, and where a comparison would be invalid.

## Regions and coverage

32 regions. Two features are narrower, and the actor tells you which case you
hit rather than returning a bare empty list:

- `top_products` and editorial content: **US, CA, GB+IE** only
- Moments: not available in every region — **JP and IN have none**

## Cost

Requests are the currency, and batching keeps them low — 50 categories or 20
keywords is usually **one** request. Three things have no batch form and are
billed per item: `enrichTopN` (one request per product), `includeRelated` (one
per keyword), and each drilled category's product list.

## Limits worth knowing

- History reaches about **365 days** and no further.
- `maxRecords` caps the answer, and the run **tells you when it cut one short**
  rather than letting a short list read as "there is nothing".
- Repeat runs return only what changed. Set `fullRescan: true` to get everything
  again.

## Support

Every input is documented in the input schema, including its valid values and
what happens at the limits.
