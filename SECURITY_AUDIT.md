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

## Result — 2026-09-02

Re-run before making any of the four Apify actors public, after the persona
actors (`actors/marketers`, `actors/ecommerce`, `actors/creators`) and
`tools/publish.py` landed.

| check | result |
|---|---|
| Sensitive filename ever committed, all branches | **none** |
| Redis URL with embedded credentials, any tracked file | clean (one hit in `browsers/deploy_gcp.sh` is a doc placeholder — literal `...`, not a real value) |
| Long base64/hex secret-like blobs in tracked source | none real — the only hits are Pinterest's public GraphQL `queryHash` (a non-secret query identifier, documented in `docs/wire/`) |
| `eval`/`exec`/`os.system`/shell-injection patterns in `src/`, `tools/` | none (`publish.py`'s one `subprocess.run` uses list-form args) |
| SSRF via customer input | not possible — target hosts (`trends.pinterest.com`, `www.pinterest.com`) are hardcoded constants; no URL/host/proxy/Redis override field exists in any actor's input schema |
| Cookies/fingerprint/proxy in customer-facing dataset records | none — checked `records.py`, no session field ever reaches a pushed record |
| Cookies/proxy credentials in operator-visible run logs | none — `Identity.__repr__` (`src/vault.py:63`) prints only `profile_id`, cookie count, and age |
| `ADS_API_KEY` / `WEBSHARE_API` / `DEEPSEEK_API_KEY` hardcoded anywhere | none — environment-only, everywhere |

**Verdict: safe to push, safe to make public.**

Not a repo finding, but worth knowing: `browsers/profiles/` on the operator's
local machine holds real cached AdsPower/keepalive session data. Confirmed
`.gitignore`d (0 files tracked) — not a leak risk, just local-machine hygiene
if that disk is ever imaged or handed off.

**Verdict: safe to push.**

## The near-miss that changed the ignore rule — 2026-08-25

`.gitignore` said `.env`. Exact name only. So when the Redis migration made a
safety copy called `.env.backup-premigration` — a verbatim copy, API keys
included — `git add -A` staged it and it landed in a commit.

Caught on the same turn, removed with `git rm --cached`, and the commit
amended before anything was pushed. The full-history value scan above was
re-run afterwards and stayed clean, and the only `.env*` ever committed is
`.env.example`.

The rule is now `.env*` with `!.env.example`, because the lesson is not "be
careful with backups" — it is that **an ignore rule matching one exact
filename protects one exact filename.** Any variant a future step invents
(`.env.bak`, `.env.old`, `.env.20260825`) walks straight past it.

## If a future scan finds something

Do **not** push and fix afterwards. A published commit is public the moment it
lands, and deleting it later does not un-publish it — assume anything pushed
has been scraped. Either scrub with `git filter-repo` before pushing, or start
the remote from a fresh history. Then rotate the exposed credential regardless,
because the scrub cannot prove nobody read it.
