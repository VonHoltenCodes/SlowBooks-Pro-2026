# ============================================================================
# Multi-Company Service — create/switch company databases
# Feature 16: Most invasive change — routes to correct database
#
# Two modes, selected by DATABASE_URL:
#
#   PostgreSQL (Docker / server installs): each company is a separate
#   database on the same Postgres server, registered in the master DB's
#   `companies` table. Unchanged behavior.
#
#   SQLite (native desktop installs): each company is a separate .db file
#   under <data dir>/companies/, tracked in a small JSON manifest
#   (<data dir>/companies.json) that also remembers the last company
#   opened. The manifest lives outside any single company's database on
#   purpose — the app must know which database to open *before* it can
#   open one. Switching companies happens by relaunching the desktop app
#   (the launcher shows a company picker before the server starts), the
#   same way QuickBooks Desktop switches company files.
# ============================================================================

import json
import logging
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

# Strict pattern for Postgres database names: alphanumeric, underscores,
# hyphens only.
_VALID_DB_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,62}$")

# Strict allow-list for company .db filenames. Same trust-boundary shape as
# backup_service._BACKUP_FILENAME_RE: safe character class, known extension,
# NO path separators, NO ".." — validated before any path is constructed.
_COMPANY_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\.db$")


def _is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


# ---------------------------------------------------------------------------
# Desktop (SQLite) mode — file-per-company + JSON manifest
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Root data directory for a desktop install.

    The desktop launcher sets SLOWBOOKS_DATA_DIR explicitly; the fallbacks
    match the launcher's own defaults so both always agree.
    """
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "SlowBooksPro" / "data"
    return Path.home() / ".slowbookspro" / "data"


def companies_dir() -> Path:
    return data_dir() / "companies"


def manifest_path() -> Path:
    return data_dir() / "companies.json"


def safe_company_filename(filename: str) -> str | None:
    """Return a safe basename for a company .db file, or None if invalid.

    Mirrors backup_service._safe_backup_filename(): os.path.basename() strips
    any directory component (a sanitizer static analyzers recognize), reject
    if that changed the value, then enforce the strict regex / length /
    leading-dot rules. Only after this may the value touch a filesystem path.
    """
    if not filename or len(filename) > 255:
        return None
    base = os.path.basename(filename)
    if base != filename:
        return None
    if base.startswith(".") or ".." in base:
        return None
    if not _COMPANY_FILENAME_RE.match(base):
        return None
    return base


def company_filename_for(name: str) -> str | None:
    """Derive a safe .db filename from a user-supplied company name.

    "Acme Consulting, LLC" → "acme-consulting-llc.db". Returns None when
    nothing safe remains (e.g. a name with no letters or digits).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not slug:
        return None
    return safe_company_filename(slug[:63].rstrip("-") + ".db")


def company_db_path(filename: str) -> Path | None:
    """Absolute path for a validated company filename, or None if invalid."""
    safe = safe_company_filename(filename)
    if safe is None:
        return None
    return companies_dir() / safe


