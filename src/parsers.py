"""One named parser per endpoint. Nothing indexes a raw response key inline.

This module exists because of a specific, expensive failure in the parent
project: seven modules fetched correct data and read `None` out of it for
months, because the response said `snake_case` and the code read `camelCase`.
Nothing errored. Every table was empty and every number was plausible.

So, the rules here:

  * every field is read in exactly one place — this file
  * `_get` returns None for absent, never 0, never "" — **absent is not zero**
  * every parser is covered by a key-coverage test (scenario B2) that diffs the
    keys it reads against the keys the committed fixture actually has
  * where Pinterest ships two spellings, this file picks one and the rest of the
    codebase never learns there were two

Percent fields come back as fractions (0.82 = 82%) and are passed through as
fractions — converting here would hide the unit from the consumer.
"""

# ------------------------------------------------------------------ helpers

def _get(node, *keys, default=None):
    """First present key wins. Absent → default (None), never a falsy stand-in.

    The varargs form is how the double-spelling problem is contained: callers
    ask for the concept, not the spelling.
    """
    if not isinstance(node, dict):
        return default
    for key in keys:
        if key in node:
            return node[key]
    return default


def _list(node, *keys):
    value = _get(node, *keys)
    return value if isinstance(value, list) else []


def _summary(block):
    """The {engagement, saves, outbound_clicks} triple shared by top/ and
    demographics/.

    ⚠️ `total` is **always 0** on every one of these — Pinterest never exposes
    absolute volume. It is dropped rather than reported, because a `total: 0`
    reaching a customer reads as "no engagement" when it means "not disclosed".
    """
    if not isinstance(block, dict):
        return None
    out = {}
    for metric in ("engagement", "saves", "outbound_clicks"):
        node = block.get(metric)
        if not isinstance(node, dict):
            out[metric] = None
            continue
        out[metric] = {
            "percent_growth": _get(node, "percent_growth"),
            "percent_relative_volume": _get(node, "percent_relative_volume"),
            "lookback": _get(node, "lookback"),
            # `total` deliberately omitted — always 0, never real.
        }
    return out


# ----------------------------------------------------------------- taxonomy

def parse_taxonomy(data):
    """§3.6 → {id: {...}}. 383 categories, levels 2/3/4. L1 is NOT here."""
    categories = _get(data, "categories", default={}) or {}
    out = {}
    for cat_id, node in categories.items():
        out[str(cat_id)] = {
            "id": str(cat_id),
            "friendly_name": _get(node, "friendly_name"),
            "level": _get(node, "level"),
            "parent_id": _get(node, "parent_product_category_id"),
            "children": [str(c) for c in _list(node, "children")],
            "l2_ids": [str(c) for c in _list(node, "l2_product_category_ids")],
        }
    return out


def category_path(taxonomy, cat_id, verticals=None):
    """Walk parent_id up to build a breadcrumb. Shopping responses return IDs
    only — a customer-facing record must carry names."""
    names, seen = [], set()
    node = taxonomy.get(str(cat_id))
    while node and node["id"] not in seen:
        seen.add(node["id"])
        if node.get("friendly_name"):
            names.append(node["friendly_name"])
        parent = node.get("parent_id")
        node = taxonomy.get(str(parent)) if parent else None
    # The L1 vertical is never in the taxonomy, so it is appended from vocab.
    if verticals:
        names.extend(str(v) for v in verticals if v)
    return list(reversed(names))


# -------------------------------------------------------- trending categories

def parse_top_categories(data):
    """§3.7 → list of trending categories for ONE vertical.

    `total` on the response is the vertical's real size, not a truncation
    signal: asking for limit=20 and getting 19 means the vertical HAS 19.
    """
    rows = []
    for node in _list(data, "ordered_values"):
        rows.append({
            "category_id": str(_get(node, "product_category") or ""),
            "parent_ids": [str(p) for p in _list(node, "parent_product_categories")],
            "search_queries": list(_list(node, "related_search_trends")),
            "summary": _summary(_get(node, "summary")),
        })
    return {
        "total_in_vertical": _get(data, "total_num_product_categories"),
        "categories": rows,
    }


