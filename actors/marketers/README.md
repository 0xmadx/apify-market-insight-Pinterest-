# Pinterest Trends for Marketers — campaign timing & search demand

Two questions decide most campaign calendars: *when does this season actually
start*, and *does this angle have real search demand before I brief creative*.
This actor answers both from Pinterest's own backend — not the filtered
dashboard, the same data unfiltered and joined together.

**Press Run with nothing filled in** and you get Pinterest's own curated
trend overview — what it is featuring right now, no setup required.

## The three questions it answers for you

| Operation | The question it answers | Requests | Records |
|---|---|---|---|
| `radar` | What is Pinterest itself featuring right now? | 2 | 11 |
| `keywords` | Is this angle still growing, or did I already miss it? | ~14 | 10 |
| `moments` | When does this season actually start — and when does it peak? | ~16 | 13 |

Each is a complete answer on its own.

## What you actually get

A real seasonal moment — not an illustration:

```json
{
  "slug": "thanksgiving",
  "phase": "approaching", "phase_label": "Approaching",
  "next_peak": { "takeoff_date": "2026-10-31", "peak_date": "2026-11-28",
                 "peak_length_days": 28,
                 "takeoff_at": 1793404800000, "peak_at": 1795824000000 },
  "series_points": 456,
  "audience": { "age_distribution": { "25-34": 0.31, "35-44": 0.22, "18-24": 0.17 } },
  "keywords": ["sweet potato recipes", "fall crafts", "green bean recipes", "…25 total"],
  "_meta": { "aggregation": "daily", "audience_basis": "measured" }
}
```

The dates are the product: *takes off 31 Oct, peaks 28 Nov, 28 days of peak.*
That is the brief — brief creative before takeoff, not after peak.

And a real keyword, for validating the angle behind the campaign:

```json
{
  "term": "halloween nails",
  "status": "ok",
  "has_forecast": true,
  "wow_change": 0.30, "mom_change": 1.00, "yoy_change": null,
  "age_distribution": { "18-24": 0.40, "25-34": 0.37, "35-44": 0.15, "65+": 0.04 },
  "series": [ { "date": "2026-11-13", "count": 0, "normalized": 0,
                "predicted_lower": 0, "predicted_upper": 1 } ],
  "related": ["nails", "nails inspo 2026", "fall nails", "halloween nail designs"],
  "pin_images": ["…9 urls"]
}
```

Where `has_forecast` is true, the tail runs **91 days into the future** —
you can see whether an angle keeps climbing before you commit budget to it.

## Output format

Every record above is one item in the run's dataset — no parsing required.
Apify converts that dataset for you on download, so pick whatever your tools
already read: **JSON**, **CSV**, **Excel (XLSX)**, **XML**, or **RSS**, from
the Console's Export button or the API (`?format=xml`, `?format=csv`, …). A
media calendar or CRM that only takes CSV is not a reason to write a parser.

## Quick start

```json
{ "operation": "radar", "region": "US" }
```

Then, once you have a season or an angle in mind:

```json
{ "operation": "moments" }
```

```json
{ "operation": "keywords", "mode": "exact", "queries": ["your campaign angle here"], "includeRelated": true }
```

`includeRelated` returns 5 sibling terms per query — useful for widening a
brief beyond the one phrase creative first suggested.

## Using the API

Press Run once in the Console and Apify generates ready-to-paste code for
this exact call (Python, JS, curl) under the **API** tab — that's the
fastest way to integrate. The shape of it:

```bash
curl "https://api.apify.com/v2/acts/0xdevers~pinterest-trends-marketers/run-sync-get-dataset-items?token=YOUR_API_TOKEN" \
  -X POST -H "Content-Type: application/json" \
  -d '{ "operation": "radar", "region": "US" }'
```

Get `YOUR_API_TOKEN` from Console → Settings → Integrations. This returns the
finished dataset directly, so it's the right call for `radar` and most
`keywords`/`moments` runs. A `keywords` run with `includeRelated: true` on a
long query list can run past `run-sync`'s timeout — for those, POST to
`/v2/acts/.../runs` instead and poll the run, or just set `maxRecords` to
keep one call inside the sync window.

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

32 regions. Moments are not available in every one — **JP and IN have none**.

## Cost

**Free to run right now** — this actor has no per-run charge. Below that,
what determines how much Apify compute a run uses: requests are the
currency, and batching keeps them low — 20 keywords is usually **one**
request. `includeRelated` is the exception: one request per keyword, because
Pinterest has no batch form for it.

## Limits worth knowing

- History reaches about **365 days** and no further.
- `maxRecords` caps the answer, and the run **tells you when it cut one short**
  rather than letting a short list read as "there is nothing".
- Repeat runs return only what changed — schedule it weekly and each run is
  cheap. Set `fullRescan: true` when you want a full snapshot regardless.

## Support

Every input is documented in the input schema, including its valid values and
what happens at the limits.
