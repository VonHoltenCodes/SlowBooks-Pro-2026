# ============================================================================
# Multi-Company Service — create/switch company databases
# Feature 16: Most invasive change — routes to correct database
# ============================================================================

import re
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.companies import Company
from scripts.bootstrap_database import run_bootstrap as run_database_bootstrap

DATABASE_NAME_PATTERN = re.compile(r"[a-z0-9_]{1,63}\Z")
DATABASE_NAME_ERROR = "Invalid database name. Use 1-63 lowercase letters, numbers, or underscores."


def _validate_database_name(database_name: str) -> str:
    """Validate company database names before using them in URLs or SQL identifiers."""
    if not isinstance(database_name, str):
        raise ValueError(DATABASE_NAME_ERROR)

    candidate = database_name.strip()
    if candidate != database_name or not DATABASE_NAME_PATTERN.fullmatch(candidate):
        raise ValueError(DATABASE_NAME_ERROR)
    return candidate


def _quoted_database_name(database_name: str) -> str:
    """Return a validated PostgreSQL identifier for database DDL statements."""
    validated_name = _validate_database_name(database_name)
    return f'"{validated_name}"'


def _database_url(database_name: str) -> str:
    """Build a database URL preserving auth/host/query settings."""
    validated_name = _validate_database_name(database_name)
    parsed = urlparse(DATABASE_URL)
    return urlunparse(parsed._replace(path=f"/{validated_name}"))


def _drop_database(database_name: str) -> None:
    validated_name = _validate_database_name(database_name)
    quoted_database_name = f'"{validated_name}"'
    system_engine = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with system_engine.connect() as conn:
            conn.execute(text(f"REVOKE CONNECT ON DATABASE {quoted_database_name} FROM PUBLIC"))
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": validated_name},
            )
            conn.execute(text(f"DROP DATABASE {quoted_database_name}"))
    finally:
        system_engine.dispose()


def list_companies(db: Session) -> list[dict]:
    companies = db.query(Company).filter(Company.is_active == True).order_by(Company.name).all()
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


def create_company(db: Session, name: str, database_name: str, description: str = None) -> dict:
    """Create a new company database."""
    try:
        validated_database_name = _validate_database_name(database_name)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    existing = db.query(Company).filter(Company.database_name == validated_database_name).first()
    if existing:
        return {"success": False, "error": f"Database '{validated_database_name}' already exists"}

    created_database = False
    system_engine = None
    try:
        system_engine = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
        with system_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {_quoted_database_name(validated_database_name)}"))
        created_database = True

        run_database_bootstrap(_database_url(validated_database_name))

        company = Company(name=name, database_name=validated_database_name, description=description)
        db.add(company)
        db.commit()

        return {"success": True, "company_id": company.id, "database_name": company.database_name}

    except Exception as e:
        db.rollback()
        if created_database:
            try:
                _drop_database(validated_database_name)
            except Exception:
                pass
        return {"success": False, "error": str(e)}
    finally:
        if system_engine is not None:
            system_engine.dispose()


def get_company_db_url(database_name: str) -> str:
    """Get the full database URL for a company."""
    return _database_url(database_name)
