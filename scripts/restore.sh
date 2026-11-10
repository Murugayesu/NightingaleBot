#!/usr/bin/env sh
# Restore a backup made by backup.sh into a database.
#
#   restore.sh <file.dump> [target_database]
#
# DESTRUCTIVE for the target: existing objects are dropped first (--clean).
# Stop the api and worker before restoring into the live database:
#   docker compose stop api worker
#   docker compose run --rm --entrypoint /bin/sh backup /scripts/restore.sh /backups/<file>.dump
#   docker compose start api worker
# (--entrypoint is required: the backup service's own entrypoint is the backup loop.
#  List the dumps with: docker compose exec backup ls -l /backups)
set -eu

FILE="${1:?usage: restore.sh <file.dump> [target_database]}"
TARGET="${2:-${PGDATABASE:-nursing_bot}}"

[ -f "$FILE" ] || { echo "No such file: $FILE" >&2; exit 2; }

echo "[$(date -u +%FT%TZ)] Restoring $FILE into database '$TARGET' on ${PGHOST:-localhost} ..."
pg_restore --clean --if-exists --no-owner --exit-on-error --dbname="$TARGET" "$FILE"
echo "[$(date -u +%FT%TZ)] Restore complete."
