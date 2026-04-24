# ============================================================================
# Slowbooks Pro 2026 — pytest configuration
#
# Each test gets a fresh in-memory SQLite database via the db_engine fixture.
# The `client` fixture wires the app's get_db dependency to that same engine
# so API calls and direct db_session queries hit the same tables.
# Rate limiting is disabled by default so per-test counters don't collide.
# ============================================================================

import os
import sys
from decimal import Decimal
from pathlib import Path

# ---- Environment overrides (must run BEFORE any app imports) ----
os.environ["SESSION_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ALLOWED_ORIGINS"] = "http://testserver,http://localhost:3001"
os.environ["CORS_ALLOW_ORIGINS"] = "http://testserver,http://localhost:3001"
os.environ["RATE_LIMIT_ENABLED"] = "0"
# Point the app at an in-memory DB by default; fixtures override per-test.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.database as db_module  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS  # noqa: E402

# Import all model modules so Base.metadata knows about every table
from app.models import accounts as _m_accounts  # noqa: F401,E402
from app.models import attachments as _m_attachments  # noqa: F401,E402
from app.models import audit as _m_audit  # noqa: F401,E402
from app.models import backups as _m_backups  # noqa: F401,E402
from app.models import bank_rules as _m_bank_rules  # noqa: F401,E402
from app.models import banking as _m_banking  # noqa: F401,E402
from app.models import bills as _m_bills  # noqa: F401,E402
from app.models import budgets as _m_budgets  # noqa: F401,E402
from app.models import companies as _m_companies  # noqa: F401,E402
from app.models import contacts as _m_contacts  # noqa: F401,E402
from app.models import credit_memos as _m_credit_memos  # noqa: F401,E402
from app.models import email_log as _m_email_log  # noqa: F401,E402
from app.models import email_templates as _m_email_templates  # noqa: F401,E402
from app.models import estimates as _m_estimates  # noqa: F401,E402
from app.models import invoices as _m_invoices  # noqa: F401,E402
from app.models import items as _m_items  # noqa: F401,E402
from app.models import payments as _m_payments  # noqa: F401,E402
from app.models import payroll as _m_payroll  # noqa: F401,E402
from app.models import purchase_orders as _m_purchase_orders  # noqa: F401,E402
from app.models import qbo_mapping as _m_qbo_mapping  # noqa: F401,E402
from app.models import recurring as _m_recurring  # noqa: F401,E402
from app.models import settings as _m_settings  # noqa: F401,E402
from app.models import tax as _m_tax  # noqa: F401,E402
from app.models import transactions as _m_transactions  # noqa: F401,E402

from app.main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Per-test in-memory engine — every test gets a clean slate
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine():
    """Per-test in-memory SQLite engine with full schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    # Point the app module at this engine so SessionLocal-based code (audit
    # hooks, etc.) also land in the same DB.
    db_module.engine = engine
    db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def TestSession(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture
def db_session(TestSession):
    """Isolated session backed by the per-test in-memory engine."""
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seed_accounts(db_session):
    """Seed the standard chart of accounts; returns dict keyed by account_number."""
    from app.models.accounts import Account, AccountType

    accounts_by_number = {}
    for data in CHART_OF_ACCOUNTS:
        acct = Account(
            account_number=data["account_number"],
            name=data["name"],
            account_type=AccountType(data["account_type"]),
            is_system=True,
            balance=Decimal("0"),
        )
        db_session.add(acct)
        accounts_by_number[data["account_number"]] = acct
    db_session.commit()
    return accounts_by_number


@pytest.fixture
def seed_customer(db_session):
    """Seed a single active Customer and return it."""
    from app.models.contacts import Customer

    customer = Customer(name="Test Customer", is_active=True)
    db_session.add(customer)
    db_session.commit()
    return customer


# ---------------------------------------------------------------------------
# Client fixtures — both wire get_db to the per-test in-memory engine
# ---------------------------------------------------------------------------


def _wire_app(TestSession):
    """Override app's get_db dependency to use the per-test session factory."""

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
def unauthed_client(db_engine, TestSession):
    """Unauthenticated TestClient backed by the per-test in-memory DB.

    Use this fixture in tests that exercise the auth flow itself (setup, login,
    logout) where you need to start from an unauthenticated state.
    """
    _wire_app(TestSession)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client(db_engine, TestSession):
    """Authenticated TestClient backed by the per-test in-memory DB.

    Auth setup is performed during fixture setup so every API call
    made through this client is already authenticated.
    """
    _wire_app(TestSession)
    with TestClient(app) as c:
        r = c.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, f"Auth setup failed: {r.text}"
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(client):
    """Alias for client (already authenticated). Kept for backwards compatibility."""
    return client


@pytest.fixture
def db():
    """Legacy fixture: fresh session against the module-level engine.

    Tests that import this fixture directly (not via client) get a session
    backed by whatever engine db_module.SessionLocal points at.
    """
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
