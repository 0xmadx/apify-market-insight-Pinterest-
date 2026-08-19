# Manual captures — the two things automation cannot reach

Both remaining unknowns need a human at a browser. Everything else in this repo
was measured through the vault; these two were not, and this directory holds the
tooling and the record of what has been tried.

---

## 1. Moment demographics — the `/_/graphql/` POST body (doc #7 §3.18)

**Status: one step from done.** A browser session on 2026-08-19 confirmed the
part that was previously ambiguous:

> Navigating cold to `/moments/halloween/?country=US` with network capture armed
> beforehand produced **exactly one** POST to `https://trends.pinterest.com/_/graphql/`,
> status 200. Not "one or more" — one. So on the moment page there is no
> disambiguation problem: that single POST carries the moment payload, age/gender
> included.

### The corrected diagnosis

An earlier write-up said the body could not be captured because a hook must be
installed "before the page's own JavaScript runs". That is not quite the
mechanism, and the difference is what unblocks it:

- The request fires **once, during hydration**, and everything afterwards is
  served from **Apollo's in-memory JS cache**.
- So DevTools' **"Disable cache" does nothing** — the cache hiding the request
  is not the HTTP cache.
- A hard reload *does* re-fire it, but a reload also **wipes any console patch**.
  That is the catch-22 the five earlier attempts hit, not a timing mistake.

### The way through: re-fetch client-side, never reload

1. Load `https://trends.pinterest.com/moments/halloween/?country=US` normally
   and let it settle.
2. Open the console and paste [`graphql_interceptor.js`](graphql_interceptor.js).
   It patches `fetch` and `XMLHttpRequest`, records body + response, and strips
   the `Cookie` header itself.
3. **Do not reload.** Force the SPA router to re-fire the same persisted query:
   switch the country dropdown to Canada, or click through to another moment and
   back.
4. Run `__findDemo()` — it reports which capture's response actually contains
   age/gender buckets — or `__dump()` for everything.

`operationName` and `doc_id` will match the US cold-load request; only
`variables.country` differs, and that is trivially flipped back.

### A tool limitation worth recording

The `claude-in-chrome` network tool reports **url / method / status only — never
request payloads**. "Copy as cURL" is therefore not available through it, which
is why the in-page interceptor is the only route to the body. (Installing that
interceptor is also what disconnected the MCP server in the 2026-08-19 session;
if driving it through an agent, install it in smaller pieces.)

### What it unlocks

`src/moments.py` already accommodates the answer: the audience block is labelled
`derived` with `_meta.audience_basis`. Landing this capture flips that to
`measured` — **no schema change**, and scenario D4 flips with it.

---

## 2. Pin merchant + price — the read endpoint (doc #7 §3.19)

**Status: data confirmed on the page, endpoint not identified.**

Manual inspection of pin `4607745477126792832` (the #1 top product from our 3.10
probe) found merchant `Oriental Trading` / `www.orientaltrading.com`, title
`97" Halloween Manor Archway Halloween Prop`, price `$380.99`, rating 2.0,
free-shipping note, and a "Visit site" CTA.

**Ruled out:** `/v3/offsite/` takes the merchant `url` as an **input** param and
fires on interaction. It is a click-tracking hop, not the lookup.

### Procedure

1. DevTools → Network open **before** hard-reloading
   `https://www.pinterest.com/pin/4607745477126792832/`.
2. Filter by `resource/` — Pinterest wraps most reads in
   `/resource/<Name>Resource/get/`.
3. Search response bodies (Ctrl+F inside the Network panel) for `380.99`.
4. Record: full request URL, method, headers (minus `Cookie`), and the JSON path
   where merchant / price / outbound url live.

If nothing under `resource/` matches, clear the filter and search all responses
for `380.99`.

⚠️ `www.pinterest.com` is a different, more defended host than
`trends.pinterest.com`, with its own PWS handler (`www/index.js`) and a
cross-origin boundary. Treat it as a separate client, and replay any captured
request through a leased session before building on it.

### What it unlocks

`parse_top_products` currently emits `price: None` and `merchant_url: None`
explicitly — "not fetched", never zero. Landing this fills them, and the
shopping records gain real, linkable product pages per trending category.
