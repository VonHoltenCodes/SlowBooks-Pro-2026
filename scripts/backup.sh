#!/bin/bash
# ============================================================================
# PostgreSQL backup: pg_dump the database to a dated, gzipped file.
#
# The desktop app and Server Edition keep each company in one SQLite file
# and back it up from Settings -> Backup / Restore; this script is for the
# Docker and native PostgreSQL installs (docs/operations.md).
#
#   DB_NAME     database to dump (default bookkeeper; one per served company)
#   DB_USER     role to connect as (default bookkeeper)
#   BACKUP_DIR  where the files go (default ~/bookkeeper-backups)
#   PGHOST etc. are read by pg_dump itself.
#
# The dump carries --clean --if-exists, so restoring it over a database the
# app has already migrated and seeded replaces those rows instead of
# failing on every one of them (2.31.3).
# ============================================================================
set -uo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/bookkeeper-backups}"
DB_NAME="${DB_NAME:-bookkeeper}"
DB_USER="${DB_USER:-bookkeeper}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/bookkeeper_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "SlowBooks Pro 2026 - Backup Utility"
echo "===================================="
echo "Database: $DB_NAME"
echo "Backup to: $BACKUP_FILE"
echo ""

# With pipefail, a failing pg_dump fails the pipeline even though gzip
# succeeded, so the half-written file is removed and never counts toward
# the 30 that are kept.
if pg_dump --clean --if-exists -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "Backup completed: $BACKUP_FILE ($SIZE)"

    # Keep only the 30 newest
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/bookkeeper_*.sql.gz 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 30 ]; then
        ls -1t "$BACKUP_DIR"/bookkeeper_*.sql.gz | tail -n +31 | xargs rm -f
        echo "Pruned old backups (kept 30 most recent)"
    fi
else
    echo "ERROR: Backup failed!" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi
