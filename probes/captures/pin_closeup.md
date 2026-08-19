# probes/captures — merchant / price / outbound URL

Two questions. Q1 is a clean negative with a mechanism; Q2 is the answer.

---

## Q1 — the shopping surface does not have the outbound URL. At all.

`https://trends.pinterest.com/shopping/1408/?country=US` -> "Explore top products"
-> panel with 33 rows, each with an external-link icon.

```js
[...document.querySelectorAll('a[href]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h && !h.startsWith('/') && !h.includes('pinterest.com'))
```

**Returns `[]`.** 210 anchors on the page, 165 of them `/pin/` links, **zero external.**

The external-link icon is not a merchant link — it's an `<a>` to the Pinterest closeup:

```
<a href="https://www.pinterest.com/pin/4607745477126792832/" target="_blank" rel="noopener noreferrer">
```

### It isn't hiding in embedded JSON either

Worth being precise, since the brief assumed the URL was in the page somewhere. It
isn't — and the reason is upstream of the DOM. The 33 rows come from a call that fires
**on page load** (so "the panel fires none" is right — the panel only renders data
already fetched):

```
GET https://trends.pinterest.com/resource/ApiResource/get/
  ?source_url=/shopping/1408/?country=US
  &data={"options":{"url":"/ads/v4/trends/shopping/product_categories/top_products/1483066549606/1408",
                    "data":{"region":"US","event":"OUTBOUND_CLICK","limit":50}},"context":{}}
  &_=<epoch ms>
```

Item shape in `resource_response.data.top_products[]`:

```
{ pin_id, merchant_name, title, images{75x75,236x,345x,474x,564x,736x,1200x} }
```

Four fields. **No `link`, no price, no merchant domain.** `merchant_name` ("Amazon.com",
"Oriental Trading") is the only merchant signal, and it's a display string, not a URL.

So there is nothing to grep for — the outbound URL never reaches this surface in any
form. Getting it requires resolving each `pin_id` individually. Which is Q2.

*(Caveat: that item shape was read off a sibling category's response — 14 items, not
1408's 33 — because the response had to be captured passively via a client-side category
switch. Same endpoint, same field set; the count differs by category.)*

---

## Q2 — `PinResource/get/`

### Request

```
GET https://www.pinterest.com/resource/PinResource/get/
  ?source_url=/pin/<pinId>/
  &data={"options":{"id":"<pinId>",
                    "field_set_key":"auth_web_main_pin",
                    "add_fields":"pin.gen_ai_topics,pin.is_eligible_for_image_download,pin.is_eligible_for_ai_comparison,pin.has_hidden_inner_pin",
                    "client_tracking_params":"<opaque>",
                    "noCache":true,
                    "fetch_visual_search_objects":false,
                    "get_page_metadata":false},"context":{}}
  &_=<epoch ms>
```

**Method: GET** (XHR, not fetch — the XHR half of the interceptor is what caught it).
Everything is in the query string; there is no request body.

`field_set_key: "auth_web_main_pin"` is the load-bearing option — it's what pulls in the
`rich_summary` / `rich_metadata` subtrees that carry commerce data.

### Header names

```
X-Requested-With, Accept, X-APP-VERSION, X-Pinterest-AppState, X-Pinterest-Source-Url,
X-Pinterest-PWS-Handler, screen-dpr, X-B3-TraceId, X-B3-SpanId, X-B3-ParentSpanId,
X-B3-Flags, X-Pinterest-Platform-BID
```

**`X-Pinterest-PWS-Handler: www/pin/[id].js`** — confirmed page-specific, as suspected.
The moment page sent `trends/moments/[momentId].js`. It tracks the route, so don't
hard-code either value.

### JSON paths

Root is `resource_response.data`:

| field | path |
|---|---|
| merchant name | `.closeup_attribution.full_name` -> `"Oriental Trading"` |
| | also `.closeup_attribution.first_name`, `.closeup_unified_attribution.*`, `.rich_summary.site_name` |
| merchant handle | `.closeup_attribution.username` -> `"orientaltrading"` |
| merchant domain | `.link_domain.id` -> `"www.orientaltrading.com"` |
| | also `.closeup_attribution.domain_url`, `.closeup_attribution.website_url` (`http://`, no `www` normalisation) |
| **outbound URL** | `.link` -> full merchant URL incl. `cm_mmc` / `utm_*` / `tw_*` tracking params |
| product name | `.rich_summary.display_name` -> `"97\" Halloween Manor Archway Halloween Prop"` |
| **price** | `.rich_summary.products[0].offer_summary.price` -> `"$380.99"` |
| price (numeric) | `.rich_summary.products[0].offer_summary.price_val` -> `380.99` |
| | also `.rich_summary.products[0].offers[0].price_value` |
| currency | `.rich_summary.products[0].offer_summary.currency` -> `"USD"` |
| stock | `.rich_summary.products[0].offer_summary.in_stock` / `.availability` (`1`) |
| **free shipping** | `.rich_summary.products[0].shipping_info.free_shipping_price` -> `"$25"` |
| | numeric: `.shipping_info.free_shipping_value` -> `25` |

`rich_metadata` mirrors `rich_summary` field-for-field in this response. Pick one and be
consistent; don't assume both are always populated.

`rich_summary` keys, for reference:
`url, display_name, type, favicon_link, apple_touch_icon_images, display_description, id,
products, actions, site_name, type_name, apple_touch_icon_link, favicon_images,
aggregate_rating`

### Capture caveat

The dumped body is from pin **200410252170463021**, not 4607745477126792832 — the
client-side trigger was clicking a related pin, and that pin turned out to be a duplicate
listing of the same Oriental Trading product (same `.link`, same $380.99). Convenient,
but it means the paths above are verified on a sibling pin. The original pin's
`PinResource/get/` was observed on cold load with the identical `field_set_key`, so the
shape holds; re-verify against 4607745477126792832 if anything downstream depends on it.

### `/v3/offsite/` — confirmed not the lookup, for a second reason

Ruled out in the brief because it takes the merchant URL as an input. Worth adding that
it also fires **on page load**, not only on click — 4 times, with `check_only: true`. So
its presence in a cold-load trace is not evidence it produced anything. It consumes
`.link`; `PinResource` is where `.link` comes from.

---

## The pipeline this implies

Two steps, no way around it:

1. `top_products` -> `pin_id[]` (+ merchant_name, title, images)
2. `PinResource/get/` per pin_id -> `.link`, price, shipping, canonical merchant name

That's 1 + N requests for an N-row strip. `noCache: true` is in the observed options, so
whether these are cacheable per pin is worth checking before running it across 33 rows,
let alone a full category sweep.
