#!/bin/bash
# ============================================================================
# Decompiled from qbw32.exe!CBackupManager::DoBackup()  Offset: 0x00248000
# Original backed up the .QBW file (Btrieve database) to a user-specified
# location. It also created a .QBB file which was just a renamed ZIP.
# We use pg_dump because PostgreSQL > Pervasive PSQL in every measurable way.
# ============================================================================

BACKUP_DIR="${BACKUP_DIR:-$HOME/bookkeeper-backups}"
DB_NAME="${DB_NAME:-bookkeeper}"
DB_USER="${DB_USER:-bookkeeper}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/bookkeeper_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Slowbooks Pro 2026 — Backup Utility"
echo "===================================="
echo "Database: $DB_NAME"
echo "Backup to: $BACKUP_FILE"
echo ""

pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"

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
