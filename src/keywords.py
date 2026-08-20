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
                 end_date=None, log=print):
        self.client = client
        self.region = vocab.region(region)
        self.days = vocab.ceiling("days", days)
        # A customer-chosen point in time, or None for "the newest settled
        # data". Discovery reaches far back; /metrics/ does not, so the two are
        # validated separately at the point of use.
        self.requested_end_date = end_date
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
            # Discovery is the seasonal time machine — far-past dates work
            # here and nowhere else.
            "endDate": vocab.history_date(self.requested_end_date,
                                          self.client.bootstrap(),
                                          endpoint="discover"),
            "trendsPreset": preset,
            "numTermsToReturn": vocab.ceiling("num_terms", num_terms),
            "shouldMock": "false",
        }
        if l1interests:
            params["l1interests"] = ",".join(str(i) for i in l1interests)
        if moments:
            # Normalised, not passed through: "Father's Day" is a 400 on the
            # wire and the customer types it the way Pinterest displays it.
            params["moments"] = ",".join(vocab.moment_slug(m) for m in moments)
        if age_buckets:
            # 18-24 expands to TWO codes (2,3). Flattening a list-valued map is
            # the whole point — sending only `2` silently narrows the band.
            codes = []
            for a in age_buckets:
                mapped = vocab.AGE_CODES_KEYWORD.get(a)
                codes.extend(mapped if mapped else [a])
            params["ageBuckets"] = ",".join(str(c) for c in codes)
        if genders:
            params["gender"] = ",".join(
                str(vocab.GENDER_CODES_KEYWORD.get(g, g)) for g in genders)
        if keywords_to_include:
            # OR logic, substring match, lowercase only — uppercase is a
            # silent empty.
            params["keywordsToInclude"] = ",".join(
                vocab.keyword(k) for k in keywords_to_include)
        return parsers.parse_discover(
            self.client.style_b("/top_trends_filtered/", params, kind="search"))

    def seed(self, stem):
        """/prefix_match/ — the discovery primitive. Whole keyword space."""
        return parsers.parse_prefix_match(self.client.style_b(
            "/prefix_match/", {"query": vocab.keyword(stem),
                               "country": self.region}, kind="search"))

    # ------------------------------------------------------------- the walk

    def run(self, mode="discover", terms=None, include_related=True,
            include_images=True, max_terms=None, **discover_kwargs):
        """Yield one enriched record per keyword."""
        # The enrichment endpoints (/metrics/, /demographics/) reject dates
        # more than ~1 year back, unlike discovery. Validated here so a customer
        # asking for 2019 gets a reason instead of an empty 400 body.
        end_date = vocab.history_date(self.requested_end_date,
                                      self.client.bootstrap(),
                                      endpoint="metrics")

        discovery_rows = {}
        # Pinterest snaps a requested date to its week boundary and reports the
        # snapped value. Carry what it ACTUALLY used, not only what was asked.
        effective_end_date = end_date
        if mode == "discover":
            found = self.discover(**discover_kwargs)
            effective_end_date = found.get("end_date") or end_date
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
                    # What Pinterest used — it snaps to its own week boundary.
                    # Discovery is the ONLY endpoint that echoes this back, so
                    # it is the only one whose basis is `echoed` rather than
                    # `requested`. Asked 2026-02-15 -> answered 2026-02-13.
                    "end_date": effective_end_date,
                    "end_date_requested": self.requested_end_date,
                    "end_date_basis": "echoed",
                    # Set when the forecast was dropped because endDate is in
                    # the past. Absent forecast != "Pinterest has no forecast".
                    "forecast_suppressed": getattr(self, "_forecast_note", None),
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
        """⚠️ NEVER cache this per term. The numbers are GROUP-RELATIVE.

        `normalize_against_group=true` means a term's counts are scaled against
        the other terms in the SAME request. Measured 2026-08-19, `eye makeup`
        on identical dates and windows:

            beside 'eyelashes'                        -> [30, 32, 36]
            beside 'nails','hairstyles','wallpaper'   -> [ 1,  1,  2]

        Same term, same period, thirty-fold difference — because the company it
        keeps sets the scale. Caching one term's series and serving it next to
        another group's would produce numbers that look ordinary and are not
        comparable, which is this codebase's defining failure mode.

        `_meta.normalization_scope` carries the term COUNT for exactly this
        reason. /demographics/ is per-term percentages and IS safe to cache
        that way; this is not.
        """
        # A forecast running forward from a PAST end_date is HTTP 500 on this
        # endpoint (measured: -7d forecasts, -14d does not, at any
        # predicted_days > 0). Style B sends no error body, so it arrived as a
        # bare 500 — and it broke `endDate` for keywords entirely. Drop the
        # forecast, keep the history, and tell the customer in the record.
        predicted, self._forecast_note = vocab.forecast_days(
            self.predicted_days, end_date, self.client.bootstrap(),
            endpoint="metrics")
        return parsers.parse_keyword_metrics(self.client.style_b("/metrics/", {
            "terms": ",".join(term_list),
            "country": self.region,
            "end_date": end_date,
            "days": self.days,
            "aggregation": 2,                      # the only valid value
            "predicted_days": predicted,
            "normalize_against_group": "true",     # ALWAYS when >1 term
            "shouldMock": "false",
        }, kind="search"))

    def _demographics(self, term_list, end_date):
        """Per-TERM cached, so customers with overlapping keywords share work.

        MEASURED SAFE 2026-08-19: `eye makeup` returns the same distribution
        whether it is requested beside two peers or beside `nails`,
        `hairstyles` and `wallpaper` — these are per-term percentages, not
        volumes relative to the request. So a term fetched for one customer can
        be handed to the next.

        ⚠️ /metrics/ is NOT safe this way and must never copy this — see the
        warning on `_metrics`.

        Only the terms nobody has fetched yet reach Pinterest; the rest come
        from cache. Two customers sharing one keyword out of ten now cost nine
        lookups, not twenty.
        """
        cache = getattr(self.client, "cache", None)
        group = {"region": self.region, "end_date": end_date,
                 "days": self.days}
        known, missing = {}, list(term_list)
        if cache is not None:
            known = {}
            missing = []
            for term in term_list:
                hit = cache.get_item("detail", group, term)
                if hit is None:
                    missing.append(term)
                else:
                    known[term] = hit
            if not missing:
                return known                      # nothing to ask Pinterest

        try:
            fetched = parsers.parse_keyword_demographics(self.client.style_b(
                "/demographics/", {"terms": ",".join(missing),
                                   "country": self.region,
                                   "end_date": end_date, "days": self.days},
                                  kind="detail"))
            if cache is not None:
                for term, value in fetched.items():
                    cache.put_item("detail", group, term, value)
            known.update(fetched)
            return known
        except Exception:
            raise
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
                                    "shouldMock": "false"},
                kind="detail"))
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
