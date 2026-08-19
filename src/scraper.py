"""Dispatch: actor input -> a traversal -> Records.

Each traversal is a packaged walk of the endpoint graph (docs/ARCHITECTURE.md).
Only `shopping` exists so far; the other three actors named in the architecture
land here the same way.

    run(ctx, task) -> iterable of Record

Rules that carry over and are not negotiable (see the pinterest-trends-coder
skill): named parsers only, absent is not zero, every relative number carries
its normalisation scope, and nothing is marked seen until it has been pushed.
"""
from .records import Record
from .shopping import ShoppingScraper
from .transport import TrendsClient

# What counts as "this record changed" for the seen-set. Deliberately the
# movement fields: a category re-emits when its growth or rank actually moves,
# not when Pinterest reorders a list or adds a field nothing reads.
SHOPPING_FIELDS = ("rank_in_vertical", "growth", "summary")


class UnknownOperation(ValueError):
    pass


def run(ctx, task):
    operation = (task.get("operation") or "shopping").lower()
    if operation != "shopping":
        raise UnknownOperation(
            f"operation={operation!r} is not implemented yet. "
            f"Available: shopping.")
    return _shopping(ctx, task)


def _shopping(ctx, task):
    client = TrendsClient(ctx.session)
    scraper = ShoppingScraper(
        client,
        region=task.get("region", "US"),
        event=task.get("event", "OUTBOUND_CLICK"),
        drill_top_n=int(task.get("drillTopN", 3)),
    )

    verticals = task.get("verticals") or None
    max_records = int(task.get("maxRecords", 0) or 0)
    emitted = 0

    for record in scraper.run(verticals=verticals,
                              with_products=task.get("includeProducts", True)):
        yield Record(
            scope=f"shopping:{record['region']}:{record['vertical_id']}",
            id=record["category_id"],
            data=record,
            fields=SHOPPING_FIELDS,
        )
        emitted += 1
        if max_records and emitted >= max_records:
            return
