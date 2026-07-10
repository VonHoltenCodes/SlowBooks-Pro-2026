# ============================================================================
# Backup/Restore Service — accessible from settings
# Feature 11: Database backup and restore
#
# PostgreSQL (Docker / server installs): pg_dump/pg_restore subprocess.
# SQLite (native desktop installs): a snapshot copy of the company's .db
# file via sqlite3's online backup API (consistent even with the app's own
# connections open).
# ============================================================================

import os
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.backups import Backup

BACKUP_DIR = Path(__file__).parent.parent.parent / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Strict filename allow-list. Backup files we create are named
# "slowbooks_YYYYMMDD_HHMMSS.sql" (Postgres) or ".db" (SQLite); we accept
# any safe basename matching this character class with a known backup
# extension. NO path separators, NO ".." components -- this is the trust
# boundary that CodeQL needs to see at the start of restore_backup()
# before BACKUP_DIR / filename is constructed.
_BACKUP_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(sql|dump|backup|db)$")


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


def _is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


def _sqlite_db_path() -> Path | None:
    """Filesystem path of the active SQLite database, or None when the URL
    isn't a file-backed SQLite database (e.g. sqlite:///:memory:)."""
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    raw = DATABASE_URL[len("sqlite:///") :]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


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


def _create_sqlite_backup(db: Session, notes: str, backup_type: str) -> dict:
    """Snapshot the active company's .db file into BACKUP_DIR."""
    src = _sqlite_db_path()
    if src is None or not src.exists():
        return {
            "success": False,
            "error": "Active database is not a file-backed SQLite database",
        }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"slowbooks_{timestamp}.db"
    filepath = BACKUP_DIR / filename

    try:
        # sqlite3's online backup API gives a consistent snapshot even if
        # the app holds open connections (unlike a raw file copy).
        with closing(sqlite3.connect(src)) as source, closing(
            sqlite3.connect(filepath)
        ) as dest:
            source.backup(dest)
    except sqlite3.Error as exc:
        filepath.unlink(missing_ok=True)
        return {"success": False, "error": f"SQLite backup failed: {exc}"}

    file_size = filepath.stat().st_size
    backup = Backup(
        filename=filename,
        file_size=file_size,
        backup_type=backup_type,
        notes=notes,
    )
    db.add(backup)
    db.commit()

    return {"success": True, "filename": filename, "file_size": file_size}


def _restore_sqlite_backup(filepath: Path, safe_name: str) -> dict:
    """Copy a backup .db over the active company's database file."""
    dest = _sqlite_db_path()
    if dest is None:
        return {
            "success": False,
            "error": "Active database is not a file-backed SQLite database",
        }

    # Close the app's pooled connections first so the live file can be
    # rewritten (SQLite locking; also matters for Windows file semantics).
    import app.database as db_module

    db_module.engine.dispose()

    try:
        with closing(sqlite3.connect(filepath)) as source, closing(
            sqlite3.connect(dest)
        ) as target:
            source.backup(target)
    except sqlite3.Error as exc:
        return {"success": False, "error": f"SQLite restore failed: {exc}"}

    return {"success": True, "message": f"Restored from {safe_name}"}


def create_backup(db: Session, notes: str = None, backup_type: str = "manual") -> dict:
    """Create a database backup (pg_dump on Postgres, file snapshot on SQLite)."""
    if _is_sqlite():
        return _create_sqlite_backup(db, notes, backup_type)

    params = _parse_db_url(DATABASE_URL)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"slowbooks_{timestamp}.sql"
    filepath = BACKUP_DIR / filename

    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            [
                "pg_dump",
                "-h",
                params["host"],
                "-p",
                params["port"],
                "-U",
                params["user"],
                "-F",
                "c",
                "-f",
                str(filepath),
                params["dbname"],
            ],
            env={**dict(__import__("os").environ), **env},
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        file_size = filepath.stat().st_size

        backup = Backup(
            filename=filename,
            file_size=file_size,
            backup_type=backup_type,
            notes=notes,
        )
        db.add(backup)
        db.commit()

        return {"success": True, "filename": filename, "file_size": file_size}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Backup timed out"}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "pg_dump not found. Is PostgreSQL client installed?",
        }


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

    if _is_sqlite():
        return _restore_sqlite_backup(filepath, safe_name)

    params = _parse_db_url(DATABASE_URL)
    env = {"PGPASSWORD": params["password"]}

    try:
        result = subprocess.run(
            [
                "pg_restore",
                "-h",
                params["host"],
                "-p",
                params["port"],
                "-U",
                params["user"],
                "-d",
                params["dbname"],
                "--clean",
                "--if-exists",
                str(filepath),
            ],
            env={**dict(__import__("os").environ), **env},
            capture_output=True,
            text=True,
            timeout=300,
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
    candidates = [
        *BACKUP_DIR.glob("slowbooks_*.sql"),
        *BACKUP_DIR.glob("slowbooks_*.db"),
    ]
    for f in sorted(candidates, key=lambda p: p.name, reverse=True):
        files.append(
            {
                "filename": f.name,
                "file_size": f.stat().st_size,
                "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            }
        )
    return files
