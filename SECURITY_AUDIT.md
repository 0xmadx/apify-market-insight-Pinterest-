# Pre-push security audit

Run before the first push to any remote, and again before making a repo
public. A push publishes **every commit ever made**, so scanning the working
tree proves nothing on its own — `.gitignore` only governs what happens next.

## Why this file exists

The parent Etsy repo shipped a tracked `registry.json` holding **32 live
session cookies** before anyone noticed. This project holds the same class of
material: `browsers/identities.json` carries live Pinterest sessions and proxy
passwords in clear text, and `.env` carries the AdsPower and Webshare API keys.
Both are gitignored — which is the easy half. The hard half is proving they
were never committed *before* the ignore rules existed.

## The scan

```bash
# 1. Was a sensitive FILE ever added, on any branch, at any point?
git log --all --diff-filter=A --name-only --pretty=format: | sort -u \
  | grep -iE '\.env$|identities\.json|registry\.json|credential|secret|\.pem$|\.key$'

# 2. Does any real secret VALUE appear in any commit's content?
#    (filenames are not enough — a value can be pasted into a doc or a test)
ALLREV=$(git rev-list --all)
git grep -I -l "<the webshare key>"   $ALLREV
git grep -I -l "<the adspower key>"   $ALLREV
git grep -I -l "<the proxy username>" $ALLREV
git grep -I -l "<the proxy password>" $ALLREV

# 3. Long base64-ish blobs that could be cookie values
git grep -InE '[A-Za-z0-9+/]{60,}={0,2}' HEAD -- '*.py' '*.json' '*.md'
```

**Cookie NAMES are not secrets.** `_auth`, `_pinterest_sess` and
`__Secure-s_a` appear throughout the code and docs because the vault checks for
them by name. A hit on a name is expected; a hit on a 1,800-character *value*
is not.

## Result — 2026-08-25

| check | result |
|---|---|
| Sensitive filename ever committed (184 files, all branches) | **none** |
| Webshare API key in any commit | clean |
| AdsPower API key in any commit | clean |
| Proxy username / password in any commit | clean |
| Long secret-like blobs in tracked source | none |

**Verdict: safe to push.**

## If a future scan finds something

Do **not** push and fix afterwards. A published commit is public the moment it
lands, and deleting it later does not un-publish it — assume anything pushed
has been scraped. Either scrub with `git filter-repo` before pushing, or start
the remote from a fresh history. Then rotate the exposed credential regardless,
because the scrub cannot prove nobody read it.
