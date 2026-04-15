# ============================================================================
# Slowbooks Pro 2026 — pytest configuration
#
# Forces a temp-file SQLite DB (so every thread/connection shares state) and
# a deterministic session secret BEFORE the app is imported. Rate limiting is
# disabled by default so per-test counters don't collide; test_rate_limit.py
# tests the limiter wiring without actually exhausting buckets.
# ============================================================================

import os
import sys
import tempfile
from pathlib import Path

# ---- Environment overrides (must run BEFORE any app imports) ----
# Use a tempfile-backed SQLite DB so every connection and thread sees the
# same schema. `:memory:` gives each connection its own empty DB under
# SingletonThreadPool, which breaks the TestClient.
_tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["SESSION_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ALLOWED_ORIGINS"] = "http://testserver,http://localhost:3001"
os.environ["RATE_LIMIT_ENABLED"] = "0"

# Make the repo root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402

# Import every model module so Base.metadata knows about them
from app.models import accounts as _m_accounts  # noqa: F401,E402
from app.models import audit as _m_audit  # noqa: F401,E402
from app.models import backups as _m_backups  # noqa: F401,E402
from app.models import banking as _m_banking  # noqa: F401,E402
from app.models import bills as _m_bills  # noqa: F401,E402
from app.models import companies as _m_companies  # noqa: F401,E402
from app.models import contacts as _m_contacts  # noqa: F401,E402
from app.models import credit_memos as _m_credit_memos  # noqa: F401,E402
from app.models import email_log as _m_email_log  # noqa: F401,E402
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

# Create schema once at import time — before any fixture runs
Base.metadata.create_all(bind=engine)

from app.main import app  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    """Delete the temp DB file when the test session ends."""
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass


@pytest.fixture
def db():
    """Fresh session per test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _clean_auth_state():
    """Reset the operator password between tests so every test starts
    from 'setup needed'. Keeps auth tests independent."""
    from app.models.settings import Settings

    session = SessionLocal()
    try:
        session.query(Settings).filter(Settings.key.in_(["auth_password_hash"])).delete(
            synchronize_session=False
        )
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def client():
    """Unauthenticated TestClient."""
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """TestClient with setup complete + active session."""
    r = client.post("/api/auth/setup", json={"password": "test-password-123"})
    assert r.status_code == 200, r.text
    return client