def parse_category_metrics(data):
    """§3.8 → the Performance chart series, per category.

    `term` holds the category ID here, not a keyword — same field name as the
    keyword endpoints, different subject.
    """
    out = {}
    for node in _list(data, "values"):
        cat_id = str(_get(node, "term") or "")
        growth = _get(node, "growth_rates") or {}
        series = []
        for point in _list(node, "daily_values"):
            series.append({
                "date": _get(point, "date"),
                "count": _get(point, "count"),
                "predicted_lower": _get(point, "normalized_predicted_lower_bound"),
                "predicted_upper": _get(point, "normalized_predicted_upper_bound"),
            })
        out[cat_id] = {
            # All three can be null on a short window — null means UNMEASURED,
            # not flat. Never render a null growth as 0%.
            "wow_change": _get(growth, "wow_change"),
            "mom_change": _get(growth, "mom_change"),
            "yoy_change": _get(growth, "yoy_change"),
            "series": series,
            "series_points": len(series),
        }
    return out


def parse_category_demographics(data, event):
    """§3.9 → audience + search queries + headline numbers, per category.

    `event` is threaded through and stamped on the result because the audience
    genuinely differs by it (measured on 1408: 65+ is 0.32 of outbound clicks
    but 0.19 of saves). An age split without its event is not a fact.
    """
    out = {}
    dists = _get(data, "product_category_distributions", default={}) or {}
    for cat_id, node in dists.items():
        demos = _list(node, "demographics")
        first = demos[0] if demos else {}
        summaries = _list(node, "summaries")
        out[str(cat_id)] = {
            "event": event,
            "age_distribution": _get(first, "age_distribution"),
            "gender_distribution": _get(first, "gender_distribution"),
            # Identical across all three events (verified) — fetch once.
            "search_queries": list(_list(node, "related_search_trends")),
            "summary": _summary(summaries[0] if summaries else None),
        }
    return out


def parse_top_products(data, region=None):
    """§3.10 → shoppable products. `pin_id` is the bridge off Pinterest.

    The response carries image URLs at 7 sizes but **no merchant URL or price** —
    those live on the pin page (§3.19) behind a call not yet captured. The pin
    permalink is constructed here so a record always has one linkable field.
    """
    products = []
    for node in _list(data, "top_products"):
        pin_id = _get(node, "pin_id")
        images = _get(node, "images") or {}
        # The response carries 7 sizes, and they are the SAME image hash with
        # only the path segment swapped (verified: 75x75 / 236x / … / 1200x all
        # end `.../08/12/37/081237925c576eee2dfc743b54864496.jpg`). So the
        # largest url plus the size list is lossless — a consumer builds any
        # other size by substituting the segment. Carrying all 7 would triple
        # the record for nothing.
        # ⚠️ `1200x` is a MAX, not the actual pixel size: it reported 1000x1000.
        best = None
        for size in ("1200x", "736x", "564x", "474x", "345x", "236x", "75x75"):
            if isinstance(images.get(size), dict) and images[size].get("url"):
                best = images[size]["url"]
                break
        products.append({
            "pin_id": pin_id,
            "pin_url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            "merchant_name": _get(node, "merchant_name"),
            "title": _get(node, "title"),
            "image_url": best,
            "image_sizes": sorted(images) or None,
            "region": region,
            # Known to exist on the pin page, not in this response. Explicitly
            # null so a consumer sees "not fetched", not "no price".
            "price": None,
            "merchant_url": None,
        })
    return products


# ------------------------------------------------- keyword pipeline (shared)

