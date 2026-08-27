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

echo "==> windows fonts"
# NOT cosmetic, and not optional. Profiles carry a Windows user agent and
# Windows-only ANGLE/Direct3D11 WebGL strings; a box without Windows fonts
# answers font-metric probes with the SAME fallback width for every family,
# which is a Linux tell sitting underneath a Windows claim.
#
# Measured 2026-08-25. Before: 432,374,376,376,376,413,376,376 (3 distinct of
# 8 — everything falling back). After: 432,374,464,436,506,413,413,410, which
# is byte-identical to what a real Windows host produced. Installing the real
# fonts is strictly better than spoofing the measurement: nothing is being
# lied about, so there is nothing to catch.
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ttf-mscorefonts-installer fontconfig 2>/dev/null || true
fc-cache -f >/dev/null 2>&1 || true

# COUNT THE FAMILIES; DO NOT TRUST apt's EXIT CODE.
#
# The package installs in two parts: dpkg registers it, then its postinst
# DOWNLOADS the actual .ttf files from SourceForge. That download can fail
# while dpkg still reports `ii`, leaving
# /usr/share/fonts/truetype/msttcorefonts/ holding nothing but a README.
#
# Measured 2026-08-27 on a fresh Ubuntu 24.04 VM: apt exited 0, this script
# printed "installed", and fc-list matched ZERO of the families. Reporting
# success there is the same class of error the fonts themselves guard against
# — a plausible-looking result that is wrong — so the check is a count, not an
# exit code.
FAMILIES='^(Arial|Times New Roman|Verdana|Georgia|Tahoma|Courier New|Comic Sans MS|Impact|Trebuchet MS)$'
have=$(fc-list : family 2>/dev/null | tr ',' '\n' | sort -u | grep -icE "$FAMILIES" || true)

if [ "${have:-0}" -ge 5 ]; then
  echo "    $have/9 Windows families present"
else
  cat <<FONTS
    ⚠️  ONLY ${have:-0}/9 Windows families are installed. apt may well have
        exited 0 — the package registers before its postinst downloads the
        fonts, and that download is what fails.

        Every font-metric probe will now return the SAME fallback width for
        every family, which is a Linux tell underneath this profile's Windows
        user agent. Copy the real files instead (nothing is spoofed, so there
        is nothing to catch):

          gcloud compute scp /c/Windows/Fonts/{arial,arialbd,georgia,tahoma,verdana,times,comic,impact,cour,trebuc}.ttf \\
              <vm>:$HOME/.local/share/fonts/ --tunnel-through-iap
          gcloud compute ssh <vm> --tunnel-through-iap --command 'fc-cache -f'

        Then re-run this script, or just re-run the count:
          fc-list : family | tr ',' '\\n' | sort -u | grep -icE '$FAMILIES'

        NOTE: \`python -m browsers.fingerprint\` cannot verify this here — it
        loads browsers/identities.json, which is never copied to this host by
        design. Measure in-browser, or use the count above.
FONTS
fi

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

NOTHING TO COPY. keepalive reads its profile list from the VAULT, so this
host needs no identities.json and no scp. Whatever is in Redis is what it
refreshes — including an account you add tomorrow, on the next 5-minute cycle.

  Adding account #9 later:
    1. create the profile + assign its proxy, on the desktop
    2. log in by hand
    3. adspower/sync_cookies.py  ->  writes it to the vault
    4. this host picks it up automatically. No file moves.

Then verify:
  systemctl list-timers keepalive.timer
  sudo journalctl -u keepalive.service -n 30 --no-pager
  $VENV_DIR/bin/python -m src.status

Do NOT stop adspower-sync.timer on the desktop until this host has held the
pool through several cycles. Rollback is re-enabling that one timer.
EOF
