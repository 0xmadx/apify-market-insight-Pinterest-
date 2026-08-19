"""The crawl — following Pinterest's own navigation, recursively.

The four operations each answer one question and stop. The Trends UI does not
stop: every screen links to the next, and the useful work is in the walk.

    Trends overview ──▶ a spotlight trend ──▶ "commonly search for:" ──▶ keyword
                    └─▶ Moments ──▶ a moment ──▶ its keywords ──────────▶ keyword
    Shopping page   ──▶ a category ──▶ "Search queries" chips ──────────▶ keyword
                                    └─▶ Top products ──────────────────▶ pin
    Keyword page    ──▶ "Related trends" ──▶ another keyword ──▶ … recursive

Those edges already exist in our records (`keywords`, `search_queries`,
`related`) and were dead ends: captured, emitted, never followed. This module
follows them.

WHY THIS IS AFFORDABLE — and the one design decision that matters:

The crawl is **breadth-first and batched per level**, never depth-first per
node. Keyword enrichment takes an ARRAY: one `/metrics/` call answers for the
whole level. So the cost scales with DEPTH, not with how many nodes you found.

    depth-first, per node      13 moments x 25 keywords x 3 calls = 975 requests
    breadth-first, per level   3 levels x ~4 calls                =  12 requests

That is the difference between a feature and an outage on a shared session. A
depth-first crawler here would be correct and unusable.

Two guards, because a crawl is the one operation that can run away:

  * `max_requests` caps the FOLLOWING. The entry page itself always loads
    first — a half-loaded entry page is not a cheaper answer, it is a wrong
    one — so the seed cost is a floor, not something the budget can prevent.
    `entry_cost` is reported so the floor is visible rather than surprising.
  * every node is visited once. `mascara` reached from three different
    categories is one keyword, not three.

Records stream as they are found, which means a record emitted early cannot
know the crawl was later cut short. So the LAST record of every crawl is a
`crawl_summary` node carrying what actually happened: nodes by kind, requests
spent, whether the budget stopped it, and how many edges were left unfollowed.
Without it, a truncated crawl is indistinguishable from a complete one — which
is the same silent-empty failure this project refuses everywhere else.
"""
from . import parsers, vocab
from .keywords import KeywordScraper
from .moments import MomentScraper
from .radar import RadarScraper
from .shopping import ShoppingScraper
from .transport import TrendsAPIError

# Where a crawl can start. These are Pinterest's own pages, named as the
# customer sees them, because "where do I start" is a navigation question.
ENTRY_POINTS = {
    "overview": "Trends overview — spotlight + editorial + every moment",
    "shopping": "Discover trending product categories (the default first page)",
    "search": "Discover trending search keywords (the default first page)",
    "moments": "Moments only — every seasonal moment for the region",
}

# One `/metrics/` call takes an array of terms. 10 is verified; the ceiling
# above that is NOT measured, so the level is chunked at a value already proven
# elsewhere in this codebase rather than at a guess. Raising it is a
# measurement, not an edit.
BATCH = 25

# A level wider than this is almost always a runaway rather than an intent.
# It is a default, not a limit — `max_nodes_per_level` overrides it.
DEFAULT_LEVEL_CAP = 50


class CrawlBudget:
    """A hard request budget shared by every scraper in one crawl.

    Wraps the client rather than trusting callers to count. `spent` is read
    back onto every record so a customer can see what their crawl cost.
    """

    def __init__(self, client, max_requests):
        self.client = client
        self.max_requests = max_requests
        self.start = len(getattr(client, "all_calls", []) or [])
        self.exhausted = False

    @property
    def spent(self):
        return len(getattr(self.client, "all_calls", []) or []) - self.start

    def remaining(self):
        return max(0, self.max_requests - self.spent)

    def can_spend(self, n=1):
        if self.spent + n > self.max_requests:
            self.exhausted = True
            return False
        return True


