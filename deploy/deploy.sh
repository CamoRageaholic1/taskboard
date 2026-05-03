#!/usr/bin/env bash
# Deploy taskboard to this Pi. Run as a sudoer on the host.
# Usage: ./deploy/deploy.sh
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER=taskboard
WEB_ROOT=/var/www/taskboard
APP_ROOT=/opt/taskboard
DATA_ROOT=/var/lib/taskboard
ETC_ROOT=/etc/taskboard
NGINX_CONF=/etc/nginx/conf.d/taskboard.conf
SYSTEMD_UNIT=/etc/systemd/system/taskboard-api.service

echo "[deploy] from: $REPO_ROOT"

# Service user + dirs
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "[deploy] creating user $SERVICE_USER"
  sudo useradd --system --home "$DATA_ROOT" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_ROOT"
sudo install -d -o root -g root -m 0755 "$APP_ROOT"
sudo install -d -o www-data -g www-data -m 0755 "$WEB_ROOT"
sudo install -d -o root -g "$SERVICE_USER" -m 0750 "$ETC_ROOT"

# Dependencies (Flask + bcrypt; both apt-installable on Debian)
NEED=()
python3 -c "import flask" 2>/dev/null || NEED+=(python3-flask)
python3 -c "import bcrypt" 2>/dev/null || NEED+=(python3-bcrypt)
if [ ${#NEED[@]} -gt 0 ]; then
  echo "[deploy] installing: ${NEED[*]}"
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${NEED[@]}"
fi

# Secret key (generated once, never rotated unless you delete the file)
ENV_FILE="$ETC_ROOT/env"
if [ ! -f "$ENV_FILE" ]; then
  echo "[deploy] generating Flask secret key"
  KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  sudo install -m 0640 -o root -g "$SERVICE_USER" /dev/null "$ENV_FILE"
  echo "TASKBOARD_SECRET_KEY=$KEY" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 0640 "$ENV_FILE"
fi

# App files
echo "[deploy] installing app files"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/backend/api.py"                "$APP_ROOT/api.py"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/backend/auth.py"               "$APP_ROOT/auth.py"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/backend/migrate.py"            "$APP_ROOT/migrate.py"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/backend/cli.py"                "$APP_ROOT/cli.py"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/deploy/taskboard-api.service"  "$SYSTEMD_UNIT"
sudo install -m 0644 -o root     -g root     "$REPO_ROOT/deploy/nginx.conf"             "$NGINX_CONF"
sudo install -m 0644 -o www-data -g www-data "$REPO_ROOT/frontend/index.html"           "$WEB_ROOT/index.html"
sudo install -m 0644 -o www-data -g www-data "$REPO_ROOT/frontend/login.html"           "$WEB_ROOT/login.html"
sudo install -m 0644 -o www-data -g www-data "$REPO_ROOT/frontend/admin.html"           "$WEB_ROOT/admin.html"

# Systemd + nginx
sudo systemctl daemon-reload
sudo systemctl enable --now taskboard-api.service
sudo systemctl restart taskboard-api.service
sudo nginx -t
sudo systemctl reload nginx

# Bootstrap (idempotent — only does anything on first deploy).
# The CLI doesn't need TASKBOARD_SECRET_KEY (only sessions use it), so the
# default DB/files paths are sufficient.
echo "[deploy] running bootstrap"
sudo touch /var/log/taskboard-bootstrap.log
sudo chmod 0600 /var/log/taskboard-bootstrap.log
sudo bash -c "
  echo '----- bootstrap $(date -Iseconds) -----' >> /var/log/taskboard-bootstrap.log
  sudo -u $SERVICE_USER python3 $APP_ROOT/cli.py bootstrap --username admin \
    2>&1 | tee -a /var/log/taskboard-bootstrap.log
"

# Health check
sleep 1
curl -fsS http://127.0.0.1:8083/api/health
echo
echo "[deploy] done. http://$(hostname -I | awk '{print $1}'):8083/"
echo "[deploy] (if first deploy, see /var/log/taskboard-bootstrap.log for the admin password — printed once)"
