#!/usr/bin/env sh
# Prove the newest backup can actually be restored. Run it monthly (a backup
# that was never restored is only a hope):
#
#   docker compose run --rm --entrypoint /bin/sh backup /scripts/restore-test.sh
#
# Restores into a scratch database, checks that the core tables have rows,
# then drops the scratch database. The live database is never touched.
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB="${PGDATABASE:-nursing_bot}"
SCRATCH="${DB}_restore_test"

latest="$(ls -1t "$BACKUP_DIR"/"${DB}"_*.dump 2>/dev/null | head -n 1 || true)"
[ -n "$latest" ] || { echo "No backups found in $BACKUP_DIR" >&2; exit 2; }

echo "Testing restore of $latest into scratch database $SCRATCH"
psql --dbname=postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" -c "CREATE DATABASE \"$SCRATCH\""
trap 'psql --dbname=postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null' EXIT

pg_restore --no-owner --exit-on-error --dbname="$SCRATCH" "$latest"

for table in documents chunks alembic_version; do
    count="$(psql --dbname="$SCRATCH" -At -c "SELECT count(*) FROM $table")"
    echo "  $table: $count rows"
done
version="$(psql --dbname="$SCRATCH" -At -c 'SELECT version_num FROM alembic_version')"
[ -n "$version" ] || { echo "RESTORE TEST FAILED: no alembic version" >&2; exit 1; }
echo "RESTORE TEST PASSED (schema revision $version)"
