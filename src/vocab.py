"""Shared vocabulary and pre-wire validation.

Every ceiling and enum here was measured, and most are cited with the error text
the API returns when you exceed them. They are enforced **before** the request
because of an asymmetry proven on 2026-08-19:

  Style A over a ceiling → 400 with a readable reason
  Style B over a ceiling → 400 with an EMPTY BODY

On Style B the wire will never tell you what you got wrong, so a client that
learns limits from responses cannot exist. It has to know them up front.

Raising `InvalidParam` is the point: refusing loudly beats a silent empty 200,
which this API hands out for at least four different mistakes.
"""


class InvalidParam(ValueError):
    """A request was refused before the wire, with the measured reason."""


# --------------------------------------------------------------- vocabulary

REGIONS = [
    "US", "CA", "DE", "FR", "ES", "IT", "DE+AT+CH", "GB+IE",
    "IT+ES+PT+GR+MT", "PL+RO+HU+SK+CZ",
]

# Regions where these actually return data. Everything else answers 200 with an
# empty payload — a silent no, not an error.
TOP_PRODUCTS_REGIONS = {"US", "CA", "GB+IE"}
EDITORIAL_REGIONS = {"US", "CA", "GB+IE"}

# L1 verticals. NOT discoverable from the taxonomy — it contains only L2/L3/L4
# (66/190/127 = 383). These 7 are the ones that return rows; the other verticals
# in the UI return nothing. Passing an L2/L3 id here yields 200 with 0 rows.
#
# ⚠️ These names are NOT guessable and must never be guessed. A first pass here
# invented all seven, and the code ran perfectly while labelling every Home-decor
# category "Beauty" — a textbook plausible-wrong-number: no error, no empty
# table, just a wrong word on every record. Source of truth is doc #7 §4.3.
#
# EXPECTED_ROWS is the cross-check that would have caught it, and is asserted in
# tests: 1181 returns 19 categories and 1250 returns 9, so a mapping that has
# them backwards contradicts the measured row counts.
VERTICALS = {
    "1181": "Fashion",
    "1250": "Home decor",
    "1042": "Beauty",
    "1148": "DIY",                    # hidden from the UI, served by the API
    "1016": "Arts & entertainment",   # hidden
    "1500": "Wedding",                # hidden
    "1315": "Media",                  # hidden
}

# Measured row counts per vertical (doc #7 §4.3). Used to sanity-check that the
# id->name mapping still lines up with what the wire returns.
EXPECTED_ROWS = {
    "1181": 19, "1250": 9, "1042": 6,
    "1148": 3, "1016": 2, "1500": 2, "1315": 1,
}

# The UI exposes only 3 of these (Fashion, Home decor, Beauty); the API serves 7.
UI_VISIBLE_VERTICALS = {"1181", "1250", "1042"}

# Verticals that exist but return 0 rows — refused before the wire so an empty
# result is never mistaken for "nothing is trending".
VERTICALS_NO_DATA = {
    "1161": "Electronics", "1007": "Animals & pet supplies",
    "1194": "Food & beverages", "1241": "Hardware",
    "1436": "Sporting goods", "1481": "Toys & games",
    "1489": "Vehicles & parts",
}

# The validator accepts 7 values; only these 3 work. The rest return HTTP 500
# ("Something went wrong on our end") — verified across all 7, 2026-08-19.
EVENTS = {"OUTBOUND_CLICK", "ENGAGEMENT", "SAVE"}
EVENTS_ACCEPTED_BUT_BROKEN = {"IMPRESSION", "CLICK", "LONG_CLICK", "SEARCH"}

RANKING_METHODS = {"HIGH_VOLUME", "GROWTH", "VIRAL"}
ORDER_BY = {"TREND_RANK", "RELATIVE_VOLUME", "PCT_CHANGE_MOM"}
ORDER = {"DESC", "ASC"}

AGE_BUCKETS = ["AGE_18_24", "AGE_25_34", "AGE_35_44", "AGE_45_49",
               "AGE_50_54", "AGE_55_64", "AGE_65_PLUS", "AGE_ALL"]
GENDERS = ["MALE", "FEMALE", "UNSPECIFIED"]

# ⚠️ The keyword endpoints use a DIFFERENT scheme for the same concepts.
# Never let one leak into the other.
AGE_CODES_KEYWORD = {"18-24": 2, "25-34": 4, "35-44": 5, "45-49": 6,
                     "50-54": 7, "55-64": 8, "65+": 9}
GENDER_CODES_KEYWORD = {"male": 0, "female": 1, "unspecified": 2}

# The detail page labels ENGAGEMENT as "All"; the shopping table labels the same
# value "Engagement". Map by meaning, never by label.
EVENT_LABELS = {
    "all": "ENGAGEMENT", "engagement": "ENGAGEMENT",
    "outbound clicks": "OUTBOUND_CLICK", "outbound_clicks": "OUTBOUND_CLICK",
    "pin saves": "SAVE", "saves": "SAVE",
}

