# Pinterest Trends for E-commerce — trending products & prices

The question that decides what to stock: *what is trending, and what is it
already selling for elsewhere?* Pinterest's own dashboard shows the trend and
the merchant name. It never shows the price, and never links to the listing.
This actor does both — pulled from the same backend the dashboard uses,
unfiltered.

**Press Run with nothing filled in** and you get today's trending product
categories — no setup required.

## What it answers

| Operation | The question it answers | Requests | Records |
|---|---|---|---|
| `shopping` | What products are people clicking through to buy — and at what price? | ~38 | 57 |
| `crawl` | Which search terms sit behind a trending category? | ~17 | 74 |

`shopping` is a complete answer on its own; `crawl` follows a category's own
search-query chips into full keyword records, for when you need the demand
data behind a product, not just the product.

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
      "title": "APPLE MASCARA SUPER LASH - AVOCADO (BLACK) (5 PCS)",
      "merchant_name": "Amazon.com",
      "price": "$14.85", "price_value": 14.85, "currency": "USD",
      "in_stock": true, "merchant_domain": "amazon.com",
      "merchant_url": "https://www.amazon.com/APPLE-MASCARA-SUPER-LASH-AVOCADO/dp/B07DMSF28S",
      "pin_url": "https://www.pinterest.com/pin/4597682773444952192/",
      "image_url": "https://i.pinimg.com/1200x/…"
    }
  ]
}
```

Up to **50 products per category**, each with its pin, merchant and image. Set
`enrichTopN` and the top N also carry **price, stock and the outbound merchant
link** — the chain from category to pin to the merchant's own page that
Pinterest's own screen does not show at all.

## Quick start

```json
{ "operation": "shopping", "verticals": ["1042"], "drillTopN": 3, "enrichTopN": 5 }
```

`verticals` is the department (`1042` = Beauty). Leave it empty for the three
Pinterest's own UI shows (Fashion, Home decor, Beauty) — the cheap default —
or pass all seven to see the four Pinterest tracks but never displays.

Once you know a category, find the demand behind it:

```json
{ "operation": "crawl", "crawlFrom": "shopping", "crawlDepth": 1 }
```

That loads trending categories and follows every one's search-query chips into
full keyword records — 74 nodes for about 17 requests, because it batches each
level instead of walking node by node.

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

32 regions. `top_products` (prices, merchant links) is narrower: **US, CA and
GB+IE only.**

## Cost

Requests are the currency, and batching keeps them low — 50 categories is
usually **one** request. `enrichTopN` is the one to watch when scaling up: one
request **per product**, no batch form exists — start small and raise it once
you know which categories are worth the depth.

## Limits worth knowing

- History reaches about **365 days** and no further.
- `maxRecords` caps the answer, and the run **tells you when it cut one short**
  rather than letting a short list read as "there is nothing".
- Repeat runs return only what changed. Set `fullRescan: true` to get everything
  again for a full snapshot.

## Support

Every input is documented in the input schema, including its valid values and
what happens at the limits.
