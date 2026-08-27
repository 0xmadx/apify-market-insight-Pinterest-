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


async def main():
    async with Actor:
        task = await Actor.get_input() or {}
        config = Config()
        state = RunState(config)
        full_rescan = bool(task.get("fullRescan"))

        Actor.log.info(f"platform={config.PLATFORM} task={task}")
        if full_rescan:
            Actor.log.warning("fullRescan: ignoring the seen-set for this run")
        if task.get("forceRefresh"):
            Actor.log.warning("forceRefresh: ignoring cached responses for this run")

        pushed = skipped = 0
        try:
            async for batch in _batches(task, config):
                fresh, already_held = _split(batch, state, full_rescan)
                skipped += already_held

                if fresh:
                    await Actor.push_data([r.data for r in fresh])
                    pushed += len(fresh)
                    _mark(fresh, state)  # only now — see the ordering rule above

                Actor.log.info(f"pushed {pushed} · skipped {skipped} already held")
        except VaultEmpty as exc:
            # Not a crash, and not a Pinterest problem: there is no session to
            # use. Failing loudly is the point — a silent empty dataset would
            # read as "Pinterest returned nothing".
            Actor.log.error(str(exc))
            await Actor.fail(status_message="No usable Pinterest session in the vault.")
            return

        Actor.log.info(f"done: {pushed} new, {skipped} skipped as unchanged")


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


async def _batches(task, config):
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
