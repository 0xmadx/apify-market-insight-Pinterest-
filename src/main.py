"""Apify actor entrypoint.

Shape of a run:

    input -> lease an identity -> scrape -> drop what we already have -> dataset

The actor holds no session state of its own. Everything it needs to look like a
real logged-in browser comes from the vault, which is filled by the operator's
Chrome extension. That is what makes this deployable at all: the actor never logs
in, never stores a password, and never runs a browser.

**Ordering rule, and the reason for it.** Records are marked seen only *after*
the dataset push returns. Marking first would mean a run that dies between the
mark and the push loses those records permanently — the next run skips them and
nothing ever notices. Marking after can at worst duplicate a batch on a crash,
which is visible and recoverable. Silent loss is not.
"""
import asyncio

import redis
from apify import Actor

from .config import Config
from .context import Context
from .records import Record
from .scraper import run as run_scraper
from .lazy import lazy_session
from .state import RunState, fingerprint
from .vault import VaultEmpty

# Batched so the dataset fills as the run proceeds rather than only at the end —
# a run that dies at record 900 should still have 800.
BATCH_SIZE = 100

# One-click recipes for customers who don't want to learn 40+ input fields
# first. Deliberately NAMED `quickStart`, not `preset` — Pinterest's own
# keyword-discovery API already has an unrelated integer field called
# `preset` (1-4, "top monthly/yearly/growing/seasonal"), and giving this the
# same name silently shadowed it during development (JSON tolerates
# duplicate keys, last one wins — caught only because the dropdown ended up
# with the wrong type). Each persona actor's input_schema.json exposes only
# ITS OWN subset of these names in the `quickStart` dropdown — the dict here
# is shared across all four actors (one engine), but a marketers customer
# never sees an ecommerce recipe, because their schema's enum never lists it.
#
# A recipe's fields WIN over whatever the customer's form submitted for that
# same field — that is what makes picking one actually simplify the run
# instead of silently doing nothing. Everything a recipe does NOT mention
# (region, queries, maxRecords, endDate, …) passes through from the
# customer's input completely untouched.
QUICK_START_RECIPES = {
    # marketers
    "whats_trending_now": {"operation": "radar"},
    "campaign_timing": {"operation": "moments"},
    "validate_my_angle": {"operation": "keywords", "mode": "exact",
                          "includeRelated": True},
    # ecommerce
    "todays_trending_products": {"operation": "shopping", "drillTopN": 3,
                                 "enrichTopN": 0},
    "trending_with_prices": {"operation": "shopping", "drillTopN": 3,
                             "enrichTopN": 5},
    "demand_behind_a_category": {"operation": "crawl", "crawlFrom": "shopping",
                                 "crawlDepth": 1},
    # creators
    "content_calendar": {"operation": "moments"},
    "trending_search_terms": {"operation": "keywords", "mode": "discover"},
    "check_my_caption_idea": {"operation": "keywords", "mode": "exact",
                              "includeRelated": True},
}


def _apply_quick_start(task):
    """Expand `quickStart` into its recipe, then discard it — nothing
    downstream of this function has ever heard of `quickStart`, only
    `operation` and the rest of the real fields. Absent, empty, "custom", or
    unrecognized all mean the same thing: use the fields the customer
    actually set, unchanged. That default keeps this fully backward
    compatible with a run that predates this feature entirely.
    """
    name = task.pop("quickStart", None)
    recipe = QUICK_START_RECIPES.get(name)
    if recipe:
        task.update(recipe)
    return task


def _customer_capacity_message(exc):
    """What a customer sees when the vault can't serve this run — never the
    raw exception, and never "vault"/"redis"/"upstash" (same reason the
    VaultEmpty message was reworded earlier: those words tell a stranger
    "this rides a real account that might get banned", which nothing else on
    the listing answers).

    `VaultEmpty` and a Redis connection failure are different problems for
    the OPERATOR — one means "buy more accounts" (src/vault.py already says
    so: "when this starts climbing the answer is more accounts"), the other
    means "check Upstash's status page" — so they are recorded under
    different RUN_METRICS keys and never conflated in the log. But they are
    the SAME problem for a CUSTOMER: nothing to do but try again shortly.

    A pure function, deliberately: testable without a live Actor or a
    live/dead Redis, the way tests/test_main_messages.py does it.
    """
    if isinstance(exc, VaultEmpty):
        return ("We're at capacity right now — every data source is in use. "
                "Please try again in a few minutes.")
    return ("We're temporarily unable to reach our data source. "
            "Please try again in a few minutes.")


