#!/usr/bin/env bash
# Is this project actually ready to deploy? Answers in one command.
#
#   ./preflight.sh
#
# WHY THIS IS SEPARATE FROM ship.sh
# ---------------------------------
# `ship.sh check` answers "is the CODE correct". This answers "is the WORLD
# ready" — tooling installed, accounts authenticated, the repo private, and the
# vault being actively written to by someone. Those fail in ways no test suite
# can see, and each one is a deploy that gets half-done and then unwound.
#
# It changes NOTHING. Safe to run at any time, including mid-deploy.
#
# THE CHECK THAT JUSTIFIES THE FILE
# ---------------------------------
# §5 compares the vault's heartbeat ages against PROFILE_MAX_AGE. Migrating the
# vault to a new Redis copies the DATA but not the WRITER, so the actor can
# read a table that looks fully populated and is in fact frozen. Measured on
# 2026-08-26, minutes after the Upstash migration:
#
#     Upstash     0/8 usable   ages ~13,400s   <- what Apify would have read
#     local 6380  6/6 usable   ages ~280s      <- where WSL was still writing
#
# Both are "the vault". Only one was alive. No unit test can catch that, and by
# the time a customer does it is a refund.
set -uo pipefail

cd "$(dirname "$0")"
PY="${PY:-.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

PASS=0; WARN=0; FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
fix()  { printf '      → %s\n' "$1"; }

echo
echo "── 1. tooling ──────────────────────────────────────────────"
[ -n "$PY" ] && [ -x "$PY" ] && ok "python  $("$PY" --version 2>&1)" \
  || { bad "no python"; fix "python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt"; }
command -v git >/dev/null && ok "git" || bad "no git"
if command -v apify >/dev/null; then ok "apify-cli  $(apify --version 2>&1 | head -1)"
else bad "apify-cli not installed"; fix "npm i -g apify-cli"; fi
if command -v gh >/dev/null; then ok "gh"
else warn "gh not installed (only needed to flip repo visibility)"; fi
if command -v gcloud >/dev/null; then ok "gcloud"
else warn "gcloud not installed — Step 4 (GCP) only, not needed to go live"; fi

echo
echo "── 2. accounts ─────────────────────────────────────────────"
if [ -n "${APIFY_TOKEN:-}" ] || [ -f "$HOME/.apify/auth.json" ]; then ok "apify authenticated"
else bad "apify not logged in"; fix "apify login"; fi
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then ok "gh authenticated"
else warn "gh not authenticated"; fix "gh auth login"; fi
# Captured, not piped: `ssh -T` to GitHub ALWAYS exits 1 (it refuses shell
# access even on success), and with `pipefail` that exit code wins over grep's
# — so the pipeline form reports a working key as broken. Also one call, not
# two: the second was purely to extract the username.
SSH_OUT=$(ssh -o BatchMode=yes -o ConnectTimeout=8 -T git@github.com 2>&1 || true)
case "$SSH_OUT" in
  *"successfully authenticated"*)
    ok "github ssh works ($(printf '%s' "$SSH_OUT" | sed -n 's/Hi \([^!]*\).*/\1/p'))" ;;
  *) bad "github ssh key not accepted"; fix "ssh-add your key, or gh auth login" ;;
esac

echo
echo "── 3. repository ───────────────────────────────────────────"
REMOTE=$(git remote get-url origin 2>/dev/null || true)
if [ -n "$REMOTE" ]; then ok "origin  $REMOTE"; else bad "no origin remote"; fi

