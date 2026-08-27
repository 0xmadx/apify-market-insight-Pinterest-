#!/usr/bin/env bash
# Prove the DEPLOYED actor works — the only check that exercises the real thing.
#
#   ./smoke.sh                 radar, the 2-request proof
#   ./smoke.sh keywords        a heavier one, once radar passes
#
# WHY THIS EXISTS SEPARATELY FROM THE GATE
# ----------------------------------------
# `ship.sh check` proves the CODE is right. It cannot prove the DEPLOYMENT is:
# every offline suite passes while `REDIS_URL` is unset in the Apify console,
# and the actor then fails in the cloud for a reason no local run can reach.
# That gap is the single most likely way this deploy breaks, so it gets its own
# command rather than a printed hint nobody runs.
#
# WHAT IT ASSERTS, AND WHY IT IS NOT JUST "EXIT 0"
# -----------------------------------------------
# This codebase's defining failure mode is a plausible wrong answer, not a
# crash. A run that returns HTTP 200 with an EMPTY dataset is exactly that: it
# reads as "Pinterest has nothing trending" when it actually means "the vault
# was empty" or "the session was signed out". So a zero-record success is a
# FAILURE here, and is reported as one.
#
# It calls `run-sync-get-dataset-items` — the same endpoint an integrator
# writes (docs/DEPLOY.md §1c), so this smokes the customer's path, not a
# private one.
set -euo pipefail

OP="${1:-radar}"
ACTOR="${APIFY_ACTOR:-pinterest-vault-scraper}"

# The token is read from the environment or from what `apify login` stored. It
# is never echoed: this script's output is the sort of thing that gets pasted
# into an issue.
# A WORKING python, not merely one on PATH. Windows ships a `python3` STUB in
# WindowsApps that exits silently, so `command -v python3` says yes and every
# call through it produces nothing -- which is how this script reported "no
# token" while `apify info` was happily logged in.
PY=""
for cand in python3 python py "./.venv/Scripts/python.exe" "./.venv/bin/python"; do
  if "$cand" -c "print(1)" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "no working python found (tried python3, python, py, ./.venv)" >&2; exit 2; }

# Authentication is the CLI's job, not this script's. It used to read a bearer
# token out of ~/.apify/auth.json and curl the API directly -- but apify-cli
# 1.8 keeps the token in the OS credential store, so that file has no `token`
# key at all and the read silently produced "". Handing the whole problem to
# `apify call` also means this script never touches a credential.
command -v apify >/dev/null || { echo "apify CLI not installed" >&2; exit 2; }
apify info >/dev/null 2>&1 || { echo "not logged in — run \`apify login\`" >&2; exit 2; }

# `~` in the actor id is how Apify addresses another user's actor; for your own
# the bare name works once the token identifies you.
case "$OP" in
# fullRescan on EVERY input, and it is not optional. The seen-set is per
# customer and remembers for SEEN_TTL (7 days), so the second smoke of the day
# legitimately has nothing new to deliver and returns zero rows. Without this
# the script then shouts "SMOKE FAILED -- returned NOTHING" at a system that is
# working perfectly, which is worse than no smoke test: it teaches you to
# ignore the one alarm that means the vault is empty.
#
# Measured 2026-08-27: first run 11 records, second run 0, same actor, same
# minute, nothing wrong.
  radar)    INPUT='{"operation":"radar","region":"US","fullRescan":true}' ; MIN=1 ;;
  keywords) INPUT='{"operation":"keywords","region":"US","fullRescan":true}' ; MIN=1 ;;
  shopping) INPUT='{"operation":"shopping","verticals":["1042"],"drillTopN":1,"maxRecords":10,"fullRescan":true}' ; MIN=1 ;;
  *) echo "unknown operation: $OP (radar|keywords|shopping)" >&2; exit 2 ;;
esac

echo "==> calling deployed actor '$ACTOR' with $OP"
BODY=$(mktemp)
trap 'rm -f "$BODY" "$BODY.err"' EXIT

if apify call "$ACTOR" -i "$INPUT" -o -s >"$BODY" 2>"$BODY.err"; then
  CODE=ok
else
  CODE=failed
fi

if [ "$CODE" != "ok" ]; then
  echo "    the run did not complete" >&2
  head -c 600 "$BODY.err" >&2; echo >&2
  head -c 400 "$BODY" >&2; echo >&2
  echo >&2
  echo "    If this is a 400/408 with an empty vault, REDIS_URL is the suspect:" >&2
  echo "    it must be set as an Actor SECRET in the Apify console, and it must" >&2
  echo "    be the rediss:// URL — a localhost value means the actor's OWN" >&2
  echo "    container, which holds nothing." >&2
  exit 1
fi

# A JSON array is what this endpoint returns. Count it, and refuse to call an
# empty one a pass.
N=$($PY -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    print("PARSE_FAIL", e, file=sys.stderr); sys.exit(3)
if not isinstance(d, list):
    print("NOT_A_LIST", file=sys.stderr); sys.exit(3)
print(len(d))
' "$BODY") || { echo "    could not read the dataset" >&2; exit 1; }

echo "    $N record(s) from the DEPLOYED actor"

if [ "$N" -lt "$MIN" ]; then
  echo >&2
  echo "    ✗ SMOKE FAILED — the run succeeded and returned NOTHING." >&2
  echo >&2
  echo "    This is the failure this project is built to catch. An empty" >&2
  echo "    dataset reads as 'Pinterest has nothing', which would be a lie." >&2
  echo "    Check, in this order:" >&2
  echo "      1. python -m src.status   — is the vault fresh, or 0/N usable?" >&2
  echo "      2. is the WRITER still running? (adspower-sync.timer in WSL)" >&2
  echo "      3. does the writer point at the SAME Redis the actor reads?" >&2
  exit 1
fi

# A demo record means the actor fell back to fixtures — real-looking data that
# is not fresh. Never let that pass as a live smoke.
if grep -q '"_demo"[[:space:]]*:[[:space:]]*true' "$BODY"; then
  echo "    ✗ records are DEMO fixtures, not live data" >&2
  exit 1
fi

echo "    ✓ smoke passed — deployed actor reached the vault and Pinterest"
