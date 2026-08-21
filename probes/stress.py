"""Load the vault the way real customers would, and measure what breaks.

    python -m probes.stress                      # the default shape: 100/6/3s
    python -m probes.stress --clients 200
    python -m probes.stress --profiles 12 --spread 60
    python -m probes.stress --stampede           # cache singleflight only

WHY THIS FILE EXISTS
--------------------
`docs/CAPACITY.md` and `docs/SCALING.md` quote measured numbers — 39/100 served
at WAIT_INTERVAL=5s, 60/100 at 1s, the profile curve, the burst behaviour. Every
one of those came from a scratch file that was never committed, which means the
central capacity claims of this project could not be reproduced or re-checked
after any change. A number nobody can re-derive is an opinion with a decimal
point.

WHAT IT DOES NOT DO
-------------------
**It never touches Pinterest.** Work is simulated with a sleep of the measured
per-run duration. Firing 100 real runs to learn about queueing would spend
~19,000 requests on the operator's accounts to measure something that happens
entirely inside Redis.

**It never touches the live pool.** Profiles are seeded into a throwaway
platform namespace and deleted afterwards, so a stress run cannot lease the
identity a real run needs, and cannot evict anything.

THE INVARIANT THAT MATTERS MOST
-------------------------------
Throughput is a business number; **exclusivity is a safety one**. If two workers
ever hold the same profile at the same time, that is one Pinterest session
driven from two places — the exact thing that gets an account flagged. This
harness checks it on every lease and reports it separately from the performance
numbers, because a fast run that violated it is a failure, not a fast run.
"""
import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import replace

from src.cache import ResponseCache
from src.config import Config
from src.vault import SessionVault, VaultEmpty

PLATFORM = "__stress"
COOKIES = {"_auth": "1", "_pinterest_sess": "s", "csrftoken": "t"}
PROXY_TEMPLATE = "http://u:p@10.0.0.{n}:8080"


def seed(vault, count):
    """Fill the throwaway pool with `count` fully valid profiles."""
    wipe(vault)
    for i in range(count):
        pid = f"stress_{i:03d}"
        vault.r.hset(f"cookie:{PLATFORM}:{pid}", mapping={
            "cookies_json": json.dumps(COOKIES),
            "user_agent": "Mozilla/5.0 (stress)",
            "last_updated": str(time.time()),
            "is_valid": "1",
            "proxy": PROXY_TEMPLATE.format(n=i + 1),
        })
        vault.r.sadd(f"valid_profiles:{PLATFORM}", pid)


def wipe(vault):
    for key in vault.r.scan_iter(f"*{PLATFORM}*"):
        vault.r.delete(key)


def heartbeat(vault, count, stop):
    """Keep the seeded profiles fresh for the length of the run.

    Without this a stress run longer than PROFILE_MAX_AGE would start EVICTING
    its own pool halfway through and report a collapse that was the harness's
    fault, not the vault's — a plausible wrong number about the thing this
    project is most careful about.
    """
    while not stop.is_set():
        now = str(time.time())
        for i in range(count):
            vault.r.hset(f"cookie:{PLATFORM}:stress_{i:03d}", "last_updated", now)
        stop.wait(30)


def run_burst(config, clients, work, spread, profiles):
    """`clients` workers each lease, hold for `work` seconds, release."""
    vault_cfg = replace(config, PLATFORM=PLATFORM)
    seeder = SessionVault(vault_cfg)
    seed(seeder, profiles)

    stop = threading.Event()
    beat = threading.Thread(target=heartbeat, args=(seeder, profiles, stop),
                            daemon=True)
    beat.start()

    held = {}                      # profile_id -> worker, while leased
    held_lock = threading.Lock()
    results = []
    results_lock = threading.Lock()
    collisions = []

    def worker(n):
        if spread:
            time.sleep((n / clients) * spread)
        own = SessionVault(vault_cfg)
        t0 = time.monotonic()
        try:
            identity = own.acquire(PLATFORM)
        except VaultEmpty:
            with results_lock:
                results.append({"n": n, "wait": time.monotonic() - t0,
                                "served": False, "profile": None})
            return
        wait = time.monotonic() - t0

        # THE SAFETY CHECK. Two workers holding one profile means one Pinterest
        # session driven from two places at once.
        with held_lock:
            if identity.profile_id in held:
                collisions.append((identity.profile_id, held[identity.profile_id], n))
            held[identity.profile_id] = n

        time.sleep(work)

        with held_lock:
            if held.get(identity.profile_id) == n:
                del held[identity.profile_id]
        own.release(PLATFORM, identity.profile_id)
        with results_lock:
            results.append({"n": n, "wait": wait, "served": True,
                            "profile": identity.profile_id})

    started = time.monotonic()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - started

    stop.set()
    wipe(seeder)
    return results, collisions, wall


