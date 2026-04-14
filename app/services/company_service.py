# ============================================================================
# Multi-Company Service — create/switch company databases
# Feature 16: Most invasive change — routes to correct database
# ============================================================================

from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.companies import Company
from scripts.bootstrap_database import run_bootstrap as run_database_bootstrap


def _database_url(database_name: str) -> str:
    """Build a database URL preserving auth/host/query settings."""
    parsed = urlparse(DATABASE_URL)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _drop_database(database_name: str) -> None:
    system_engine = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with system_engine.connect() as conn:
            conn.execute(text(f'REVOKE CONNECT ON DATABASE "{database_name}" FROM PUBLIC'))
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            conn.execute(text(f'DROP DATABASE "{database_name}"'))
    finally:
        system_engine.dispose()


def list_companies(db: Session) -> list[dict]:
    companies = db.query(Company).filter(Company.is_active == True).order_by(Company.name).all()
    return [
        {"id": c.id, "name": c.name, "database_name": c.database_name,
         "description": c.description, "last_accessed": c.last_accessed.isoformat() if c.last_accessed else None}
        for c in companies
    ]


def create_company(db: Session, name: str, database_name: str, description: str = None) -> dict:
    """Create a new company database."""
    existing = db.query(Company).filter(Company.database_name == database_name).first()
    if existing:
        return {"success": False, "error": f"Database '{database_name}' already exists"}

    created_database = False
    try:
        system_engine = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
        with system_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        system_engine.dispose()
        created_database = True

        run_database_bootstrap(_database_url(database_name))

        company = Company(name=name, database_name=database_name, description=description)
        db.add(company)
        db.commit()

        return {"success": True, "company_id": company.id, "database_name": company.database_name}

    except Exception as e:
        db.rollback()
        if created_database:
            try:
                _drop_database(database_name)
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def get_company_db_url(database_name: str) -> str:
    """Get the full database URL for a company."""
    return _database_url(database_name)
