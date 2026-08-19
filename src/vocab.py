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
# The SAME customer input maps to two different schemes. Keyword endpoints take
# numeric codes (above); shopping endpoints take these enums. One shared input
# ("25-34") must never leak the wrong form into the wrong endpoint — that is
# scenario C6, and these two maps are why it cannot.
AGE_ENUMS_SHOPPING = {"18-24": "AGE_18_24", "25-34": "AGE_25_34",
                      "35-44": "AGE_35_44", "45-49": "AGE_45_49",
                      "50-54": "AGE_50_54", "55-64": "AGE_55_64",
                      "65+": "AGE_65_PLUS", "all": "AGE_ALL"}
GENDER_ENUMS_SHOPPING = {"male": "MALE", "female": "FEMALE",
                         "unspecified": "UNSPECIFIED"}


def age_buckets_shopping(values):
    """Customer age bands -> the shopping enum form. Unknown -> refused."""
    out = []
    for v in values or []:
        key = str(v).strip().lower()
        if key in AGE_ENUMS_SHOPPING:
            out.append(AGE_ENUMS_SHOPPING[key])
        elif str(v).upper() in set(AGE_BUCKETS):
            out.append(str(v).upper())
        else:
            raise InvalidParam(
                f"age band {v!r} unknown — use one of {sorted(AGE_ENUMS_SHOPPING)}")
    return out


def genders_shopping(values):
    """Customer genders -> the shopping enum form. Unknown -> refused."""
    out = []
    for v in values or []:
        key = str(v).strip().lower()
        if key in GENDER_ENUMS_SHOPPING:
            out.append(GENDER_ENUMS_SHOPPING[key])
        elif str(v).upper() in set(GENDERS):
            out.append(str(v).upper())
        else:
            raise InvalidParam(
                f"gender {v!r} unknown — use male, female or unspecified")
    return out


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


# How far back each endpoint will actually look. These DIFFER, which is the
# whole reason this is a function and not one constant:
#
#   top_trends_filtered   ~365 days. ⚠️ MEASURED 2026-08-19, and it CORRECTS
#                         doc #7 §3.12, which calls this "far-past" and "the
#                         seasonal time machine". Binary-searched from a
#                         2026-08-14 baseline: -365d returns terms, -400d
#                         returns **HTTP 200 with an empty list**. Not an
#                         error — a silent empty, which reads as "nothing was
#                         trending that week". Exactly the failure this
#                         codebase exists to prevent, so it is refused here.
#   /metrics/             future → 400, older than ~1 year → 400.
#                         Measured: 2026-01-01 ok, 2025-08-01 rejected.
#   moment/metrics        MEASURED 2026-08-19 from the same baseline: -730d
#                         answers with a full series, -800d → HTTP 500. So 730
#                         is real here — now measured, not inherited.
#   shopping top/{region} MEASURED 2026-08-19 — and the old 730 was WRONG. It
#                         was never probed; it was copied from the `days` /
#                         `lookback_days` ceiling, which is a *lookback window*
#                         and a different parameter entirely. Binary-searched:
#                         -257d (2025-11-30) returns rows, -260d (2025-11-27)
#                         returns **HTTP 200 with an empty list**. The old cap
#                         waved ~470 days of silent empties straight through,
#                         and an empty shopping run reads as "nothing is
#                         trending in this vertical" — a plausible wrong
#                         answer, the one failure mode this project exists to
#                         stop.
#
# ⚠️ The shopping boundary is RAGGED, not a clean floor: 2025-11-28 returns
# rows, 2025-11-29 returns none, 2025-11-30 returns rows again. Well inside the
# window there are no gaps at all (21 consecutive days across 2026-05 every one
# answered), so the raggedness is an edge effect. The cap is the last
# *reliable* day, not the last day that happened to answer.
#
# ⚠️ UNDETERMINED: whether shopping's floor is a fixed data-start (~2025-11-28,
# so the usable window grows a day per day) or a rolling ~257-day window. One
# observation cannot separate them. The day-count cap is deliberate: if the
# floor is fixed, this drifts toward refusing dates that would have worked — a
# visible refusal carrying a reason. The other choice drifts toward silent
# empties. Re-measure with probes/history_caps.py.
#
# Style B returns its 400s with an EMPTY BODY, so a customer who guesses gets no
# reason at all. Hence refusing here, with the endpoint named.
HISTORY_LIMIT_DAYS = {"discover": 365, "metrics": 365, "shopping": 257,
                      "moments": 730}

