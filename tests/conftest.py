"""Shared pytest fixtures.

Uses an in-memory SQLite database per test. The app schema (SQLAlchemy models)
is created directly with Base.metadata.create_all rather than running Alembic —
this is intentional: tests don't exercise the migration path, they exercise the
ORM/route code against the current model definitions.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.database import Base
from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS


# Import all model modules so Base.metadata sees every table before create_all.
# Without these imports, tables defined in unimported modules wouldn't be created.
from app.models import (  # noqa: F401
    accounts,
    attachments,
    audit,
    backups,
    banking,
    bank_rules,
    bills,
    budgets,
    companies,
    contacts,
    credit_memos,
    email_log,
    email_templates,
    estimates,
    invoices,
    items,
    payments,
    payroll,
    purchase_orders,
    qbo_mapping,
    recurring,
    settings as settings_model,
    tax,
    transactions,
)


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def TestSession(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture
def db_session(TestSession):
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seed_accounts(db_session):
    """Seed the default chart of accounts. Returns a name->Account map for convenience."""
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
    from app.models.contacts import Customer
    c = Customer(name="Test Customer", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def client(db_engine, TestSession):
    """TestClient with get_db overridden to use the test session.

    Important: we import app.main here (after the engine fixture has run) so that
    register_audit_hooks etc don't fire against the production SessionLocal.
    """
    # Point the module-level SessionLocal at the test engine before importing app
    db_module.engine = db_engine
    db_module.SessionLocal = TestSession

    from app.main import app
    from app.database import get_db

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
