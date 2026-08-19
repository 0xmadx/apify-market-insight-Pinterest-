"""The Keyword Research traversal — discover, then enrich through the pipeline.

Three doors in, one pipeline out:

    discover  /top_trends_filtered/  presets 1-4 + filters   → up to 100 terms
    seed      /prefix_match/         a lowercase stem         → up to 10 terms
    exact     the operator's own term list

    then, for every term, batched:
    /metrics/       ONE call, group-normalised   → curve + 🔮 has_forecast
    /demographics/  ONE call                     → age + gender per term
    /related_terms/ one per term (no array form) → 5 siblings
    /term_images/   ONE POST                     → 9 pin images per term

Budget for N discovered terms with full enrichment: 2 + 2 + N + 1 requests —
the per-term cost is related_terms only, and it is optional.

The crystal-ball branch (ARCHITECTURE §2.3) is implemented here, not hinted at:
`has_forecast=False` is a final answer — no retry with different params (proven
inert), rank on growth + seasonality instead, and the record says so.
"""
from . import parsers, vocab
from .transport import TrendsAPIError


class KeywordScraper:
    def __init__(self, client, region="US", days=365, predicted_days=91,
                 log=print):
        self.client = client
        self.region = vocab.region(region)
        self.days = vocab.ceiling("days", days)
        self.predicted_days = vocab.ceiling("predicted_days", predicted_days)
        self.log = log

    # -------------------------------------------------------------- doors in

    def discover(self, preset=3, num_terms=100, l1interests=None, moments=None,
                 age_buckets=None, genders=None, keywords_to_include=None):
        """/top_trends_filtered/. Presets: 1 top monthly · 2 top yearly ·
        3 growing · 4 seasonal. `lookbackWindow`/`rankingMethod` are inert
        (verified) and never sent."""
        if preset not in (1, 2, 3, 4):
            raise vocab.InvalidParam(f"trendsPreset={preset} — only 1-4 exist")
        params = {
            "country": self.region,
            "endDate": vocab.end_date(self.client.bootstrap()),
            "trendsPreset": preset,
            "numTermsToReturn": vocab.ceiling("num_terms", num_terms),
            "shouldMock": "false",
        }
        if l1interests:
            params["l1interests"] = ",".join(str(i) for i in l1interests)
        if moments:
            params["moments"] = ",".join(moments)
        if age_buckets:
            params["ageBuckets"] = ",".join(
                str(vocab.AGE_CODES_KEYWORD[a]) if a in vocab.AGE_CODES_KEYWORD
                else str(a) for a in age_buckets)
        if genders:
            params["gender"] = ",".join(
                str(vocab.GENDER_CODES_KEYWORD[g]) if g in vocab.GENDER_CODES_KEYWORD
                else str(g) for g in genders)
        if keywords_to_include:
            # OR logic, substring match, lowercase only — uppercase is a
            # silent empty.
            params["keywordsToInclude"] = ",".join(
                vocab.keyword(k) for k in keywords_to_include)
        return parsers.parse_discover(
            self.client.style_b("/top_trends_filtered/", params))

    def seed(self, stem):
        """/prefix_match/ — the discovery primitive. Whole keyword space."""
        return parsers.parse_prefix_match(self.client.style_b(
            "/prefix_match/", {"query": vocab.keyword(stem),
                               "country": self.region}))

    # ------------------------------------------------------------- the walk

    def run(self, mode="discover", terms=None, include_related=True,
            include_images=True, max_terms=None, **discover_kwargs):
        """Yield one enriched record per keyword."""
        end_date = self.client.bootstrap()

        discovery_rows = {}
        if mode == "discover":
            found = self.discover(**discover_kwargs)
            discovery_rows = {r["term"]: r for r in found["terms"]}
            term_list = list(discovery_rows)
        elif mode == "seed":
            stem = (terms or [""])[0]
            term_list = [r["term"] for r in self.seed(stem)]
        elif mode == "exact":
            term_list = [vocab.keyword(t) for t in (terms or [])]
        else:
            raise vocab.InvalidParam(f"mode={mode!r} — discover | seed | exact")

        if max_terms:
            term_list = term_list[:max_terms]
        if not term_list:
            self.log("[keywords] nothing to enrich (empty discovery is a real "
                     "answer, not an error)")
            return

        metrics = self._metrics(term_list, end_date)
        demographics = self._demographics(term_list, end_date)
        images = self._images(term_list) if include_images else {}
        dropped = parsers.missing_terms(term_list, metrics)
        if dropped:
            self.log(f"[keywords] {len(dropped)} term(s) returned no metrics "
                     f"(dropped by the API, absence != zero): {dropped}")

        for term in term_list:
            metric = metrics.get(term)
            has_forecast = (metric or {}).get("has_forecast")
            related = (self._related(term, end_date)
                       if include_related and metric else [])

            yield {
                "term": term,
                "region": self.region,

                # discovery-time signals (only in discover mode)
                "seasonality_score": discovery_rows.get(term, {}).get("seasonality_score"),
                "search_count": discovery_rows.get(term, {}).get("search_count"),

                # the pipeline
                "status": "ok" if metric else "no_data",
                "has_forecast": has_forecast,
                # The branch, stated on the record: False is FINAL — the correct
                # move is ranking on growth+seasonality, and retrying with other
                # params is proven pointless.
                "forecast_note": (
                    None if has_forecast
                    else "no forecast exists for this term+region; rank on "
                         "growth and seasonality — do not retry"),
                "wow_change": (metric or {}).get("wow_change"),
                "mom_change": (metric or {}).get("mom_change"),
                "yoy_change": (metric or {}).get("yoy_change"),
                "series": (metric or {}).get("series"),
                "age_distribution": (demographics.get(term) or {}).get("age_distribution"),
                "gender_distribution": (demographics.get(term) or {}).get("gender_distribution"),
                "related": related,
                "pin_images": images.get(term),

                "_meta": {
                    "end_date": end_date,
                    "mode": mode,
                    "basis": "measured" if metric else None,
                    # ONE metrics call, group-normalised → every series in this
                    # run shares a scale and IS comparable within it.
                    "normalization_scope": f"metrics:{self.region}:{end_date}:"
                                           f"{len(term_list)}terms",
                    "forecast_region_note": "forecasts observed US-only",
                },
            }

    # ------------------------------------------------------------ the calls

    def _metrics(self, term_list, end_date):
        return parsers.parse_keyword_metrics(self.client.style_b("/metrics/", {
            "terms": ",".join(term_list),
            "country": self.region,
            "end_date": end_date,
            "days": self.days,
            "aggregation": 2,                      # the only valid value
            "predicted_days": self.predicted_days,
            "normalize_against_group": "true",     # ALWAYS when >1 term
            "shouldMock": "false",
        }))

    def _demographics(self, term_list, end_date):
        try:
            return parsers.parse_keyword_demographics(self.client.style_b(
                "/demographics/", {"terms": ",".join(term_list),
                                   "country": self.region,
                                   "end_date": end_date, "days": self.days}))
        except TrendsAPIError as exc:
            self.log(f"[keywords] demographics failed ({exc}) — records will "
                     f"carry null audiences, not zeros")
            return {}

    def _related(self, term, end_date):
        try:
            return parsers.parse_related_terms(self.client.style_b(
                "/related_terms/", {"requestTerm": term, "country": self.region,
                                    "endDate": end_date, "aggregation": 2,
                                    "lookback": self.days,
                                    "shouldMock": "false"}))
        except TrendsAPIError:
            return []

    def _images(self, term_list):
        try:
            return parsers.parse_term_images(self.client.style_b(
                "/term_images/", method="POST",
                json_body={"terms": term_list, "country": self.region,
                           "limit": 9, "requestImageSize": "236x",
                           "cacheTtlInSeconds": 86400}))
        except TrendsAPIError:
            return {}
