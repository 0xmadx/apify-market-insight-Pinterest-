"""Where the Pinterest logic lands. Deliberately empty.

The operator is supplying the endpoint specs (the API notes and .md files)
separately. Nothing here should guess at a URL, a parameter name or a response
key before those arrive — a wrong-but-plausible endpoint costs more than no
endpoint, because it fails like a site change rather than like a mistake.

The contract the rest of the actor depends on:

    run(ctx, task) -> iterable of Record

  ctx   a Context (src/context.py): ctx.get() for cached fetches, ctx.seen() to
        skip work, ctx.watermark() for where the last run stopped
  task  the actor input dict
  yield Record(scope=…, id=…, data={…}) — `data` is what lands in the dataset

Sketch:

    from .records import Record

    METRICS = ("saves", "impressions")     # what "changed" means for a pin

    def run(ctx, task):
        for term in task.get("queries", []):
            r = ctx.get("search", SEARCH_URL, params={...})
            if r.status_code != 200:
                continue
            for raw in parse_results(r.json()):        # a named parser, always
                pin_id = raw.get("id")
                if ctx.seen("pins", pin_id, raw, fields=METRICS):
                    continue                           # skips the detail fetch
                yield Record(scope="pins", id=pin_id, data=raw, fields=METRICS)

Rules that carry over from the parent repo and are not negotiable:

  1. Never index a raw response key. Parse through a named function, and diff the
     keys the response actually has against the keys the code reads. Seven
     modules there fetched correct data and read None out of it for months.
  2. Absent is not zero. A field that did not render is None, not 0. Let the
     consumer decide at the point of use.
  3. A failed fetch is never cached and never stored as 0. `ctx.get` already
     refuses to cache anything that is not a usable 200 with a body.

`session.classify(response)` separates auth_expired from rate_limited from
blocked from malformed — use it rather than treating every non-200 alike. A
`malformed` verdict means our request was wrong; never evict a profile over one.
"""


class NotImplementedYet(RuntimeError):
    pass


def run(ctx, task):
    raise NotImplementedYet(
        "Pinterest endpoint logic has not been added yet. Drop the endpoint specs "
        "in and implement run() to yield Record objects."
    )
