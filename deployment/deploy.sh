#!/usr/bin/env bash
# Push local code to the server and restart.
#
# Sync only what the app needs. Never rsync the whole directory: data/ holds the
# live database and .env holds the server's own credentials, and overwriting
# either from a laptop is how you lose a client's contact list.
#
#   deploy.sh              deploy for real
#   deploy.sh --dry-run    print every step, contact no server, build nothing
#   deploy.sh --allow-dirty  deploy uncommitted work (say why in the PR)
#
# The dry run exists so this script is reviewable and testable without a box to
# deploy to. It is the only way anyone checks the rsync excludes before they are
# pointed at a live database.
#
# The order below is the whole design and it is not arbitrary:
#
#   clean tree -> build assets -> sync code -> BACK UP -> MIGRATE -> restart
#                                                                -> health check
#                                                                -> roll back
#
# Back up before migrating, because a migration is the one step with no undo
# button. Migrate before restarting, because a worker that comes up against a
# schema it does not know serves 500s to every page. Health-check after
# restarting, because "systemctl says active" and "the app answers" are
# different claims and only the second one matters.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=0
ALLOW_DIRTY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        -h|--help)     sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "unknown option: $arg" >&2; exit 2 ;;
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
# The app binds 127.0.0.1:8000 (see app.service.template). Health-check that
# directly rather than the public URL: this is asking "did the process come
# back", and a green answer through nginx and TLS could be a cached page or a
# stale worker.
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"

