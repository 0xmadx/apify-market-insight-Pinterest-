"""Acquire an identity only when a request actually reaches the wire.

THE PROBLEM THIS SOLVES

`main.py` used to wrap the whole run in `leased_session(...)`. A lease is
exclusive, so concurrent capacity equals profile count — and a run that never
touches Pinterest still held a profile for its full duration. Measured: a
repeat crawl logged `cache: 6 hits, 0 misses` and occupied an identity the
entire time it served from cache.

With the response cache and singleflight in front, most runs need the wire for
only a fraction of their requests. Leasing at run START prices every run as if
it were a full miss.

    before   lease held for the whole run          capacity = profiles
    after    lease held from the first real fetch  capacity = profiles / miss_rate

WHY A PROXY OBJECT AND NOT A REFACTOR

`TrendsClient` touches exactly two things on a session — `.get` and `.post`
(verified). So a stand-in that forwards those two and acquires on first use
needs no change to the transport, the traversals, or the scrapers. The lease
still covers every request that happens, and is still released exactly once.

WHAT THIS DOES NOT CHANGE

The lease remains exclusive while held: two runs never share an identity. This
shortens how long one is held, it does not weaken the guarantee. A run that
does hit the wire behaves exactly as before.
"""
from contextlib import contextmanager

from .session import build_session
from .vault import SessionVault


class LazySession:
    """Stands in for a curl_cffi session; leases on the first real request.

    `acquired` lets a caller report which identity was used without forcing an
    acquisition — `main.py` logs it after the run, when the answer is known and
    a run that never fetched can honestly say so.
    """

    def __init__(self, config, log=None):
        self._config = config
        self._log = log
        self._vault = SessionVault(config)
        self._session = None
        self.identity = None
        self.fetches = 0

    # ---------------------------------------------------------- the seam

    def _ensure(self):
        if self._session is None:
            self.identity = self._vault.acquire(self._config.PLATFORM)
            if self._log:
                self._log(f"leased {self.identity.profile_id} on first wire "
                          f"request (cache served everything before it)")
            self._session = build_session(self.identity, self._config)
        return self._session

    def get(self, *args, **kwargs):
        self.fetches += 1
        return self._ensure().get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self.fetches += 1
        return self._ensure().post(*args, **kwargs)

    # --------------------------------------------------------- lifecycle

    @property
    def acquired(self):
        return self._session is not None

    def close(self):
        """Release exactly once, and only what was actually taken.

        A run that never fetched holds nothing — closing it must be a no-op
        rather than an error, because "we served this entirely from cache" is
        the successful case this whole module exists to make cheap.
        """
        if self._session is None:
            return
        try:
            self._session.close()
        finally:
            session, self._session = self._session, None
            if self.identity is not None:
                self._vault.release(self._config.PLATFORM,
                                    self.identity.profile_id)


@contextmanager
def lazy_session(config, log=None):
    """`leased_session`'s shape, but the lease starts at the first fetch.

        with lazy_session(config) as session:
            ...
        # released here, if it was ever taken

    Yields the session alone rather than (session, identity): the identity is
    not known at entry, and pretending otherwise would hand callers a None they
    would have to remember to re-read. `session.identity` is there afterwards.
    """
    session = LazySession(config, log=log)
    try:
        yield session
    finally:
        session.close()
