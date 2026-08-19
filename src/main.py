"""Apify actor entrypoint.

Shape of a run:

    input -> lease an identity from the vault -> scraper.run() -> dataset

The actor holds no session state of its own. Everything it needs to look like a
real logged-in browser comes from the vault, which is filled by the operator's
Chrome extension. That is what makes this deployable at all: the actor never logs
in, never stores a password, and never runs a browser.
"""
import asyncio

from apify import Actor

from .config import Config
from .scraper import run as run_scraper
from .session import leased_session
from .vault import VaultEmpty

# Records are pushed in batches so the dataset fills as the run proceeds rather
# than only at the end — a run that dies at record 900 should still have 800.
BATCH_SIZE = 100


async def main():
    async with Actor:
        task = await Actor.get_input() or {}
        config = Config()
        Actor.log.info(f"platform={config.PLATFORM} task={task}")

        total = 0
        try:
            async for batch in _batches(task, config):
                await Actor.push_data(batch)
                total += len(batch)
                Actor.log.info(f"pushed {total} records")
        except VaultEmpty as exc:
            # Not a crash, and not a Pinterest problem: there is no session to
            # use. Failing loudly here is the point — a silent empty dataset
            # would read as "Pinterest returned nothing".
            Actor.log.error(str(exc))
            await Actor.fail(status_message="No usable Pinterest session in the vault.")
            return

        Actor.log.info(f"done: {total} records")


async def _batches(task, config):
    """Run the sync scraper in a worker thread, yielding batches to the loop.

    curl_cffi here is synchronous, so it must not run on the event loop; the
    thread keeps the actor responsive to Apify's heartbeats and abort signals.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    loop = asyncio.get_running_loop()
    DONE = object()

    def worker():
        batch = []
        try:
            with leased_session(config) as (session, identity):
                Actor.log.info(f"using profile {identity.profile_id}")
                for record in run_scraper(session, task):
                    batch.append(record)
                    if len(batch) >= BATCH_SIZE:
                        asyncio.run_coroutine_threadsafe(queue.put(batch), loop).result()
                        batch = []
            if batch:
                asyncio.run_coroutine_threadsafe(queue.put(batch), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(DONE), loop).result()

    task_future = asyncio.to_thread(worker)
    runner = asyncio.ensure_future(task_future)

    while True:
        item = await queue.get()
        if item is DONE:
            break
        yield item

    # Re-raises whatever the worker raised (VaultEmpty, a scraper error, …) on the
    # loop, so it is handled by the caller rather than swallowed in the thread.
    await runner


if __name__ == "__main__":
    asyncio.run(main())
