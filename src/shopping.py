"""The Shopping Trends traversal — the walk, not the endpoints.

What a customer buys here is a record that exists on no Pinterest page: a
trending category with its real name, its audience, the search queries people
use to reach it, and the actual products they click — assembled from four calls
Pinterest never joins for you.

    top/ (one vertical)          "category 1311 is trending in Beauty"
      └► taxonomy join           → "Mascaras", Beauty > Makeup > Mascaras
      └► metrics/                → the Performance chart series
      └► demographics/           → audience + 17 search queries + headline growth
      └► top_products            → 33 shoppable pins with merchants

Request budget is the design constraint. A naive implementation calls
metrics/demographics/top_products once per category; this batches what batches
(metrics and demographics both take arrays) and drills deeply only into
`drill_top_n`. For one vertical with drill_top_n=3 the cost is:

    1 taxonomy (cached across the whole run and all verticals)
  + 1 top/
  + 1 metrics/     (array — all categories at once)
  + 1 demographics/(array — the drilled categories at once)
  + N top_products (no array param; one per drilled category)
  = 4 + N requests, not 1 + 4N.
"""
from . import parsers, vocab
from .transport import TrendsAPIError


class ShoppingScraper:
    """Traverses the shopping graph for one region.

    `client` is a TrendsClient; `cache` is optional (src.cache.ResponseCache)
    and is used only for the taxonomy, which is 383 rows that change rarely and
    is needed by every vertical and every run.
    """

    def __init__(self, client, region="US", event="OUTBOUND_CLICK",
                 drill_top_n=3, chart_days=730, predicted_days=28,
                 enrich_top_n=0, end_date=None, age_buckets=None, genders=None,
                 ranking_method="GROWTH", order_by="RELATIVE_VOLUME",
                 log=print):
        self.client = client
        self.region = vocab.region(region)
        self.event = vocab.event(event)
        self.drill_top_n = drill_top_n
        # 730 by default, not Pinterest's own 180: one year shows each season
        # exactly once, which cannot tell a seasonal pattern from a one-off.
        # Two years costs the SAME single request and the same ~0.7s — only the
        # payload grows. Pass chartDays=180 to match their detail page (180
        # with a 28-day forecast is what draws their dashed prediction band).
        self.requested_end_date = end_date
        self.chart_days = vocab.ceiling("days", chart_days)
        self.predicted_days = vocab.ceiling("predicted_days", predicted_days)
        # Price + outbound merchant URL cost ONE REQUEST PER PIN — there is no
        # batch form, and a drilled category returns up to 33 products. Left OFF
        # by default: enriching every product of every category in every
        # vertical would be hundreds of requests against a session shared by all
        # customers. Opt in, capped, and the cap is per category.
        self.enrich_top_n = enrich_top_n
        # The demographic filters the UI's Age/Gender chips drive. Empty = all,
        # which is what the page sends when nothing is selected. These use the
        # ENUM scheme (AGE_25_34/FEMALE), NOT the numeric codes the keyword
        # endpoints take — same customer input, two wire forms (C6).
        self.age_buckets = vocab.age_buckets_shopping(age_buckets)
        self.genders = vocab.genders_shopping(genders)
        if ranking_method not in vocab.RANKING_METHODS:
            raise vocab.InvalidParam(
                f"ranking_method={ranking_method!r} — one of "
                f"{sorted(vocab.RANKING_METHODS)}. The UI only ever sends GROWTH; "
                f"HIGH_VOLUME and VIRAL are real but unexposed.")
        if order_by not in vocab.ORDER_BY:
            raise vocab.InvalidParam(
                f"order_by={order_by!r} — one of {sorted(vocab.ORDER_BY)}")
        self.ranking_method = ranking_method
        self.order_by = order_by
        self.log = log
        self._taxonomy = None

    # ---------------------------------------------------------- bootstrap

    def taxonomy(self):
        """383 categories, fetched once per run. Shopping responses return IDs
        only — without this join every record is unreadable to a human."""
        if self._taxonomy is None:
            self._taxonomy = parsers.parse_taxonomy(
                self.client.style_a("/ads/v4/trends/shopping/product_categories",
                                    kind="taxonomy"))
            self.log(f"[shopping] taxonomy: {len(self._taxonomy)} categories")
        return self._taxonomy

    # ------------------------------------------------------------- the walk

    def run(self, verticals=None, with_products=True):
        """Yield one enriched record per trending category.

        Each record is self-contained: a customer can act on it without
        re-joining anything.
        """
        end_date = vocab.history_date(self.requested_end_date,
                                      self.client.bootstrap(),
                                      endpoint="shopping")
        taxonomy = self.taxonomy()
        targets = [str(v) for v in (verticals or vocab.VERTICALS)]

        for vertical_id in targets:
            try:
                yield from self._one_vertical(vertical_id, taxonomy, end_date,
                                              with_products)
            except TrendsAPIError as exc:
                # One vertical failing must not abort the other six.
                self.log(f"[shopping] vertical {vertical_id} failed: {exc}")

    def _one_vertical(self, vertical_id, taxonomy, end_date, with_products):
        name = vocab.VERTICALS.get(vertical_id, vertical_id)

        # ONE vertical per call — combining re-normalises percent_relative_volume
        # and silently crushes the smaller vertical.
        top = parsers.parse_top_categories(self.client.style_a(
            f"/ads/v4/trends/shopping/product_categories/top/{self.region}",
            {"event": self.event,
             "ranking_method": self.ranking_method,
             "end_date": vocab.end_date(end_date),
             "parent_product_categories": vocab.verticals_one_per_call([vertical_id]),
             "limit": vocab.ceiling("top_limit", 100),
             "order_by": self.order_by,
             "order": "DESC",
             "age_bucket": self.age_buckets,
             "gender": self.genders}, kind="trends"))

        categories = top["categories"]
        if not categories:
            # An empty here has TWO causes and they mean opposite things:
            #   1. no endDate  -> this vertical genuinely has nothing trending
            #   2. an endDate  -> possibly past the ~257-day floor, where this
            #      endpoint answers 200 + [] instead of erroring. The cap
            #      refuses the clearly-out-of-range dates, but the boundary is
            #      ragged (2025-11-29 empty between two working days), so a
            #      near-floor date can still land here.
            # Reading (2) as (1) is "nothing is trending in home decor" — a
            # plausible wrong answer. Never collapse them into one message.
            if self.requested_end_date:
                self.log(f"[shopping] {name}: 0 categories at "
                         f"endDate={self.requested_end_date} — this vertical "
                         f"had nothing trending, OR the date is at the edge of "
                         f"the ~{vocab.HISTORY_LIMIT_DAYS['shopping']}-day "
                         f"window where Pinterest answers 200 with an empty "
                         f"list. Re-run without endDate to tell them apart.")
            else:
                self.log(f"[shopping] {name}: 0 categories "
                         f"(empty, not an error)")
            return
        self.log(f"[shopping] {name}: {len(categories)} categories "
                 f"(vertical holds {top['total_in_vertical']})")

        ids = [c["category_id"] for c in categories if c["category_id"]]

        # Batched: one call covers every category's chart series.
        metrics = self._metrics(ids, end_date)

        # Drill only the top N — demographics batches, top_products does not.
        drilled = ids[:self.drill_top_n] if self.drill_top_n else []
        demographics = self._demographics(drilled, end_date) if drilled else {}

        for rank, category in enumerate(categories, start=1):
            cat_id = category["category_id"]
            node = taxonomy.get(cat_id, {})
            demo = demographics.get(cat_id)
            products = []
            if with_products and cat_id in drilled:
                products = self._products(cat_id)
                if self.enrich_top_n:
                    self._enrich(products[:self.enrich_top_n])

            yield {
                # identity
                "category_id": cat_id,
                "category_name": node.get("friendly_name"),
                "category_level": node.get("level"),
                "category_path": parsers.category_path(
                    taxonomy, cat_id, verticals=[name]),
                "vertical_id": vertical_id,
                "vertical_name": name,
                "region": self.region,

                # ranking — relative to THIS response only
                "rank_in_vertical": rank,
                "total_in_vertical": top["total_in_vertical"],
                "summary": category["summary"],

                # the chart
                "chart": metrics.get(cat_id, {}).get("series"),
                "growth": {
                    "wow_change": metrics.get(cat_id, {}).get("wow_change"),
                    "mom_change": metrics.get(cat_id, {}).get("mom_change"),
                    "yoy_change": metrics.get(cat_id, {}).get("yoy_change"),
                },

                # the drill-down (only for the top N)
                "drilled": cat_id in drilled,
                "age_distribution": (demo or {}).get("age_distribution"),
                "gender_distribution": (demo or {}).get("gender_distribution"),
                # Free with `top/` for every category; demographics returns the
                # same list, so the shallow one is used when not drilled.
                "search_queries": ((demo or {}).get("search_queries")
                                   or category["search_queries"]),
                "top_products": products,

                "_meta": self._meta(cat_id, vertical_id, end_date, demo, metrics),
            }

    # ------------------------------------------------------------ the calls

    def _metrics(self, ids, end_date):
        if not ids:
            return {}
        # Same trap as the keyword chart: forecasting forward from a past
        # end_date is HTTP 500 here too (measured -120d + predicted_days=28).
        # moment/metrics is the exception and forecasts from the past fine.
        predicted, self._forecast_note = vocab.forecast_days(
            self.predicted_days, end_date, self.client.bootstrap(),
            endpoint="shopping")
        return parsers.parse_category_metrics(self.client.style_a(
            f"/ads/v4/trends/shopping/product_categories/metrics/{self.region}",
            {"product_category_ids": ids,
             "event": self.event,
             "end_date": end_date,
             "days": self.chart_days,
             "predicted_days": predicted,
             "age_bucket": self.age_buckets,
             "gender": self.genders}, kind="trends"))

    def _demographics(self, ids, end_date):
        return parsers.parse_category_demographics(self.client.style_a(
            f"/ads/v4/trends/shopping/product_categories/demographics/{self.region}",
            {"product_category_ids": ids,
             "event": self.event,
             "end_date": end_date}, kind="trends"), self.event)

    def _products(self, cat_id):
        """top_products takes no array and only works in 3 regions with one
        event — both refused before the wire rather than returning a silent []."""
        try:
            vocab.region(self.region, capability="top_products")
            event = vocab.event("OUTBOUND_CLICK", endpoint="top_products")
        except vocab.InvalidParam as exc:
            self.log(f"[shopping] products skipped for {cat_id}: {exc}")
            return []
        return parsers.parse_top_products(self.client.style_a(
            "/ads/v4/trends/shopping/product_categories/top_products",
            {"product_category_id": cat_id, "region": self.region,
             "event": event}, kind="detail"), region=self.region)

    def _enrich(self, products):
        """Fill price / outbound_url / merchant domain from each pin's own page.

        The trends surface genuinely does not have this: verified by capture,
        `top_products` returns four fields, and the shopping page's 210 anchors
        include zero external links — its "external-link" icon points back to
        pinterest.com/pin/. So this is a second host, one request per pin.

        A failure enriches nothing and leaves the fields None. It must never
        take the whole category down, and a pin without an offer is a pin with
        no price, not a pin priced 0.
        """
        for product in products:
            pin_id = product.get("pin_id")
            if not pin_id:
                continue
            try:
                detail = parsers.parse_pin_closeup(
                    self.client.pin_resource(pin_id))
            except TrendsAPIError as exc:
                self.log(f"[shopping] pin {pin_id} not enriched: {exc}")
                continue
            if not detail:
                continue
            product.update({
                "price": detail["price"],
                "price_value": detail["price_value"],
                "currency": detail["currency"],
                "merchant_url": detail["outbound_url"],
                "merchant_domain": detail["merchant_domain"],
                "in_stock": detail["in_stock"],
                "free_shipping_over": detail["free_shipping_over"],
                # The canonical merchant name from the pin itself. top_products'
                # `merchant_name` is a display string; keep both rather than
                # overwrite, since they can differ.
                "merchant_name_canonical": detail["merchant_name"],
                "enriched": True,
            })

    # ------------------------------------------------------------ provenance

    def _meta(self, cat_id, vertical_id, end_date, demo, metrics):
        """Provenance travels with every record.

        `normalization_scope` is the machine-checkable form of "never compare
        across responses": two records sharing a scope id are comparable, two
        with different ones are not. `event` is on the audience because the
        same category has a materially different audience per event.
        """
        chart = metrics.get(cat_id, {})
        return {
            # ⚠️ This endpoint echoes NO date back — measured 2026-08-19, there
            # is not one date string anywhere in the response. So unlike
            # keywords, this is the date we ASKED for, and we cannot see
            # whether Pinterest snapped it to a week boundary. `end_date_basis`
            # says so, rather than letting it be read as Pinterest's own.
            "end_date": end_date,
            "end_date_requested": self.requested_end_date,
            "end_date_basis": "requested",
            "event": self.event,
            "audience_event": (demo or {}).get("event"),
            "audience_basis": "measured" if demo else None,
            "normalization_scope": f"top:{self.region}:{vertical_id}:{end_date}",
            "chart_basis": "measured" if chart.get("series") else None,
            "chart_days": self.chart_days,
            "predicted_days": self.predicted_days,
            "forecast_suppressed": getattr(self, "_forecast_note", None),
            # Absolute volumes are never exposed by this API — `total` is always
            # 0. Said out loud so nobody reads a missing number as a zero one.
            "absolute_volume": None,
            "absolute_volume_note": "Pinterest never exposes absolute volume; "
                                    "all counts are peak-normalised within their "
                                    "response.",
        }
