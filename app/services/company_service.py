# ============================================================================
# Multi-Company Service — create/switch company databases
# Feature 16: Most invasive change — routes to correct database
# ============================================================================

import subprocess
from datetime import datetime
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.companies import Company


def _database_url(database_name: str) -> str:
    """Build a database URL preserving auth/host/query settings."""
    parsed = urlparse(DATABASE_URL)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def list_companies(db: Session) -> list[dict]:
    companies = db.query(Company).filter(Company.is_active == True).order_by(Company.name).all()
    return [
        {"id": c.id, "name": c.name, "database_name": c.database_name,
         "description": c.description, "last_accessed": c.last_accessed.isoformat() if c.last_accessed else None}
        for c in companies
    ]


def create_company(db: Session, name: str, database_name: str, description: str = None) -> dict:
    """Create a new company database."""
    # Check if company already exists
    existing = db.query(Company).filter(Company.database_name == database_name).first()
    if existing:
        return {"success": False, "error": f"Database '{database_name}' already exists"}

    # Create the database
    try:
        # Connect to postgres system database to create new DB
        system_engine = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT")
        with system_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database_name}"'))
        system_engine.dispose()

        # Run Alembic migrations on new database
        new_engine = create_engine(_database_url(database_name))
        from app.database import Base
        Base.metadata.create_all(new_engine)
        new_engine.dispose()

        # Register in master DB
        company = Company(name=name, database_name=database_name, description=description)
        db.add(company)
        db.commit()

        return {"success": True, "company_id": company.id, "database_name": database_name}

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_company_db_url(database_name: str) -> str:
    """Get the full database URL for a company."""
    return _database_url(database_name)
