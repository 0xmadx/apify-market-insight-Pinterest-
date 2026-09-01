"""A DeepSeek client — for DRAFTING PROSE, never for the actor's data.

WHERE THIS DOES AND DOES NOT BELONG
------------------------------------
This project's whole premise is that every number and every piece of text a
customer receives is REAL — pulled from Pinterest, never invented. `src/`
already enforces that: parsers return `None` rather than guessing, and
`radar.py`'s `description` field is Pinterest's own editorial copy, not
generated text. Wiring an LLM into that path would break the actual product.

So this file is not imported by `src/` and never will be. Its only legitimate
job is drafting PROSE A HUMAN WILL READ AND EDIT before it goes anywhere near a
customer — e.g. a first pass at a new product's README (`actors/marketers/`,
`actors/ecommerce/`, once those exist), a marketing blurb, a changelog entry.
Always a draft. Never auto-published.

It is not wired into any of the four agents (`publish`, `farm`, `cost`,
`verify`) as a caller — none of their jobs is bulk text generation. `cost`
DOES read the usage log this file writes (see below), because a cost audit
that misses one of the things spending money is not an audit.

THE KEY
-------
`DEEPSEEK_API_KEY` in `.env`, read the same way `adspower/keys.py` reads
`ADS_API_KEY` — loaded once, never logged, never printed. This file lives in
`tools/`, which `.actorignore` and `.dockerignore` both already exclude, so it
never reaches Apify or the image regardless.

WHY THIS FILE ALSO LOGS EVERY CALL'S COST
------------------------------------------
The Upstash bill on 2026-09-01 was a real lesson: $32.34 arrived with no
warning, and untangling it after the fact took reading an invoice and querying
the server directly. Nothing here should repeat that. Every `complete()` call
appends its token counts and an estimated cost to a local, gitignored log —
so "what has this cost me" is always a `python -m tools.deepseek --usage` away,
not a surprise next month.

PRICING IS TIME-DEPENDENT AND WILL DRIFT
------------------------------------------
DeepSeek bills PEAK vs OFF-PEAK, and peak is 2x the price. Verified against
https://api-docs.deepseek.com/quick_start/pricing on 2026-09-01:

    peak hours (UTC, Mon-Fri): 01:00-04:00 and 06:00-10:00
    everything else, and all of Sat/Sun: off-peak, half the peak rate

The rates in PRICING below are a snapshot of that same fetch. Treat them as
approximate and re-check the URL above before trusting a large estimate — this
file does not phone home to verify its own prices, on principle: an LLM client
that silently calls a pricing API on every text-drafting request is exactly the
kind of hidden cost this file exists to prevent.
"""
import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
PRICING_DOCS_URL = "https://api-docs.deepseek.com/quick_start/pricing"

# "deepseek-chat" is a deprecated alias (retirement flagged for 2026-07-24) —
# using it risks silently pointing at whatever it gets remapped to next.
# deepseek-v4-flash is the current cheap/fast model and is the right default
# for drafting prose that a human edits anyway.
DEFAULT_MODEL = "deepseek-v4-flash"

# $ per MILLION tokens. Snapshot 2026-09-01 from the docs URL in the module
# docstring — re-verify before relying on a large estimate.
PRICING = {
    "deepseek-v4-flash": {
        "cache_hit":  {"peak": 0.014, "off_peak": 0.007},
        "cache_miss": {"peak": 0.44,  "off_peak": 0.22},
        "output":     {"peak": 1.32,  "off_peak": 0.66},
    },
    "deepseek-v4-pro": {
        "cache_hit":  {"peak": 0.044, "off_peak": 0.022},
        "cache_miss": {"peak": 1.32,  "off_peak": 0.66},
        "output":     {"peak": 3.96,  "off_peak": 1.98},
    },
}
PRICING["deepseek-v4-flash-vision-exp"] = PRICING["deepseek-v4-flash"]

USAGE_LOG = pathlib.Path(__file__).resolve().parent / ".deepseek_usage.jsonl"

_loaded = False