# Visibility is checked UNAUTHENTICATED on purpose: that is exactly the view a
# stranger gets. A 200 means the world can read it. docs/wire/ and
# probes/results/ are the reverse-engineering corpus — the actual product —
# so public is a business decision, not a default to drift into.
SLUG=$(echo "$REMOTE" | sed -E 's#.*github\.com[:/]##; s#\.git$##')
if [ -n "$SLUG" ]; then
  VIS=$(curl -s -o /dev/null -w '%{http_code}' "https://api.github.com/repos/$SLUG" 2>/dev/null)
  case "$VIS" in
    404) ok "repo is private (or not created yet)" ;;
    200) bad "repo is PUBLIC — docs/wire/ + probes/results/ would be world-readable"
         fix "https://github.com/$SLUG/settings → Danger Zone → Change visibility" ;;
    *)   warn "could not determine visibility (HTTP $VIS)" ;;
  esac
fi

if [ -z "$(git status --porcelain)" ]; then ok "working tree clean"
else warn "working tree dirty — ship.sh refuses to deploy this"; git status --short | sed 's/^/      /'; fi

echo
echo "── 4. secrets hygiene ──────────────────────────────────────"
if git ls-files | grep -qE '^\.env$'; then bad ".env is TRACKED — it carries the vault password"
else ok ".env not tracked"; fi
if git check-ignore -q .env 2>/dev/null; then ok ".env is gitignored"
else bad ".env is NOT gitignored"; fix "add .env* with !.env.example"; fi
LEAK=$(git log --all --diff-filter=A --name-only --pretty=format: 2>/dev/null \
       | sort -u | grep -E '^\.env' | grep -v '^\.env\.example$' || true)
[ -z "$LEAK" ] && ok "no .env variant ever committed" \
  || { bad "a .env variant EXISTS IN HISTORY: $LEAK"; fix "scrub with git filter-repo BEFORE pushing, then rotate"; }

echo
echo "── 5. the vault (the one that bites) ───────────────────────"
if [ ! -f .env ]; then
  bad "no .env"; fix "cp .env.example .env, then set REDIS_URL"
else
  URL=$(grep -E '^REDIS_URL=' .env | cut -d= -f2- | tr -d '\r')
  case "$URL" in
    rediss://*) ok "REDIS_URL is TLS and remote" ;;
    *localhost*|*127.0.0.1*|*172.*)
      bad "REDIS_URL is LOCAL — an Apify container reaching 'localhost' finds ITSELF"
      fix "point it at the rediss:// Upstash URL" ;;
    "") bad "REDIS_URL not set" ;;
    *)  warn "REDIS_URL is remote but not TLS" ;;
  esac

  # Freshness is the real question. A populated-but-frozen vault looks healthy
  # in every way except the ages.
  STATUS=$("$PY" -m src.status 2>&1)
  USABLE=$(echo "$STATUS" | grep -oE '^[0-9]+/[0-9]+ usable' | head -1)
  if [ -z "$USABLE" ]; then
    bad "src.status did not report — vault unreachable?"
    echo "$STATUS" | tail -3 | sed 's/^/      /'
  else
    N=${USABLE%%/*}
    if [ "$N" -gt 0 ]; then ok "vault: $USABLE"
    else bad "vault: $USABLE — a deploy now serves NOTHING"; fi

    MAXAGE=$(echo "$STATUS" | grep -oE 'age=[0-9]+' | cut -d= -f2 | sort -rn | head -1)
    if [ -n "$MAXAGE" ]; then
      if [ "$MAXAGE" -lt 600 ]; then
        ok "writer is ALIVE on this Redis (oldest heartbeat ${MAXAGE}s)"
      else
        bad "NO WRITER on this Redis (oldest heartbeat ${MAXAGE}s > 600s)"
        fix "the syncer is writing somewhere else — set REDIS_URL in"
        fix "/etc/adspower/api.env (WSL) to the SAME rediss:// URL, then"
        fix "sudo systemctl restart adspower-sync.timer"
      fi
    fi
  fi
fi

echo
echo "────────────────────────────────────────────────────────────"
printf '  %d passed · %d warning(s) · %d blocker(s)\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "  NOT ready to deploy — fix the ✗ above."
  echo
  exit 1
fi
echo "  Ready. Next:  ./ship.sh check   then   ./ship.sh apify"
echo
