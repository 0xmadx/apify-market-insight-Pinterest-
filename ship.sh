#!/usr/bin/env bash
# One command from a tested local checkout to production.
#
#   ./ship.sh check          run the gate, deploy nothing   (do this first)
#   ./ship.sh apify          gate, then push the actor
#   ./ship.sh gcp            gate, then update the session farm
#   ./ship.sh all            both
#
# TWO TARGETS, DELIBERATELY INDEPENDENT
# -------------------------------------
#   apify  gets src/ only — customers run this. `.dockerignore` keeps
#          browsers/ and adspower/ out, which is why the image is ~497MB and
#          not a browser base.
#   gcp    gets browsers/ — the session farm. Never pushed to Apify.
#
# A change to browsers/ cannot affect a customer; a change to src/ cannot
# affect the farm. Deploying them with one command would quietly destroy that
# separation, so this takes a target and refuses to guess.
#
# WHY THE GATE IS NOT OPTIONAL
# ----------------------------
# `.actor/actor.json` carries `buildTag: latest`, so an `apify push` changes
# what EXISTING customers get on their next run — possibly minutes later, with
# no notice. The gate is the only thing between a local edit and that.
#
# `probe_endpoints` runs BEFORE the suites on purpose: under `check` it rewrites
# the fixtures in probes/results/ that the suites then read, so the tests run
# against today's wire rather than a snapshot of a wire that may have changed.
# Reordering these would turn the release gate into a test of last week's
# Pinterest.
#
# It only rewrites under `check`. On `apify`/`gcp` the same 16 endpoints are
# probed and drift still fails the gate, but nothing tracked changes — because
# rewriting there would dirty the tree that the clean-tree refusal below then
# rejects, which made the documented deploy order impossible to complete and
# broke `git checkout <sha> && ./ship.sh apify` rollback. See probe_endpoints.py.
set -euo pipefail

PY="${PY:-.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="python3"
TARGET="${1:-}"

case "$TARGET" in
  check|apify|gcp|all) ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac

# ---------------------------------------------------------------- the gate

echo "==> vault"
"$PY" -m src.status >/dev/null || {
  echo "    vault is not green — probes below would fail for the wrong reason" >&2
  exit 1
}

echo "==> live wire (16 endpoints)"
if [ "$TARGET" = "check" ] || [ "$TARGET" = "all" ]; then
  "$PY" -m probes.probe_endpoints --refresh
else
  "$PY" -m probes.probe_endpoints
fi

echo "==> offline suites"
FAILED=0
for suite in test_full_project test_shopping_api test_shopping_traversal \
             test_dispatch test_incremental test_adspower test_vault \
             test_quick_start test_main_messages test_status_healthcheck \
             test_ignore_parity test_ci_contract test_actor_manifests; do
  line=$("$PY" -m "tests.$suite" 2>&1 | grep -E '^[0-9]+/[0-9]+ checks passed' || true)
  passed="${line%%/*}"
  total=$(echo "$line" | cut -d/ -f2 | cut -d' ' -f1)
  if [ -z "$line" ] || [ "$passed" != "$total" ]; then
    echo "    FAIL $suite ${line:-(no result line)}" >&2
    FAILED=1
  else
    printf '    %-28s %s\n' "$suite" "$line"
  fi
done
[ "$FAILED" -eq 0 ] || { echo "gate failed — nothing deployed" >&2; exit 1; }

# Deploying a dirty tree means the thing running in production matches no
# commit, so "what changed?" becomes unanswerable the moment it misbehaves.
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠️  working tree is dirty:" >&2
  git status --short >&2
  [ "$TARGET" = "check" ] || {
    echo "    commit first — production must match a commit you can point at" >&2
    exit 1
  }
fi

echo "==> gate passed ($(git rev-parse --short HEAD))"
[ "$TARGET" = "check" ] && exit 0

# ------------------------------------------------------------------ apify

if [ "$TARGET" = "apify" ] || [ "$TARGET" = "all" ]; then
  echo
  echo "==> apify"
  # The one question the gate cannot answer, because it is about MEANING, not
  # correctness: additive changes are safe on `latest`, breaking ones need a
  # version bump first or every existing customer breaks tonight.
  echo "    buildTag is 'latest' — this reaches existing customers immediately."
  echo "    Could a customer's existing code break if this landed silently?"
  read -r -p "    (renamed/removed field, changed default, removed operation) [y/N] " risky
  case "$risky" in
    [yY]*) echo "    bump \`version\` in .actor/actor.json first. Stopping." >&2; exit 1 ;;
  esac
  apify push

  # Smoke the DEPLOYED actor, not the local one. The gate above proves the code
  # is right; only this proves the deployment is — REDIS_URL missing from the
  # Apify console fails here and nowhere else. Printing the command instead of
  # running it made this the step that gets skipped.
  echo
  echo "==> smoke (deployed)"
  if [ "${SKIP_SMOKE:-}" = "1" ]; then
    echo "    SKIPPED (SKIP_SMOKE=1) — the push is live and UNVERIFIED" >&2
  else
    ./smoke.sh radar || {
      echo >&2
      echo "    The push is LIVE and the smoke failed. Customers are on it now." >&2
      echo "    Roll back by pushing the last good commit:" >&2
      echo "      git checkout <last-good-sha> && ./ship.sh apify" >&2
      exit 1
    }
  fi
fi

# -------------------------------------------------------------------- gcp

if [ "$TARGET" = "gcp" ] || [ "$TARGET" = "all" ]; then
  echo
  echo "==> gcp session farm"
  : "${GCP_VM:?set GCP_VM to the instance name}"
  ZONE_FLAG=""
  [ -n "${GCP_ZONE:-}" ] && ZONE_FLAG="--zone=$GCP_ZONE"
  REPO_DIR="${GCP_REPO_DIR:-\$HOME/pinterest-apify}"

  # git pull, not scp: the VM then reports a commit hash that matches this
  # repo, so `git log` on either side answers "what is actually running".
  # Restart the TIMER rather than the service — the service is Type=oneshot and
  # starting it directly would run a pass outside the schedule.
  gcloud compute ssh "$GCP_VM" $ZONE_FLAG --command "
    set -e
    cd $REPO_DIR
    git fetch --quiet origin
    git checkout --quiet $(git rev-parse --abbrev-ref HEAD)
    git pull --quiet --ff-only
    .venv/bin/pip install -q -r browsers/requirements.txt
    sudo systemctl restart keepalive.timer
    echo \"    now at \$(git rev-parse --short HEAD)\"
  "
  echo "    verify:  gcloud compute ssh $GCP_VM $ZONE_FLAG --command \\"
  echo "               'sudo journalctl -u keepalive.service -n 20 --no-pager'"
fi
