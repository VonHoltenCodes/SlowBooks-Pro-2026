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

from sqlalchemy import create_engine
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
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SlowBooksPro" / "data"
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


_warned_missing_manifest = False


def warn_if_manifest_missing() -> str | None:
    """Log (once) when the desktop manifest is absent.

    The launcher writes companies.json under data_dir(); a wrong
    SLOWBOOKS_DATA_DIR (a typo, a stale value from another install) makes
    GET /api/companies answer `[]` while every ledger endpoint keeps
    serving the file DATABASE_URL points at, so the symptom is "my
    companies vanished" with nothing in the log. Called at startup and on
    the company list. Returns the message it logged, for the caller."""
    global _warned_missing_manifest
    if not _is_sqlite():
        return None
    path = manifest_path()
    if path.exists():
        return None
    override = os.environ.get("SLOWBOOKS_DATA_DIR")
    message = (
        f"Company manifest not found at {path}"
        + (f" (SLOWBOOKS_DATA_DIR={override})" if override else "")
        + "; the company list will be empty until a company is created or "
        "the data directory points at the existing one"
    )
    if not _warned_missing_manifest:
        _warned_missing_manifest = True
        logger.warning(message)
    return message


def _read_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        warn_if_manifest_missing()
        return {"companies": [], "last_opened": None}
    try:
        # utf-8-sig: a manifest saved by Notepad or PowerShell carries a BOM
        data = json.loads(path.read_text(encoding="utf-8-sig"))
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


def sync_manifest_name(company_name: str | None) -> bool:
    """Make the manifest entry for the running company file carry the
    company_name the books display.

    A company has two names: the manifest's (what the picker and
    GET /api/companies show, and what a client's is_current safety check
    reads) and settings.company_name (what every screen and every printed
    document shows). They were independently writable, so first-run setup
    typed into a populated file renamed the books to another company while
    the manifest — and the harness guard reading it — still said the old
    name (2.9.0 gate, skytech). Settings is authoritative for display;
    this keeps the manifest equal to it. Returns True when it changed."""
    if not _is_sqlite():
        return False
    name = (company_name or "").strip()
    current = _current_company_file()
    if not name or not current:
        return False
    other = company_name_taken_by(name, exclude_file=current)
    if other:
        # Never emit a duplicate: two files with one name make is_current —
        # the only signal a client has for which company it reached —
        # ambiguous, and a harness guard matching on the name passed
        # against the wrong file (2.9.0 gate, round 4). Creation already
        # refuses the collision; reconciliation must not sneak past it.
        logger.warning(
            "Not renaming manifest entry %s to %r: %s already uses that name",
            current,
            name,
            other,
        )
        return False
    manifest = _read_manifest()
    changed = False
    for entry in manifest["companies"]:
        if entry.get("file") == current and entry.get("name") != name:
            entry["name"] = name
            changed = True
    if changed:
        _write_manifest(manifest)
    return changed


def current_manifest_name() -> str | None:
    """The name the manifest (the company picker) gives the file this app
    is serving, or None. SQLite mode only."""
    if not _is_sqlite():
        return None
    current = _current_company_file()
    if not current:
        return None
    for entry in _read_manifest()["companies"]:
        if entry.get("file") == current:
            return (entry.get("name") or "").strip() or None
    return None


def company_name_taken_by(name: str, exclude_file: str | None = None) -> str | None:
    """The manifest file (other than ``exclude_file``) that already carries
    ``name`` — by the same rule creation uses (the derived filename) or a
    case-insensitive name match — or None. SQLite mode only."""
    if not _is_sqlite():
        return None
    name = (name or "").strip()
    if not name:
        return None
    wanted_file = company_filename_for(name)
    for entry in _read_manifest()["companies"]:
        file = entry.get("file") or ""
        if exclude_file and file == exclude_file:
            continue
        if (entry.get("name") or "").strip().lower() == name.lower():
            return file
        if wanted_file and file == wanted_file:
            return file
    return None


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


