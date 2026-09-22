#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/rednotedownloader"
SERVICE_NAME="rednotedownloader"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is required"
  exit 1
fi

if ! command -v git >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git ffmpeg python3 python3-venv python3-pip
fi

if [ ! -d "$APP_DIR/.git" ]; then
  mkdir -p "$APP_DIR"
  git clone https://github.com/hsdarestani/rednotedownloader.git "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" reset --hard origin/main
fi

cd "$APP_DIR"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install --upgrade -r requirements.txt
.venv/bin/python -m py_compile main.py

umask 077
cat > /etc/rednotedownloader.env <<EOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
DOWNLOAD_CONCURRENCY=2
LOG_LEVEL=INFO
EOF

cat > /etc/systemd/system/rednotedownloader.service <<'EOF'
[Unit]
Description=RedNote Downloader Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/rednotedownloader
EnvironmentFile=/etc/rednotedownloader.env
ExecStart=/opt/rednotedownloader/.venv/bin/python /opt/rednotedownloader/main.py
Restart=always
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME"
