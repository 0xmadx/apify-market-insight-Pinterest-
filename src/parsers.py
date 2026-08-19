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
        best = None
        for size in ("736x", "564x", "474x", "345x", "236x"):
            if isinstance(images.get(size), dict):
                best = images[size].get("url")
                break
        products.append({
            "pin_id": pin_id,
            "pin_url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            "merchant_name": _get(node, "merchant_name"),
            "title": _get(node, "title"),
            "image_url": best,
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
