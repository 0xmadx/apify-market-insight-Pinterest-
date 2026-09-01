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
`verify`) — none of their jobs is bulk text generation, and giving one of them
a fifth responsibility is exactly the kind of scope creep the agent split was
built to avoid.

THE KEY
-------
`DEEPSEEK_API_KEY` in `.env`, read the same way `adspower/keys.py` reads
`ADS_API_KEY` — loaded once, never logged, never printed. This file lives in
`tools/`, which `.actorignore` and `.dockerignore` both already exclude, so it
never reaches Apify or the image regardless.
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

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
    return choices[0]["message"]["content"]


def main():
    """CLI for a quick manual draft. Prints ONLY the model's text to stdout,
    so it composes with a redirect: `python -m tools.deepseek "..." > draft.md`
    """
    if len(sys.argv) < 2:
        print("usage: python -m tools.deepseek \"<prompt>\" [--system \"...\"]",
              file=sys.stderr)
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
