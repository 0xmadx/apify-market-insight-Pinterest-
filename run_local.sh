#!/usr/bin/env bash
# Run the real Apify actor locally, on Linux, against the Windows vault.
#
#   ./run_local.sh                       # radar — cheapest, 2 requests
#   ./run_local.sh keywords              # zero-input discovery
#   ./run_local.sh shopping '{"verticals":["1042"],"drillTopN":1}'
#
# WHY WSL AND NOT WINDOWS
# ----------------------
# This is a rehearsal of the deploy, not a convenience wrapper. Three things
# only Linux + a networked Redis can prove, and all three are how a cloud run
# differs from a dev run:
#
#   1. The actor's deploy target is Linux (`apify/actor-python:3.12`). Ubuntu
#      WSL runs the same 3.12, so a Windows-only pass proves less than it looks.
#   2. WSL is a separate network namespace. Reaching the vault at the Windows
#      host IP is the same shape as an Apify container reaching Upstash — and
#      it is the exact failure the deploy runbook warns about, where a
#      `localhost` REDIS_URL silently means "this container".
#   3. The vault stays where it is. Nothing about the Chrome extension or the
#      Go cookie server changes to run this.
#
# Prereqs, one time:
#   wsl -d Ubuntu -e bash -c 'cd ~/pinterest-actor && python3 -m venv .venv \
#     && ./.venv/bin/pip install -r requirements.txt'
set -euo pipefail

OP="${1:-radar}"
EXTRA="${2:-{\}}"
DIST="${WSL_DISTRO:-Ubuntu}"
SRC="/mnt/c/Users/0xdevy/Desktop/pinterest-apify"

wsl.exe -d "$DIST" -e bash -c "
set -e
cd ~/pinterest-actor 2>/dev/null || { echo 'run the one-time setup first (see header)'; exit 1; }

# Sync the source every run — the working copy on Windows is the source of
# truth; this directory is a throwaway build.
for d in src tests probes .actor; do
  rm -rf \"\$d\"; cp -r '$SRC'/\"\$d\" . 2>/dev/null || true
done
cp '$SRC'/requirements.txt . 2>/dev/null || true
find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# The Windows host, from inside WSL — a real network hop, not localhost.
HOSTIP=\$(ip route show default | awk '{print \$3}')
export REDIS_URL=\"redis://\$HOSTIP:6379/0\"
export VAULT_WAIT_TIMEOUT=\${VAULT_WAIT_TIMEOUT:-30}

mkdir -p storage/key_value_stores/default
./.venv/bin/python - <<PY
import json, pathlib
extra = json.loads('''$EXTRA''')
task = {'operation': '$OP', 'region': 'US'}
task.update(extra)
pathlib.Path('storage/key_value_stores/default/INPUT.json').write_text(
    json.dumps(task, indent=2))
print('INPUT:', json.dumps(task))
PY

echo \"REDIS_URL=redis://\$HOSTIP:6379/0  (non-localhost, on purpose)\"
echo '--- actor ---'
./.venv/bin/python -m src.main || true

echo '--- dataset ---'
DS=storage/datasets/default
if [ -d \"\$DS\" ]; then
  echo \"\$(ls \$DS/*.json 2>/dev/null | wc -l) records written\"
  ls \$DS/*.json 2>/dev/null | head -1 | xargs -r ./.venv/bin/python -c \\
    'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d, indent=2)[:1200])'
else
  echo 'no dataset — the run produced no records (see the actor log above)'
fi
"
