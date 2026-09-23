#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/rednotedownloader"
SERVICE_NAME="rednotedownloader"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is required"
  exit 1
fi

LOCAL_BOT_API_ENABLED=0

if [ -n "${TELEGRAM_API_ID:-}" ] && [ -n "${TELEGRAM_API_HASH:-}" ]; then
  LOCAL_BOT_API_ENABLED=1

  if ! command -v docker >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io curl
    systemctl enable --now docker
  else
    systemctl enable --now docker
    if ! command -v curl >/dev/null 2>&1; then
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y curl
    fi
  fi

  mkdir -p /var/lib/telegram-bot-api

  if [ ! -f "$APP_DIR/.local-bot-api-migrated" ]; then
    echo "Migrating bot from Telegram cloud Bot API to local Bot API..."
    curl -fsS --max-time 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/logOut" >/tmp/rednote-telegram-logout.json || true
  fi

  docker pull aiogram/telegram-bot-api:latest >/dev/null
  docker rm -f telegram-bot-api >/dev/null 2>&1 || true
  docker run -d \
    --name telegram-bot-api \
    --restart=always \
    -p 127.0.0.1:8081:8081 \
    -v /var/lib/telegram-bot-api:/var/lib/telegram-bot-api \
    -e TELEGRAM_API_ID="$TELEGRAM_API_ID" \
    -e TELEGRAM_API_HASH="$TELEGRAM_API_HASH" \
    -e TELEGRAM_LOCAL=1 \
    aiogram/telegram-bot-api:latest >/dev/null

  BOT_API_READY=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 "http://127.0.0.1:8081/bot${TELEGRAM_BOT_TOKEN}/getMe" >/tmp/rednote-local-getme.json 2>/dev/null; then
      BOT_API_READY=1
      break
    fi
    sleep 2
  done

  if [ "$BOT_API_READY" -ne 1 ]; then
    echo "Local Telegram Bot API failed to become ready."
    docker logs --tail 100 telegram-bot-api || true
    exit 1
  fi

  touch "$APP_DIR/.local-bot-api-migrated"
  echo "Local Telegram Bot API is ready."
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
DOWNLOAD_CONCURRENCY=4
LOG_LEVEL=INFO
EOF

if [ "$LOCAL_BOT_API_ENABLED" -eq 1 ]; then
  cat >> /etc/rednotedownloader.env <<EOF
LOCAL_BOT_API_URL=http://127.0.0.1:8081
EOF
fi

cat > /etc/systemd/system/rednotedownloader.service <<'EOF'
[Unit]
Description=RedNote Downloader Telegram Bot
After=network-online.target docker.service
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
