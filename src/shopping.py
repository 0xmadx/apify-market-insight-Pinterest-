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
                 drill_top_n=3, chart_days=180, predicted_days=28, log=print):
        self.client = client
        self.region = vocab.region(region)
        self.event = vocab.event(event)
        self.drill_top_n = drill_top_n
        # The detail page's own defaults: 180 days with a 28-day forecast is
        # what produces the dashed prediction band. The table uses 60/0.
        self.chart_days = vocab.ceiling("days", chart_days)
        self.predicted_days = vocab.ceiling("predicted_days", predicted_days)
        self.log = log
        self._taxonomy = None

    # ---------------------------------------------------------- bootstrap

    def taxonomy(self):
        """383 categories, fetched once per run. Shopping responses return IDs
        only — without this join every record is unreadable to a human."""
        if self._taxonomy is None:
            self._taxonomy = parsers.parse_taxonomy(
                self.client.style_a("/ads/v4/trends/shopping/product_categories"))
            self.log(f"[shopping] taxonomy: {len(self._taxonomy)} categories")
        return self._taxonomy

    # ------------------------------------------------------------- the walk

    def run(self, verticals=None, with_products=True):
        """Yield one enriched record per trending category.

        Each record is self-contained: a customer can act on it without
        re-joining anything.
        """
        end_date = self.client.bootstrap()
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
             "ranking_method": "GROWTH",
             "end_date": vocab.end_date(end_date),
             "parent_product_categories": vocab.verticals_one_per_call([vertical_id]),
             "limit": vocab.ceiling("top_limit", 100),
             "order_by": "RELATIVE_VOLUME",
             "order": "DESC"}))

        categories = top["categories"]
        if not categories:
            # A real answer, not a failure: this vertical has nothing trending.
            self.log(f"[shopping] {name}: 0 categories (empty, not an error)")
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
        return parsers.parse_category_metrics(self.client.style_a(
            f"/ads/v4/trends/shopping/product_categories/metrics/{self.region}",
            {"product_category_ids": ids,
             "event": self.event,
             "end_date": end_date,
             "days": self.chart_days,
             "predicted_days": self.predicted_days,
             "age_bucket": [], "gender": []}))

    def _demographics(self, ids, end_date):
        return parsers.parse_category_demographics(self.client.style_a(
            f"/ads/v4/trends/shopping/product_categories/demographics/{self.region}",
            {"product_category_ids": ids,
             "event": self.event,
             "end_date": end_date}), self.event)

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
             "event": event}), region=self.region)

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
            "end_date": end_date,          # Pinterest's date, not the run's
            "event": self.event,
            "audience_event": (demo or {}).get("event"),
            "audience_basis": "measured" if demo else None,
            "normalization_scope": f"top:{self.region}:{vertical_id}:{end_date}",
            "chart_basis": "measured" if chart.get("series") else None,
            "chart_days": self.chart_days,
            "predicted_days": self.predicted_days,
            # Absolute volumes are never exposed by this API — `total` is always
            # 0. Said out loud so nobody reads a missing number as a zero one.
            "absolute_volume": None,
            "absolute_volume_note": "Pinterest never exposes absolute volume; "
                                    "all counts are peak-normalised within their "
                                    "response.",
        }
