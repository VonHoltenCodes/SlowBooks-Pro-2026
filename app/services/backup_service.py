# ============================================================================
# Backup/Restore Service — pg_dump/pg_restore subprocess wrapper
# Feature 11: Database backup and restore accessible from settings
# ============================================================================

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.backups import Backup

BACKUP_DIR = Path(__file__).parent.parent.parent / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Strict filename allow-list. Backup files we create are named
# "slowbooks_YYYYMMDD_HHMMSS.sql"; we accept any safe basename matching
# this character class with a known backup extension. NO path separators,
# NO ".." components -- this is the trust boundary that CodeQL needs to
# see at the start of restore_backup() before BACKUP_DIR / filename is
# constructed.
_BACKUP_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(sql|dump|backup)$")


def _safe_backup_filename(filename: str) -> str | None:
    """Return a safe basename for a backup file, or None if invalid.

    Uses os.path.basename() to strip any path components — this is a
    sanitizer recognized by static analyzers (CodeQL py/path-injection)
    so the returned value is treated as path-safe at downstream sinks.
    Then enforces the strict regex / length / leading-dot rules.
    """
    if not filename or len(filename) > 255:
        return None
    # os.path.basename strips any directory component; if the input
    # contained separators, the result will differ from the input and we
    # reject it (keeps "files only" semantics rather than silently
    # accepting "evil/foo.sql" as "foo.sql").
    base = os.path.basename(filename)
    if base != filename:
        return None
    if base.startswith(".") or ".." in base:
        return None
    if not _BACKUP_FILENAME_RE.match(base):
        return None
    return base


def _parse_db_url(url: str) -> dict:
    """Parse PostgreSQL connection URL into components."""
    # postgresql://user:pass@host:port/dbname
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "bookkeeper",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "bookkeeper",
    }


def create_backup(db: Session, notes: str = None, backup_type: str = "manual") -> dict:
    """Create a database backup using pg_dump."""
    params = _parse_db_url(DATABASE_URL)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"slowbooks_{timestamp}.sql"
    filepath = BACKUP_DIR / filename

    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            ["pg_dump", "-h", params["host"], "-p", params["port"],
             "-U", params["user"], "-F", "c", "-f", str(filepath), params["dbname"]],
            env={**dict(__import__("os").environ), **env},
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        file_size = filepath.stat().st_size

        backup = Backup(
            filename=filename, file_size=file_size,
            backup_type=backup_type, notes=notes,
        )
        db.add(backup)
        db.commit()

        return {"success": True, "filename": filename, "file_size": file_size}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Backup timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "pg_dump not found. Is PostgreSQL client installed?"}


def restore_backup(db: Session, filename: str) -> dict:
    """Restore a database from a backup file."""
    # Trust boundary: validate filename as a safe basename BEFORE using it
    # to construct any filesystem path. is_relative_to is the
    # belt-and-suspenders check after.
    safe_name = _safe_backup_filename(filename)
    if safe_name is None:
        return {"success": False, "error": "Invalid filename"}

    backup_root = BACKUP_DIR.resolve()
    filepath = (backup_root / safe_name).resolve()
    if not filepath.is_relative_to(backup_root):
        return {"success": False, "error": "Invalid filename"}
    if not filepath.exists():
        return {"success": False, "error": f"Backup file not found: {safe_name}"}

    params = _parse_db_url(DATABASE_URL)
    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            ["pg_restore", "-h", params["host"], "-p", params["port"],
             "-U", params["user"], "-d", params["dbname"], "--clean", "--if-exists",
             str(filepath)],
            env={**dict(__import__("os").environ), **env},
            capture_output=True, text=True, timeout=300,
        )
        # pg_restore may return non-zero even on partial success
        if result.returncode != 0 and "error" in result.stderr.lower():
            return {"success": False, "error": result.stderr[:500]}

        return {"success": True, "message": f"Restored from {filename}"}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Restore timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "pg_restore not found"}


def list_backup_files() -> list[dict]:
    """List all backup files in the backup directory."""
    files = []
    for f in sorted(BACKUP_DIR.glob("slowbooks_*.sql"), reverse=True):
        files.append({
            "filename": f.name,
            "file_size": f.stat().st_size,
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return files
