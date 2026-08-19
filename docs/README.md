# Pinterest Trends API specs — what is canonical here

34 markdown files were dropped into this project as `CLAUDE EXTETION/`, six of
them copies of one document under Chrome's `(1)`, `(2)`, … download naming. This
directory holds the **one canonical version of each**, renamed to match the names
`00-INDEX-and-coverage.md` uses internally so its cross-references resolve.

Nothing was deleted. The 23 superseded drafts are in [`_superseded/`](_superseded/).

## The canonical set

| File | Covers |
|---|---|
| [00-INDEX-and-coverage.md](00-INDEX-and-coverage.md) | Start here — 18 endpoints, which doc covers what |
| [00-COVERAGE-AUDIT.md](00-COVERAGE-AUDIT.md) | Separate audit doc: coverage gaps, endpoint-by-endpoint status |
| [01-spotlight-featured-topics.md](01-spotlight-featured-topics.md) | "Trends in the spotlight" (homepage) |
| [02-moments.md](02-moments.md) | Moments: list, detail, keywords |
| [03-shopping-trends.md](03-shopping-trends.md) | Shopping trends + category detail pages |
| [04-search-trends-dashboard.md](04-search-trends-dashboard.md) | `/detail/` keyword dashboard |
| [04b-keyword-dashboard-detail.md](04b-keyword-dashboard-detail.md) | A **second, independent** write-up of the same page — see below |
| [05-shopping-category-taxonomy.md](05-shopping-category-taxonomy.md) | 383-category tree, 4 levels |
| [06-search-keywords-discovery.md](06-search-keywords-discovery.md) | `/search` keyword discovery, 4 presets |
| [07-API-REFERENCE.md](07-API-REFERENCE.md) | ⭐ endpoint-first reference: every param, every limit |
| [08-BUILD-GUIDE.md](08-BUILD-GUIDE.md) | ⭐ data model, call chains, client design, checklist |
| [pinterest-category-taxonomy.json](pinterest-category-taxonomy.json) | machine-readable taxonomy |

**To build the scraper, use #7 and #8.** The API does not map 1:1 to the UI —
one endpoint powers two unrelated screens, and one screen hides five endpoints.

## How canonical was decided, and why not by filename or size

Chrome's `(N)` counter tracks download collisions, not document versions, and the
file timestamps here span three minutes — so neither the number nor the mtime
says which draft is current. Size does not either: `00INDEXandcoverage.md` was
the **smallest** file in its family and is a different document entirely.

So each family was compared line by line against the others, and the winner
confirmed by checking whether its facts are strictly better. They are:

| Check | Superseded draft says | Canonical says |
|---|---|---|
| `limit` on keyword discovery | "UI sends 20, use 100" | max **522** — `1000 → 400 "too large: 1000 > 522"` |
| `aggregation` | "`2` ONLY, no daily data exists" | `2` only for keywords, **but moments do support daily** — 456 points vs 66 |
| `l1interests` | 12 of 24 IDs, one flagged unverified | **all 24 mapped** |
| PWS handler | "required" | required, **value must be exactly `trends/index.js`** — every other value, empty, or omitted → 403 |

In each case the draft is not merely shorter, it is *wrong in a way that would
have shipped*. That is why they were checked rather than deduplicated by size.

## Two documents that share a filename

Two families turned out to hold **different documents**, not drafts, so both were
kept:

- `00INDEXandcoverage.md` was a reader's **DOCUMENTATION INDEX**; its `(1)`–`(4)`
  siblings were a **Master Index & Coverage Audit**. Kept as
  `00-INDEX-and-coverage.md` and `00-COVERAGE-AUDIT.md`.
- `04searchtrendsdashboard.md` was "Doc #4 — the KEYWORD DASHBOARD (`/detail/`)"
  with 199 lines found in no other file; its `(1)`–`(5)` siblings were a separate
  "Search trends (the keyword DASHBOARD)" write-up. Kept as
  `04b-keyword-dashboard-detail.md` and `04-search-trends-dashboard.md`.

Worth reconciling into one document each at some point. Until then, if the two
disagree on a fact, **verify it on the wire before building on it** — that is
what settles it, not which document reads better.

## One fact in here the code already relies on

`07-API-REFERENCE.md` §: the `X-Pinterest-PWS-Handler: trends/index.js` header is
mandatory, and any other value returns **403 `Invalid Resource Request`**. This
project found that independently by probing, and `src/session.py:classify()`
returns `malformed` for it rather than `auth_expired` — so a missing header can
never be mistaken for a dead session and evict a healthy profile.
