#!/usr/bin/env bash
set -Eeuo pipefail
exec 9>/var/lock/pars-vds-bot-deploy.lock
flock -w 1200 9 || { echo "Shared deployment lock busy"; exit 1; }

APP_DIR=/opt/rishestore-pars
cd "$APP_DIR"
test -f compose.yml
test -f rishe-image.tar.gz
command -v docker
command -v nginx
command -v openssl

# Refuse conflict with existing services and refuse a reroute to the wrong server.
if ss -ltn | grep -qE '127\.0\.0\.1:3108[[:space:]]' && ! docker ps --format '{{.Names}}' | grep -q '^rishestore_pars-app-1$'; then
  echo "Port 3108 is already owned by another application"; exit 1
fi
if ! getent ahostsv4 rishe.store | awk '{print $1}' | grep -Fxq '143.20.60.195'; then
  echo "DNS for rishe.store does not yet point directly to this server; aborting cutover"; exit 1
fi

echo "== Existing critical services =="
systemctl is-active rednotedownloader
docker ps --format '{{.Names}} | {{.Status}}' | grep -E '(rednote|ordersvesta|orderscutella)' || true

if [ ! -f .env ]; then
  umask 077
  printf 'POSTGRES_PASSWORD=%s\nAPP_SECRET=%s\nNEXT_PUBLIC_SITE_URL=https://rishe.store\nZIBAL_MERCHANT=\nSMSIR_API=\n' \
    "$(openssl rand -hex 24)" "$(openssl rand -hex 32)" > .env
fi
chmod 600 .env

if [ ! -f .legacy-migration-private.pem ]; then
  umask 077
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out .legacy-migration-private.pem >/dev/null 2>&1
fi
chmod 600 .legacy-migration-private.pem

# Tiny VPS: provide swap to keep the existing bots responsive while Rishe initializes.
if ! swapon --noheadings 2>/dev/null | grep -q '^'; then
  if [ ! -e /swapfile-rishe ]; then
    fallocate -l 2G /swapfile-rishe
    chmod 600 /swapfile-rishe
    mkswap /swapfile-rishe >/dev/null
  fi
  swapon /swapfile-rishe
  grep -Fq '/swapfile-rishe none swap sw 0 0' /etc/fstab || echo '/swapfile-rishe none swap sw 0 0' >> /etc/fstab
fi

echo "== Loading prebuilt image (no build on 2GB VPS) =="
gzip -dc rishe-image.tar.gz | docker load
rm -f rishe-image.tar.gz
echo "== Starting isolated containers =="
docker compose -f compose.yml -p rishestore_pars up -d --no-build

echo "== App health =="
ready=0
for i in $(seq 1 90); do
  if curl -fsS --max-time 3 http://127.0.0.1:3108/api/health 2>/dev/null | grep -q '"ok":true'; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  docker compose -f compose.yml -p rishestore_pars ps
  docker compose -f compose.yml -p rishestore_pars logs --tail 140 app db worker
  echo "App health failed. No Nginx cutover performed."
  exit 1
fi

echo "== Ensure persistent brand media mount ==" 
media_source=/var/lib/docker/volumes/rishestore_pars_brand_assets/_data
media_mount=/srv/rishe-assets
test -d "$media_source"
mkdir -p "$media_mount"
if ! mountpoint -q "$media_mount"; then mount --bind "$media_source" "$media_mount"; fi
if ! grep -Fq "$media_source $media_mount none bind 0 0" /etc/fstab; then
  echo "$media_source $media_mount none bind 0 0" >> /etc/fstab
fi

echo "== Installing only the rishe.store Nginx vhost =="
site=/etc/nginx/sites-available/rishe.store
link=/etc/nginx/sites-enabled/rishe.store
if [ -e "$site" ] && ! grep -q 'rishe-pars-isolated' "$site"; then
  echo "Existing unmanaged rishe.store Nginx config. Refusing to overwrite."
  exit 1
fi
if [ -f "$site" ]; then cp -a "$site" "$site.bak.$(date +%s)"; fi
cat > "$site" <<'NGINX'
# rishe-pars-isolated
server {
    listen 80;
    listen [::]:80;
    server_name rishe.store;
    client_max_body_size 25m;
    # Never permit public first-run admin takeover before protected migration/setup.
    location = /setup { return 403; }
    location = /api/setup { return 403; }
    location ^~ /brand/ {
        alias /srv/rishe-assets/;
        autoindex off;
        add_header X-Content-Type-Options nosniff always;
        add_header Cache-Control "public, max-age=1800";
    }
    location ^~ /wp-content/uploads/ {
        alias /srv/rishe-assets/wp-content/uploads/;
        autoindex off;
        add_header X-Content-Type-Options nosniff always;
        add_header Cache-Control "public, max-age=1800";
    }
    location = /brand/products/legacy-210.jpg { return 302 /brand/products/legacy-210-story.png; }
    location = /brand/products/legacy-228.jpg { return 302 /brand/products/legacy-228-story.png; }
    location = /brand/products/legacy-251.jpg { return 302 /brand/products/legacy-251-story.png; }
    location = /brand/products/legacy-255.jpg { return 302 /brand/products/legacy-255-story.png; }
    location = /brand/products/legacy-256.jpg { return 302 /brand/products/legacy-256-story.png; }
    location = /brand/products/legacy-258.jpg { return 302 /brand/products/legacy-258-story.png; }
    location / {
        proxy_pass http://127.0.0.1:3108;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 90s;
    }
}
NGINX
ln -sfn "$site" "$link"
nginx -t
systemctl reload nginx

echo "== Issuing HTTPS cert for root domain =="
if ! command -v certbot >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx
fi
certbot --nginx -d rishe.store --agree-tos --non-interactive --redirect --register-unsafely-without-email
nginx -t
systemctl reload nginx

echo "== Verify TLS and API =="
for i in $(seq 1 12); do
  if curl -fsS --max-time 10 https://rishe.store/api/health | grep -q '"ok":true'; then
    echo 'RISHE_PARALLEL_DEPLOY_SUCCESS'
    break
  fi
  if [ "$i" -eq 12 ]; then
    echo "Public HTTPS health failed"
    exit 1
  fi
  sleep 5
done
echo "== Final services =="
docker compose -f compose.yml -p rishestore_pars ps
systemctl is-active rednotedownloader
docker ps --format '{{.Names}} | {{.Status}}' | grep -E '(rednote|ordersvesta|orderscutella)' || true
