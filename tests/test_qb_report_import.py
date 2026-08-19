"""QuickBooks report-CSV sales receipt import.

Fixture is an anonymized recreation of a real customer's "Transaction
Detail by Date" export (RV dealer): thousands separators, an
applied-deposit contra line that must reduce the total (a sign-blind
abs() parse would inflate it by $2,000), percentage tax rows carrying
the tax agency's name, a deposits-only receipt, and a receipt with
actual sales tax deposited straight to Checking.
"""

from decimal import Decimal
from pathlib import Path

FIXTURE = (
    Path(__file__).parent / "fixtures" / "qb_transaction_detail_sales_receipts.csv"
)


def _import(db_session):
    from app.services.qb_report_import import import_sales_receipt_report

    return import_sales_receipt_report(db_session, FIXTURE.read_text())


def test_report_import_creates_paid_receipts(db_session, seed_accounts):
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment

    result = _import(db_session)
    assert result["errors"] == [], result
    assert result["imported"] == 3

    receipts = db_session.query(Invoice).order_by(Invoice.id).all()
    assert len(receipts) == 3
    assert all(r.is_sales_receipt for r in receipts)
    assert all(r.status == InvoiceStatus.PAID for r in receipts)
    assert all(r.balance_due == 0 for r in receipts)
    assert db_session.query(Payment).count() == 3


def test_contra_deposit_line_reduces_the_total(db_session, seed_accounts):
    """The abs()-trap case: 23,995 + 250 + 595 + 425 − 1,000 = 24,265."""
    from app.models.invoices import Invoice

    _import(db_session)
    rec = db_session.query(Invoice).filter_by(invoice_number="1").first()
    assert rec is not None
    assert rec.total == Decimal("24265.00")
    assert rec.subtotal == Decimal("24265.00")
    assert rec.tax_amount == Decimal("0")
    contra = [ln for ln in rec.lines if ln.amount < 0]
    assert len(contra) == 1
    assert contra[0].amount == Decimal("-1000.00")

    # Journal balances with the contra line on the debit side
    from app.models.transactions import TransactionLine

    lines = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=rec.transaction_id)
        .all()
    )
    dr = sum((Decimal(str(line.debit)) for line in lines), Decimal("0"))
    cr = sum((Decimal(str(line.credit)) for line in lines), Decimal("0"))
    assert dr == cr == Decimal("24265.00") + Decimal("1000.00")


def test_taxed_receipt_and_deposit_account(db_session, seed_accounts):
    from app.models.invoices import Invoice
    from app.models.payments import Payment

    _import(db_session)
    rec = db_session.query(Invoice).filter_by(invoice_number="3").first()
    assert rec.subtotal == Decimal("100.00")
    assert rec.tax_amount == Decimal("6.40")
    assert rec.tax_rate == Decimal("0.064")
    assert rec.total == Decimal("106.40")
    assert rec.lines[0].quantity == Decimal("2")
    assert rec.lines[0].rate == Decimal("-50.00") or rec.lines[0].rate == Decimal(
        "50.00"
    )

    checking = seed_accounts["1000"]
    pay = db_session.query(Payment).filter_by(amount=Decimal("106.40")).first()
    assert pay.deposit_to_account_id == checking.id

    # The other receipts fall back to Undeposited Funds by account name
    uf = seed_accounts["1200"]
    other = db_session.query(Payment).filter_by(amount=Decimal("1000.00")).first()
    assert other.deposit_to_account_id == uf.id


def test_reimport_skips_duplicates(db_session, seed_accounts):
    from app.models.invoices import Invoice

    first = _import(db_session)
    assert first["imported"] == 3
    second = _import(db_session)
    assert second["imported"] == 0
    assert second["duplicates_skipped"] == 3
    assert db_session.query(Invoice).count() == 3


def test_unrecognized_csv_reports_a_clear_error(db_session, seed_accounts):
    from app.services.qb_report_import import import_sales_receipt_report

    result = import_sales_receipt_report(db_session, "a,b,c\n1,2,3\n")
    assert result["imported"] == 0
    assert result["errors"] and "column header" in result["errors"][0]


def test_endpoint_imports_the_fixture(client, seed_accounts):
    r = client.post(
        "/api/csv/import/sales-receipts",
        files={"file": ("receipts.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 3
    assert body["errors"] == []

    receipts = client.get("/api/invoices?is_sales_receipt=true").json()
    assert len(receipts) == 3
