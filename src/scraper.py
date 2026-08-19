"""Dispatch: actor input -> a traversal -> Records.

Each operation is a packaged walk of the endpoint graph (docs/ARCHITECTURE.md):

    shopping   trending categories, joined + drilled          (src/shopping.py)
    keywords   discover/seed/exact -> the keyword pipeline    (src/keywords.py)
    moments    seasonal phases + the API's only daily curves  (src/moments.py)
    radar      Pinterest's curated layer, schedule-friendly   (src/radar.py)

    run(ctx, task) -> iterable of Record

Per-operation `fields` are the movement fields: a record re-emits when its
numbers move, not when Pinterest reorders a list or adds a field nothing reads.
Rules that are not negotiable (pinterest-trends-coder skill): named parsers
only, absent is not zero, every relative number carries its normalisation
scope, and nothing is marked seen until it has been pushed.
"""
from .keywords import KeywordScraper
from .moments import MomentScraper
from .radar import RadarScraper
from .records import Record
from .shopping import ShoppingScraper
from .transport import TrendsClient

SHOPPING_FIELDS = ("rank_in_vertical", "growth", "summary")
KEYWORD_FIELDS = ("wow_change", "mom_change", "yoy_change", "has_forecast",
                  "seasonality_score")
MOMENT_FIELDS = ("phase", "next_occurrence_at", "series_points")
RADAR_FIELDS = ("pct_growth_mom", "keywords", "campaign_start")

OPERATIONS = ("shopping", "keywords", "moments", "radar")


class UnknownOperation(ValueError):
    pass


def run(ctx, task):
    operation = (task.get("operation") or "shopping").lower()
    if operation not in OPERATIONS:
        raise UnknownOperation(
            f"operation={operation!r} — available: {', '.join(OPERATIONS)}")

    # The cache is the actor's shared asset: the taxonomy is 383 rows that
    # every run and every vertical needs, and two customers asking the same
    # question inside a TTL should cost Pinterest one request, not two.
    client = TrendsClient(ctx.session, cache=ctx.cache,
                          force_refresh=ctx.force_refresh)
    handler = {
        "shopping": _shopping,
        "keywords": _keywords,
        "moments": _moments,
        "radar": _radar,
    }[operation]

    max_records = int(task.get("maxRecords", 0) or 0)
    emitted = 0
    for record in handler(client, task):
        yield record
        emitted += 1
        if max_records and emitted >= max_records:
            return


def _shopping(client, task):
    scraper = ShoppingScraper(
        client,
        region=task.get("region", "US"),
        event=task.get("event", "OUTBOUND_CLICK"),
        drill_top_n=int(task.get("drillTopN", 3)),
        enrich_top_n=int(task.get("enrichTopN", 0) or 0),
    )
    for record in scraper.run(verticals=task.get("verticals") or None,
                              with_products=task.get("includeProducts", True)):
        yield Record(
            scope=f"shopping:{record['region']}:{record['vertical_id']}",
            id=record["category_id"], data=record, fields=SHOPPING_FIELDS)


def _keywords(client, task):
    scraper = KeywordScraper(
        client,
        region=task.get("region", "US"),
        days=int(task.get("days", 365)),
        predicted_days=int(task.get("predictedDays", 91)),
        interest_ids=task.get("interestIds") or None,
    )
    mode = task.get("mode", "discover")
    for record in scraper.run(
            mode=mode,
            terms=task.get("queries") or None,
            include_related=task.get("includeRelated", True),
            include_images=task.get("includeImages", True),
            max_terms=int(task.get("maxTerms", 0) or 0) or None,
            preset=int(task.get("preset", 3)),
            num_terms=int(task.get("numTerms", 100)),
            l1interests=task.get("interests") or None,
            moments=task.get("moments") or None,
            age_buckets=task.get("ageBuckets") or None,
            genders=task.get("genders") or None,
            keywords_to_include=task.get("keywordsToInclude") or None,
    ) if mode == "discover" else scraper.run(
            mode=mode, terms=task.get("queries") or None,
            include_related=task.get("includeRelated", True),
            include_images=task.get("includeImages", True),
            max_terms=int(task.get("maxTerms", 0) or 0) or None):
        yield Record(scope=f"keywords:{record['region']}:{record['_meta']['mode']}",
                     id=record["term"], data=record, fields=KEYWORD_FIELDS)


def _moments(client, task):
    scraper = MomentScraper(
        client,
        region=task.get("region", "US"),
        aggregation=task.get("aggregation", "daily"),
        lookback_days=int(task.get("lookbackDays", 365)),
        predicted_days=int(task.get("predictedDays", 91)),
        interest_ids=task.get("interestIds") or None,
    )
    for record in scraper.run(
            phases=tuple(task.get("phases") or ("rising", "approaching")),
            drill=task.get("drill", True),
            with_audience=task.get("includeAudience", True)):
        yield Record(scope=f"moments:{record['region']}",
                     id=record["slug"], data=record, fields=MOMENT_FIELDS)


def _radar(client, task):
    scraper = RadarScraper(
        client,
        region=task.get("region", "US"),
        interest=task.get("interest") or None,
    )
    for record in scraper.run(
            include_spotlight=task.get("includeSpotlight", True),
            include_editorial=task.get("includeEditorial", True)):
        yield Record(scope=f"radar:{record['region']}:{record['source']}",
                     id=record["id"], data=record, fields=RADAR_FIELDS)
