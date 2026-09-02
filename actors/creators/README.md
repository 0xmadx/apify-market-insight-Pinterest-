# Pinterest Trends for Creators — a content calendar from real data

Posting about a season after it peaks is the same as not posting about it.
This actor turns Pinterest's own trend data into a calendar: which seasonal
moments are approaching, and which search terms people are actually using
right now — so a post goes up while the demand is still climbing, not after.

**Press Run with nothing filled in** and you get every seasonal moment,
where it stands, and when it peaks — no setup required.

## What it answers

| Operation | The question it answers | Requests | Records |
|---|---|---|---|
| `moments` | When does this season take off, and when does it peak? | ~16 | 13 |
| `keywords` | Which search terms are gaining, and what should I actually caption with? | ~14 | 10 |

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

*Takes off 31 Oct, peaks 28 Nov, 28 days of peak* — that is the posting
window, and the 25 `keywords` attached are what to actually caption and tag
with, not a guess at what people search.

And a real keyword, for checking a caption idea before you commit to it:

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

`related` gives you 4 more angles on the same post idea for free, and where
`has_forecast` is true the series runs **91 days into the future** — post
early enough and you are riding the curve up, not catching the tail.

## Output format

Every record above is one item in the run's dataset — no parsing required.
Apify converts that dataset for you on download, so pick whatever your tools
already read: **JSON**, **CSV**, **Excel (XLSX)**, **XML**, or **RSS**, from
the Console's Export button or the API (`?format=xml`, `?format=csv`, …). Feed
the RSS export straight into a content-calendar tool, or the CSV into a
spreadsheet — no script needed either way.

## Quick start

```json
{ "operation": "moments" }
```

Then narrow to the keywords behind one moment:

```json
{ "operation": "keywords", "mode": "discover", "moments": ["thanksgiving"] }
```

Or check a caption idea you already have:

```json
{ "operation": "keywords", "mode": "exact", "queries": ["your caption idea here"] }
```

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

Requests are the currency, and batching keeps them low — 20 keywords is
usually **one** request.

## Limits worth knowing

- History reaches about **365 days** and no further.
- `maxRecords` caps the answer, and the run **tells you when it cut one short**
  rather than letting a short list read as "there is nothing".
- Repeat runs return only what changed — schedule it weekly and each run stays
  cheap. Set `fullRescan: true` when you want a complete snapshot regardless.

## Support

Every input is documented in the input schema, including its valid values and
what happens at the limits.
