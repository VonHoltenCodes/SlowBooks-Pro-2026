"""A value longer than its column is cut to the column's length on import,
and the import says what it shortened, instead of failing.

SQLite does not enforce String(n) lengths, so an over-long value imported
fine in every test; PostgreSQL refuses the row (StringDataRightTruncation),
and a real QuickBooks Online import answered "nothing was saved": a journal
line's memo (transaction_lines.description, String(300)) ran past 300
characters. Every importer writes under column_fit.fit_to_columns, and the
dry runs say the same sentences the import will.

The books here are the invented ones in tests/fixtures/qbo_export/."""

import csv
import io
from pathlib import Path

import pytest

from app.models.contacts import Customer
from app.models.transactions import Transaction, TransactionLine
from app.services import qbo_export_import as qbo
from app.services.column_fit import fit_notes, fit_to_columns

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qbo_export"
BUNDLE = [
    "Balance_sheet.csv",
    "Profit_and_loss.csv",
    "Journal.csv",
    "General_ledger.csv",
    "Trial_balance.csv",
    "Customers.csv",
    "Vendors.csv",
    "Employees.csv",
]
LINE_MEMO_MAX = TransactionLine.__table__.c.description.type.length  # 300


def _journal_with_memo(memo: str) -> str:
    """The fixture Journal with the invoice's income line memo replaced."""
    text = (FIXTURES / "Journal.csv").read_text(encoding="utf-8")
    old = ",Listing commission,Commission Income"
    assert old in text
    return text.replace(old, f",{memo},Commission Income", 1)


def _invoice_line(journal_text: str) -> dict:
    journals, errors = qbo.parse_gl(journal_text)
    assert errors == []
    for group in journals:
        for row in group:
            if row["account"] == "Commission Income" and "Invoice 1001" in (
                row["journal"] or ""
            ):
                return row
    raise AssertionError("the invoice's income line is not in the journal")


