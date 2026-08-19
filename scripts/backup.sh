#!/usr/bin/env bash
# Nightly backup of the SQLite database, with a restore check.
#
# The prior client's 181 MB production database had no backup at all. This is
# the fix, and the --verify half is the part that matters: an untested backup is
# a rumour. Nothing here proves the file is restorable until it has been opened
# and integrity-checked, so this script does that on demand and the cron entry
# does it every night.
#
#   scripts/backup.sh                 take a backup
#   scripts/backup.sh --verify        take a backup, then restore and check it
#   scripts/backup.sh --verify-only   check the newest existing archive
#   scripts/backup.sh --dry-run       print what would happen, touch nothing
#
# Off-box copy: set BACKUP_REMOTE=user@host:/path and each new archive is scp'd
# there after it is written. A backup on the same disk as the database survives
# a bad deploy but not a dead droplet.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"        # nightly, so ~2 weeks of history
BACKUP_REMOTE="${BACKUP_REMOTE:-}"

DRY_RUN=0
VERIFY=0
TAKE_BACKUP=1

for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --verify)      VERIFY=1 ;;
        --verify-only) VERIFY=1; TAKE_BACKUP=0 ;;
        -h|--help)     sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY_RUN" -eq 1 ]; then say "  would run: $*"; else "$@"; fi; }

# ── Locate the database ──────────────────────────────────────────────────────
# DATABASE_URL is the single source of truth, same as the app reads. Parsing it
# here rather than hardcoding data/app.db means a moved database is backed up
# without anyone remembering to edit this file.
if [ -z "${DATABASE_URL:-}" ] && [ -f .env ]; then
    DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | tail -1 | cut -d= -f2- || true)"
fi
DATABASE_URL="${DATABASE_URL:-sqlite:///./data/app.db}"

case "$DATABASE_URL" in
    sqlite:*) ;;
    *)  say "ABORT: this script backs up SQLite only; DATABASE_URL is not a sqlite URL."
        say "A Postgres deployment should use pg_dump on the database host instead."
        exit 1 ;;
esac
# sqlite:///./data/app.db -> ./data/app.db ; sqlite:////var/db/app.db -> /var/db/app.db
DB_PATH="${DATABASE_URL#sqlite://}"
DB_PATH="${DB_PATH#/}"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/app-$STAMP.db.gz"

# ── Take the backup ──────────────────────────────────────────────────────────
if [ "$TAKE_BACKUP" -eq 1 ]; then
    say "Database: $DB_PATH"
    if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$DB_PATH" ]; then
        say "ABORT: no database at $DB_PATH"
        exit 1
    fi

    run mkdir -p "$BACKUP_DIR"
    say "Backing up -> $ARCHIVE"

    # sqlite3's online backup API, not cp. Copying a live database file while a
    # campaign is writing to it yields a torn page and a backup that only fails
    # when you need it. The stdlib module is used rather than the sqlite3 CLI so
    # the server needs no package beyond the Python the app already runs on.
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  would run: python3 -c '<sqlite3 online backup>' $DB_PATH -> $ARCHIVE"
    else
        TMP_SNAPSHOT="$(mktemp "${TMPDIR:-/tmp}/a4a-backup.XXXXXX")"
        # shellcheck disable=SC2064
        trap "rm -f '$TMP_SNAPSHOT'" EXIT
        python3 - "$DB_PATH" "$TMP_SNAPSHOT" <<'PY'
import sqlite3, sys

src_path, dest_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dest = sqlite3.connect(dest_path)
try:
    src.backup(dest)          # consistent snapshot even under concurrent writes
finally:
    dest.close()
    src.close()
PY
        gzip -c "$TMP_SNAPSHOT" > "$ARCHIVE"
        rm -f "$TMP_SNAPSHOT"
        trap - EXIT
        say "Wrote $ARCHIVE ($(wc -c < "$ARCHIVE" | tr -d ' ') bytes)"
    fi

    # ── Off-box copy ─────────────────────────────────────────────────────────
    if [ -n "$BACKUP_REMOTE" ]; then
        say "Copying off-box -> $BACKUP_REMOTE"
        run scp -q "$ARCHIVE" "$BACKUP_REMOTE"
    else
        say "BACKUP_REMOTE not set — archive stays on this box only."
    fi

    # ── Retention ────────────────────────────────────────────────────────────
    # Prune by count, oldest first. Deliberately only touches files this script
    # names, so an unrelated file dropped in the directory is never deleted.
    if [ "$DRY_RUN" -eq 0 ]; then
        # while-read rather than mapfile: macOS still ships bash 3.2 and this
        # script is run by hand from a laptop as often as it is by cron.
        ls -1t "$BACKUP_DIR"/app-*.db.gz 2>/dev/null | tail -n "+$((BACKUP_KEEP + 1))" \
        | while IFS= read -r f; do
            [ -n "$f" ] || continue
            say "Pruning $f"
            rm -f "$f"
        done
    else
        say "  would prune archives beyond the newest $BACKUP_KEEP"
    fi
fi

# ── Verify: restore the archive and integrity-check it ───────────────────────
if [ "$VERIFY" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say ""
        say "  would restore the newest archive to a temp file and run PRAGMA integrity_check"
        exit 0
    fi

    TARGET="$ARCHIVE"
    if [ "$TAKE_BACKUP" -eq 0 ]; then
        TARGET="$(ls -1t "$BACKUP_DIR"/app-*.db.gz 2>/dev/null | head -1 || true)"
        [ -n "$TARGET" ] || { say "ABORT: no archive found in $BACKUP_DIR"; exit 1; }
    fi

    say ""
    say "Verifying $TARGET"
    TMP_RESTORE="$(mktemp "${TMPDIR:-/tmp}/a4a-verify.XXXXXX")"
    # shellcheck disable=SC2064
    trap "rm -f '$TMP_RESTORE'" EXIT
    gunzip -c "$TARGET" > "$TMP_RESTORE"

    # integrity_check alone passes on a zero-byte file, which is exactly the
    # backup failure worth catching. So the table count is asserted too: a
    # restored database that reports "ok" but contains nothing is not a backup.
    python3 - "$TMP_RESTORE" <<'PY'
import sqlite3, sys

path = sys.argv[1]
db = sqlite3.connect(path)
try:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
    tables = db.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
except sqlite3.DatabaseError as exc:
    # A truncated or garbled archive fails here rather than at integrity_check.
    # Caught only to report it as a backup failure instead of a traceback; the
    # exit status is still non-zero and cron still sees a failed job.
    sys.exit(f"VERIFY FAILED: archive did not open as a database ({exc})")
finally:
    db.close()

print(f"  PRAGMA integrity_check: {result}")
print(f"  tables restored: {tables}")
if result != "ok":
    sys.exit("VERIFY FAILED: integrity_check did not return ok")
if tables == 0:
    sys.exit("VERIFY FAILED: restored database has no tables")
PY
    rm -f "$TMP_RESTORE"
    trap - EXIT
    say "VERIFY OK — archive restores and passes integrity_check."
fi