class Crawler:
    def __init__(self, client, region="US", entry="overview", depth=1,
                 max_requests=60, max_nodes_per_level=DEFAULT_LEVEL_CAP,
                 end_date=None, date_range_days=365, event="OUTBOUND_CLICK",
                 enrich_top_n=0, related_fanout=10, log=print):
        if entry not in ENTRY_POINTS:
            raise vocab.InvalidParam(
                f"crawlFrom={entry!r} — one of {', '.join(sorted(ENTRY_POINTS))}. "
                + "; ".join(f"{k}: {v}" for k, v in ENTRY_POINTS.items()))
        if depth < 0:
            raise vocab.InvalidParam("crawlDepth must be 0 or more")
        self.client = client
        self.region = vocab.region(region)
        self.entry = entry
        self.depth = depth
        self.max_nodes_per_level = max_nodes_per_level
        self.end_date = end_date
        self.date_range_days = date_range_days
        self.event = event
        self.enrich_top_n = enrich_top_n
        # How many keywords per level get their `related` siblings fetched.
        # 1 request each, so this is the crawl's only linear cost.
        self.related_fanout = related_fanout
        self.budget = CrawlBudget(client, max_requests)
        self.log = log
        # (kind, key) -> the depth it was first reached at. Identity is the
        # pair: a keyword and a category can share a name without colliding.
        self.visited = {}
        self.entry_cost = 0
        self.unfollowed = 0

    # ------------------------------------------------------------- the walk

    def run(self):
        """Yield records level by level, seeds first.

        Every record carries how it was reached (`_meta.crawl_path`), because
        "why am I looking at this keyword" is the question a crawl output
        otherwise cannot answer.
        """
        self.log(f"[crawl] {self.entry} in {self.region}, depth={self.depth}, "
                 f"budget={self.budget.max_requests} requests")

        frontier = []
        for record, kind, key, edges in self._seed():
            if self._mark(kind, key, 0):
                yield self._stamp(record, 0, [f"{self.entry}"], kind)
                frontier.extend(edges)
        # The floor cost. Reported rather than enforced: the budget governs
        # what the crawl FOLLOWS, and a partly-loaded entry page would be a
        # wrong answer, not a cheaper one.
        self.entry_cost = self.budget.spent
        if self.entry_cost >= self.budget.max_requests:
            self.log(f"[crawl] the {self.entry} page alone cost "
                     f"{self.entry_cost} requests, at or over the "
                     f"{self.budget.max_requests} budget — nothing will be "
                     f"followed. Raise maxRequests to crawl.")

        path = [self.entry]
        for level in range(1, self.depth + 1):
            terms = self._next_terms(frontier)
            if not terms:
                self.log(f"[crawl] level {level}: nothing new to follow — "
                         f"stopping early (not an error)")
                break
            if self.budget.exhausted or not self.budget.can_spend(3):
                self.log(f"[crawl] budget exhausted at level {level} "
                         f"({self.budget.spent}/{self.budget.max_requests})")
                break

            # `related` is the ONLY edge out of a keyword, and unlike the rest
            # of the pipeline it has no batch form — it costs ONE REQUEST PER
            # KEYWORD. So it is fetched only when a further level will actually
            # consume it, and only for the first `related_fanout` terms.
            # Without this, depth=2 silently returned depth=1's result: the
            # level was enriched with no edges, the next frontier came back
            # empty, and the crawl stopped while reporting success.
            going_deeper = level < self.depth
            fanout = self.related_fanout if going_deeper else 0
            cost = -(-len(terms) // BATCH) * 3 + min(fanout, len(terms))
            self.log(f"[crawl] level {level}: following {len(terms)} keyword(s) "
                     f"— batched, ~{cost} requests"
                     + (f" (incl. {min(fanout, len(terms))} related lookups, "
                        f"1 per keyword — no batch form exists)"
                        if fanout else ""))
            frontier = []
            for record in self._enrich(terms, level, fanout):
                yield self._stamp(record, level, path + ["keyword"], "keyword")
                frontier.extend(
                    {"kind": "keyword", "key": r["term"]}
                    for r in (record.get("related") or []) if r.get("term"))
            path = path + ["keyword"]

        self.unfollowed = len([e for e in frontier
                               if ("keyword", e.get("key")) not in self.visited])
        self.log(f"[crawl] done — {len(self.visited)} node(s), "
                 f"{self.budget.spent} request(s)"
                 + (f", TRUNCATED by budget" if self.budget.exhausted else ""))
        yield self._summary()

    def _summary(self):
        """The terminal record. Always emitted, always last.

        A streamed record cannot be retro-flagged when the crawl is cut short
        later, so the fact of truncation has to live somewhere that is written
        at the end. This is that place.
        """
        by_kind = {}
        for (kind, _key) in self.visited:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "crawl_summary": True,
            "region": self.region,
            "entry": self.entry,
            "depth_requested": self.depth,
            "depth_reached": max(self.visited.values(), default=0),
            "nodes_total": len(self.visited),
            "nodes_by_kind": by_kind,
            "requests_spent": self.budget.spent,
            "request_budget": self.budget.max_requests,
            "entry_cost": self.entry_cost,
            "truncated": bool(self.budget.exhausted),
            "edges_unfollowed": self.unfollowed,
            "_meta": {
                "crawl_entry": self.entry,
                "crawl_depth": self.depth,
                "crawl_path": self.entry,
                "crawl_node_kind": "summary",
                "crawl_requests_spent": self.budget.spent,
                "crawl_truncated": bool(self.budget.exhausted) or None,
                "note": (
                    "terminal record of this crawl. `truncated` true means the "
                    "budget stopped it early and the dataset is PARTIAL — "
                    "raise maxRequests or lower crawlDepth. `entry_cost` is "
                    "the floor: the entry page always loads in full, because "
                    "half of it would be a wrong answer rather than a cheap "
                    "one."),
                "normalization_scope": f"crawl:{self.region}:{self.entry}",
            },
        }

    # ------------------------------------------------------------- the seeds

    def _seed(self):
        """Load the entry page exactly as the UI loads it by default.

        Yields (record, kind, key, edges) so the caller can both emit the node
        and queue what it links to.
        """
        if self.entry in ("overview", "moments"):
            yield from self._seed_moments()
        if self.entry == "overview":
            yield from self._seed_radar()
        if self.entry == "shopping":
            yield from self._seed_shopping()
        if self.entry == "search":
            yield from self._seed_search()

    def _seed_moments(self):
        scraper = MomentScraper(
            self.client, region=self.region, aggregation="weekly",
            lookback_days=self.date_range_days, predicted_days=91,
            end_date=self.end_date, log=self.log)
        try:
            for rec in scraper.run(drill=True, with_audience=False):
                edges = [{"kind": "keyword", "key": k["term"]}
                         for k in (rec.get("keywords") or []) if k.get("term")]
                yield rec, "moment", rec["slug"], edges
        except (TrendsAPIError, vocab.InvalidParam) as exc:
            # A region with no moments is a real answer, and the rest of the
            # overview crawl is still worth running.
            self.log(f"[crawl] moments seed skipped: {exc}")

    def _seed_radar(self):
        scraper = RadarScraper(self.client, region=self.region,
                               end_date=self.end_date, log=self.log)
        try:
            for rec in scraper.run():
                edges = [{"kind": "keyword", "key": k}
                         for k in (rec.get("keywords") or []) if k]
                yield rec, "trend", rec["id"], edges
        except (TrendsAPIError, vocab.InvalidParam) as exc:
            self.log(f"[crawl] radar seed skipped: {exc}")

    def _seed_shopping(self):
        scraper = ShoppingScraper(
            self.client, region=self.region, event=self.event,
            drill_top_n=3, chart_days=self.date_range_days,
            enrich_top_n=self.enrich_top_n, end_date=self.end_date,
            log=self.log)
        try:
            for rec in scraper.run(verticals=sorted(vocab.UI_VISIBLE_VERTICALS),
                                   with_products=bool(self.enrich_top_n)):
                # The "Search queries" chips — clickable on Pinterest's own
                # page, and until now a dead end here.
                edges = [{"kind": "keyword", "key": q}
                         for q in (rec.get("search_queries") or []) if q]
                yield rec, "category", rec["category_id"], edges
        except (TrendsAPIError, vocab.InvalidParam) as exc:
            self.log(f"[crawl] shopping seed skipped: {exc}")

    def _seed_search(self):
        scraper = KeywordScraper(
            self.client, region=self.region, days=self.date_range_days,
            end_date=self.end_date, log=self.log)
        try:
            for rec in scraper.run(mode="discover", include_related=True,
                                   include_images=False, max_terms=BATCH):
                edges = [{"kind": "keyword", "key": r["term"]}
                         for r in (rec.get("related") or []) if r.get("term")]
                yield rec, "keyword", rec["term"], edges
        except (TrendsAPIError, vocab.InvalidParam) as exc:
            self.log(f"[crawl] search seed skipped: {exc}")

    # -------------------------------------------------------- level plumbing

    def _next_terms(self, frontier):
        """Unvisited keyword terms from the frontier, capped and deduped."""
        out = []
        for edge in frontier:
            if edge["kind"] != "keyword":
                continue
            key = vocab.keyword(edge["key"]) if edge["key"] else None
            if not key or ("keyword", key) in self.visited:
                continue
            if key in out:
                continue
            out.append(key)
            if len(out) >= self.max_nodes_per_level:
                self.log(f"[crawl] level capped at {self.max_nodes_per_level} "
                         f"keyword(s); more were available")
                break
        return out

    def _enrich(self, terms, level, fanout=0):
        """Run a whole level through the keyword pipeline in batches.

        This is the reason the crawl is cheap: `exact` mode takes the array,
        so one chunk of 25 terms costs ~3 requests rather than 75.

        `fanout` is how many of these terms also get their `related` siblings
        fetched — the edges the NEXT level walks. It is separate from the batch
        because it is the one part that does not batch.
        """
        scraper = KeywordScraper(
            self.client, region=self.region, days=self.date_range_days,
            end_date=self.end_date, log=self.log)
        expanded = 0
        for start in range(0, len(terms), BATCH):
            chunk = terms[start:start + BATCH]
            if not self.budget.can_spend(3):
                self.log(f"[crawl] budget stopped this level after "
                         f"{start} of {len(terms)} keyword(s)")
                return
            # Related is per-keyword, so it is requested for a prefix of the
            # chunk and the budget is re-checked against that cost.
            want = max(0, min(fanout - expanded, len(chunk)))
            if want and not self.budget.can_spend(3 + want):
                want = 0
            try:
                for rec in scraper.run(mode="exact", terms=chunk,
                                       include_related=bool(want),
                                       include_images=False):
                    if self._mark("keyword", rec["term"], level):
                        yield rec
                expanded += want
            except (TrendsAPIError, vocab.InvalidParam) as exc:
                self.log(f"[crawl] level {level} chunk failed: {exc}")

    def _mark(self, kind, key, depth):
        """First visit wins. Returns False for a node already seen."""
        if key is None:
            return True                 # no id is a parser problem, not a dup
        if (kind, key) in self.visited:
            return False
        self.visited[(kind, key)] = depth
        return True

    def _stamp(self, record, depth, path, kind):
        """Attach the crawl trail. Never mutates the traversal's own _meta."""
        meta = dict(record.get("_meta") or {})
        meta.update({
            "crawl_entry": self.entry,
            "crawl_depth": depth,
            # How this node was reached. Without it a crawl dataset is a pile
            # of records with no explanation of why any of them is there.
            "crawl_path": " > ".join(path),
            "crawl_node_kind": kind,
            "crawl_requests_spent": self.budget.spent,
            # Set only when the budget cut the crawl short. A truncated crawl
            # that looked complete would be the worst output this can produce.
            "crawl_truncated": self.budget.exhausted or None,
        })
        out = dict(record)
        out["_meta"] = meta
        return out