def _read_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {"companies": [], "last_opened": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read company manifest %s", path)
        return {"companies": [], "last_opened": None}
    if not isinstance(data, dict):
        return {"companies": [], "last_opened": None}
    data.setdefault("companies", [])
    data.setdefault("last_opened", None)
    return data


def _write_manifest(data: dict) -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    manifest_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _current_company_file() -> str | None:
    """Basename of the SQLite file the running app is connected to, if any."""
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    raw = DATABASE_URL[len("sqlite:///") :]
    if not raw or raw == ":memory:":
        return None
    return os.path.basename(raw)


def manifest_list_companies() -> list[dict]:
    current = _current_company_file()
    return [
        {
            "name": c.get("name", ""),
            "file": c.get("file", ""),
            "is_current": bool(c.get("file")) and c.get("file") == current,
        }
        for c in _read_manifest()["companies"]
    ]


def get_last_opened() -> str | None:
    last = _read_manifest().get("last_opened")
    return safe_company_filename(last) if last else None


def set_last_opened(filename: str) -> None:
    safe = safe_company_filename(filename)
    if safe is None:
        return
    manifest = _read_manifest()
    manifest["last_opened"] = safe
    _write_manifest(manifest)


def _init_company_db(url: str) -> None:
    """Bring a brand-new company database to the current schema and seed it.

    Runs `alembic upgrade head` (so the file is version-stamped and future
    upgrades apply cleanly — deliberately NOT Base.metadata.create_all),
    then seeds the Chart of Accounts, matching what the Docker entrypoint
    does on first run.
    """
    from alembic import command
    from alembic.config import Config

    # migrations/env.py imports app.database, which creates its engine at
    # import time from DATABASE_URL. In the launcher process that var may
    # be unset, and the Postgres fallback would try to import psycopg2 —
    # which the frozen desktop bundle deliberately doesn't ship. Make sure
    # a first import binds to this SQLite URL instead. (Migrations
    # themselves use cfg.attributes below, which takes precedence.)
    os.environ.setdefault("DATABASE_URL", url)

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    # migrations/env.py gives this attribute precedence over the process's
    # DATABASE_URL, so we can migrate a database other than the active one.
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")

    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            if session.query(Account).count() == 0:
                for entry in CHART_OF_ACCOUNTS:
                    session.add(
                        Account(
                            name=entry["name"],
                            account_number=entry["account_number"],
                            account_type=AccountType(entry["account_type"]),
                            is_system=True,
                        )
                    )
                session.commit()
    finally:
        engine.dispose()


def manifest_create_company(name: str) -> dict:
    """Create a new company file: migrate it, seed it, register it."""
    name = (name or "").strip()
    if not name:
        return {"success": False, "error": "Company name is required"}

    filename = company_filename_for(name)
    if filename is None:
        return {
            "success": False,
            "error": "Company name must contain at least one letter or number",
        }

    manifest = _read_manifest()
    # company_filename_for() already sanitized, but re-check containment
    # with the normpath + startswith pattern (the guard CodeQL documents
    # as the py/path-injection barrier) before any filesystem use.
    companies_root = os.path.normpath(str(companies_dir()))
    candidate = os.path.normpath(os.path.join(companies_root, filename))
    if not candidate.startswith(companies_root + os.sep):
        return {"success": False, "error": "Invalid company file name"}
    db_path = Path(candidate)
    if any(c.get("file") == filename for c in manifest["companies"]) or (
        db_path.exists()
    ):
        return {
            "success": False,
            "error": f"A company file named '{filename}' already exists",
        }

    companies_dir().mkdir(parents=True, exist_ok=True)
    url = "sqlite:///" + db_path.as_posix()
    try:
        _init_company_db(url)
    except Exception:
        logger.exception("Failed to create company database %s", filename)
        db_path.unlink(missing_ok=True)
        return {
            "success": False,
            "error": "Failed to create company database. Check logs for details.",
        }

    manifest["companies"].append({"name": name, "file": filename})
    if not manifest.get("last_opened"):
        manifest["last_opened"] = filename
    _write_manifest(manifest)

    return {"success": True, "name": name, "file": filename}


# ---------------------------------------------------------------------------
# Route-facing API — branches on the active database dialect
# ---------------------------------------------------------------------------


def _base_url():
    """Get the base URL without database name."""
    # postgresql://user:pass@host:port/dbname → postgresql://user:pass@host:port/
    parts = DATABASE_URL.rsplit("/", 1)
    return parts[0] + "/"


def list_companies(db: Session) -> list[dict]:
    if _is_sqlite():
        return manifest_list_companies()

    from app.models.companies import Company

    companies = db.query(Company).filter(Company.is_active).order_by(Company.name).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "database_name": c.database_name,
            "description": c.description,
            "last_accessed": c.last_accessed.isoformat() if c.last_accessed else None,
        }
        for c in companies
    ]


def create_company(
    db: Session, name: str, database_name: str = None, description: str = None
) -> dict:
    """Create a new company database (Postgres DB or SQLite file per mode)."""
    if _is_sqlite():
        return manifest_create_company(name)

    if not database_name:
        return {"success": False, "error": "Database name is required"}

    # Validate database_name to prevent SQL injection
    if not _VALID_DB_NAME.match(database_name):
        return {
            "success": False,
            "error": "Invalid database name. Use only letters, numbers, underscores, and hyphens.",
        }

    from app.models.companies import Company

    # Check if company already exists
    existing = db.query(Company).filter(Company.database_name == database_name).first()
    if existing:
        return {"success": False, "error": f"Database '{database_name}' already exists"}

    # Create the database
    base_url = _base_url()
    try:
        # Connect to postgres system database to create new DB
        system_engine = create_engine(
            base_url + "postgres", isolation_level="AUTOCOMMIT"
        )
        with system_engine.connect() as conn:
            # database_name is validated above against strict alphanumeric pattern
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        system_engine.dispose()

        # Run Alembic migrations on new database
        new_engine = create_engine(base_url + database_name)
        from app.database import Base

        Base.metadata.create_all(new_engine)
        new_engine.dispose()

        # Register in master DB
        company = Company(
            name=name, database_name=database_name, description=description
        )
        db.add(company)
        db.commit()

        return {
            "success": True,
            "company_id": company.id,
            "database_name": database_name,
        }

    except Exception:
        logger.exception("Failed to create company database %s", database_name)
        return {
            "success": False,
            "error": "Failed to create company database. Check server logs for details.",
        }


def get_company_db_url(database_name: str) -> str:
    """Get the full database URL for a company."""
    return _base_url() + database_name