def parse_keyword_metrics(data):
    """§3.13 → per-term curve + the 🔮 forecast flag.

    ⚠️ This endpoint returns BOTH `has_prediction` and `hasPrediction` with the
    same value; §3.15 returns only the camelCase one. One canonical field is
    emitted here so nothing downstream ever sees two spellings — and both being
    absent yields None, never False (absent is not zero).

    ⚠️ Terms with no data are silently dropped: 10 requested → 4 returned,
    measured. Callers MUST match by term; `missing_terms` makes that checkable.
    """
    rows = {}
    for node in data if isinstance(data, list) else []:
        term = _get(node, "term")
        growth = _get(node, "growth_rates") or {}
        series = []
        for point in _list(node, "counts"):
            series.append({
                "date": _get(point, "date"),
                "count": _get(point, "count"),
                "normalized": _get(point, "normalizedCount"),
                "predicted_lower": _get(point, "predictedLowerBoundNormalizedCount"),
                "predicted_upper": _get(point, "predictedUpperBoundNormalizedCount"),
            })
        rows[term] = {
            "term": term,
            "has_forecast": _get(node, "has_prediction", "hasPrediction"),
            "wow_change": _get(growth, "wow_change"),
            "mom_change": _get(growth, "mom_change"),
            "yoy_change": _get(growth, "yoy_change"),
            "series": series,
        }
    return rows


def missing_terms(requested, parsed):
    """Terms the API dropped. Absence means 'no data returned', NOT zero volume
    — `halloween` and `christmas ornament` were both dropped in testing."""
    return [t for t in requested if t not in parsed]


# ------------------------------------------------------ keyword discovery

def parse_discover(data):
    """§3.12 `/top_trends_filtered/` → ranked keywords with growth + seasonality.

    Growth fields arrive as `{value, index}`. `index` was decoded live
    (2026-08-19, 100 rows): it is the 1..N rank of the value WITHIN THIS
    RESPONSE — monotone with value, one per row. It is exposed as
    `*_rank_in_response` so its scope is in its name; it must never be compared
    across responses.

    `affinity` is null in 100/100 rows — dropped, not carried.
    `searchCount`/`normalizedCount` are response-scoped relative volumes.
    """
    def growth(node):
        if not isinstance(node, dict):
            return None, None
        return _get(node, "value"), _get(node, "index")

    rows = []
    for node in _list(data, "values"):
        wow_v, wow_i = growth(_get(node, "wow_change"))
        mom_v, mom_i = growth(_get(node, "mom_change"))
        yoy_v, yoy_i = growth(_get(node, "yoy_change"))
        rows.append({
            "term": _get(node, "term"),
            "search_count": _get(node, "searchCount"),
            "normalized_count": _get(node, "normalizedCount"),
            "reverse_rank": _get(node, "reverseRank"),
            "seasonality_score": _get(node, "seasonality_score"),
            "wow_change": wow_v, "wow_rank_in_response": wow_i,
            "mom_change": mom_v, "mom_rank_in_response": mom_i,
            "yoy_change": yoy_v, "yoy_rank_in_response": yoy_i,
        })
    return {"end_date": _get(data, "endDate"), "terms": rows}


def parse_prefix_match(data):
    """§3.17 typeahead → up to 10 terms, each with a ~1-year weekly sparkline.

    Searches the WHOLE keyword space (works for non-trending terms), unlike
    `keywordsToInclude` which only filters the trending set. Counts are bare
    ints, response-scoped. Empty list = no match, not an error.
    """
    return [{"term": _get(node, "term"),
             "sparkline": list(_list(node, "counts"))}
            for node in (data if isinstance(data, list) else [])]


def parse_keyword_demographics(data):
    """§3.14 → {term: {age_distribution, gender_distribution}}, fractions 0-1.

    ⚠️ A DIFFERENT endpoint and a different subject from the category
    demographics (§3.9) despite the name — the same theme can have opposite
    audiences as a keyword vs as a category. Key order may differ from request
    order; consumers match by term.
    """
    out = {}
    for term, node in (_get(data, "term_distributions", default={}) or {}).items():
        out[term] = {
            "age_distribution": _get(node, "age_distribution"),
            "gender_distribution": _get(node, "gender_distribution"),
        }
    return out


def parse_related_terms(data):
    """§3.15 → exactly 5 siblings. camelCase-only `hasPrediction` here (§3.13
    ships both spellings) — normalised to the same canonical field.

    Forecastability does NOT propagate: a forecastable parent can have 0/5
    forecastable siblings and vice-versa.
    """
    return [{"term": _get(node, "term"),
             "has_forecast": _get(node, "has_prediction", "hasPrediction"),
             "sparkline": list(_list(node, "counts"))}
            for node in (data if isinstance(data, list) else [])]


