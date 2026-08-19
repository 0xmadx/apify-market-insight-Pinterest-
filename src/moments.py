"""The Seasonal Moments traversal — the API's only daily-resolution data.

    moment/available/{region}     → phases + peak dates (1 call, all moments)
        │ gate: phase ∈ {rising, approaching}   ← the D2 decision node
    moment/metrics/{region}       → demand curve (DAILY!) + forecast + interest split
    /top_trends_filtered/?moments= → the moment's keywords
    /demographics/ on those terms  → aggregated audience — DERIVED, and says so

Audience is MEASURED (§3.18 captured 2026-08-19): the persisted GraphQL query
behind Pinterest's own "Who's driving this moment" chart. The keyword-aggregate
workaround remains as the FALLBACK and is labelled `derived` when it runs, so
the two are never mixed or mislabelled.

⭐ The same query also answers **moment x interest** — and Pinterest's dropdown
of ~7 "relevant" interests per moment is UI curation, not a data limit. Any
moment x any interest is queryable (halloween x Food and Drinks is not offered
and returns 18-24 = 0.19 against 0.43 unfiltered), so `interest_ids=` opens a
matrix no competitor reading the UI can see. One request per cell, off by
default.

Budget: 1 + actionable×3 requests. Cooled/frozen moments cost zero extra calls
— they are still emitted (phase + dates are real data) but never drilled.
"""
from . import parsers, vocab
from .transport import StaleQueryHash, TrendsAPIError

ACTIONABLE = ("rising", "approaching")


