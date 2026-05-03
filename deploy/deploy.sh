#!/usr/bin/env bash
# Deploy taskboard to this Pi. Run as a sudoer on the host.
# Usage: ./deploy/deploy.sh
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER=taskboard
WEB_ROOT=/var/www/taskboard
APP_ROOT=/opt/taskboard
DATA_ROOT=/var/lib/taskboard
NGINX_CONF=/etc/nginx/conf.d/taskboard.conf
SYSTEMD_UNIT=/etc/systemd/system/taskboard-api.service

echo "[deploy] from: $REPO_ROOT"

# Service user + dirs (idempotent)
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "[deploy] creating user $SERVICE_USER"
  sudo useradd --system --home "$DATA_ROOT" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_ROOT"
sudo install -d -o root -g root -m 0755 "$APP_ROOT"
sudo install -d -o www-data -g www-data -m 0755 "$WEB_ROOT"

# Dependencies
if ! python3 -c "import flask" >/dev/null 2>&1; then
  echo "[deploy] installing python3-flask"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-flask
fi

# Files
echo "[deploy] installing app files"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/backend/api.py"                "$APP_ROOT/api.py"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/deploy/taskboard-api.service"  "$SYSTEMD_UNIT"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/deploy/nginx.conf"             "$NGINX_CONF"
sudo install -m 0644 -o www-data -g www-data "$REPO_ROOT/frontend/index.html"           "$WEB_ROOT/index.html"

# Reload + restart
echo "[deploy] reloading systemd, restarting API"
sudo systemctl daemon-reload
sudo systemctl enable --now taskboard-api.service
sudo systemctl restart taskboard-api.service

echo "[deploy] testing nginx config"
sudo nginx -t
sudo systemctl reload nginx

# Health check
sleep 1
echo "[deploy] health check"
curl -fsS http://127.0.0.1:8083/api/health
echo
echo "[deploy] done. http://$(hostname -I | awk '{print $1}'):8083/"