def _load_env():
    """Read the repo-root `.env` once, without overriding an exported value.

    Same rule as `adspower/keys.py`: a variable exported in the shell for this
    one run is deliberate and must not be silently beaten by the file.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = pathlib.Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value:
            os.environ.setdefault(name, value)


def api_key():
    _load_env()
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def is_peak(when):
    """DeepSeek's peak window: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri.

    `when` must already be in UTC. Weekends are off-peak entirely, at every
    hour — a common mistake would be checking only the hour and missing that.
    """
    if when.weekday() >= 5:            # Saturday=5, Sunday=6
        return False
    hour = when.hour
    return (1 <= hour < 4) or (6 <= hour < 10)


def estimate_cost(usage, model, when=None):
    """Turn a DeepSeek `usage` object into a dollar estimate.

    Conservative by construction: if the response does not break prompt
    tokens into cache-hit vs cache-miss, EVERY prompt token is billed at the
    more expensive cache-miss rate. Assuming the cheap case when it is not
    confirmed is exactly the kind of optimistic guess this project refuses to
    make anywhere else — an under-estimate here would be a second version of
    the Upstash surprise, not a fix for it.
    """
    when = when or datetime.datetime.now(datetime.timezone.utc)
    band = "peak" if is_peak(when) else "off_peak"
    rates = PRICING.get(model, PRICING[DEFAULT_MODEL])

    hit = usage.get("prompt_cache_hit_tokens", 0) or 0
    total_prompt = usage.get("prompt_tokens", 0) or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max(total_prompt - hit, 0)
    completion = usage.get("completion_tokens", 0) or 0

    cost = (hit / 1_000_000 * rates["cache_hit"][band]
            + miss / 1_000_000 * rates["cache_miss"][band]
            + completion / 1_000_000 * rates["output"][band])
    return round(cost, 6), band


def _log(model, usage, cost, band, prompt):
    """Append one line. Never let logging failure break a draft that already
    succeeded — the call happened and cost money either way; losing the log
    entry is bad, losing the draft on top of it would be worse.
    """
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": model,
        "band": band,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "estimated_cost_usd": cost,
        # First 60 chars only — enough to recall what a line was for, not a
        # permanent copy of every draft ever generated.
        "prompt_preview": (prompt or "")[:60],
    }
    try:
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def complete(prompt, system=None, model=DEFAULT_MODEL, temperature=0.7,
            max_tokens=2000, timeout=60):
    """One prompt in, the model's text out. Raises on any failure rather than
    returning an empty string — a silent empty here would look exactly like
    "the model said nothing" instead of "the call failed", which is the kind
    of ambiguity this whole project refuses to allow anywhere else.
    """
    key = api_key()
    if not key:
        raise SystemExit(
            "no DeepSeek API key (DEEPSEEK_API_KEY). Add it to .env — the "
            "same file that holds ADS_API_KEY and WEBSHARE_API, not tracked "
            "by git. Never pass it on a command line; that puts it in shell "
            "history.")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"DeepSeek HTTP {exc.code}: {detail}") from None

    choices = payload.get("choices") or []
    if not choices:
        raise SystemExit(f"DeepSeek returned no choices: {payload}")

    usage = payload.get("usage") or {}
    if usage:
        cost, band = estimate_cost(usage, model)
        _log(model, usage, cost, band, prompt)
        print(f"  [deepseek: {usage.get('prompt_tokens', 0)}+"
              f"{usage.get('completion_tokens', 0)} tokens, {band}, "
              f"~${cost:.4f}]", file=sys.stderr)

    return choices[0]["message"]["content"]


def usage_summary(log_path=USAGE_LOG):
    """Total calls, tokens and estimated cost ever logged. `cost` reads this.

    Returns zeros rather than raising when nothing has been logged yet — an
    agent asking "what has this cost" before the first call is a normal
    question, not an error.
    """
    if not log_path.exists():
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "estimated_cost_usd": 0.0}
    calls = prompt_tok = completion_tok = 0
    cost = 0.0
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue          # a corrupt line must not sink the whole total
            calls += 1
            prompt_tok += entry.get("prompt_tokens", 0)
            completion_tok += entry.get("completion_tokens", 0)
            cost += entry.get("estimated_cost_usd", 0)
    return {"calls": calls, "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
            "estimated_cost_usd": round(cost, 4)}


def main():
    """CLI. Either drafts (prints ONLY the draft to stdout, so it composes
    with a redirect: `python -m tools.deepseek "..." > draft.md`) or, with
    `--usage`, prints the running cost total and exits.
    """
    if "--usage" in sys.argv:
        s = usage_summary()
        print(f"  {s['calls']} call(s) logged")
        print(f"  {s['prompt_tokens']} prompt + {s['completion_tokens']} "
              f"completion tokens")
        print(f"  ~${s['estimated_cost_usd']:.4f} estimated total "
              f"(re-check {PRICING_DOCS_URL} periodically)")
        return 0

    if len(sys.argv) < 2:
        print("usage: python -m tools.deepseek \"<prompt>\" [--system \"...\"]"
              "\n       python -m tools.deepseek --usage", file=sys.stderr)
        return 2
    prompt = sys.argv[1]
    system = None
    if "--system" in sys.argv:
        i = sys.argv.index("--system")
        system = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    print(complete(prompt, system=system))
    return 0


if __name__ == "__main__":
    sys.exit(main())
