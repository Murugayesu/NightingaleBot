#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DB_NAME="${POSTGRES_DB:-nursing_bot}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

echo "[$(date -u +%FT%TZ)] Starting backup of ${DB_NAME} from ${DB_HOST}:${DB_PORT}..."

PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --format=custom \
    --compress=9 \
    --file="$BACKUP_FILE"

echo "[$(date -u +%FT%TZ)] Backup successfully created at: $BACKUP_FILE"

find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime +7 -delete || true
echo "[$(date -u +%FT%TZ)] Retention policy applied (7-day window)."
