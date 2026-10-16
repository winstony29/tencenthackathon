#!/usr/bin/env bash
# Deploy DCTA on one Ubuntu box behind Caddy (HTTPS). Re-run to update.
#
#   DOMAIN=demo.example.com bash scripts/deploy.sh
#
# Before the first run: clone the repo, copy .env.example to .env, fill in your API keys,
# and point the domain's A record at this server so Caddy can fetch a certificate.
# Blank secrets (MASK_KEY etc.) are generated here. Judges share one demo user, so
# delete var/devices.json between judging sessions (the first browser to sign pins it).
# ponytail: single process, sqlite in var/, no Docker; add a container when a second box exists.
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN=your.hostname}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
PY="${PY:-python3.12}"
cd "$APP_DIR"

[ -f .env ] || { echo "Copy .env.example to .env and fill in your keys first."; exit 1; }

# System packages: Python, Node 22 (Next 16 needs >= 20), Caddy.
if ! command -v caddy >/dev/null || ! command -v node >/dev/null || ! command -v "$PY" >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q "${PY}-venv" curl debian-keyring debian-archive-keyring apt-transport-https
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg --yes
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q && sudo apt-get install -y -q nodejs caddy
fi

# Secrets: fill blank keys so config.py's strength check passes (D-040); never log them.
for key in MASK_KEY VALIDATION_RECEIPT_KEY AUDIT_EVIDENCE_KEY AUDIT_ADMIN_TOKEN; do
  if grep -qE "^${key}=$" .env; then
    sed -i "s|^${key}=$|${key}=$(openssl rand -hex 32)|" .env
    echo "generated $key"
  fi
done
grep -qE "^API_DOCS=0" .env || sed -i 's|^API_DOCS=.*|API_DOCS=0|' .env

# Swap: a 2 GB box runs out of memory during `next build` without it.
if [ ! -f /swapfile ] && [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 3500 ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Build.
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
(cd web && npm ci --no-audit --no-fund && npm run build)
chmod 600 .env
mkdir -p var

# Service.
sudo tee /etc/systemd/system/dcta.service >/dev/null <<UNIT
[Unit]
Description=DCTA voice banking demo
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
CADDY

sudo systemctl daemon-reload
sudo systemctl enable --now dcta
sudo systemctl restart dcta caddy

for _ in $(seq 30); do
  curl -fsS -o /dev/null http://127.0.0.1:8000/v5/ && { echo "up: https://$DOMAIN/v5/"; exit 0; }
  sleep 1
done
echo "server did not answer in 30 s; run: journalctl -u dcta -n 30 --no-pager"; exit 1
