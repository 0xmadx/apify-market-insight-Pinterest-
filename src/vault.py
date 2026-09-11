"""Read side of the Redis session vault.

This is the piece carried over from the Etsy repo, deliberately narrowed: no Etsy
platforms, no seller-token guard, no shop_id. It reads the same keys the Go cookie
server already writes, so the existing Chrome extension + Go server keep working
untouched.

Key schema (unchanged, so both projects can share one vault):

    cookie:{platform}:{profile_id}   HASH
        cookies_json   json object  {name: value}
        user_agent     str          the UA of the browser the cookies were born in
        last_updated   unix seconds written by the Go server on every beacon
        is_valid       "1" | "0"
        proxy          str          per-profile exit IP, written by
                                    adspower/sync_cookies.py. Absent means
                                    the profile exits from the HOST.
    valid_profiles:{platform}        SET of profile_id — the SERVING pool.
                                     What `acquire` may hand a customer now.
    known_profiles:{platform}        SET of profile_id — the FARM ROSTER.
                                     Every profile the refresher should keep
                                     trying, including ones currently out of
                                     service. A profile enters when it is
                                     evicted and leaves only when a human
                                     retires it. See `_evict` for why these
                                     must not be the same set.

Added here, and absent from the parent repo: a LEASE. Two concurrent Apify runs
drawing the same profile would drive one Pinterest session from two IPs at once,
which is the fastest way to get it flagged. The lease makes that impossible
without changing anything the extension writes.

    lease:{platform}:{profile_id}    STRING with TTL, set NX
"""
import json
import time

import redis

from .config import Config


class VaultEmpty(RuntimeError):
    """No usable session for this platform.

    A distinct type because the caller must tell "we have no session" apart from
    "Pinterest said no". The first is fixed in Chrome; the second is real signal.
    """


class Identity:
    """One browser session as a single unit: cookies + the UA + the exit IP.

    These travel together or not at all. Mixing a cookie jar with a UA or an IP it
    was never seen with is exactly what a fingerprinter looks for.
    """

    def __init__(self, profile_id, cookies, user_agent, proxy, age):
        self.profile_id = profile_id
        self.cookies = cookies
        self.user_agent = user_agent
        self.proxy = proxy
        self.age = age
        # Filled in by session.build_session, which derives the TLS fingerprint
        # from THIS identity's user agent rather than a global constant. Kept on
        # the identity so a log line, a probe or a test can say which handshake
        # actually went out and how it was chosen.
        self.impersonate = None
        self.impersonate_basis = None

    def __repr__(self):
        tls = f" tls={self.impersonate}" if self.impersonate else ""
        return (f"<Identity {self.profile_id} cookies={len(self.cookies)} "
                f"age={int(self.age)}s{tls}>")


