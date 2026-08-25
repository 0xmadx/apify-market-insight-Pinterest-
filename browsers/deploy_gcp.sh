#!/usr/bin/env bash
# Provision a GCP VM (or any Debian/Ubuntu host) to run the keepalive service.
#
#   gcloud compute scp browsers/deploy_gcp.sh pinterest-keepalive:~/
#   gcloud compute ssh pinterest-keepalive
#   REDIS_URL='rediss://...' bash deploy_gcp.sh
#
# WHAT THIS REPLACES
# ------------------
# AdsPower, as the thing that keeps Pinterest sessions fresh in the vault. It
# does NOT replace AdsPower as the place a human logs a new account in for the
# first time — that stays on the operator's desktop (docs/SESSION_SOURCES.md).
#
# WHY A SCRIPT AND NOT `cp` OF THE UNIT FILES
# --------------------------------------------
# browsers/keepalive.service was written for WSL and hardcodes /home/devy and a
# venv at ~/pinterest-actor. Copying it to a VM with a different username
# produces a unit that fails at ExecStart with a bare status=203/EXEC, which
# says nothing about the real cause. This generates the unit from the paths
# that actually exist on THIS host instead.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/pinterest-apify}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
ENV_DIR=/etc/pinterest-keepalive
VAULT_PLATFORM="${VAULT_PLATFORM:-pinterest}"

if [ -z "${REDIS_URL:-}" ]; then
  cat >&2 <<'EOF'
REDIS_URL is not set.

It must be the Upstash rediss:// URL — NOT localhost. On a VM, localhost Redis
is reachable only by this VM, and the Apify actor could never read the vault
this service fills. That failure is silent from here: keepalive would report
every profile written and the actor would find an empty pool.

  REDIS_URL='rediss://default:...@....upstash.io:6379' bash deploy_gcp.sh
EOF
  exit 2
fi

echo "==> host packages"
# Chromium needs system libraries even though the BROWSER itself needs no root
# to install. `patchright install-deps` is the supported way to get exactly the
# right set; guessing at libnss3/libatk/... by hand is how this breaks on the
# next Ubuntu release.
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git

echo "==> repo at $REPO_DIR"
if [ ! -d "$REPO_DIR/.git" ]; then
  echo "    clone the repo to $REPO_DIR first, then re-run:" >&2
  echo "      git clone <your-remote> $REPO_DIR" >&2
  exit 2
fi
cd "$REPO_DIR"

echo "==> python env"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r requirements.txt
"$VENV_DIR/bin/pip" install -q -r browsers/requirements.txt

echo "==> chromium (no root for the browser itself)"
"$VENV_DIR/bin/python" -m patchright install chromium
# The system libs Chromium links against DO need root. Separate step, separate
# failure mode: a missing lib shows up as the browser exiting instantly with no
# message, which reads like a code bug rather than a packaging one.
sudo "$VENV_DIR/bin/python" -m patchright install-deps chromium || \
  echo "    ⚠️  install-deps failed — if Chromium exits instantly later, this is why"

echo "==> secrets at $ENV_DIR"
sudo mkdir -p "$ENV_DIR"
sudo tee "$ENV_DIR/env" >/dev/null <<EOF
REDIS_URL=$REDIS_URL
VAULT_PLATFORM=$VAULT_PLATFORM
EOF
# The vault URL carries the Upstash password. Root-only, like any credential
# file — systemd reads it as root before dropping to the service user.
sudo chmod 600 "$ENV_DIR/env"

echo "==> systemd units (generated for THIS host, not copied)"
sudo tee /etc/systemd/system/keepalive.service >/dev/null <<EOF
[Unit]
Description=Refresh Pinterest session cookies into the vault (DrissionPage)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_DIR/env
Environment=HOME=$HOME
ExecStart=$VENV_DIR/bin/python -m browsers.keepalive --once
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
EOF

sudo cp browsers/keepalive.timer /etc/systemd/system/keepalive.timer
sudo systemctl daemon-reload
sudo systemctl enable --now keepalive.timer

cat <<EOF

==> provisioned.

STILL REQUIRED — this host has no sessions yet:

  browsers/identities.json is NOT in the repo (gitignored: it holds live
  Pinterest sessions and proxy passwords in clear text). Copy it from the
  machine that has AdsPower:

    gcloud compute scp browsers/identities.json <vm>:$REPO_DIR/browsers/

  Until it exists, every pass writes nothing and says so.

Then verify:
  systemctl list-timers keepalive.timer
  sudo journalctl -u keepalive.service -n 30 --no-pager
  $VENV_DIR/bin/python -m src.status

Do NOT stop adspower-sync.timer on the desktop until this host has held the
pool through several cycles. Rollback is re-enabling that one timer.
EOF