async def main():
    async with Actor:
        task = _apply_quick_start(await Actor.get_input() or {})
        config = Config()
        state = RunState(config)
        full_rescan = bool(task.get("fullRescan"))

        Actor.log.info(f"platform={config.PLATFORM} task={task}")
        if full_rescan:
            Actor.log.warning("fullRescan: ignoring the seen-set for this run")
        if task.get("forceRefresh"):
            Actor.log.warning("forceRefresh: ignoring cached responses for this run")

        pushed = skipped = 0
        # Filled by the worker; both are set here because they are outcomes
        # the worker itself never reaches (it raises past them instead).
        metrics = {"vault_empty": 0, "vault_unreachable": 0}
        try:
            async for batch in _batches(task, config, metrics):
                fresh, already_held = _split(batch, state, full_rescan)
                skipped += already_held

                if fresh:
                    await Actor.push_data([r.data for r in fresh])
                    pushed += len(fresh)
                    _mark(fresh, state)  # only now — see the ordering rule above

                Actor.log.info(f"pushed {pushed} · skipped {skipped} already held")
        except (VaultEmpty, redis.exceptions.RedisError) as exc:
            # Two different failures, handled the same shape on purpose.
            # VaultEmpty: Redis answered, every account is in use — this
            # already waited up to WAIT_TIMEOUT for a slot to free up, so
            # "try again shortly" is an honest instruction, not a brush-off.
            # RedisError: Upstash itself is unreachable — previously
            # UNHANDLED, which meant a customer saw a raw traceback instead
            # of a clean message. Both are "not a crash, and not a Pinterest
            # problem" in the same sense VaultEmpty always was: failing
            # loudly is the point, a silent empty dataset would read as
            # "Pinterest returned nothing".
            #
            # The full technical reason (str(exc)) goes to the
            # operator-visible log only; the customer sees a plain capacity
            # message from _customer_capacity_message, never "vault"/
            # "redis"/"upstash" — see that function for why. The two cases
            # are recorded under DIFFERENT metrics keys so the operator can
            # tell "buy more accounts" apart from "Upstash is down" —
            # conflating them would be the same mistake as evicting a
            # profile over a `malformed` classify() verdict.
            key = "vault_empty" if isinstance(exc, VaultEmpty) else "vault_unreachable"
            Actor.log.error(f"{type(exc).__name__}: {exc}")
            metrics[key] = 1
            await _record(metrics, pushed, skipped)
            await Actor.fail(status_message=_customer_capacity_message(exc))
            return

        Actor.log.info(f"done: {pushed} new, {skipped} skipped as unchanged")
        await _record(metrics, pushed, skipped)


async def _record(metrics, pushed, skipped):
    """Persist one run's numbers, and say them out loud.

    WHY THIS EXISTS. Four questions decide everything about scaling this actor,
    and before 2026-08-27 not one of them was measured in production: how long a
    run waits for a profile, how often the vault is empty, how much the cache
    actually saves, and whether Pinterest ever rate-limits us. "We have never
    seen a 429" was a belief with nothing counting.

    Written to the run's key-value store under RUN_METRICS so it can be read
    per run without scraping logs, AND logged as one line, because a number
    nobody can see is not a measurement.
    """
    metrics = dict(metrics)
    metrics["records_pushed"] = pushed
    metrics["records_skipped"] = skipped
    hits = metrics.get("cache_hits") or 0
    total = hits + (metrics.get("cache_misses") or 0)
    # None, not 0, when nothing was asked of the cache -- a run that made no
    # requests has no hit rate, and 0% would read as a broken cache.
    metrics["cache_hit_rate"] = round(hits / total, 3) if total else None
    try:
        await Actor.set_value("RUN_METRICS", metrics)
    except Exception as exc:          # never let bookkeeping fail a good run
        Actor.log.warning(f"could not store RUN_METRICS: {type(exc).__name__}")
    Actor.log.info("metrics: " + " · ".join(
        f"{k}={v}" for k, v in sorted(metrics.items()) if v is not None))