def parse_term_images(data):
    """§3.16 POST /term_images/ → {term: [image urls]}. URLs only — no pin ids,
    so nothing here bridges off Pinterest; §3.10 pin_id is that bridge."""
    if not isinstance(data, dict):
        return {}
    return {term: list(urls) for term, urls in data.items()
            if isinstance(urls, list)}


def parse_moment_demographics(data):
    """§3.18 GraphQL → {age_distribution, gender_distribution}, MEASURED.

    ⚠️ The two distributions arrive in **different shapes**, and this is the
    whole reason the parser exists:

        ageDistribution     [{"key": "18-24", "value": 0.43}, …]   an ARRAY
        genderDistribution  {"male": 0.05, "female": 0.87, …}      an OBJECT

    Age is flattened to the same `{bucket: fraction}` dict the REST endpoints
    (§3.9/§3.14) return, so a consumer never learns there were two shapes and
    the `derived` → `measured` upgrade needs no schema change.

    ⚠️ Buckets are NOT decades: `45-49` and `50-54` are split, not `45-54`.
    Anything that assumes ten-year bands silently mis-buckets two of seven.

    ⚠️ **The fractions do NOT reliably sum to 1.** They are rounded to 2dp, and
    small buckets round UP off a 0.04 floor. Measured: halloween sums to
    **1.07** (four buckets at 0.04), christmas/thanksgiving/hanukkah to 1.00,
    and gender to 1.01 on thanksgiving. So a consumer computing "X% of the
    audience" is off by up to 7% on the worst case.

    They are passed through **unnormalised on purpose**. Rescaling to sum to 1
    would invent precision Pinterest did not publish, and this codebase reports
    what the wire said. Normalise at the point of use, and only if the use
    actually requires a closed distribution.
    """
    items = ((data or {}).get("trendsDemographicsRead") or {}).get("items") or []
    if not items:
        return None
    first = items[0] or {}

    ages = {}
    for bucket in first.get("ageDistribution") or []:
        key = bucket.get("key")
        if key is not None:
            ages[key] = bucket.get("value")

    gender = first.get("genderDistribution")
    return {
        "age_distribution": ages or None,
        "gender_distribution": gender if isinstance(gender, dict) else None,
    }


# ---------------------------------------------------------------- moments

