"""Where the AdsPower and Webshare keys come from.

Both scripts here read `os.environ` and nothing loaded `.env` into it, so every
invocation needed the operator to export two variables by hand first — and the
failure was a flat "need ADS_API_KEY and WEBSHARE_API in the environment" even
with both sitting in `.env` a directory up.

The AdsPower key also has TWO spellings in the wild. The scripts were written
against `ADS_API_KEY`; the operator's `.env` calls it `adspower_api`. Accepting
both is not sloppiness — the alternative is a rename that silently breaks
whichever half of the setup is not being looked at today.

Nothing here ever prints or returns a key by accident: `describe()` exists so a
failure message can say WHICH name was found without showing the value.
"""
import os
import pathlib

# First name wins when several are set. The documented spelling leads.
ADS_NAMES = ("ADS_API_KEY", "ADSPOWER_API", "adspower_api")
WEBSHARE_NAMES = ("WEBSHARE_API", "WEBSHARE_API_KEY", "webshare_api")

_loaded = False


def load_env():
    """Read the repo-root `.env` into os.environ, once, without overriding.

    Not overriding is the point: a variable exported in the shell is a
    deliberate act for this one run, and a file must never quietly beat it.
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
        # `setdefault`, so an exported value survives.
        if name and value:
            os.environ.setdefault(name, value)


def get(*names):
    """The first of these environment variables that holds something."""
    load_env()
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def ads_key():
    return get(*ADS_NAMES)


def webshare_key():
    return get(*WEBSHARE_NAMES)


def describe(names):
    """Which of these names is set — for an error message, never the value."""
    load_env()
    found = [n for n in names if (os.environ.get(n) or "").strip()]
    return ", ".join(found) if found else "none of " + "/".join(names)
