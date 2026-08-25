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
        "/api/csv/import/qb-report",
        files={"file": ("receipts.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected"] == "sales_receipts"
    assert body["sales_receipts"] == 3
    assert body["errors"] == []

    receipts = client.get("/api/invoices?is_sales_receipt=true").json()
    assert len(receipts) == 3


# ---------------------------------------------------------------------------
# Deposit Detail and Check Detail reports + auto-detection dispatcher
# ---------------------------------------------------------------------------

DEPOSIT_FIXTURE = Path(__file__).parent / "fixtures" / "qb_deposit_detail.csv"
CHECK_FIXTURE = Path(__file__).parent / "fixtures" / "qb_check_detail.csv"


def _sum_dr_cr(db_session, txn_id):
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return (
        sum((Decimal(str(line.debit)) for line in lines), Decimal("0")),
        sum((Decimal(str(line.credit)) for line in lines), Decimal("0")),
    )


def test_detect_report_type():
    from app.services.qb_report_import import detect_report_type

    assert detect_report_type(FIXTURE.read_text()) == "sales_receipts"
    assert detect_report_type(DEPOSIT_FIXTURE.read_text()) == "deposits"
    assert detect_report_type(CHECK_FIXTURE.read_text()) == "checks"
    assert detect_report_type("a,b\n1,2\n") is None


def test_deposit_report_imports_balanced_journals(db_session, seed_accounts):
    from app.models.transactions import Transaction, TransactionLine
    from app.services.qb_report_import import import_deposit_report

    result = import_deposit_report(db_session, DEPOSIT_FIXTURE.read_text())
    assert result["errors"] == [], result
    assert result["deposits"] == 2

    deposits = db_session.query(Transaction).filter_by(source_type="deposit").all()
    assert len(deposits) == 2
    checking = seed_accounts["1000"]
    uf = seed_accounts["1200"]
    totals = set()
    for txn in deposits:
        dr, cr = _sum_dr_cr(db_session, txn.id)
        assert dr == cr
        bank_line = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=txn.id, account_id=checking.id)
            .first()
        )
        totals.add(Decimal(str(bank_line.debit)))
        # Every credit line lands on Undeposited Funds
        for line in db_session.query(TransactionLine).filter_by(transaction_id=txn.id):
            if Decimal(str(line.credit)) > 0:
                assert line.account_id == uf.id
    assert totals == {Decimal("17918.67"), Decimal("3087.17")}


def test_deposit_report_rerun_skips_duplicates(db_session, seed_accounts):
    from app.models.transactions import Transaction
    from app.services.qb_report_import import import_deposit_report

    assert (
        import_deposit_report(db_session, DEPOSIT_FIXTURE.read_text())["deposits"] == 2
    )
    second = import_deposit_report(db_session, DEPOSIT_FIXTURE.read_text())
    assert second["deposits"] == 0
    assert second["duplicates_skipped"] == 2
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 2


def test_check_report_imports_balanced_journals(db_session, seed_accounts):
    from app.models.transactions import Transaction, TransactionLine
    from app.services.qb_report_import import import_check_report

    result = import_check_report(db_session, CHECK_FIXTURE.read_text())
    assert result["errors"] == [], result
    assert result["checks"] == 4

    checks = db_session.query(Transaction).filter_by(source_type="check").all()
    assert len(checks) == 4
    checking = seed_accounts["1000"]
    credits = set()
    for txn in checks:
        dr, cr = _sum_dr_cr(db_session, txn.id)
        assert dr == cr
        bank_line = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=txn.id, account_id=checking.id)
            .first()
        )
        credits.add(Decimal(str(bank_line.credit)))
    assert credits == {
        Decimal("30.20"),
        Decimal("190.90"),
        Decimal("1185.90"),
        Decimal("711.12"),
    }
    # Numbered check keeps its number in the reference
    numbered = (
        db_session.query(Transaction)
        .filter_by(source_type="check", reference="1045")
        .first()
    )
    assert numbered is not None
    assert "Registry of Vehicles" in numbered.description


def test_check_report_rerun_skips_duplicates(db_session, seed_accounts):
    from app.services.qb_report_import import import_check_report

    assert import_check_report(db_session, CHECK_FIXTURE.read_text())["checks"] == 4
    second = import_check_report(db_session, CHECK_FIXTURE.read_text())
    assert second["checks"] == 0
    assert second["duplicates_skipped"] == 4


def test_check_report_unknown_account_errors_that_block_only(db_session, seed_accounts):
    from app.services.qb_report_import import import_check_report

    text = CHECK_FIXTURE.read_text().replace("Office Supplies", "No Such Account")
    result = import_check_report(db_session, text)
    assert result["checks"] == 3
    assert len(result["errors"]) == 1
    assert (
        "No Such Account" in result["errors"][0]
        and "chart of accounts" in result["errors"][0]
    )


def test_qb_report_endpoint_autodetects(client, seed_accounts):
    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("Deposits.csv", DEPOSIT_FIXTURE.read_bytes(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected"] == "deposits"
    assert body["deposits"] == 2

    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("Checks.csv", CHECK_FIXTURE.read_bytes(), "text/csv")},
    )
    assert r.json()["detected"] == "checks"
    assert r.json()["checks"] == 4

    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("receipts.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert r.json()["detected"] == "sales_receipts"
    assert r.json()["sales_receipts"] == 3

    r = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("junk.csv", b"a,b\n1,2\n", "text/csv")},
    )
    body = r.json()
    assert body["detected"] is None
    assert body["errors"] and "Unrecognized report layout" in body["errors"][0]


def test_payroll_check_with_withholding_contra_lines(db_session, seed_accounts):
    """A payroll check: gross wages DR, withholding lines CR their
    liability accounts, netting to the check amount — the sign-aware
    parse the real customer file demanded (abs() fails the balance gate
    on every payroll check)."""
    from app.models.transactions import Transaction, TransactionLine
    from app.services.qb_report_import import import_check_report

    result = import_check_report(db_session, CHECK_FIXTURE.read_text())
    assert result["errors"] == [], result
    assert result["checks"] == 4

    payroll = (
        db_session.query(Transaction)
        .filter(Transaction.source_type == "check", Transaction.reference == "27866")
        .first()
    )
    assert payroll is not None
    lines = db_session.query(TransactionLine).filter_by(transaction_id=payroll.id).all()
    by_acct = {
        line.account_id: (Decimal(str(line.debit)), Decimal(str(line.credit)))
        for line in lines
    }
    checking = seed_accounts["1000"]
    wages = next(a for a in seed_accounts.values() if a.name == "Wages & Salaries")
    fed = next(
        a for a in seed_accounts.values() if a.name == "Federal Income Tax Payable"
    )
    assert by_acct[checking.id] == (Decimal("0"), Decimal("711.12"))
    assert by_acct[wages.id] == (Decimal("1250.00"), Decimal("0"))
    assert by_acct[fed.id] == (Decimal("0"), Decimal("275.00"))
    dr = sum(d for d, _ in by_acct.values())
    cr = sum(c for _, c in by_acct.values())
    assert dr == cr
