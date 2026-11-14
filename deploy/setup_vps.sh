#!/usr/bin/env bash
# Installs or updates NDIS Lead Finder on an Ubuntu or Debian VPS. Safe to run again.
#
#   sudo bash deploy/setup_vps.sh                         (no domain: plain HTTP, for trying it out)
#   sudo bash deploy/setup_vps.sh example.com me@mail.com (domain: HTTPS, for real use)
#
# Your database, .env, backups and uploaded media are kept when updating.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR=/opt/ndis-lead-finder
APP_USER=ndis
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"
DB_FILE="$APP_DIR/db.sqlite3"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }
command -v apt-get >/dev/null || { echo "This script needs Ubuntu or Debian."; exit 1; }
if [ -n "$DOMAIN" ] && [ -z "$EMAIL" ]; then
  echo "A domain also needs an email address for the HTTPS certificate:"
  echo "  sudo bash deploy/setup_vps.sh $DOMAIN you@example.com"
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv nginx rsync sqlite3 certbot python3-certbot-nginx redis-server
# Redis listens only on this server (the Ubuntu default) and holds the Celery job queue.
systemctl enable --now redis-server

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"

echo "==> Copying the application to $APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  rsync -a --delete \
    --exclude .env --exclude .env.example --exclude db.sqlite3 --exclude .venv --exclude .deps --exclude .git \
    --exclude backups --exclude media --exclude staticfiles --exclude .scraper.lock --exclude '.celerybeat-schedule*' \
    --exclude '*.zip' --exclude __pycache__ \
    "$SRC_DIR/" "$APP_DIR/"
fi

touch "$ENV_FILE"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"

env_value() { sed -n "s|^$1=||p" "$ENV_FILE" | tail -n 1; }
set_env() {
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
ask_key() {  # Asks only when the key is not set yet and someone is at the keyboard.
  local answer=""
  if [ -z "$(env_value "$1")" ] && [ -t 0 ]; then
    read -r -s -p "$2 (press Enter to skip): " answer
    echo
    [ -z "$answer" ] || set_env "$1" "$answer"
  fi
}

echo "==> Configuring settings"
ask_key GROQ_API_KEY "Groq API key"
ask_key GEMINI_API_KEY "Gemini API key"
ask_key BRAVE_API_KEY "Brave Search API key"

[ -n "$(env_value DJANGO_SECRET_KEY)" ] || set_env DJANGO_SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
PUBLIC_HOST="${DOMAIN:-$(hostname -I | awk '{print $1}')}"
set_env DJANGO_DEBUG false
set_env DJANGO_ALLOWED_HOSTS "$PUBLIC_HOST,127.0.0.1,localhost"
set_env DJANGO_BEHIND_PROXY true
set_env APP_TIME_ZONE Australia/Melbourne
set_env DB_NAME "$DB_FILE"
set_env BACKUP_DIR "$APP_DIR/backups"
set_env REDIS_URL "redis://127.0.0.1:6379/0"
# Cookies and the redirect stay off until HTTPS is confirmed, or nobody could log in.
set_env DJANGO_SECURE_COOKIES false
set_env DJANGO_SECURE_SSL_REDIRECT false
set_env CSRF_TRUSTED_ORIGINS "http://$PUBLIC_HOST"
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "==> Installing Python packages"
runuser -u "$APP_USER" -- python3 -m venv "$APP_DIR/.venv"
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

manage() { (cd "$APP_DIR" && runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" manage.py "$@"); }

echo "==> Preparing the database"
[ ! -f "$DB_FILE" ] || manage backup_database
manage migrate --noinput
manage collectstatic --noinput -v 0

SUPERUSERS="$(sqlite3 "$DB_FILE" "select count(*) from auth_user where is_superuser=1")"
if [ "$SUPERUSERS" = "0" ]; then
  if [ -t 0 ]; then
    echo "Create the administrator account you will log in with:"
    manage createsuperuser
  else
    echo "WARNING: no administrator account. Run: sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py createsuperuser"
  fi
fi

# Removes demo data on every run. Mastodon and Bluesky public search need no key and have been reviewed.
APPROVE=(--approve-source "Mastodon public hashtags" --approve-source "Bluesky post search")
if [ -n "$(env_value BRAVE_API_KEY)" ] && [ -t 0 ]; then
  read -r -p "Does your Brave plan allow storing results and using them with AI? [y/N]: " BRAVE_OK
  case "$BRAVE_OK" in [yY]*) APPROVE+=(--approve-source "Brave web search") ;; esac
fi
manage prepare_live "${APPROVE[@]}"

echo "==> Setting up the website service"
cat > /etc/systemd/system/ndis-web.service <<EOF
[Unit]
Description=NDIS Lead Finder website
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/gunicorn ndis_lead_finder.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 240
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Celery does the background work: scheduled scans every six hours, the nightly backup, the daily
# summary, and scans started with the Scan now button. One process only, because SQLite has one writer.
cat > /etc/systemd/system/ndis-worker.service <<EOF
[Unit]
Description=NDIS Lead Finder background jobs (Celery)
After=network-online.target redis-server.service
Wants=network-online.target
Requires=redis-server.service

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/celery -A ndis_lead_finder worker --beat --concurrency 1 --loglevel info --schedule $APP_DIR/.celerybeat-schedule
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Older installs used systemd timers. Celery replaces them, so turn them off to avoid double runs.
for old in scrape backup summary; do
  systemctl disable --now "ndis-$old.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/ndis-$old.timer" "/etc/systemd/system/ndis-$old.service"
done

systemctl daemon-reload
systemctl enable ndis-web.service ndis-worker.service
systemctl restart ndis-web.service ndis-worker.service

echo "==> Setting up nginx"
cat > /etc/nginx/sites-available/ndis-lead-finder <<EOF
server {
    listen 80;
    server_name ${DOMAIN:-_};
    client_max_body_size 5m;

    location /static/ { alias $APP_DIR/staticfiles/; }
    location /media/  { alias $APP_DIR/media/; }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 240s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/ndis-lead-finder /etc/nginx/sites-enabled/ndis-lead-finder
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl reload nginx || systemctl restart nginx
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow "Nginx Full" >/dev/null; fi

SITE_URL="http://$PUBLIC_HOST"
if [ -n "$DOMAIN" ]; then
  echo "==> Requesting the HTTPS certificate"
  if certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect --keep-until-expiring; then
    set_env DJANGO_SECURE_COOKIES true
    set_env DJANGO_SECURE_SSL_REDIRECT true
    set_env CSRF_TRUSTED_ORIGINS "https://$DOMAIN"
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    systemctl restart ndis-web.service
    SITE_URL="https://$DOMAIN"
  else
    echo "WARNING: HTTPS failed, so the site is on plain HTTP. Check that the domain's DNS points to this server, then run this script again."
  fi
fi

echo "==> Running the first scan in the background"
(cd "$APP_DIR" && runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/celery" -A ndis_lead_finder call leads.run_scan >/dev/null) \
  || echo "WARNING: could not queue the first scan. Press Scan now under Settings once the site is up."

cat <<EOF

Finished. Site: $SITE_URL

  Worker:        systemctl status ndis-worker.service
  Scan logs:     journalctl -u ndis-worker.service -n 50
  Website logs:  journalctl -u ndis-web.service -n 50
  Settings:      $ENV_FILE (run: systemctl restart ndis-web.service after changing it)
EOF
if [ "$SITE_URL" = "http://$PUBLIC_HOST" ]; then
  echo
  echo "WARNING: this site has no HTTPS, so passwords and lead details are sent unencrypted. Use a domain before real use."
fi