run() { if [ "$DRY_RUN" -eq 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi; }
step() { printf '\n── %s\n' "$*"; }

echo "Deploying to $SERVER:$REMOTE_DIR"

# ── 1. Refuse a dirty tree ───────────────────────────────────────────────────
# What gets deployed must be something you can get back. A deploy from a dirty
# tree cannot be reproduced, and "roll back to the previous commit" does not
# restore it — the thing that was running was never committed anywhere.
#
# A dry run reports the refusal instead of performing it. Its whole job is to
# print every later step for review, and a dry run that exits at step 1 because
# the reviewer happens to have an open editor shows nothing at all.
step "working tree"
if [ ! -d .git ]; then
    echo "  not a git checkout — skipping the clean-tree check"
elif [ -n "$(git status --porcelain)" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  tree is DIRTY — a real deploy would ABORT here:"
        git status --short | sed 's/^/    /'
        echo "  (--allow-dirty overrides it; continuing the dry run regardless)"
    elif [ "$ALLOW_DIRTY" -eq 1 ]; then
        echo "  tree is DIRTY, continuing because --allow-dirty was passed:"
        git status --short | sed 's/^/    /'
    else
        echo "ABORT: the working tree has uncommitted changes."
        git status --short | sed 's/^/    /'
        echo ""
        echo "Commit them, stash them, or re-run with --allow-dirty if you"
        echo "genuinely mean to ship something that exists on no branch."
        exit 1
    fi
else
    echo "  clean at $(git rev-parse --short HEAD)"
fi

# ── 2. Build the assets ──────────────────────────────────────────────────────
# The stylesheet and the fonts are build artifacts (both gitignored), and the
# server carries no node toolchain — so they are built here and shipped as
# files. fonts:sync is not optional: the rsync below runs --delete, so a deploy
# that syncs a static/ directory with no fonts in it DELETES the server's copy,
# and the UI silently falls back to system-ui with no error anywhere.
step "assets"
run npm run build:css
run npm run fonts:sync
if [ "$DRY_RUN" -eq 0 ]; then
    test -s app/static/app.css || { echo "app/static/app.css missing after build"; exit 1; }
    ls app/static/fonts/*.woff2 >/dev/null 2>&1 || {
        echo "app/static/fonts/ is empty after fonts:sync — the sync below would"
        echo "delete the server's fonts. Run 'npm install' and try again."
        exit 1
    }
fi

# ── 3. Sync ──────────────────────────────────────────────────────────────────
step "sync"
run rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    app/ "$SERVER:$REMOTE_DIR/app/"

run rsync -avz requirements.txt alembic.ini "$SERVER:$REMOTE_DIR/"
run rsync -avz --delete --exclude '__pycache__' alembic/ "$SERVER:$REMOTE_DIR/alembic/"
run rsync -avz scripts/ "$SERVER:$REMOTE_DIR/scripts/"

# ── 4. Back up, migrate, restart, verify ─────────────────────────────────────
# Migrations run here, before the restart, and never from the app itself: the
# app skips its startup upgrade when ENVIRONMENT=production precisely so two
# workers restarting together cannot race each other into the same migration.
step "backup, migrate, restart, health-check"
if [ "$DRY_RUN" -eq 1 ]; then
    cat <<DRY
  would run over ssh on $SERVER:
      ./venv/bin/pip install -r requirements.txt
      cp -a app app.prev                      (rollback copy of the running code)
      detect a pre-Alembic database and abort rather than guess at stamping
      ./scripts/backup.sh                     (BACKUP, immediately before migrating)
      ./venv/bin/alembic upgrade head         (MIGRATE; abort the deploy if it fails)
      sudo systemctl restart $SERVICE         (RESTART, only after the migration)
      curl -fsS $HEALTH_URL                   (HEALTH CHECK, retried for ~30s)
      on health-check failure:
          restore app.prev over app, restart, re-check, and exit non-zero
          (the code is rolled back; the migration is NOT — restore the backup
           taken above by hand if the schema is the problem)
DRY
else
ssh "$SERVER" bash -s <<REMOTE
set -euo pipefail
cd "$REMOTE_DIR"
./venv/bin/pip install --quiet -r requirements.txt

# The rollback copy. Taken after the sync of code but before anything is
# restarted, so it is the code that was *running* a moment ago.
rm -rf app.prev
cp -a app app.prev

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

# Immediately before the migration, not nightly-and-hopefully-recent. If an
# upgrade mangles a table, the only acceptable distance between the backup and
# the damage is zero.
echo "── backing up before migrating"
./scripts/backup.sh

echo "── migrating"
if ! ./venv/bin/alembic upgrade head; then
    echo "ABORT: 'alembic upgrade head' failed. The service was NOT restarted and"
    echo "is still running the previous code against the previous schema."
    echo "Restore from the backup above if the database was left part-migrated."
    exit 1
fi

echo "── restarting"
sudo systemctl restart "$SERVICE"

# Retry rather than sleep-and-hope: uvicorn is usually up in under a second,
# but a cold page cache after a reboot has taken fifteen. A fixed 'sleep 2'
# reports a false failure on exactly the deploy you were most nervous about.
healthy=0
for _ in \$(seq 1 15); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then healthy=1; break; fi
    sleep 2
done

if [ "\$healthy" -eq 1 ]; then
    echo "── healthy"
    rm -rf app.prev
    exit 0
fi

echo "── HEALTH CHECK FAILED — rolling the code back to app.prev"
rm -rf app.broken
mv app app.broken
mv app.prev app
sudo systemctl restart "$SERVICE"

for _ in \$(seq 1 15); do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "Rolled back. The previous code is serving again."
        echo "The failed build is in $REMOTE_DIR/app.broken."
        echo "NOTE: the MIGRATION was not rolled back. If the schema is what"
        echo "broke it, restore the backup taken above by hand."
        exit 1
    fi
    sleep 2
done

echo "ROLLBACK ALSO UNHEALTHY. The service is down."
echo "  sudo journalctl -u $SERVICE -n 100 --no-pager"
exit 2
REMOTE
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "Dry run complete. Nothing was built, synced or restarted."
else
    echo ""
    echo "Deployed and healthy. Public check: curl -sf \$PUBLIC_BASE_URL/health"
fi