def report(results, collisions, wall, args, config):
    served = [r for r in results if r["served"]]
    failed = [r for r in results if not r["served"]]
    waits = sorted(r["wait"] for r in served)

    def pct(p):
        if not waits:
            return 0.0
        return waits[min(len(waits) - 1, int(len(waits) * p))]

    print(f"\n{'=' * 62}")
    print(f"{len(served)}/{len(results)} served · {len(failed)} turned away "
          f"· {wall:.1f}s wall")
    if waits:
        print(f"lease wait  p50 {pct(.5):.1f}s · p95 {pct(.95):.1f}s · "
              f"max {waits[-1]:.1f}s · mean {statistics.mean(waits):.1f}s")

    # Arithmetic floor: no amount of tuning beats it, and knowing that stops a
    # config change being tried where only capacity will do.
    floor = (args.clients * args.work) / args.profiles
    print(f"floor       {args.clients}x{args.work}s / {args.profiles} profiles "
          f"= {floor:.0f}s minimum wall clock")
    if failed:
        print(f"            patience is {config.WAIT_TIMEOUT}s, so "
              f"{'everything past it fails' if floor > config.WAIT_TIMEOUT else 'the failures are queueing, not arithmetic'}")

    spread = {}
    for r in served:
        spread[r["profile"]] = spread.get(r["profile"], 0) + 1
    if spread:
        lo, hi = min(spread.values()), max(spread.values())
        print(f"per-profile {lo}-{hi} runs each across {len(spread)} profiles "
              f"({'balanced' if hi - lo <= max(2, hi * .35) else 'LOPSIDED'})")

    print(f"\nEXCLUSIVITY  ", end="")
    if collisions:
        print(f"❌ {len(collisions)} COLLISION(S) — two runs held one profile:")
        for pid, a, b in collisions[:5]:
            print(f"               {pid}: workers {a} and {b}")
        print("             This is an account-safety failure, not a slow run.")
    else:
        print(f"✅ no profile was ever held by two runs at once "
              f"({len(served)} leases)")
    return 1 if collisions else 0


def run_stampede(config, clients):
    """N workers ask the SAME cold question. Singleflight should collapse it."""
    cfg = replace(config, PLATFORM=PLATFORM)
    cache = ResponseCache(cfg)
    url, params = "https://stress/one", {"k": "v"}
    cache.clear()

    fills = []
    late = []
    lock = threading.Lock()

    class Filled:
        """The shape cache.put() checks: a 200 with a non-empty JSON body."""
        status_code = 200
        text = '{"answer": 1}'

    def worker(n):
        hit = cache.get("trends", url, params)
        if hit is not None:
            return
        if cache.acquire_fill("trends", url, params):
            with lock:
                fills.append(n)
            time.sleep(0.4)                      # the "wire request"
            # PUT THEN RELEASE, in that order, because that is what
            # transport._store does. An earlier version of this harness
            # released without putting, so every waiter polled an empty key
            # for the full 30s timeout and the run still reported "1 wire
            # request" — a plausible wrong number produced by a fake that did
            # not do what the real code does.
            cache.put("trends", url, Filled(), params)
            cache.release_fill("trends", url, params)
        else:
            if cache.wait_for_fill("trends", url, params) is None:
                # Timed out. In production this worker now fetches anyway, so
                # it is a REAL extra request — count it as one.
                with lock:
                    late.append(n)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - t0
    cache.clear()

    total = len(fills) + len(late)
    print(f"\n{'=' * 62}")
    print(f"stampede: {clients} clients on ONE cold key -> "
          f"{total} wire request(s) in {wall:.1f}s")
    if late:
        print(f"          {len(late)} waiter(s) timed out after "
              f"{config.FILL_WAIT_TIMEOUT}s and would fetch anyway")
    ok = total == 1
    print("          " + ("✅ collapsed to a single fetch"
                          if ok else
                          f"❌ {total} fetches — singleflight did not hold"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clients", type=int, default=100)
    ap.add_argument("--profiles", type=int, default=6,
                    help="pool size (default 6 = the live pool on 2026-08-20)")
    ap.add_argument("--work", type=float, default=3.0,
                    help="seconds per run (measured: 3 cached, 10 keyword, 38 shopping)")
    ap.add_argument("--spread", type=float, default=0.0,
                    help="seconds to spread arrivals over (0 = all at once, "
                         "which is a load test and not traffic)")
    ap.add_argument("--stampede", action="store_true",
                    help="run the cache singleflight test instead")
    args = ap.parse_args()

    config = Config()
    vault = SessionVault(config)
    try:
        vault.r.ping()
    except Exception as exc:
        print(f"cannot reach Redis — {exc}", file=sys.stderr)
        return 2

    if args.stampede:
        return run_stampede(config, args.clients)

    print(f"{args.clients} clients · {args.profiles} profiles · {args.work}s work "
          f"· arrivals over {args.spread}s")
    print(f"patience {config.WAIT_TIMEOUT}s · poll {config.WAIT_INTERVAL}s "
          f"· lease TTL {config.LEASE_TTL}s")
    results, collisions, wall = run_burst(config, args.clients, args.work,
                                          args.spread, args.profiles)
    return report(results, collisions, wall, args, config)


if __name__ == "__main__":
    sys.exit(main())