class MomentScraper:
    def __init__(self, client, region="US", aggregation="daily",
                 lookback_days=365, predicted_days=91, keywords_per_moment=25,
                 interest_ids=None, log=print):
        self.client = client
        self.region = vocab.region(region)
        if aggregation not in ("daily", "weekly", "monthly"):
            raise vocab.InvalidParam(
                f"aggregation_level={aggregation!r} — daily|weekly|monthly "
                f"(hourly → 400). This endpoint is the API's ONLY daily source.")
        # "Predicted days must divide evenly into the aggregation unit" — the
        # measured case is monthly+91 → 400. Weekly 91 (=13 weeks) works.
        # The month heuristic is 30 days; 91 % 30 = 1, refused.
        if aggregation == "monthly" and predicted_days % 30 != 0:
            raise vocab.InvalidParam(
                f"monthly aggregation with predicted_days={predicted_days} → 400 "
                f"(measured on 91: predicted days must divide evenly into the "
                f"aggregation unit; use a multiple of 30, or weekly)")
        if aggregation == "weekly" and predicted_days % 7 != 0:
            raise vocab.InvalidParam(
                f"weekly aggregation with predicted_days={predicted_days}: "
                f"points are predicted_days/7, so use a multiple of 7 "
                f"(91 = 13 weeks is the measured maximum)")
        self.aggregation = aggregation
        self.lookback_days = vocab.ceiling("lookback_days", lookback_days)
        self.predicted_days = vocab.ceiling("predicted_days", predicted_days)
        self.keywords_per_moment = vocab.ceiling("num_terms", keywords_per_moment)
        # The moment x interest matrix. Off by default: one request per
        # cell, and 13 moments x 24 interests is 312 calls on a shared
        # session.
        self.interest_ids = [str(i) for i in (interest_ids or [])]
        self.log = log

    # ------------------------------------------------------------- the walk

    def run(self, phases=ACTIONABLE, drill=True, with_audience=True):
        end_date = self.client.bootstrap()

        moments = parsers.parse_moments_list(self.client.style_a(
            f"/ads/v4/trends/moment/available/{self.region}",
            kind="trends"))
        if not moments:
            # JP and IN genuinely have zero moments — a real answer.
            self.log(f"[moments] {self.region}: no moments for this region "
                     f"(real answer, not an error)")
            return

        wanted = set(phases or ACTIONABLE)
        self.log(f"[moments] {self.region}: {len(moments)} moments, drilling "
                 f"those in {sorted(wanted)}")

        for moment in moments:
            drilled = drill and moment["phase"] in wanted
            detail = self._metrics(moment["slug"], end_date) if drilled else None
            keywords = self._keywords(moment["slug"], end_date) if drilled else None
            audience, audience_basis, audience_note = None, None, None
            if drilled and with_audience:
                audience, audience_basis, audience_note = self._audience(
                    moment["slug"], keywords, end_date)

            yield {
                "slug": moment["slug"],
                "region": self.region,
                "phase": moment["phase"],
                "actionable": moment["actionable"],
                "next_peak": moment["next_peak"],
                "last_peak": moment["last_peak"],
                "next_occurrence_at": moment["next_occurrence_at"],

                "drilled": drilled,
                "series": (detail or {}).get("series"),
                "series_points": len((detail or {}).get("series") or []),
                "interest_split": (detail or {}).get("interest_split"),
                "keywords": keywords,
                "audience": audience,
                "audience_by_interest": (
                    self._interest_audiences(moment["slug"], end_date,
                                             self.interest_ids)
                    if drilled and self.interest_ids else None),

                "_meta": {
                    "end_date": end_date,
                    "aggregation": self.aggregation if drilled else None,
                    "series_basis": "measured" if detail else None,
                    # `measured` when the GraphQL query answered — the same
                    # numbers Pinterest's own chart draws. `derived` when it did
                    # not and the keyword-aggregate fallback ran. The label is
                    # never assumed; it reports what actually happened (D4).
                    "audience_basis": audience_basis,
                    "audience_note": audience_note,
                    "normalization_scope":
                        f"moment:{self.region}:{moment['slug']}:{end_date}",
                },
            }

    # ------------------------------------------------------------ the calls

    def _metrics(self, slug, end_date):
        data = parsers.parse_moment_metrics(self.client.style_a(
            f"/ads/v4/trends/moment/metrics/{self.region}",
            {"moments": [slug], "end_date": end_date,
             "aggregation_level": self.aggregation,
             "lookback_days": self.lookback_days,
             "predicted_days": self.predicted_days,
             "interest_limit": vocab.ceiling("interest_limit", 6),
             "normalize_against_group": False}, kind="trends"))
        return data.get(slug)

    def _keywords(self, slug, end_date):
        try:
            found = parsers.parse_discover(self.client.style_b(
                "/top_trends_filtered/",
                {"country": self.region, "endDate": end_date,
                 "moments": slug, "trendsPreset": 1,
                 "numTermsToReturn": self.keywords_per_moment,
                 "shouldMock": "false"}, kind="search"))
            return found["terms"]
        except TrendsAPIError as exc:
            self.log(f"[moments] keywords for {slug} failed: {exc}")
            return None

    def _audience(self, slug, keywords, end_date):
        """Measured first, derived second. Returns (audience, basis, note).

        The GraphQL query is what Pinterest's own "Who's driving this moment"
        chart uses, so it is the real answer. The keyword aggregate is a decent
        approximation and stays as the fallback — but the two are never mixed
        and never mislabelled.
        """
        try:
            measured = self._graphql_audience(slug, end_date)
            if measured:
                return measured, "measured", None
        except StaleQueryHash as exc:
            # Loud, and specific: this is a maintenance task, not missing data.
            self.log(f"[moments] ⚠️ persisted queryHash is stale — re-capture it "
                     f"(probes/captures/README.md). Falling back to derived. {exc}")
        except TrendsAPIError as exc:
            self.log(f"[moments] measured audience unavailable for {slug} "
                     f"({exc}); falling back to derived")

        if not keywords:
            return None, None, None
        derived = self._derived_audience([k["term"] for k in keywords], end_date)
        if not derived:
            return None, None, None
        return derived, "derived", (
            "aggregated from the audiences of this moment's top keywords — "
            "Pinterest's own figures were not reachable on this run "
            "(doc #7 §3.18)")

    def _graphql_audience(self, slug, end_date, interest_id=None):
        """One S3.18 query. None when the response carries no items.

        `items: []` is HTTP 200 with no error, so it is indistinguishable from
        a real empty at the transport layer. Surfaced as None; the caller
        decides what it means.
        """
        terms, category = vocab.moment_terms(slug, interest_id)
        data = self.client.graphql(
            query_hash=vocab.MOMENT_DEMOGRAPHICS["query_hash"],
            variables={"terms": terms, "region": self.region,
                       "endDate": end_date, "event": None,
                       "category": category},
            operation_name=vocab.MOMENT_DEMOGRAPHICS["operation"],
            handler=vocab.MOMENT_DEMOGRAPHICS["handler"],
            kind="trends")
        parsed = parsers.parse_moment_demographics(data)
        return parsed if parsed and parsed.get("age_distribution") else None

    def _interest_audiences(self, slug, end_date, interest_ids):
        """The moment x interest matrix - audiences the UI cannot show you.

        Pinterest's dropdown offers ~7 "relevant" interests per moment, and that
        list is UI CURATION, not a data constraint: halloween x Food and Drinks
        is not offered and still returns a full distribution (18-24 = 0.19
        against 0.43 unfiltered). Any moment x any interest is queryable, and
        the un-offered cells are precisely the ones a competitor reading the UI
        will never have.

        Cost is ONE REQUEST PER CELL - capped by the caller, never automatic.

        Pinterest may curate the list because the un-offered pairs are thin, so
        cells carry `offered_in_ui: None` (unknown) rather than a claim that
        they are as trustworthy as the surfaced ones.
        """
        out = {}
        for interest_id in interest_ids:
            try:
                audience = self._graphql_audience(slug, end_date, interest_id)
            except vocab.InvalidParam as exc:
                self.log(f"[moments] {exc}")
                continue
            except TrendsAPIError as exc:
                self.log(f"[moments] {slug} x {interest_id}: {exc}")
                continue
            if not audience:
                continue          # 200 + items:[] - a real answer for this pair
            out[str(interest_id)] = {
                "interest_id": str(interest_id),
                "interest_name": vocab.INTERESTS.get(str(interest_id)),
                "age_distribution": audience["age_distribution"],
                "gender_distribution": audience["gender_distribution"],
                "basis": "measured",
                "offered_in_ui": None,
            }
        return out or None

    def _derived_audience(self, terms, end_date):
        """Mean the per-term distributions. Terms the API drops contribute
        nothing (absent, not zero); if every term drops, there is no audience."""
        try:
            per_term = parsers.parse_keyword_demographics(self.client.style_b(
                "/demographics/", {"terms": ",".join(terms[:25]),
                                   "country": self.region,
                                   "end_date": end_date, "days": 90},
                kind="detail"))
        except TrendsAPIError:
            return None
        if not per_term:
            return None

        def mean(field):
            buckets = {}
            counted = 0
            for node in per_term.values():
                dist = node.get(field)
                if not isinstance(dist, dict):
                    continue
                counted += 1
                for bucket, share in dist.items():
                    if share is not None:
                        buckets[bucket] = buckets.get(bucket, 0.0) + share
            if not counted:
                return None
            return {b: round(total / counted, 4) for b, total in buckets.items()}

        return {
            "age_distribution": mean("age_distribution"),
            "gender_distribution": mean("gender_distribution"),
            "from_terms": len(per_term),
        }
