#!/usr/bin/env sh
# PostgreSQL backup with local retention.
#
#   backup.sh            take one backup and exit
#   backup.sh --loop     take a backup every BACKUP_INTERVAL_SECONDS (docker compose `backup` service)
#
# Connection comes from the standard libpq variables: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE.
# The docker compose `backup-sync` service mirrors BACKUP_DIR into object storage.
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
DB="${PGDATABASE:-nursing_bot}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

backup_once() {
    # Dumps are written into .partial/ and renamed into place when complete.
    # The rename is atomic (same filesystem), and backup-sync excludes
    # .partial/, so a half-written dump is never uploaded or restored.
    mkdir -p "$BACKUP_DIR/.partial"
    name="${DB}_$(date -u +%Y%m%d_%H%M%S).dump"
    log "Backing up ${DB} from ${PGHOST:-localhost} ..."
    # Custom format: compressed, and pg_restore can restore selectively and in parallel.
    pg_dump --format=custom --compress=9 --no-owner --file="$BACKUP_DIR/.partial/$name" "$DB"
    mv "$BACKUP_DIR/.partial/$name" "$BACKUP_DIR/$name"
    log "Created $BACKUP_DIR/$name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
    rm -f "$BACKUP_DIR/.partial/"*   # leftovers from an interrupted run
    find "$BACKUP_DIR" -maxdepth 1 -name "${DB}_*.dump" -type f -mtime +"$RETENTION_DAYS" -print -delete
    log "Retention applied (${RETENTION_DAYS} days)."
}

if [ "${1:-}" = "--loop" ]; then
    while true; do
        backup_once || log "BACKUP FAILED (will retry next cycle)"
        sleep "$INTERVAL"
    done
else
    backup_once
fi
