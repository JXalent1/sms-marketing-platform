#!/usr/bin/env bash
# Bring up a fresh Ubuntu server for this app. Run once, on the server, as a
# user with sudo. After this finishes the box is ready for deployment/deploy.sh.
#
#   bootstrap.sh --dry-run          print every step, change nothing
#   bootstrap.sh --domain sms.example.com
#
# What it does, in order:
#   1. apt packages: Python 3.12, Node (Tailwind build), nginx, certbot, rsync
#   2. a non-root service user that owns the app directory
#   3. the virtualenv and the app's runtime directories
#   4. nginx site + systemd unit, rendered from the templates in this directory
#   5. the nightly backup cron entry
#
# It does NOT write .env, run migrations, obtain a certificate, or start
# sending. Those are the go-live steps and they are deliberately a human's,
# because .env carries the carrier credentials and the provider switch is the
# one change nobody should make by script.
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="${APP_NAME:-a4a-sms}"
APP_USER="${APP_USER:-appuser}"
APP_DIR="${APP_DIR:-/home/$APP_USER/app}"
DOMAIN="${DOMAIN:-}"
PYTHON="${PYTHON:-python3.12}"

DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --domain)  DOMAIN="${2:?--domain needs a value}"; shift ;;
        --user)    APP_USER="${2:?--user needs a value}"; APP_DIR="/home/$APP_USER/app"; shift ;;
        --name)    APP_NAME="${2:?--name needs a value}"; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n── %s\n' "$*"; }
run()  {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  would run: %s\n' "$*"
    else
        "$@"
    fi
}

if [ "$DRY_RUN" -eq 1 ]; then
    say "DRY RUN — nothing on this machine will be changed."
else
    # A dry run must work anywhere, including a laptop. A real run must not.
    [ -f /etc/os-release ] && grep -qi ubuntu /etc/os-release || {
        say "ABORT: this script targets Ubuntu. Re-run with --dry-run to inspect it."
        exit 1
    }
    [ "$(id -u)" -ne 0 ] || {
        say "ABORT: run as a sudo-capable user, not as root."
        say "The whole point of this script is that the app does not run as root."
        exit 1
    }
fi

say "app name : $APP_NAME"
say "user     : $APP_USER"
say "directory: $APP_DIR"
say "domain   : ${DOMAIN:-<unset — nginx site will keep the YOUR_DOMAIN placeholder>}"

# ── 1. Packages ──────────────────────────────────────────────────────────────
# Node is here for one reason: the stylesheet is compiled, not fetched from a
# CDN at runtime. deploy.sh builds it on the laptop and ships the file, so this
# is the fallback for building on the box.
step "packages"
run sudo apt-get update -qq
run sudo apt-get install -y --no-install-recommends \
    "$PYTHON" "$PYTHON-venv" "$PYTHON-dev" \
    build-essential nodejs npm nginx certbot python3-certbot-nginx \
    rsync sqlite3 ufw

# ── 2. Service user ──────────────────────────────────────────────────────────
step "service user"
if [ "$DRY_RUN" -eq 1 ] || ! id -u "$APP_USER" >/dev/null 2>&1; then
    run sudo adduser --system --group --home "/home/$APP_USER" --shell /bin/bash "$APP_USER"
else
    say "  $APP_USER already exists"
fi

# ── 3. Directories and virtualenv ────────────────────────────────────────────
# data/, logs/ and exports/ are the only paths the systemd unit grants write
# access to (ProtectSystem=full, ReadWritePaths=...). They must exist and be
# owned by the service user before the unit will start.
step "directories"
run sudo mkdir -p "$APP_DIR"/{app,data,logs,exports,backups,alembic}
run sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"

step "virtualenv"
run sudo -u "$APP_USER" "$PYTHON" -m venv "$APP_DIR/venv"
run sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip

# ── 4. nginx + systemd, rendered from the templates ──────────────────────────
# Rendered here rather than hand-copied so the placeholders cannot be missed.
# A forgotten YOUR_DOMAIN is a site that serves the default nginx page and a
# certbot run that fails with a confusing error.
step "nginx site"
NGINX_SITE="/etc/nginx/sites-available/$APP_NAME"
if [ "$DRY_RUN" -eq 1 ]; then
    say "  would render deployment/nginx.conf.template -> $NGINX_SITE"
    say "  would substitute YOUR_DOMAIN -> ${DOMAIN:-<unset>}"
    say "  would link into sites-enabled and reload nginx"
else
    RENDERED="$(mktemp)"
    sed "s/YOUR_DOMAIN/${DOMAIN:-YOUR_DOMAIN}/g" deployment/nginx.conf.template > "$RENDERED"
    sudo cp "$RENDERED" "$NGINX_SITE"
    rm -f "$RENDERED"
    sudo ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/$APP_NAME"
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t
    sudo systemctl reload nginx
fi

step "systemd unit"
UNIT="/etc/systemd/system/$APP_NAME.service"
if [ "$DRY_RUN" -eq 1 ]; then
    say "  would render deployment/app.service.template -> $UNIT"
    say "  would substitute appuser -> $APP_USER and /home/appuser/app -> $APP_DIR"
    say "  would daemon-reload and enable $APP_NAME (not start — no .env yet)"
else
    RENDERED="$(mktemp)"
    sed -e "s#/home/appuser/app#$APP_DIR#g" \
        -e "s/^User=appuser/User=$APP_USER/" \
        -e "s/^Group=appuser/Group=$APP_USER/" \
        deployment/app.service.template > "$RENDERED"
    sudo cp "$RENDERED" "$UNIT"
    rm -f "$RENDERED"
    sudo systemctl daemon-reload
    # enable, not start: there is no .env on the box yet, so a start here would
    # crash-loop and bury the real error under RestartSec noise.
    sudo systemctl enable "$APP_NAME"
fi

# ── 5. Nightly backup ────────────────────────────────────────────────────────
step "nightly backup cron"
CRON_LINE="15 3 * * * cd $APP_DIR && ./scripts/backup.sh --verify >> $APP_DIR/logs/backup.log 2>&1"
if [ "$DRY_RUN" -eq 1 ]; then
    say "  would install for $APP_USER:"
    say "    $CRON_LINE"
else
    # --verify on every run, not just install day. A backup job that silently
    # started writing corrupt archives is the failure this catches.
    ( sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -v 'scripts/backup.sh' || true
      echo "$CRON_LINE" ) | sudo -u "$APP_USER" crontab -u "$APP_USER" -
fi

# ── 6. Firewall ──────────────────────────────────────────────────────────────
step "firewall"
run sudo ufw allow OpenSSH
run sudo ufw allow 'Nginx Full'
run sudo ufw --force enable

# ── What is left for a human ─────────────────────────────────────────────────
cat <<EOF

── Bootstrap complete. Remaining steps are deliberately manual:

  1. Write $APP_DIR/.env from .env.example.
     Generate SECRET_KEY with:  openssl rand -hex 32
     Generate the admin hash with:  python scripts/hash_password.py
     Leave SMS_PROVIDER=console.

  2. From your laptop:  SERVER=$APP_USER@<host> SERVICE=$APP_NAME ./deployment/deploy.sh
     That builds the stylesheet, syncs the code and runs 'alembic upgrade head'.

  3. Certificate:  sudo certbot --nginx -d ${DOMAIN:-<your-domain>}

  4. Click through every screen over HTTPS with SMS_PROVIDER=console.

  5. Only then does a human switch the provider and send the first real message.
     That step is not automated anywhere in this repo, on purpose.
EOF
