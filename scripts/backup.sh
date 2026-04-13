#!/bin/bash
# ============================================================================
# Decompiled from qbw32.exe!CBackupManager::DoBackup()  Offset: 0x00248000
# Original backed up the .QBW file (Btrieve database) to a user-specified
# location. It also created a .QBB file which was just a renamed ZIP.
# We use pg_dump because PostgreSQL > Pervasive PSQL in every measurable way.
# ============================================================================

if [ -f ".env" ]; then
    set -a
    . ./.env
    set +a
fi

BACKUP_DIR="${BACKUP_DIR:-$HOME/bookkeeper-backups}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-bookkeeper}"
DB_USER="${POSTGRES_USER:-bookkeeper}"
DB_PASSWORD="${POSTGRES_PASSWORD:-bookkeeper}"
DB_SSLMODE="${POSTGRES_SSLMODE:-disable}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/bookkeeper_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Slowbooks Pro 2026 — Backup Utility"
echo "===================================="
echo "Host: $DB_HOST:$DB_PORT"
echo "Database: $DB_NAME"
echo "Backup to: $BACKUP_FILE"
echo ""

PGPASSWORD="$DB_PASSWORD" PGSSLMODE="$DB_SSLMODE" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "Backup completed: $BACKUP_FILE ($SIZE)"

    # Keep only last 30 backups
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/bookkeeper_*.sql.gz 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 30 ]; then
        ls -1t "$BACKUP_DIR"/bookkeeper_*.sql.gz | tail -n +31 | xargs rm -f
        echo "Pruned old backups (kept 30 most recent)"
    fi
else
    echo "ERROR: Backup failed!"
    rm -f "$BACKUP_FILE"
    exit 1
fi
