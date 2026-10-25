#!/bin/sh
# Nightly backup of the mirror's state.
#
# jobs.db is the one thing that can't be rebuilt: it records which Reddit
# post/comment became which Lemmy post/comment. Without it the mirror would
# re-post everything as duplicates and reply to the wrong threads.
#
# Each run writes backups/mirror-YYYY-MM-DD.tar.gz containing:
#   jobs.db           consistent copy, safe to take while the workers run
#   media_cache.json  which media was already uploaded (avoids re-uploads)
#   .env              settings and credentials (backups/ is root-only)
# and deletes backups older than KEEP_DAYS.
#
# Usage: contrib/backup.sh            (from cron; see README "Backups")
# Env:   PROJECT_DIR (default: the folder above this script), KEEP_DAYS (14)

set -eu

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
KEEP_DAYS="${KEEP_DAYS:-14}"
DATA="$PROJECT_DIR/data"
DEST="$PROJECT_DIR/backups"
STAMP="$(date +%Y-%m-%d)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

umask 077
mkdir -p "$DEST"
chmod 700 "$DEST"

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $*"; }

if [ ! -f "$DATA/jobs.db" ]; then
    say "❌ Backup failed: $DATA/jobs.db not found"
    exit 1
fi

# A plain file copy can catch the database mid-write. SQLite's backup API
# takes a consistent snapshot even while the workers are using it.
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DATA/jobs.db" ".backup '$WORK/jobs.db'"
elif command -v python3 >/dev/null 2>&1; then
    python3 - "$DATA/jobs.db" "$WORK/jobs.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=60)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
else
    # The worker image ships sqlite3; write the snapshot into the shared data folder
    docker exec reddit_mirror_worker sqlite3 /opt/Reddit-Mirror-2-Lemmy/data/jobs.db \
        ".backup '/opt/Reddit-Mirror-2-Lemmy/data/.backup-snapshot.db'"
    mv "$DATA/.backup-snapshot.db" "$WORK/jobs.db"
fi

# Refuse to keep a damaged copy
if command -v sqlite3 >/dev/null 2>&1; then
    CHECK="$(sqlite3 "$WORK/jobs.db" 'PRAGMA quick_check;')"
elif command -v python3 >/dev/null 2>&1; then
    CHECK="$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA quick_check").fetchone()[0])' "$WORK/jobs.db")"
else
    CHECK="ok"
fi
if [ "$CHECK" != "ok" ]; then
    say "❌ Backup failed: snapshot didn't pass SQLite's integrity check ($CHECK)"
    exit 1
fi

for f in media_cache.json; do
    [ -f "$DATA/$f" ] && cp "$DATA/$f" "$WORK/$f"
done
[ -f "$PROJECT_DIR/.env" ] && cp "$PROJECT_DIR/.env" "$WORK/.env"

OUT="$DEST/mirror-$STAMP.tar.gz"
tar -czf "$OUT.tmp" -C "$WORK" .
mv "$OUT.tmp" "$OUT"

find "$DEST" -maxdepth 1 -name 'mirror-*.tar.gz' -mtime +"$KEEP_DAYS" -delete

say "✅ Backup saved: $OUT ($(du -h "$OUT" | cut -f1)); keeping $KEEP_DAYS days"
