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
from .vault import SessionVault, VaultEmpty

# How many times one run may swap identities after Pinterest refuses one.
#
# Bounded, and low. Each rotation is a fresh account spending a request to
# discover the same "no", so walking a whole pool of dead profiles would turn
# one bad run into five. Two covers the case this exists for — a single corpse
# left in the pool — and stops short of burning the pool to prove a point.
MAX_ROTATIONS = 2


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
        # None means no profile was ever leased -- a fully cached run. That is
        # a different fact from "waited 0s", and conflating them would hide the
        # cheapest runs the actor makes.
        self.lease_wait_seconds = None
        # How many times this run had to swap identities, and whether it gave
        # up. Surfaced in RUN_METRICS: a run that quietly rotated twice is a
        # pool problem the operator should see, not a clean run.
        self.rotations = 0

    # ------------------------------------------------------- recovery

    def rotate(self, kind):
        """Pinterest refused this identity — strike it and take another.

        WHY THE RETRY LIVES HERE AND NOT AROUND THE SCRAPE. Restarting the run
        with a new identity would re-yield records already pushed to the
        dataset; with `fullRescan` the seen-set does not dedup them, so the
        customer gets duplicates. Swapping the identity UNDER one request lets
        `transport.py` retry that single call and carry on, and nothing
        downstream ever learns it happened.

        Returns True when a different identity is in place and the caller
        should retry. False means stop: no replacement was available.
        """
        if self._session is None or self.identity is None:
            return False
        if self.rotations >= MAX_ROTATIONS:
            return False

        failed = self.identity.profile_id
        try:
            self._session.close()
        except Exception:          # a refused session may already be unusable
            pass
        self._session = None

        self._vault.report_rejection(self._config.PLATFORM, failed, kind)
        self.rotations += 1

        # ACQUIRE BEFORE RELEASING, and the order is the whole correctness of
        # this function. `report_rejection` only RETIRES at the strike
        # threshold; on the first strike the profile stays in the serving pool,
        # so releasing it here would let the acquire below hand back the very
        # identity that just failed — a rotation that rotates onto itself, then
        # fails again for the same reason. Holding the lease across the acquire
        # makes that impossible: `_try_lease` skips anything already leased, and
        # we are still the holder.
        try:
            self.identity = self._vault.acquire(self._config.PLATFORM)
        except VaultEmpty:
            # Nothing left to try. The caller raises, and main.py turns that
            # into the ordinary capacity message — the customer never learns
            # that an account died, only that we are busy.
            self.identity = None
            return False
        finally:
            # Always hand the failed one back, success or not. If it was
            # retired this is a no-op on an empty pool slot; if it was only
            # struck, it stays available to others rather than being stranded
            # until the lease TTL expires.
            self._vault.release(self._config.PLATFORM, failed)

        if self._log:
            self._log(f"session refused ({kind}) — rotated off {failed} onto "
                      f"{self.identity.profile_id}")
        self._session = build_session(self.identity, self._config)
        return True

    # ---------------------------------------------------------- the seam

    def _ensure(self):
        if self._session is None:
            self.identity = self._vault.acquire(self._config.PLATFORM)
            self.lease_wait_seconds = self._vault.last_wait_seconds
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