def _split(batch, state: RunState, full_rescan: bool):
    """Partition a batch into records worth pushing and records already held."""
    if full_rescan:
        return list(batch), 0

    fresh, skipped = [], 0
    for record in batch:
        if record.id is None:
            # No id means we cannot dedup it. Pass it through rather than drop
            # it — a missing id is a parser bug, not a duplicate.
            fresh.append(record)
            continue
        if state.is_new(record.scope, record.id,
                        fingerprint(record.data, record.fields),
                        record.fields):
            fresh.append(record)
        else:
            skipped += 1
    return fresh, skipped


def _mark(records, state: RunState):
    """Group by scope so each seen-set is written once per batch."""
    by_scope = {}
    for record in records:
        if record.id is not None:
            by_scope.setdefault(record.scope, []).append(record)

    for scope, group in by_scope.items():
        state.mark_seen(
            scope,
            [{"__id": r.id, **r.data} for r in group],
            id_key="__id",
            fields=group[0].fields,
        )


async def _batches(task, config, metrics=None):
    """Run the sync scraper in a worker thread, yielding batches to the loop.

    curl_cffi here is synchronous, so it must not run on the event loop; the
    thread keeps the actor responsive to Apify's heartbeats and abort signals.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    loop = asyncio.get_running_loop()
    DONE = object()

    def put(item):
        asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()

    def worker():
        batch = []
        try:
            # Lazy: the identity is taken on the first request that actually
            # reaches Pinterest, not at run start. A run served from cache or
            # coalesced onto another worker's fetch never occupies a profile —
            # which is what lets a handful of profiles serve many customers.
            with lazy_session(config, log=Actor.log.info) as session:
                ctx = Context(session, None, task, config)
                for record in run_scraper(ctx, task):
                    if not isinstance(record, Record):
                        raise TypeError(
                            "scraper.run must yield Record objects — see "
                            f"src/records.py. Got {type(record).__name__}"
                        )
                    batch.append(record)
                    if len(batch) >= BATCH_SIZE:
                        put(batch)
                        batch = []
                ctx.identity = session.identity      # known only now
                # THE FOUR NUMBERS every scaling decision needs, none of which
                # existed in production before 2026-08-27. Recorded here because
                # this is the only place that can see all of them at once.
                if metrics is not None:
                    metrics.update({
                        "cache_hits": ctx.cache.hits,
                        "cache_misses": ctx.cache.misses,
                        "wire_requests": session.fetches,
                        # None, not 0: a fully cached run leased no profile at
                        # all, which is a different fact from waiting no time.
                        "lease_wait_seconds": session.lease_wait_seconds,
                        "profile": (session.identity.profile_id
                                    if session.acquired else None),
                        "rate_limited_429s": getattr(ctx.client, "rate_limited", 0),
                        "truncated": bool(ctx.truncated),
                    })
                Actor.log.info(
                    f"cache: {ctx.cache.hits} hits, {ctx.cache.misses} misses"
                    f" · wire: {session.fetches} request(s)"
                    + (f" as {session.identity.profile_id}"
                       if session.acquired
                       else " · NO identity needed (fully cached)"))
                # Say so when the cap cut the answer. A short dataset is
                # indistinguishable from "Pinterest has nothing" unless the run
                # states which one it was.
                if ctx.truncated:
                    Actor.log.warning(
                        f"maxRecords={task.get('maxRecords')} cut this run short"
                        " — MORE WAS AVAILABLE. This dataset is a slice, not the"
                        " whole answer. Raise or remove maxRecords to get it all.")
            if batch:
                put(batch)
        finally:
            put(DONE)

    runner = asyncio.ensure_future(asyncio.to_thread(worker))

    while True:
        item = await queue.get()
        if item is DONE:
            break
        yield item

    # Re-raises whatever the worker raised (VaultEmpty, a scraper error, …) on
    # the loop, so the caller handles it rather than it being lost in the thread.
    await runner


if __name__ == "__main__":
    asyncio.run(main())
