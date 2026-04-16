# ============================================================================
# Backup/Restore Service — pg_dump/pg_restore subprocess wrapper
# Feature 11: Database backup and restore accessible from settings
# ============================================================================

import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.backups import Backup

BACKUP_DIR = Path(__file__).parent.parent.parent / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_FILENAME_PATTERN = re.compile(r"slowbooks_\d{8}_\d{6}\.sql\Z")


def _parse_db_url(url: str) -> dict:
    """Parse PostgreSQL connection URL into components."""
    # postgresql://user:pass@host:port/dbname
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "bookkeeper",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "bookkeeper",
        "sslmode": (query.get("sslmode") or [""])[0],
    }


def resolve_backup_path(filename: str) -> Path:
    """Resolve a managed backup filename to a path inside the backup directory."""
    if not isinstance(filename, str):
        raise ValueError("Invalid backup filename")

    candidate = filename.strip()
    if candidate != filename or not candidate:
        raise ValueError("Invalid backup filename")
    if Path(candidate).is_absolute() or "/" in candidate or "\\" in candidate:
        raise ValueError("Invalid backup filename")
    if not BACKUP_FILENAME_PATTERN.fullmatch(candidate):
        raise ValueError("Invalid backup filename")

    backup_root = BACKUP_DIR.resolve()
    resolved = (backup_root / candidate).resolve()
    try:
        resolved.relative_to(backup_root)
    except ValueError as exc:
        raise ValueError("Invalid backup filename") from exc
    return resolved


def create_backup(db: Session, notes: str = None, backup_type: str = "manual") -> dict:
    """Create a database backup using pg_dump."""
    params = _parse_db_url(DATABASE_URL)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"slowbooks_{timestamp}.sql"
    filepath = BACKUP_DIR / filename

    env = {"PGPASSWORD": params["password"]}
    if params["sslmode"]:
        env["PGSSLMODE"] = params["sslmode"]

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
    try:
        filepath = resolve_backup_path(filename)
    except ValueError:
        return {"success": False, "error": "Invalid backup filename", "status_code": 400}

    if not filepath.exists():
        return {"success": False, "error": "Backup file not found", "status_code": 404}

    params = _parse_db_url(DATABASE_URL)
    env = {"PGPASSWORD": params["password"]}
    if params["sslmode"]:
        env["PGSSLMODE"] = params["sslmode"]

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
            return {"success": False, "error": "Restore failed"}

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
