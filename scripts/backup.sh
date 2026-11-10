#!/usr/bin/env sh
# PostgreSQL backup with safe retention.
#
#   backup.sh            take one backup; exit 1 if it failed
#   backup.sh --loop     take a backup every BACKUP_INTERVAL_SECONDS (docker compose `backup` service)
#
# Connection comes from the standard libpq variables: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE.
# The docker compose `backup-sync` service mirrors BACKUP_DIR into object storage.
#
# Every step is checked explicitly: `set -e` does not apply inside a function
# called from `if` or `||`, so relying on it would let a failed pg_dump slip through.
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
KEEP_MIN="${BACKUP_KEEP_MIN:-3}"   # never prune below this many good dumps
INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
DB="${PGDATABASE:-nursing_bot}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

prune() {
    # Delete dumps older than RETENTION_DAYS, but always keep the newest KEEP_MIN,
    # so a long run of failed backups can never age out the last good copies.
    ls -1t "$BACKUP_DIR"/"${DB}"_*.dump 2>/dev/null | tail -n +"$((KEEP_MIN + 1))" |
        while IFS= read -r old; do
            if [ -n "$(find "$old" -mtime +"$RETENTION_DAYS" 2>/dev/null)" ]; then
                rm -f "$old" && log "Pruned $old"
            fi
        done
}

backup_once() {
    # Dumps are written into .partial/ and renamed into place only when complete
    # and verified; backup-sync excludes .partial/, so nothing half-written is
    # ever uploaded or restored.
    mkdir -p "$BACKUP_DIR/.partial" || return 1
    rm -f "$BACKUP_DIR/.partial/"* 2>/dev/null || true   # leftovers of an interrupted run
    name="${DB}_$(date -u +%Y%m%d_%H%M%S).dump"
    tmp="$BACKUP_DIR/.partial/$name"
    log "Backing up ${DB} from ${PGHOST:-localhost} ..."

    # Custom format: compressed, and pg_restore can restore selectively and in parallel.
    if ! pg_dump --format=custom --compress=9 --no-owner --file="$tmp" "$DB"; then
        rm -f "$tmp"
        log "ERROR: pg_dump failed; existing backups left untouched."
        return 1
    fi
    if ! pg_restore --list "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        log "ERROR: the new dump is not a readable archive; existing backups left untouched."
        return 1
    fi
    if ! mv "$tmp" "$BACKUP_DIR/$name"; then
        log "ERROR: could not move the dump into $BACKUP_DIR."
        return 1
    fi
    log "Created $BACKUP_DIR/$name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
    prune
    log "Retention applied (${RETENTION_DAYS} days, keeping at least ${KEEP_MIN})."
}

if [ "${1:-}" = "--loop" ]; then
    while true; do
        if ! backup_once; then
            log "BACKUP FAILED (will retry next cycle)"
        fi
        sleep "$INTERVAL"
    done
else
    if ! backup_once; then
        log "BACKUP FAILED"
        exit 1
    fi
fi
