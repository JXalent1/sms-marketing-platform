#!/usr/bin/env bash
# Push local code to the server and restart.
#
# Sync only what the app needs. Never rsync the whole directory: data/ holds the
# live database and .env holds the server's own credentials, and overwriting
# either from a laptop is how you lose a client's contact list.
#
#   deploy.sh              deploy for real
#   deploy.sh --dry-run    print every step, contact no server, build nothing
#
# The dry run exists so this script is reviewable and testable without a box to
# deploy to. It is the only way anyone checks the rsync excludes before they are
# pointed at a live database.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$DRY_RUN" -eq 1 ]; then
    # Placeholders, so a dry run needs no environment at all.
    SERVER="${SERVER:-appuser@example.invalid}"
    SERVICE="${SERVICE:-a4a-sms}"
    echo "DRY RUN — no files are built, synced or restarted."
else
    SERVER="${SERVER:?set SERVER=user@host}"
    SERVICE="${SERVICE:?set SERVICE=your-service-name}"
fi
REMOTE_DIR="${REMOTE_DIR:-/home/appuser/app}"

run() { if [ "$DRY_RUN" -eq 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }

echo "Deploying to $SERVER:$REMOTE_DIR"

# The stylesheet is a build artifact (gitignored), and the server carries no node
# toolchain — so it is compiled here, before the sync, and shipped as a file.
# Skipping this step serves the whole dashboard with no CSS at all.
echo "Building stylesheet..."
run npm run build:css
if [ "$DRY_RUN" -eq 0 ]; then
    test -s app/static/app.css || { echo "app/static/app.css missing after build"; exit 1; }
fi

run rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    app/ "$SERVER:$REMOTE_DIR/app/"

run rsync -avz requirements.txt alembic.ini "$SERVER:$REMOTE_DIR/"
run rsync -avz --delete --exclude '__pycache__' alembic/ "$SERVER:$REMOTE_DIR/alembic/"
run rsync -avz scripts/ "$SERVER:$REMOTE_DIR/"

# Migrations run here, before the restart, and never from the app itself: the
# app skips its startup upgrade when ENVIRONMENT=production precisely so two
# workers restarting together cannot race each other into the same migration.
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  would run over ssh on $SERVER:"
    echo "      ./venv/bin/pip install -r requirements.txt"
    echo "      detect a pre-Alembic database and abort rather than guess at stamping"
    echo "      ./venv/bin/alembic upgrade head"
    echo "      sudo systemctl restart $SERVICE"
    echo "      systemctl is-active $SERVICE"
else
ssh "$SERVER" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
./venv/bin/pip install --quiet -r requirements.txt

# A database from before Alembic has every table and no alembic_version row, so
# 'upgrade head' fails with "table app_settings already exists". The fix is
# 'alembic stamp head' — but only for that case. Stamping an EMPTY database
# marks every migration as applied when none is, and the tables are then never
# created at all. The two situations look identical to a script that only checks
# for a missing version, so this one refuses and hands the decision to a human
# rather than guessing on the client's live data.
SCHEMA_STATE=\$(./venv/bin/python - <<'PY'
from sqlalchemy import create_engine, inspect
from app.core.config import settings

names = set(inspect(create_engine(settings.DATABASE_URL)).get_table_names())
unversioned = names - {"alembic_version"} and "alembic_version" not in names
print("legacy" if unversioned else "ok")
PY
)
if [ "\$SCHEMA_STATE" = "legacy" ]; then
    echo "ABORT: the database has tables but no Alembic version — it predates migrations."
    echo "Verify the schema matches the initial migration, then run ONCE, by hand:"
    echo "    cd $REMOTE_DIR && ./venv/bin/alembic stamp head"
    echo "Do NOT stamp an empty database."
    exit 1
fi
./venv/bin/alembic upgrade head

sudo systemctl restart "$SERVICE"
sleep 2
systemctl is-active "$SERVICE"
REMOTE
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "Dry run complete. Nothing was built, synced or restarted."
else
    echo "Deployed. Verify: curl -sf \$PUBLIC_BASE_URL/health"
fi
