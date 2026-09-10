"""What a customer sees when the vault can't serve their run.

    .venv/Scripts/python.exe -m tests.test_main_messages

Two failures land here, and they must look the same to a CUSTOMER while
staying distinguishable to the OPERATOR:

  - VaultEmpty: Redis answered, every account is in use right now. Recorded
    as `vault_empty` — the "buy more accounts" signal (src/vault.py's own
    comment: "when this starts climbing the answer is more accounts").
  - A Redis connection failure: Upstash itself is unreachable. A DIFFERENT
    problem for the operator — this means "check Upstash's status", not "buy
    accounts" — so it must never be silently folded into vault_empty and it
    must never reach the customer as a raw traceback mentioning "redis" or
    "vault" (the same internal-jargon leak already fixed once this session
    for VaultEmpty itself).

`_customer_capacity_message` is a pure function specifically so this is
testable without a live Actor or a live/dead Redis — see CLAUDE.md's own
lesson about the config-precedence incident: guessing at what a failure
looks like from the caller's log line, instead of reading the code, is how a
90-minute outage went unnoticed twice.
"""
import sys

sys.path.insert(0, ".")

import redis  # noqa: E402

from src.main import _customer_capacity_message  # noqa: E402
from src.vault import VaultEmpty  # noqa: E402

checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not condition else ""))


def main():
    empty_msg = _customer_capacity_message(VaultEmpty("no usable profile after 60s"))
    down_msg = _customer_capacity_message(
        redis.exceptions.ConnectionError("Error 111 connecting to ...upstash.io:6379"))
    timeout_msg = _customer_capacity_message(
        redis.exceptions.TimeoutError("Timeout connecting to server"))

    for label, msg in (("VaultEmpty", empty_msg), ("ConnectionError", down_msg),
                       ("TimeoutError", timeout_msg)):
        for leak in ("redis", "vault", "upstash", "6379", "6380"):
            check(f"{label}: customer message never leaks {leak!r}",
                  leak not in msg.lower(), msg)

    check("VaultEmpty and a Redis outage read differently to a customer "
          "(they are different situations, even if both say 'try again')",
          empty_msg != down_msg, (empty_msg, down_msg))

    check("a plain 'try again' framing appears in every message",
          all("try again" in m.lower() for m in (empty_msg, down_msg, timeout_msg)))

    # Any RedisError subclass must be handled the same way — the customer
    # should never see a raw exception type name determine whether they get
    # a clean message or a crash.
    for cls in (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError,
               redis.exceptions.RedisError):
        msg = _customer_capacity_message(cls("anything"))
        check(f"{cls.__name__} produces a real customer message, not None/empty",
              isinstance(msg, str) and len(msg) > 10)

    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