# Which endpoints can forecast FORWARD from a PAST end_date. Measured
# 2026-08-19, and this one was a live bug rather than a missing feature: with
# `endDate` set to anything older than the current week, `/metrics/` and
# shopping's `product_categories/metrics` answer **HTTP 500** whenever
# `predicted_days > 0`. Style B carries no error body, so it surfaced as a bare
# 500 with nothing to explain it.
#
#   /metrics/ (keywords)   -0d..-7d ok with predicted_days=91
#                          -14d and older -> 500 for ANY predicted_days > 0
#                          -120d with predicted_days=0 -> fine
#   shopping metrics       -120d + predicted_days=28 -> 500
#                          -120d + predicted_days=0  -> fine
#   moment/metrics         -120d + predicted_days=91 -> FINE (26 points)
#                          Style A, a different service; it forecasts happily
#                          from the past, so moments needs no special case.
#
# It is a sensible thing for Pinterest to refuse — a "forecast" running forward
# from a historical date is predicting a period that has already happened. We
# drop the forecast instead of the whole request, and the record says we did.
FORECAST_FROM_PAST = {"discover": False, "metrics": False,
                      "shopping": False, "moments": True}

# How close to the newest settled date still counts as "now" for forecasting.
# Measured: -7d forecasts, -14d does not. Pinterest works in whole weeks.
FORECAST_CURRENT_WEEK_DAYS = 7


def forecast_days(requested, end_date, latest, *, endpoint):
    """Zero out predicted_days when this endpoint cannot forecast from `end_date`.

    Returns (days, note). `note` is None when nothing was changed, and
    otherwise explains the drop in the customer's own record — a forecast that
    silently vanished would read as "Pinterest has no forecast for this term",
    which is a different and wrong claim (absent is not zero).
    """
    import datetime as _dt

    if not requested or FORECAST_FROM_PAST.get(endpoint, True):
        return requested, None
    try:
        asked = _dt.date.fromisoformat(str(end_date))
        newest = _dt.date.fromisoformat(str(latest))
    except (ValueError, TypeError):
        return requested, None
    back = (newest - asked).days
    if back <= FORECAST_CURRENT_WEEK_DAYS:
        return requested, None
    return 0, (
        f"forecast not requested: endDate is {back} days before Pinterest's "
        f"newest settled date ({newest}), and this endpoint returns HTTP 500 "
        f"for any forecast running forward from a past date. The historical "
        f"series is unaffected. Omit endDate to get forecasts.")


# Does the endpoint echo back the date it actually used? Only discovery does —
# it is how the week-snapping below was found. Shopping and moment/metrics
# return NO date anywhere in the response (measured 2026-08-19), so for those
# the only date we can honestly report is the one we asked for. Every record
# carries `_meta.end_date_basis` saying which it got.
ECHOES_END_DATE = {"discover": True, "metrics": True,
                   "shopping": False, "moments": False}


def history_date(value, latest, *, endpoint="discover"):
    """Validate a customer-supplied endDate against one endpoint's real window.

    `latest` is /latest_available_date/ — the newest settled date. Anything
    after it is a future date to Pinterest even if it is in your past.
    """
    import datetime as _dt

    if not value:
        return latest
    try:
        asked = _dt.date.fromisoformat(str(value))
    except ValueError:
        raise InvalidParam(
            f"endDate={value!r} must be YYYY-MM-DD") from None
    try:
        newest = _dt.date.fromisoformat(str(latest))
    except (ValueError, TypeError):
        return str(value)

    if asked > newest:
        raise InvalidParam(
            f"endDate={asked} is after Pinterest's newest settled date "
            f"({newest}) — a future date returns 400. Their data lags a few "
            f"days; omit endDate to get the newest.")

    cap = HISTORY_LIMIT_DAYS.get(endpoint)
    if cap is not None and (newest - asked).days > cap:
        raise InvalidParam(
            f"endDate={asked} is {(newest - asked).days} days back; the "
            f"{endpoint} endpoint only carries about {cap} days "
            f"(back to ~{newest - _dt.timedelta(days=cap)}). Past that it "
            f"answers with HTTP 200 and an EMPTY LIST rather than an error — "
            f"which reads as 'nothing was trending', so it is refused here "
            f"instead of handing you a silent nothing. Measured per endpoint; "
            f"they differ a lot (shopping "
            f"{HISTORY_LIMIT_DAYS['shopping']}d vs moments "
            f"{HISTORY_LIMIT_DAYS['moments']}d).")
    return str(asked)


# ⚠️ Pinterest SNAPS a requested date to its own week boundary and reports the
# snapped value back. Measured: asked 2026-02-15 → answered 2026-02-13; asked
# 2025-12-01 → answered 2025-11-28. So the date in the data is not necessarily
# the date you asked for, and records carry both.
