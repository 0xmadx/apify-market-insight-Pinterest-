"""The Seasonal Moments traversal — the API's only daily-resolution data.

    moment/available/{region}     → phases + peak dates (1 call, all moments)
        │ gate: phase ∈ {rising, approaching}   ← the D2 decision node
    moment/metrics/{region}       → demand curve (DAILY!) + forecast + interest split
    /top_trends_filtered/?moments= → the moment's keywords
    /demographics/ on those terms  → aggregated audience — DERIVED, and says so

The audience workaround is the honest substitute for §3.18 (moment Age/Gender
lives behind a persisted GraphQL query nobody has captured yet): aggregate the
per-keyword audiences of the moment's top terms. It will not match Pinterest's
own chart exactly, so it is labelled `derived` and the label is asserted in
tests (D4). If the GraphQL capture ever lands, `measured` replaces it and the
schema does not change.

Budget: 1 + actionable×3 requests. Cooled/frozen moments cost zero extra calls
— they are still emitted (phase + dates are real data) but never drilled.
"""
from . import parsers, vocab
from .transport import TrendsAPIError

ACTIONABLE = ("rising", "approaching")


class MomentScraper:
    def __init__(self, client, region="US", aggregation="daily",
                 lookback_days=365, predicted_days=91, keywords_per_moment=25,
                 log=print):
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
        self.log = log

    # ------------------------------------------------------------- the walk

    def run(self, phases=ACTIONABLE, drill=True, with_audience=True):
        end_date = self.client.bootstrap()

        moments = parsers.parse_moments_list(self.client.style_a(
            f"/ads/v4/trends/moment/available/{self.region}"))
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
            audience = None
            if drilled and with_audience and keywords:
                audience = self._derived_audience(
                    [k["term"] for k in keywords], end_date)

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

                "_meta": {
                    "end_date": end_date,
                    "aggregation": self.aggregation if drilled else None,
                    "series_basis": "measured" if detail else None,
                    # The one derived number in the whole record, labelled as
                    # such at the point it appears (D4).
                    "audience_basis": "derived" if audience else None,
                    "audience_note": (
                        "aggregated from the audiences of this moment's top "
                        "keywords — Pinterest's own moment chart is not "
                        "reachable via REST (doc #7 §3.18)"
                        if audience else None),
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
             "normalize_against_group": False}))
        return data.get(slug)

    def _keywords(self, slug, end_date):
        try:
            found = parsers.parse_discover(self.client.style_b(
                "/top_trends_filtered/",
                {"country": self.region, "endDate": end_date,
                 "moments": slug, "trendsPreset": 1,
                 "numTermsToReturn": self.keywords_per_moment,
                 "shouldMock": "false"}))
            return found["terms"]
        except TrendsAPIError as exc:
            self.log(f"[moments] keywords for {slug} failed: {exc}")
            return None

    def _derived_audience(self, terms, end_date):
        """Mean the per-term distributions. Terms the API drops contribute
        nothing (absent, not zero); if every term drops, there is no audience."""
        try:
            per_term = parsers.parse_keyword_demographics(self.client.style_b(
                "/demographics/", {"terms": ",".join(terms[:25]),
                                   "country": self.region,
                                   "end_date": end_date, "days": 90}))
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
