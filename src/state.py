"""Incremental state: what has already been collected, so runs stop re-pulling it.

Two things live here, both in Redis so they survive across Apify runs (an actor's
own filesystem does not):

    seen:{platform}:{tenant}:{scope}      HASH  id -> "fingerprint|ts|sig"
    watermark:{platform}:{tenant}:{scope} STRING  a cursor the scraper defines

**{tenant} is Config.DEDUP_SCOPE**, and leaving it out was a real bug. The
seen-set records what has already been DELIVERED, which is a per-customer fact;
without the tenant every customer on the shared vault suppressed every other
customer's results for SEEN_TTL (7 days). The response cache is shared on
purpose — sharing an answer is free — but sharing a delivery receipt is not.

**Why a fingerprint and not just an id.** A Pinterest record is not immutable — a
pin seen last week has different save and impression counts today. Keying dedup
on identity alone would mean never seeing a number change again, which quietly
converts a live metric into a one-time snapshot. So a record is "new" when:

  * its id has never been seen, OR
  * its content fingerprint differs from the stored one, OR
  * the stored entry is older than SEEN_TTL — a forced re-read so metrics that
    change slowly still refresh eventually.

**Why marking happens after the push, never before.** If a run marks a batch seen
and then dies before the dataset write lands, those records are lost permanently:
the next run skips them and nothing ever notices. Marking after the push can at
worst duplicate a batch, which is recoverable. Losing data silently is not.
"""
import hashlib
import json
import time

import redis

from .config import Config


def fingerprint(record: dict, fields=None) -> str:
    """A stable hash of the parts of a record whose change should matter.

    `fields` narrows it: pass the metric keys and a record counts as changed only
    when a metric moves, not when Pinterest reorders a list or adds a field we do
    not read.
    """
    if fields:
        subject = {k: record.get(k) for k in sorted(fields)}
    else:
        subject = record
    blob = json.dumps(subject, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def fields_signature(fields) -> str:
    """Identifies *which* fields a stored fingerprint was computed over.

    Without this, changing the `fields` of a record type silently makes every
    stored fingerprint incomparable, so every record looks new forever and the
    actor quietly re-pulls the whole history on every run — expensive, and it
    looks exactly like working correctly. Storing the signature turns that into
    a warning that says what happened.
    """
    if not fields:
        return "all"
    return hashlib.sha1(",".join(sorted(fields)).encode("utf-8")).hexdigest()[:8]


class RunState:
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.r = redis.Redis.from_url(self.config.REDIS_URL, decode_responses=True)
        self._warned = set()

    def _key(self, scope):
        return f"seen:{self.config.PLATFORM}:{self.config.DEDUP_SCOPE}:{scope}"

    def _watermark_key(self, scope):
        return (f"watermark:{self.config.PLATFORM}:"
                f"{self.config.DEDUP_SCOPE}:{scope}")

    # ------------------------------------------------------------- dedup

    def is_new(self, scope: str, record_id: str, fp: str = None,
               fields=None) -> bool:
        """Should this record be collected? See the module docstring for the rule."""
        stored = self.r.hget(self._key(scope), str(record_id))
        if stored is None:
            return True

        parts = stored.split("|")
        if len(parts) != 3:
            # Written by an older version, or corrupt. Unknown is not "seen".
            return True
        stored_fp, stored_ts, stored_sig = parts

        sig = fields_signature(fields)
        if stored_sig != sig:
            warn_key = (scope, stored_sig, sig)
            if warn_key not in self._warned:
                self._warned.add(warn_key)
                print(f"[state] scope '{scope}': the `fields` used for dedup "
                      f"changed ({stored_sig} -> {sig}). Stored fingerprints are "
                      f"not comparable, so this scope re-collects once. Expected "
                      f"after a parser change; unexpected otherwise.")
            return True

        if fp is not None and stored_fp != fp:
            return True

        try:
            age = time.time() - float(stored_ts)
        except (ValueError, TypeError):
            return True
        return age > self.config.SEEN_TTL

    def filter_new(self, scope: str, records, id_key: str, fields=None):
        """Yield only the records worth collecting. Marks nothing."""
        for record in records:
            record_id = record.get(id_key)
            if record_id is None:
                # No id means we cannot dedup it. Pass it through rather than
                # drop it — a missing id is a parser problem, not a duplicate.
                yield record
                continue
            if self.is_new(scope, record_id, fingerprint(record, fields), fields):
                yield record

    def mark_seen(self, scope: str, records, id_key: str, fields=None):
        """Record a batch as collected. Call this AFTER the push succeeds."""
        key = self._key(scope)
        now = time.time()
        mapping = {}
        for record in records:
            record_id = record.get(id_key)
            if record_id is None:
                continue
            mapping[str(record_id)] = (
                f"{fingerprint(record, fields)}|{now}|{fields_signature(fields)}")

        if not mapping:
            return 0

        pipe = self.r.pipeline()
        pipe.hset(key, mapping=mapping)
        # A coarse bound so an abandoned scope cannot grow forever. Refreshed on
        # every write, so an active scope never expires out from under a run.
        pipe.expire(key, self.config.SEEN_KEY_TTL)
        pipe.execute()
        return len(mapping)

    # --------------------------------------------------------- watermark

    def get_watermark(self, scope: str):
        """The cursor/date the last run reached, or None on a first run."""
        return self.r.get(self._watermark_key(scope))

    def set_watermark(self, scope: str, value: str):
        """Only ever move this forward, and only after the data behind it landed."""
        self.r.set(self._watermark_key(scope), str(value))

    # ------------------------------------------------------------ admin

    def stats(self, scope: str) -> dict:
        key = self._key(scope)
        return {
            "scope": scope,
            "records_seen": self.r.hlen(key),
            "expires_in": self.r.ttl(key),
            "watermark": self.get_watermark(scope),
        }

    def reset(self, scope: str):
        """Forget everything about a scope, forcing a full re-pull next run."""
        removed = self.r.delete(self._key(scope))
        self.r.delete(self._watermark_key(scope))
        print(f"[state] reset scope '{scope}' ({removed} key(s) dropped)")