def _init_company_db(url: str, company_name: str | None = None) -> None:
    """Bring a brand-new company database to the current schema and seed it.

    Runs `alembic upgrade head` (so the file is version-stamped and future
    upgrades apply cleanly — deliberately NOT Base.metadata.create_all),
    then seeds the Chart of Accounts, matching what the Docker entrypoint
    does on first run.

    ``company_name`` — the name typed in the New Company dialog — is written
    into the new books, so first-run setup opens with it filled in rather
    than blank (explore 2.17.3, macbase1 F3), and the sign-in screen and
    status bar can name the company before anyone has signed in.
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
    from app.models.settings import Settings
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            name = (company_name or "").strip()
            if (
                name
                and session.query(Settings)
                .filter(Settings.key == "company_name")
                .first()
                is None
            ):
                session.add(Settings(key="company_name", value=name))
                session.commit()
            if session.query(Account).count() == 0:
                for entry in CHART_OF_ACCOUNTS:
                    session.add(
                        Account(
                            name=entry["name"],
                            account_number=entry["account_number"],
                            account_type=AccountType(entry["account_type"]),
                            bank_kind=entry.get("bank_kind"),
                            is_system=True,
                        )
                    )
                session.flush()
                from app.seed.fixed_assets import ensure_default_asset_type

                ensure_default_asset_type(session)
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
        _init_company_db(url, company_name=name)
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
        # Reconcile before answering: a file staged by hand (or renamed by
        # an older build) can carry a manifest name the books no longer use.
        try:
            from app.services.settings_service import get_setting_raw

            sync_manifest_name(get_setting_raw(db, "company_name"))
        except Exception:
            logger.exception("Could not reconcile the manifest name")
        return manifest_list_companies()

    from app.models.companies import Company

    # Postgres: the server serves exactly one database — the one DATABASE_URL
    # names — and the companies table describes the OTHERS it can create.
    # A client still needs is_current to know which books it reached (the
    # fixture's write guard refuses without it, and a Docker install has no
    # Company row for its own database), so the served database is always
    # listed, flagged, and named from settings.company_name.
    current_db = _current_database_name()
    rows = []
    found_current = False
    for c in db.query(Company).filter(Company.is_active).order_by(Company.name).all():
        is_current = bool(current_db) and c.database_name == current_db
        found_current = found_current or is_current
        rows.append(
            {
                "id": c.id,
                "name": c.name,
                "database_name": c.database_name,
                "description": c.description,
                "last_accessed": (
                    c.last_accessed.isoformat() if c.last_accessed else None
                ),
                "is_current": is_current,
            }
        )
    if current_db and not found_current:
        from app.services.settings_service import get_setting_raw

        rows.insert(
            0,
            {
                "id": None,
                "name": get_setting_raw(db, "company_name") or current_db,
                "database_name": current_db,
                "description": "the database this server is connected to",
                "last_accessed": None,
                "is_current": True,
            },
        )
    return rows


def _current_database_name() -> str | None:
    """The database name in a Postgres DATABASE_URL (query string dropped)."""
    if _is_sqlite() or "/" not in DATABASE_URL:
        return None
    tail = DATABASE_URL.rsplit("/", 1)[1]
    return tail.split("?", 1)[0] or None


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
            # database_name is validated above against strict alphanumeric pattern;
            # dialect-level quoting is defence-in-depth for the DDL statement,
            # which cannot use bound parameters.
            quoted_db_name = conn.dialect.identifier_preparer.quote(database_name)
            conn.exec_driver_sql(f"CREATE DATABASE {quoted_db_name}")
        system_engine.dispose()

        # Bring the new database to the current schema and seed it
        # (alembic upgrade head + Chart of Accounts), matching what the
        # SQLite path (manifest_create_company) and the Docker entrypoint
        # both do. A create_all-only database would boot with zero accounts
        # and no alembic version stamp, so future upgrades would not apply
        # cleanly.
        _init_company_db(base_url + database_name, company_name=name)

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
