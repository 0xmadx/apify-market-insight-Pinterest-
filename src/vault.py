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
        proxy          str          OPTIONAL, nothing writes it in phase 1
    valid_profiles:{platform}        SET of profile_id

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

    def __repr__(self):
        return f"<Identity {self.profile_id} cookies={len(self.cookies)} age={int(self.age)}s>"


class SessionVault:
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.r = redis.Redis.from_url(self.config.REDIS_URL, decode_responses=True)

    # ---------------------------------------------------------------- reading

    def acquire(self, platform: str = None) -> Identity:
        """Lease a usable identity, waiting a bounded time for one to appear.

        Raises VaultEmpty rather than looping forever: a scheduled run that wedges
        overnight and reports nothing is worse than one that fails loudly.
        """
        platform = platform or self.config.PLATFORM
        deadline = time.time() + self.config.WAIT_TIMEOUT

        while True:
            for profile_id in self._candidates(platform):
                identity = self._try_lease(platform, profile_id)
                if identity is not None:
                    return identity

            if time.time() >= deadline:
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
            # None throughout phase 1 — nothing writes this field yet. Read from
            # the profile and never from a global, so that phase 2 (AdsPower +
            # Webshare, one proxy per profile) changes the writer, not this code.
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

    def mark_blocked(self, platform: str, profile_id: str):
        """Pinterest rejected this session — take it out of rotation.

        With one profile in the pool this empties the vault, which is correct: the
        fix is in Chrome (re-login), and a run that keeps hammering a burned
        session only makes that worse.
        """
        self._evict(platform, profile_id, "blocked by the target")
        self.release(platform, profile_id)

    def _evict(self, platform, profile_id, reason):
        self.r.hset(f"cookie:{platform}:{profile_id}", "is_valid", "0")
        self.r.srem(f"valid_profiles:{platform}", profile_id)
        print(f"[vault] evicted {platform}/{profile_id}: {reason}")

    # ------------------------------------------------------------- inspection

    def describe(self, platform: str = None) -> list:
        """Every profile with why it is or is not usable. Diagnostics only."""
        platform = platform or self.config.PLATFORM
        out = []
        for profile_id in self._candidates(platform):
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
                "leased": bool(self.r.exists(f"lease:{platform}:{profile_id}")),
            })
        return out
