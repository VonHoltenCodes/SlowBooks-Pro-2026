"""W-148: the CSV invoice import's dry run posted on the installed app.

Each invoice is made in a savepoint and the lot rolled back at the end. On
SQLite the pysqlite driver emits no BEGIN before a SAVEPOINT, so with
nothing written yet the savepoint opened its own transaction and its
RELEASE committed the invoice. The suite's own session hid it: the test
engine emits BEGIN itself and runs every test inside an outer transaction.

These tests use what the installed app uses: a company file on disk,
brought to the head by `alembic upgrade head` and seeded the way a new
company is (company_service._init_company_db), opened through the app's
engine factory (company_file.create_sqlite_engine + enable_sqlite_tuning)
with a plain session per request, as app.database.get_db hands out."""

import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

import app.database as db_module
from app.database import enable_sqlite_tuning, get_db
from app.main import app
from app.models.accounts import Account
from app.models.invoices import Invoice
from app.models.transactions import Transaction, TransactionLine
from app.services import company_file
from app.services.audit import register_audit_hooks
from app.services.company_service import _init_company_db

HEADER = (
    "invoice_number,date,due_date,customer,item,description,quantity,rate,"
    "taxable,memo,class,job\n"
)
TWO = HEADER + (
    "F-1,2026-10-05,,Harbor Bakery,,Lobby sign,2,50.00,no,,,\n"
    "F-2,2026-10-06,,Pine Street Clinic,,Window lettering,1,25.00,no,,,\n"
)
UNKNOWN_CUSTOMER = HEADER + (
    "M-1,2026-10-05,,Harbor Bakery,,Lobby sign,1,70.00,no,,,\n"
    "M-2,2026-10-06,,Nobody By This Name,,Window lettering,1,25.00,no,,,\n"
)
UNKNOWN_ITEM = HEADER + (
    "M-3,2026-10-05,,Harbor Bakery,,Lobby sign,1,40.00,no,,,\n"
    "M-4,2026-10-06,,Pine Street Clinic,NO-SUCH-ITEM,,1,25.00,no,,,\n"
)


@pytest.fixture
def file_app(tmp_path, monkeypatch):
    """The app served from a company file on disk, as installed."""
    path = tmp_path / "company.db"
    url = "sqlite:///" + path.as_posix()
    _init_company_db(url, company_name="File Books")
    engine = company_file.create_sqlite_engine(url, None, pool_pre_ping=True)
    enable_sqlite_tuning(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    register_audit_hooks(factory)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", factory)

    def file_get_db(request=None):
        session = factory()
        http_session = getattr(request, "session", None) if request else None
        if isinstance(http_session, dict) and http_session.get("authenticated"):
            session.info["acting_username"] = http_session.get("username") or "operator"
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = file_get_db
    try:
        c = TestClient(app)
        r = c.post("/api/auth/setup", json={"password": "test-password-123"})
        assert r.status_code == 200, r.text
        for name in ("Harbor Bakery", "Pine Street Clinic"):
            r = c.post("/api/customers", json={"name": name})
            assert r.status_code in (200, 201), r.text
        yield c, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _upload(client, text, dry_run):
    return client.post(
        f"/api/csv/import/invoices?dry_run={'true' if dry_run else 'false'}",
        files={"file": ("invoices.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


def _books(engine):
    """What is on disk, read on a connection of its own: the invoices by
    number, the transactions, and A/R's debits less credits."""
    with Session(engine) as s:
        numbers = sorted(n for (n,) in s.query(Invoice.invoice_number))
        txns = s.query(func.count(Transaction.id)).scalar()
        ar = s.query(Account).filter(Account.account_number == "1100").one()
        rows = (
            s.query(TransactionLine).filter(TransactionLine.account_id == ar.id).all()
        )
        net = sum(
            (Decimal(str(r.debit or 0)) - Decimal(str(r.credit or 0)) for r in rows),
            Decimal("0"),
        )
    return numbers, txns, net


def test_a_dry_run_of_two_invoices_writes_nothing(file_app):
    client, engine = file_app
    before = _books(engine)
    for _ in range(2):  # twice: the second must not find them in the books
        r = _upload(client, TWO, dry_run=True)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["errors"] == [], out
        assert out["dry_run"] is True and out["posted"] is False
        assert out["invoices"] == 2 and out["total"] == "125.00"
    assert _books(engine) == before
    assert before[0] == [] and before[2] == Decimal("0")
    assert client.get("/api/invoices").json() == []


def test_the_dry_run_is_the_default_and_writes_nothing(file_app):
    client, engine = file_app
    r = client.post(
        "/api/csv/import/invoices",
        files={"file": ("invoices.csv", io.BytesIO(TWO.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["posted"] is False
    assert _books(engine)[0] == []


@pytest.mark.parametrize(
    "text", [UNKNOWN_CUSTOMER, UNKNOWN_ITEM], ids=["unknown-customer", "unknown-item"]
)
def test_a_dry_run_with_a_refused_row_leaves_the_good_row_unposted(file_app, text):
    client, engine = file_app
    r = _upload(client, text, dry_run=True)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["errors"] and out["posted"] is False
    # the good row is still counted, as what would post
    assert len(out["documents"]) == 1
    numbers, _, ar = _books(engine)
    assert numbers == [] and ar == Decimal("0")


@pytest.mark.parametrize(
    "text", [UNKNOWN_CUSTOMER, UNKNOWN_ITEM], ids=["unknown-customer", "unknown-item"]
)
def test_a_real_post_with_one_bad_row_posts_nothing(file_app, text):
    client, engine = file_app
    r = _upload(client, text, dry_run=False)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["errors"] and out["posted"] is False
    numbers, txns, ar = _books(engine)
    assert numbers == [] and txns == 0 and ar == Decimal("0")


def test_a_good_file_posts_and_a_second_post_is_refused_as_duplicates(file_app):
    client, engine = file_app
    r = _upload(client, TWO, dry_run=True)
    assert r.json()["errors"] == []
    r = _upload(client, TWO, dry_run=False)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["errors"] == [], out
    assert out["posted"] is True
    posted = _books(engine)
    assert posted[0] == ["F-1", "F-2"] and posted[2] == Decimal("125.00")

    again = _upload(client, TWO, dry_run=False).json()
    assert again["posted"] is False
    assert any("F-1 is already in the books" in e for e in again["errors"])
    assert any("F-2 is already in the books" in e for e in again["errors"])
    assert _books(engine) == posted