def _workbook(csv_text: str) -> bytes:
    """The sheet as the .xlsx QuickBooks Online writes (figures stay
    formula text)."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for row in csv.reader(io.StringIO(csv_text)):
        sheet.append([cell if cell != "" else None for cell in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _files(journal_text: str):
    files = []
    for name in BUNDLE:
        text = (
            journal_text
            if name == "Journal.csv"
            else (FIXTURES / name).read_text(encoding="utf-8")
        )
        files.append(
            (
                "files",
                (name.replace(".csv", ".xlsx"), io.BytesIO(_workbook(text)), XLSX),
            )
        )
    return files


def _cut_notes(warnings: list[str]) -> list[str]:
    return [w for w in warnings if " was cut to " in w or " were cut to " in w]


# ── QuickBooks Online export ───────────────────────────────────────────────


def test_a_journal_line_memo_over_its_column_is_cut_and_said(
    client, db_session, seed_accounts
):
    journal = _journal_with_memo("Listing commission " + "x" * 381)
    row = _invoice_line(journal)
    assert len(row["description"]) > LINE_MEMO_MAX

    dry = client.post("/api/migration/qbo_export/dry-run", files=_files(journal))
    dry_data = dry.json()
    assert dry.status_code == 200, dry_data
    assert dry_data["ok"] is True, dry_data["errors"]
    dry_cuts = _cut_notes(dry_data["warnings"])
    assert len(dry_cuts) == 1, dry_data["warnings"]
    sentence = dry_cuts[0]
    assert sentence.startswith("Journal entry 'Invoice 1001")
    assert "of 2026-01-10" in sentence
    assert sentence.endswith(
        f"— the line memo was cut to {LINE_MEMO_MAX} characters"
    ), sentence
    # nothing written by the dry run
    assert db_session.query(Transaction).count() == 0

    resp = client.post("/api/migration/qbo_export/import", files=_files(journal))
    data = resp.json()
    assert resp.status_code == 200, data
    assert data["ok"] is True, data["errors"]
    assert data["imported_journals"] == 19
    assert data["imported_customers"] == 2
    # the dry run's sentence is the import's, said once
    assert _cut_notes(data["warnings"]) == [sentence]

    stored = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.description.like("Invoice 1001%xxx%"))
        .one()
    )
    assert stored.description == row["description"][:LINE_MEMO_MAX]
    assert len(stored.description) == LINE_MEMO_MAX


def test_a_memo_exactly_at_the_limit_is_left_whole(client, db_session, seed_accounts):
    base = _invoice_line(_journal_with_memo("M"))["description"]
    # the parsed description is "<journal> — <name>: <memo>"; fill the memo
    # so the line is exactly the column's length
    memo = "M" * (LINE_MEMO_MAX - len(base) + 1)
    journal = _journal_with_memo(memo)
    row = _invoice_line(journal)
    assert len(row["description"]) == LINE_MEMO_MAX

    dry = client.post("/api/migration/qbo_export/dry-run", files=_files(journal))
    assert _cut_notes(dry.json()["warnings"]) == []
    data = client.post("/api/migration/qbo_export/import", files=_files(journal)).json()
    assert data["ok"] is True, data["errors"]
    assert _cut_notes(data["warnings"]) == []
    stored = (
        db_session.query(TransactionLine)
        .filter(TransactionLine.description == row["description"])
        .one()
    )
    assert len(stored.description) == LINE_MEMO_MAX


# ── IIF ────────────────────────────────────────────────────────────────────


def _iif(company: str) -> str:
    return (
        "!CUST\tNAME\tCOMPANYNAME\tEMAIL\r\n"
        f"CUST\tHarbor Lane Bakery\t{company}\torders@example.com\r\n"
        "CUST\tPine Ridge Tutoring\tPine Ridge\tdesk@example.com\r\n"
    )


def _upload(path: str, client, text: str):
    return client.post(
        path,
        files={"file": ("lists.iif", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )


def test_an_iif_list_value_over_its_column_is_cut_and_said(
    client, db_session, seed_accounts
):
    limit = Customer.__table__.c.company.type.length  # 200
    text = _iif("C" * 400)
    expected = (
        f"Customer 'Harbor Lane Bakery' — the company was cut to {limit} characters"
    )

    report = _upload("/api/iif/validate", client, text).json()
    assert report["valid"] is True, report["errors"]
    assert _cut_notes(report["warnings"]) == [expected]

    resp = _upload("/api/iif/import", client, text)
    data = resp.json()
    assert resp.status_code == 200, data
    assert data["errors"] == []
    assert data["customers"] == 2
    assert _cut_notes(data["warnings"]) == [expected]
    bakery = (
        db_session.query(Customer).filter(Customer.name == "Harbor Lane Bakery").one()
    )
    assert bakery.company == "C" * limit


def test_an_iif_value_exactly_at_the_limit_is_untouched(
    client, db_session, seed_accounts
):
    limit = Customer.__table__.c.company.type.length
    text = _iif("C" * limit)
    assert (
        _cut_notes(_upload("/api/iif/validate", client, text).json()["warnings"]) == []
    )
    data = _upload("/api/iif/import", client, text).json()
    assert _cut_notes(data["warnings"]) == []
    bakery = (
        db_session.query(Customer).filter(Customer.name == "Harbor Lane Bakery").one()
    )
    assert bakery.company == "C" * limit


# ── the guard itself ───────────────────────────────────────────────────────


def test_only_text_over_its_length_is_cut():
    from decimal import Decimal

    customer = Customer(
        name="Harbor Lane Bakery",
        company="C" * 200,
        bill_address1="A" * 250,
        credit_limit=Decimal("12345.67"),
        is_taxable=False,
    )
    assert fit_notes(customer) == [
        "Customer 'Harbor Lane Bakery' — the billing address line 1 was cut to "
        "200 characters"
    ]
    assert customer.company == "C" * 200
    assert customer.bill_address1 == "A" * 200
    assert customer.credit_limit == Decimal("12345.67")
    assert customer.is_taxable is False


def test_the_guard_cuts_at_flush_and_says_so_once(db_session):
    said: list[str] = []
    with fit_to_columns(db_session, said):
        db_session.add(Customer(name="Pine Ridge Tutoring", email="e" * 250))
        db_session.flush()
        db_session.flush()
    assert said == [
        "Customer 'Pine Ridge Tutoring' — the email was cut to 200 characters"
    ]
    # outside the block nothing is watched
    db_session.add(Customer(name="Harbor Lane Bakery", email="ok@example.com"))
    db_session.flush()
    assert len(said) == 1


# ── on PostgreSQL, where the length is enforced ────────────────────────────


@pytest.fixture
def postgres_url():
    """A scratch PostgreSQL database for one test (test_atomic_account_balance's
    convention): SLOWBOOKS_TEST_POSTGRES_URL is an admin URL; skipped without."""
    import os
    import uuid

    import sqlalchemy as sa

    admin_url = os.environ.get("SLOWBOOKS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("SLOWBOOKS_TEST_POSTGRES_URL is not set")
    pytest.importorskip("psycopg2")
    name = "slowbooks_colfit_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on the machine
        admin.dispose()
        pytest.skip(f"cannot create a scratch PostgreSQL database: {exc}")
    url = sa.engine.make_url(admin_url).set(database=name)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_qbo_import_succeeds_on_postgres(postgres_url):
    from decimal import Decimal

    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models.accounts import Account, AccountType
    from app.seed.chart_of_accounts import CHART_OF_ACCOUNTS

    engine = sa.create_engine(postgres_url)
    try:
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        for data in CHART_OF_ACCOUNTS:
            db.add(
                Account(
                    account_number=data["account_number"],
                    name=data["name"],
                    account_type=AccountType(data["account_type"]),
                    bank_kind=data.get("bank_kind"),
                    detail_type=data.get("detail_type"),
                    is_system=True,
                    balance=Decimal("0"),
                )
            )
        db.commit()
        journal = _journal_with_memo("Listing commission " + "x" * 381)
        row = _invoice_line(journal)
        read = lambda name: (FIXTURES / name).read_text(encoding="utf-8")  # noqa
        bundle = {
            "coa": read("Balance_sheet.csv") + read("Profit_and_loss.csv"),
            "gl": journal,
            "gl_order": read("General_ledger.csv"),
            "tb": read("Trial_balance.csv"),
            "customers": read("Customers.csv"),
            "vendors": read("Vendors.csv"),
        }
        dry = qbo.dry_run(db, bundle)
        assert dry["ok"] is True, dry["errors"]
        result = qbo.run_import(db, bundle)
        assert result["ok"] is True, result["errors"]
        assert _cut_notes(result["warnings"]) == _cut_notes(dry["warnings"])
        assert len(_cut_notes(result["warnings"])) == 1
        stored = (
            db.query(TransactionLine)
            .filter(TransactionLine.description.like("Invoice 1001%xxx%"))
            .one()
        )
        assert stored.description == row["description"][:LINE_MEMO_MAX]
        db.close()
    finally:
        engine.dispose()