# ---- the persisted GraphQL query (doc #7 §3.18) ----------------------
# Captured live 2026-08-19 via an in-page interceptor; see
# probes/captures/README.md for the procedure and the verbatim body.
#
# ⚠️ A persisted-query hash is a DEPLOY ARTEFACT. Pinterest rotates it whenever
# they ship, and a rotated hash returns HTTP 200 with no `data` — which is why
# transport raises StaleQueryHash rather than reporting an empty audience.
# When it rotates, re-capture; do not guess.
MOMENT_DEMOGRAPHICS = {
    "query_hash": "85bfe810f1f9a895ec901e57dcbb9b193bfade5c8504299d645ca89053b31a50",
    "operation": "useGetMomentDemographicsAdsQuery",
    # NOT the global trends/index.js — this query is bound to the moment page.
    "handler": "trends/moments/[momentId].js",
}

# Measured ceilings. The value is the maximum that WORKS.
LIMITS = {
    "top_limit": 522,          # 1000 → 400 "'limit' is too large: 1000 > 522"
    "predicted_days": 91,      # 180 → 400
    "days": 730,               # 1095 → 400 "too large: 1095 > 730"
    "lookback_days": 730,
    "interest_limit": 24,      # 50 → 400 "too large: 50 > 24"
    "num_terms": 100,          # 500 → 400
}


# --------------------------------------------------------------- validators

def event(value, *, endpoint="top"):
    """Normalise a UI label or enum to a working `event`."""
    if value is None:
        raise InvalidParam("event is required — omitting it on top_products 500s")
    raw = str(value).strip()
    resolved = EVENT_LABELS.get(raw.lower(), raw.upper())

    if resolved in EVENTS_ACCEPTED_BUT_BROKEN:
        raise InvalidParam(
            f"event={resolved!r} passes Pinterest's validator but returns HTTP 500. "
            f"Only {sorted(EVENTS)} work.")
    if resolved not in EVENTS:
        raise InvalidParam(f"event={value!r} is not one of {sorted(EVENTS)}")

    if endpoint == "top_products" and resolved != "OUTBOUND_CLICK":
        # 200 + [] — indistinguishable from "this category has no products".
        raise InvalidParam(
            f"top_products with event={resolved} returns 200 with an EMPTY list, "
            f"not an error. Only OUTBOUND_CLICK returns products.")
    return resolved


def vertical(value):
    """An L1 vertical id. L2/L3/L4 ids return 200 with 0 rows — silently."""
    key = str(value)
    if key in VERTICALS_NO_DATA:
        raise InvalidParam(
            f"vertical {key} ({VERTICALS_NO_DATA[key]}) is a real L1 id but "
            f"returns 0 rows — measured. Requesting it yields an empty result "
            f"that reads as 'nothing is trending'.")
    if key not in VERTICALS:
        raise InvalidParam(
            f"parent_product_categories={key!r} is not an L1 vertical that "
            f"returns data. L2/L3/L4 ids return 200 with 0 rows and look like "
            f"'nothing is trending'. Valid: {sorted(VERTICALS)}")
    return key


def verticals_one_per_call(values):
    """`percent_relative_volume` is normalised WITHIN the response, so combining
    verticals crushes the smaller one (Beauty's #1 reads 1.00 alone, 0.01 beside
    Fashion). One vertical per call is a correctness rule, not a style choice."""
    ids = [str(v) for v in (values or [])]
    if len(ids) != 1:
        raise InvalidParam(
            f"exactly one vertical per top/ call, got {len(ids)}. Combining them "
            f"re-normalises percent_relative_volume and silently crushes the "
            f"smaller vertical.")
    return [vertical(ids[0])]


def region(value, *, capability=None):
    """Region, optionally checked against an endpoint's capability set."""
    if value not in REGIONS:
        raise InvalidParam(f"region={value!r} not in {REGIONS}")
    if capability == "top_products" and value not in TOP_PRODUCTS_REGIONS:
        raise InvalidParam(
            f"top_products returns data only for {sorted(TOP_PRODUCTS_REGIONS)}; "
            f"{value} answers 200 with an empty list.")
    if capability == "editorial" and value not in EDITORIAL_REGIONS:
        raise InvalidParam(
            f"editorial/content returns data only for {sorted(EDITORIAL_REGIONS)}; "
            f"{value} answers 200 with 0 items.")
    return value


def ceiling(name, value):
    """Refuse anything over a measured ceiling, naming the number."""
    cap = LIMITS[name]
    if value is None:
        return value
    if not isinstance(value, int) or value < 0:
        raise InvalidParam(f"{name} must be a non-negative int, got {value!r}")
    if value > cap:
        raise InvalidParam(f"{name}={value} exceeds the measured maximum {cap} "
                           f"— Pinterest answers 400 (Style B: with no message)")
    return value


def keyword(value):
    """Keyword input is case-sensitive and lowercase-only. `HALLOW` returns 200
    with an empty list — a silent no."""
    if value is None or not str(value).strip():
        raise InvalidParam("empty keyword — prefix_match with an empty query 500s")
    return str(value).strip().lower()


def end_date(value):
    if not value:
        raise InvalidParam(
            "end_date is required and must come from /latest_available_date/. "
            "today() is a bug: the data lags and future dates 400.")
    return value