class SessionVault:
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.r = redis.Redis.from_url(self.config.REDIS_URL, decode_responses=True)
        self._noted = set()

    # ---------------------------------------------------------------- reading

    # Seconds the last acquire() spent waiting. None until one runs.
    last_wait_seconds = None

    def acquire(self, platform: str = None) -> Identity:
        """Lease a usable identity, waiting a bounded time for one to appear.

        Raises VaultEmpty rather than looping forever: a scheduled run that wedges
        overnight and reports nothing is worse than one that fails loudly.
        """
        platform = platform or self.config.PLATFORM
        started = time.time()
        deadline = started + self.config.WAIT_TIMEOUT

        while True:
            for profile_id in self._candidates(platform):
                identity = self._try_lease(platform, profile_id)
                if identity is not None:
                    # How long a run waited for a profile is THE number that
                    # says whether the pool is big enough. Concurrent capacity
                    # equals the profile count, so when this starts climbing the
                    # answer is more accounts -- not more tuning. Recorded on
                    # the vault rather than logged, so the caller decides what
                    # to do with it.
                    self.last_wait_seconds = round(time.time() - started, 3)
                    return identity

            if time.time() >= deadline:
                self.last_wait_seconds = round(time.time() - started, 3)
                raise VaultEmpty(
                    f"No leasable '{platform}' profile after "
                    f"{self.config.WAIT_TIMEOUT}s. Check that Chrome is open with the "
                    f"extension active, then run `python -m src.status`."
                )
            time.sleep(self.config.WAIT_INTERVAL)

    def _candidates(self, platform):
        """Every profile in the valid pool, SHUFFLED.

        The docstring used to say "arbitrary order", which is true of Redis and
        false in practice: SMEMBERS returns the same order every call for an
        unchanged set, and `acquire` takes the first leasable one. So one
        profile absorbed every request while the rest idled — measured with
        three profiles in the pool, eight consecutive leases all drew the same
        identity.

        That is the opposite of what the pool is for. Each profile is a
        separate account behind its own proxy; concentrating traffic on one
        makes that one look like a bot and leaves the others cold, which is
        itself a signal. Shuffling spreads the load, and the lease still
        guarantees no two runs share an identity.
        """
        import random

        pool = list(self.r.smembers(f"valid_profiles:{platform}") or [])
        random.shuffle(pool)
        return pool

    def _try_lease(self, platform, profile_id):
        """Validate one profile and claim it. None if it is unusable or taken."""
        data = self.r.hgetall(f"cookie:{platform}:{profile_id}")

        if not data:
            # Pool member with no hash behind it. Self-heal.
            self.r.srem(f"valid_profiles:{platform}", profile_id)
            return None

        raw_cookies = data.get("cookies_json")
        if not raw_cookies:
            # A profile with no cookies authenticates as nobody. Letting it through
            # sends an anonymous request whose failure looks like a site change
            # rather than a missing session.
            self._evict(platform, profile_id, "no cookies")
            return None

        # A jar can be non-empty and still be signed out — the check above passes
        # on a logged-out browser. Without the auth cookies every request goes out
        # anonymous, and Pinterest answers those with plausible *public* data, so
        # the run "succeeds" while collecting the wrong thing. That is the failure
        # mode this whole file exists to prevent: a plausible wrong number.
        try:
            present = set(json.loads(raw_cookies) or {})
        except (ValueError, TypeError):
            present = set()
        missing = [c for c in self.config.required_cookies if c not in present]
        if missing:
            self._evict(platform, profile_id,
                        f"signed out — missing {', '.join(missing)}")
            return None

        age = self._age(data)
        if age is None:
            # No heartbeat field at all — written by something other than the Go
            # server. Unknown freshness is not freshness; refuse rather than guess.
            self._evict(platform, profile_id, "no last_updated heartbeat")
            return None
        if age > self.config.PROFILE_MAX_AGE:
            self._evict(platform, profile_id, f"stale ({int(age)}s since heartbeat)")
            return None

        # No proxy means this identity exits from whatever host the run is on.
        # SKIPPED, NOT EVICTED — and the distinction is the point. An eviction
        # says "this session is burned"; a missing proxy says "this profile is
        # not finished being set up". The writer re-adds it on its next beacon
        # either way, so evicting would only churn the pool while hiding the
        # real state. Skipping leaves it visible in `describe()` with the reason
        # attached, which is where the operator can act on it.
        if self.config.REQUIRE_PROXY and not (data.get("proxy") or "").strip():
            self._note_once(
                platform, profile_id,
                "no proxy — would exit from this host's IP. Assign one with "
                "`adspower/assign_proxies.py`, or set REQUIRE_PROXY=0 if this "
                "machine has no proxies at all.")
            return None

        # SET NX is the whole of the mutual exclusion: whoever sets it owns the
        # profile until the TTL expires, so a crashed run releases it unaided.
        if not self.r.set(
            f"lease:{platform}:{profile_id}", "1",
            nx=True, ex=self.config.LEASE_TTL,
        ):
            return None

        try:
            cookies = json.loads(raw_cookies)
        except (ValueError, TypeError):
            self.release(platform, profile_id)
            self._evict(platform, profile_id, "cookies_json is not valid JSON")
            return None

        return Identity(
            profile_id=profile_id,
            cookies=cookies,
            user_agent=data.get("user_agent") or self.config.USER_AGENT_FALLBACK,
            # Read from THIS profile's hash and never from a global: the whole
            # point is one exit IP per account. None is reachable only when
            # REQUIRE_PROXY is off, and then means "exits from the host".
            proxy=data.get("proxy") or None,
            age=age,
        )

    @staticmethod
    def _age(data):
        raw = data.get("last_updated")
        if not raw:
            return None
        try:
            return time.time() - float(raw)
        except (ValueError, TypeError):
            return None

    # ---------------------------------------------------------------- writing

    def release(self, platform: str, profile_id: str):
        """Hand the identity back. Safe to call twice."""
        self.r.delete(f"lease:{platform}:{profile_id}")

    # Consecutive rejections that retire a profile.
    #
    # NOT ONE. A single 403 can be a blip, and with five profiles an instant
    # retirement throws away a fifth of capacity on one bad response. NOT HIGH
    # either: every strike is a real customer run that already failed. Two means
    # "it happened twice in a row", which no longer reads as noise.
    #
    # Reset is FREE and lives in browsers/keepalive.py:write(), which already
    # sends an HSET on every successful refresh -- `auth_failures: 0` rides
    # along in that existing mapping. Clearing it from the actor instead would
    # cost one Redis command per successful request, on a plan metered per
    # command, to record the ordinary case.
    REJECTION_STRIKES = 2

    def report_rejection(self, platform: str, profile_id: str, kind: str) -> bool:
        """Pinterest refused this identity. Count it. Retire at the threshold.

        WHY THIS EXISTS. `classify()` has always been able to tell `auth_expired`
        and `blocked` from a merely malformed request, and `transport.py` has
        always raised on them -- but it told NOBODY WHICH PROFILE. `mark_blocked`
        below sat with zero callers in the entire repo, so a dead session stayed
        in the serving pool until it happened to age out, and every run that drew
        it failed. Found 2026-09-10.

        Returns True when the profile was retired by this call.
        """
        key = f"cookie:{platform}:{profile_id}"
        strikes = self.r.hincrby(key, "auth_failures", 1)
        # The reason travels with the profile so `src.status` can say what to DO
        # -- "auth_expired" means a human logs in again, "blocked" means that
        # account tripped bot detection. Different jobs, same as keepalive's
        # signed-out vs wrong-exit-IP distinction.
        self.r.hset(key, mapping={"last_error": f"Pinterest rejected it ({kind})",
                                  "last_error_at": str(time.time())})
        if strikes >= self.REJECTION_STRIKES:
            self.mark_blocked(platform, profile_id, kind, strikes)
            return True
        print(f"[vault] {platform}/{profile_id}: {kind}, strike "
              f"{strikes}/{self.REJECTION_STRIKES} — still in the pool")
        return False

    def mark_blocked(self, platform: str, profile_id: str,
                     kind: str = "blocked", strikes: int = None):
        """Pinterest rejected this session — take it out of rotation.

        With one profile in the pool this empties the vault, which is correct: the
        fix is a human re-login, and a run that keeps hammering a burned session
        only makes that worse.

        Reached through `report_rejection` rather than directly, so one unlucky
        response cannot cost a profile. It stays on the `known_profiles` roster,
        so the refresher keeps watching it and returns it to service by itself
        once someone logs back in.
        """
        count = f" x{strikes}" if strikes else ""
        self._evict(platform, profile_id, f"{kind}{count}")
        self.release(platform, profile_id)

    def _note_once(self, platform, profile_id, reason):
        """Log a skip reason once per vault instance.

        `acquire` re-walks the pool every WAIT_INTERVAL (1s), so a plain print
        here would emit the same line sixty times while a run waits out its
        timeout — and a reason repeated sixty times reads as noise rather than
        as the one thing the operator needs to fix.
        """
        if profile_id in self._noted:
            return
        self._noted.add(profile_id)
        print(f"[vault] skipping {platform}/{profile_id}: {reason}")

    def _evict(self, platform, profile_id, reason):
        """Take a profile OUT OF SERVICE without losing it.

        THE BUG THIS FIXES, found 2026-09-10. Eviction is `SREM valid_profiles`,
        and `browsers/keepalive.py:load_from_vault()` builds its work list from
        that same set. So evicting a profile hid it from the ONLY writer that
        could ever repair it: the operator re-logged in inside AdsPower and
        nothing happened, because the refresher no longer knew the profile
        existed. Recovery needed a manual `sync_cookies.py` run, every time.

        Two sets, two different questions, and conflating them was the defect:

            valid_profiles   may `acquire` hand this to a customer RIGHT NOW?
            known_profiles   is this profile part of the farm at all?

        A profile leaves the first the moment it misbehaves and stays in the
        second until a human retires it with `browsers.identities remove`. The
        keepalive pass reads the union, so a re-login heals the profile back
        into service on the next 5-minute cycle with nothing copied and nothing
        triggered. That is also the answer to "how does Apify tell GCP it needs
        a fresh session": it does not send anything. It records a fact in Redis
        and the writer reads it — no inbound path into private infrastructure.

        One extra command, and only on an eviction. Evictions are meant to be
        rare; if this shows up on the Upstash bill, that is the alarm working.
        """
        self.r.sadd(f"known_profiles:{platform}", profile_id)
        self.r.hset(f"cookie:{platform}:{profile_id}", "is_valid", "0")
        self.r.srem(f"valid_profiles:{platform}", profile_id)
        print(f"[vault] evicted {platform}/{profile_id}: {reason}")

    # ------------------------------------------------------------- inspection

    def describe(self, platform: str = None) -> list:
        """Every profile with why it is or is not usable. Diagnostics only."""
        platform = platform or self.config.PLATFORM
        # THE ROSTER, not just the serving pool. This used to walk
        # `_candidates()`, which reads `valid_profiles` alone — so the moment a
        # profile was evicted it disappeared from `src.status` too, taking its
        # `last_error` with it. The operator lost sight of a profile at exactly
        # the moment it needed attention, and the pool silently looked smaller
        # rather than damaged. Same root cause as the keepalive blindness; see
        # `_evict`.
        serving = set(self.r.smembers(f"valid_profiles:{platform}") or [])
        roster = set(self.r.smembers(f"known_profiles:{platform}") or [])
        out = []
        for profile_id in sorted(serving | roster):
            data = self.r.hgetall(f"cookie:{platform}:{profile_id}") or {}
            age = self._age(data)
            cookies_raw = data.get("cookies_json")
            try:
                cookie_count = len(json.loads(cookies_raw)) if cookies_raw else 0
            except (ValueError, TypeError):
                cookie_count = None
            out.append({
                "profile_id": profile_id,
                "cookie_count": cookie_count,
                "age_seconds": None if age is None else int(age),
                "has_user_agent": bool(data.get("user_agent")),
                # The VALUE, not just its presence: `status` compares the
                # browser it claims against the TLS fingerprint we impersonate.
                "user_agent": data.get("user_agent"),
                "has_proxy": bool((data.get("proxy") or "").strip()),
                # Written by browsers/keepalive.py when a refresh fails, so the
                # REASON travels to whoever runs `src.status` instead of staying
                # in the VM's journal. Empty once a later pass succeeds.
                "last_error": (data.get("last_error") or "").strip(),
                "leased": bool(self.r.exists(f"lease:{platform}:{profile_id}")),
                # False = on the roster but NOT servable right now. It is being
                # refreshed and will return by itself if it starts answering.
                "serving": profile_id in serving,
                "strikes": int(data.get("auth_failures") or 0),
            })
        return out