def _epoch_ms(value):
    """Moment timestamps arrive as epoch-ms STRINGS. Absent stays None."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_moments_list(data):
    """§3.3 → the region's moments. PARALLEL ARRAYS, zipped by index.

    A length mismatch raises rather than truncating: zipping unequal arrays
    silently mis-assigns every phase after the gap — a wrong number on every
    record, not a parse hiccup.
    """
    moments = _list(data, "moments")
    phases = _list(data, "phase_labels")
    peaks = _list(data, "peaks")
    historical = _list(data, "historical_peaks")
    next_ts = _list(data, "moment_next_occurrence_timestamps")

    lengths = {len(moments), len(phases), len(peaks), len(historical), len(next_ts)}
    if len(lengths) > 1:
        raise ValueError(
            f"moment/available parallel arrays disagree on length: "
            f"moments={len(moments)} phases={len(phases)} peaks={len(peaks)} "
            f"historical={len(historical)} next={len(next_ts)} — zipping these "
            f"would mis-assign every field after the shortest.")

    def peak(node):
        if not isinstance(node, dict):
            return None
        return {
            "peak_at": _epoch_ms(_get(node, "peak_timestamp_millis")),
            "takeoff_at": _epoch_ms(_get(node, "takeoff_timestamp_millis")),
            "peak_length_days": _get(node, "peak_length_in_days"),
        }

    out = []
    for i, slug in enumerate(moments):
        out.append({
            "slug": slug,
            "phase": phases[i],
            "actionable": phases[i] in ("rising", "approaching"),
            "next_peak": peak(peaks[i]),
            "last_peak": peak(historical[i]),
            "next_occurrence_at": _epoch_ms(next_ts[i]),
        })
    return out


def parse_moment_metrics(data):
    """§3.4 → demand curve + forecast + per-interest split, keyed by slug.

    The ONLY endpoint in the whole API with daily granularity. Counts are
    response-scoped (`normal_counts`); `moment_interests` is keyed by interest
    ID and each interest carries its own series.
    """
    def series(values):
        return [{
            "at": _get(point, "timestamp"),
            "count": _get(point, "normal_counts"),
            "predicted_lower": _get(point, "predicted_normalized_lower_bound_count"),
            "predicted_upper": _get(point, "predicted_normalized_upper_bound_count"),
        } for point in (values or [])]

    out = {}
    for node in _list(data, "moments"):
        slug = _get(node, "name")
        moment = _get(node, "moment") or {}
        interests = {}
        for interest_id, sub in (_get(node, "moment_interests") or {}).items():
            interests[str(interest_id)] = {
                "series": series(_list(sub, "daily_values")),
            }
        out[slug] = {
            "slug": slug,
            "series": series(_list(moment, "daily_values")),
            "peaks": [{
                "peak_at": _epoch_ms(_get(p, "peak_timestamp_millis")),
                "takeoff_at": _epoch_ms(_get(p, "takeoff_timestamp_millis")),
                "peak_length_days": _get(p, "peak_length_in_days"),
            } for p in _list(moment, "peaks")],
            "interest_split": interests,
        }
    return out


# ------------------------------------------------------ spotlight/editorial

def _pins(node):
    """Pin thumbnails, shared by spotlight and editorial. `id` is kept because
    it is the bridge to the pin page (§3.19); `color` is the dominant-color hex
    — free creative-palette signal (F6)."""
    out = []
    for pin in _list(node, "pins"):
        pin_id = _get(pin, "id")
        out.append({
            "pin_id": pin_id,
            "pin_url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            "image_url": _get(pin, "src"),
            "color": _get(pin, "color"),
            "width": _get(pin, "width"),
            "height": _get(pin, "height"),
        })
    return out


def parse_spotlight(data):
    """§3.2 → 5 curated trends. The detail view fires no request — this object
    IS the detail, so everything is kept.

    time_series carries forecast fields that were null on every captured trend
    (F4/H3) — passed through nullable, never assumed always-null.
    """
    out = []
    for node in (data if isinstance(data, list) else []):
        out.append({
            "id": _get(node, "id"),
            "name": _get(node, "name"),
            "description": _get(node, "description"),
            "interests": [str(i) for i in _list(node, "interests")],
            "pct_growth_mom": _get(node, "pct_growth_mom"),
            "keywords": list(_list(node, "related_search_trends")),
            "series": [{
                "date": _get(p, "date"),
                "count": _get(p, "count"),
                "normalized": _get(p, "normalized_count"),
                "predicted_lower": _get(p, "normalized_predicted_lower_bound"),
                "predicted_upper": _get(p, "normalized_predicted_upper_bound"),
            } for p in _list(node, "time_series")],
            "pins": _pins(node),
            "is_published": _get(node, "is_published"),
        })
    return out


def parse_editorial(data, region):
    """§3.5 → 6 hand-written trends. Keywords are nested PER REGION inside each
    item — the requested region's list is picked here, and an item that does not
    cover that region gets None, never another region's list (scenario B6).

    `start_date` is the campaign window (F3): when Pinterest's editors began the
    push. Emitted, because "how long has this been promoted" is a signal.
    """
    out = []
    for node in (data if isinstance(data, list) else []):
        keywords_by_region = _get(node, "keywords") or {}
        out.append({
            "id": _get(node, "id"),
            "title": _get(node, "title"),
            "body": _get(node, "body"),
            "trend_type": _get(node, "trend_type"),
            "interests": [str(i) for i in _list(node, "interests")],
            "regions": list(_list(node, "regions")),
            "keywords": (list(keywords_by_region[region])
                         if region in keywords_by_region else None),
            "campaign_start": _get(node, "start_date") or None,
            "campaign_end": _get(node, "end_date") or None,
            "pins": _pins(node),
            "is_published": _get(node, "is_published"),
        })
    return out
