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

# All 32 regions the API accepts (doc #7 §4.1). The UI exposes 26.
# ⚠️ An earlier version of this list held only the FIRST LINE of the doc's
# wrapped code block — 10 of 32 — which silently refused BR, JP, AU+NZ, KR and
# 18 others that Pinterest serves perfectly well. `+` is literal in paths.
REGIONS = [
    "US", "CA", "DE", "FR", "ES", "IT", "DE+AT+CH", "GB+IE",
    "IT+ES+PT+GR+MT", "PL+RO+HU+SK+CZ", "SE+DK+FI+NO", "NL+BE+LU",
    "AR", "BR", "CO", "MX", "MX+AR+CO+CL", "AU+NZ", "JP", "IN", "ID", "MY",
    "PH", "TH", "SA", "EG", "AE+SA+KW+QA+OM+BH+EG+IQ+DZ",
    "IL+NG+PK+ZA+TR+MA+IN", "CR+DO+EC+GT+PE",
    "CY+CZ+GR+HU+MT+PL+RO+SK", "TR", "KR",
]

# Regions with NO seasonal moments at all — a real answer, not a failure.
# Worth naming so a `moments` run against them is explained rather than empty.
REGIONS_WITHOUT_MOMENTS = {"JP", "IN"}

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
# ⚠️ 18-24 maps to TWO codes — that is why 7 UI options send 8 codes. Sending
# only `2` silently narrows the band and returns a different keyword set, with
# no error to notice. Values are LISTS for that reason.
AGE_CODES_KEYWORD = {"18-24": [2, 3], "25-34": [4], "35-44": [5], "45-49": [6],
                     "50-54": [7], "55-64": [8], "65+": [9]}
GENDER_CODES_KEYWORD = {"male": 0, "female": 1, "unspecified": 2}

# The detail page labels ENGAGEMENT as "All"; the shopping table labels the same
# value "Engagement". Map by meaning, never by label.
EVENT_LABELS = {
    "all": "ENGAGEMENT", "engagement": "ENGAGEMENT",
    "outbound clicks": "OUTBOUND_CLICK", "outbound_clicks": "OUTBOUND_CLICK",
    "pin saves": "SAVE", "saves": "SAVE",
}

# The 24 interest ids (doc #7 §4.2). Pinterest-internal and not derivable — the
# API never returns this list, so it is pinned here from a capture. Used by
# `l1interests` on keyword discovery AND by the moment x interest audience
# matrix (§3.18).
INTERESTS = {
    "925056443165": "Animals",            "909983286710": "Gardening",
    "918105274631": "Architecture",       "898620064290": "Health",
    "961238559656": "Art",                "935249274030": "Home Decor",
    "935541271955": "Beauty",             "924581335376": "Men's Fashion",
    "903733943146": "Children's Fashion", "920236059316": "Parenting",
    "902065567321": "Design",             "948192800438": "Quotes",
    "934876475639": "DIY and Crafts",     "919812032692": "Sport",
    "922134410098": "Education",          "908182459161": "Travel",
    "960887632144": "Electronics",        "918093243960": "Vehicles",
    "953061268473": "Entertainment",      "903260720461": "Wedding",
    "941870572865": "Event Planning",     "948967005229": "Women's Fashion",
    "913207199297": "Finance",            "918530398158": "Food and Drinks",
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

# The interest filter is the SAME persisted query. Two variables move TOGETHER:
#   terms     "<moment>"  ->  "<moment>:<interestId>"   (colon-joined)
#   category  "MOMENT"    ->  "MOMENT_INTEREST"
# Moving one without the other returns HTTP 200 with items:[] — a silent empty,
# not an error. Hence `moment_terms()` below, which cannot produce a half-move.
MOMENT_CATEGORIES = {"base": "MOMENT", "interest": "MOMENT_INTEREST"}


def moment_terms(slug, interest_id=None):
    """Build (terms, category) for the §3.18 query as one atomic pair.

    Verified: `halloween:961238559656` with `category:"MOMENT"` returns
    `items: []` — HTTP 200, no error. The two fields are a single decision, so
    this returns both or neither and no call site can desynchronise them.
    """
    if interest_id is None:
        return [slug], MOMENT_CATEGORIES["base"]
    key = str(interest_id)
    if key not in INTERESTS:
        raise InvalidParam(
            f"interest id {key!r} is not one of the 24 known ids (§4.2). An "
            f"unknown id returns HTTP 200 with items:[] — a silent empty that "
            f"is indistinguishable from 'no data'.")
    return [f"{slug}:{key}"], MOMENT_CATEGORIES["interest"]

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


def moment_slug(value):
    """Normalise a moment name to the wire form. Wrong form → 400.

    The rule, measured: **lowercase, spaces KEPT, apostrophes stripped** —
    `Father's Day` → `fathers day`. A customer typing the name as it appears in
    Pinterest's own UI would otherwise get a 400 they cannot diagnose.

    Non-ASCII slugs exist (`carnevale martedì grasso`, IT) and are left intact —
    the transport URL-encodes them; stripping accents would produce a slug that
    does not exist.
    """
    if value is None or not str(value).strip():
        raise InvalidParam("empty moment slug")
    return str(value).strip().lower().replace("'", "").replace("’", "")


def moments_for(region_code):
    """None means 'not known here — fetch moment/available for this region'.

    Slugs are REGION-SPECIFIC (25 globally, 13 in the US), so the only reliable
    source is that region's own list. This exists to answer the one question
    that has a static answer.
    """
    if region_code in REGIONS_WITHOUT_MOMENTS:
        return []
    return None


def region(value, *, capability=None):
    """Region, optionally checked against an endpoint's capability set."""
    if value not in REGIONS:
        raise InvalidParam(f"region={value!r} not in {REGIONS}")
    if capability == "top_products" and value not in TOP_PRODUCTS_REGIONS:
        raise InvalidParam(
            f"top_products returns data only for {sorted(TOP_PRODUCTS_REGIONS)}; "
            f"{value} answers 200 with an empty list.")
    if capability == "moments" and value in REGIONS_WITHOUT_MOMENTS:
        raise InvalidParam(
            f"{value} has no seasonal moments at all (measured) — a moments run "
            f"there returns nothing. That is a real answer, not an outage.")
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
