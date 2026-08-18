"""IIF import tests — common case coverage.

The handler only understands INVOICE, PAYMENT, and ESTIMATE blocks. For the
common-case QB export convention (SPL amounts stored with opposite sign from
the AR debit), the abs()-based parse is correct. Edge cases like mixed-sign
SPL lines (e.g. discount lines) are a known limitation — separate work item.
"""

from decimal import Decimal

INVOICE_IIF = (
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tTERMS\n"
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tINVITEM\tQNTY\tPRICE\n"
    "!ENDTRNS\n"
    "TRNS\t1\tINVOICE\t2026-04-01\tAccounts Receivable\tAcme Co\t108.75\tINV-001\tNet 30\n"
    "SPL\t2\tINVOICE\t2026-04-01\tService Income\tAcme Co\t-100.00\t\t1\t100.00\n"
    "SPL\t3\tINVOICE\t2026-04-01\tSales Tax Payable\tAcme Co\t-8.75\t\t\t\n"
    "ENDTRNS\n"
)

# Sub-cent SPL amounts (e.g. fuel surcharge exports from QB Pro) drift the
# stored subtotal / total away from sum(line.amount) when accumulated raw.
SUBCENT_INVOICE_IIF = (
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tTERMS\n"
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tINVITEM\tQNTY\tPRICE\n"
    "!ENDTRNS\n"
    "TRNS\t1\tINVOICE\t2026-04-01\tAccounts Receivable\tAcme Co\t99.99\tINV-DRIFT\tNet 30\n"
    "SPL\t2\tINVOICE\t2026-04-01\tService Income\tAcme Co\t-49.995\t\t1.5\t33.33\n"
    "SPL\t3\tINVOICE\t2026-04-01\tService Income\tAcme Co\t-49.995\t\t1.5\t33.33\n"
    "ENDTRNS\n"
)


def test_iif_import_invoice_common_case(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.invoices import Invoice
    from app.models.contacts import Customer

    # Ensure a customer exists to avoid the auto-create path complicating the test
    db_session.add(Customer(name="Acme Co", is_active=True))
    db_session.commit()

    parsed = parse_iif(INVOICE_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["invoices"] == 1, result
    invoice = db_session.query(Invoice).filter_by(invoice_number="INV-001").first()
    assert invoice is not None
    assert invoice.total == Decimal("108.75")
    # Subtotal = sum of non-tax SPL amounts (absolute value convention)
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")

    # Journal entry should exist and be balanced
    assert invoice.transaction_id is not None
    from app.models.transactions import TransactionLine

    lines = (
        db_session.query(TransactionLine)
        .filter_by(
            transaction_id=invoice.transaction_id,
        )
        .all()
    )
    total_dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    total_cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert total_dr == total_cr == Decimal("108.75")


def test_iif_import_subcent_amounts_quantize_to_match_subtotal(
    db_session, seed_accounts
):
    """Sub-cent SPL AMOUNT values (e.g. -49.995) must round to 2dp on import
    so the stored InvoiceLine.amount sum matches the stored subtotal and the
    journal entry remains balanced after persistence."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.invoices import Invoice, InvoiceLine
    from app.models.contacts import Customer
    from app.models.transactions import TransactionLine

    db_session.add(Customer(name="Acme Co", is_active=True))
    db_session.commit()

    parsed = parse_iif(SUBCENT_INVOICE_IIF)
    import_transactions(db_session, parsed["TRNS"])
    db_session.commit()
    db_session.expire_all()

    invoice = db_session.query(Invoice).filter_by(invoice_number="INV-DRIFT").first()
    assert invoice is not None

    lines = db_session.query(InvoiceLine).filter_by(invoice_id=invoice.id).all()
    sum_lines = sum((Decimal(str(l.amount)) for l in lines), Decimal("0"))
    assert (
        invoice.subtotal == sum_lines
    ), f"subtotal {invoice.subtotal} != sum(line.amount) {sum_lines}"

    # JE balanced after persistence (stored cents on both sides match)
    if invoice.transaction_id:
        txn_lines = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=invoice.transaction_id)
            .all()
        )
        dr = sum((Decimal(str(l.debit)) for l in txn_lines), Decimal("0"))
        cr = sum((Decimal(str(l.credit)) for l in txn_lines), Decimal("0"))
        assert dr == cr, f"JE unbalanced: debits={dr} credits={cr}"


def test_iif_import_dedupes_on_doc_number(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.invoices import Invoice
    from app.models.contacts import Customer

    db_session.add(Customer(name="Acme Co", is_active=True))
    db_session.commit()

    parsed = parse_iif(INVOICE_IIF)
    import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    # Re-import same IIF — should be a no-op (existing doc number detected)
    import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert db_session.query(Invoice).filter_by(invoice_number="INV-001").count() == 1


# ============================================================================
# BILL import tests
#
# Sign convention (standard QB IIF for BILL): TRNS line carries the
# AP-account amount as NEGATIVE; SPL line(s) carry expense-account amounts
# as POSITIVE. They must sum to zero. The IIF below mirrors the May 2026
# bulk-import test file: two simple Apple Store bills with $1 and $2 totals.
# ============================================================================

BILL_IIF = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tDUEDATE\tTERMS\tMEMO\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!ENDTRNS\n"
    "TRNS\tBILL\t05/01/2026\tAccounts Payable\tApple Store\t-1.00\tTEST-001\t05/31/2026\tNet 30\tIIF import test #1\n"
    "SPL\tBILL\t05/01/2026\tOffice Supplies\tApple Store\t1.00\tTEST-001\ttest expense line\n"
    "ENDTRNS\n"
    "TRNS\tBILL\t05/01/2026\tAccounts Payable\tApple Store\t-2.00\tTEST-002\t05/31/2026\tNet 30\tIIF import test #2\n"
    "SPL\tBILL\t05/01/2026\tOffice Supplies\tApple Store\t2.00\tTEST-002\ttest expense line 2\n"
    "ENDTRNS\n"
)


def _seed_apple_vendor(db_session):
    from app.models.contacts import Vendor

    v = Vendor(name="Apple Store", is_active=True)
    db_session.add(v)
    db_session.commit()
    return v


def test_iif_import_bill_happy_path(db_session, seed_accounts):
    """The acceptance criterion from the bulk-import spec: drop the test
    IIF in, get 2 bills with bill_numbers TEST-001/TEST-002, status UNPAID,
    totals $1 and $2, vendor=Apple Store."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.bills import Bill, BillStatus

    _seed_apple_vendor(db_session)

    parsed = parse_iif(BILL_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["bills"] == 2, result
    assert result["errors"] == [], result["errors"]

    bills = db_session.query(Bill).order_by(Bill.bill_number).all()
    assert [b.bill_number for b in bills] == ["TEST-001", "TEST-002"]
    assert all(b.status == BillStatus.UNPAID for b in bills)
    assert [b.total for b in bills] == [Decimal("1.00"), Decimal("2.00")]
    assert all(b.vendor.name == "Apple Store" for b in bills)
    # Each bill has one BillLine pointed at the Office Supplies account.
    for b in bills:
        assert len(b.lines) == 1
        assert b.lines[0].account.name == "Office Supplies"
    # Each bill has a balanced journal entry (DR Expense, CR AP).
    from app.models.transactions import TransactionLine

    for b in bills:
        assert b.transaction_id is not None
        lines = (
            db_session.query(TransactionLine)
            .filter_by(transaction_id=b.transaction_id)
            .all()
        )
        total_dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
        total_cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
        assert total_dr == total_cr == b.total


def test_iif_import_bill_missing_vendor_returns_error_no_partial(
    db_session, seed_accounts
):
    """Spec: don't auto-create vendors. Surface the missing name so the
    user can fix Vendors first."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.bills import Bill

    # Note: NO _seed_apple_vendor call — vendor doesn't exist.
    parsed = parse_iif(BILL_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["bills"] == 0
    assert len(result["errors"]) == 2
    msgs = [e["message"] for e in result["errors"]]
    assert all("Apple Store" in m for m in msgs), msgs
    assert all("vendor" in m.lower() and "not found" in m.lower() for m in msgs), msgs
    # No partial Bill rows were left behind by the savepoint rollback.
    assert db_session.query(Bill).count() == 0


def test_iif_import_bill_missing_account_returns_error_no_partial(
    db_session, seed_accounts
):
    """Same defensive posture for the SPL expense account: error out,
    don't silently fall back to a default expense category."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.bills import Bill

    _seed_apple_vendor(db_session)

    bad_iif = (
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\n"
        "!ENDTRNS\n"
        "TRNS\tBILL\t05/01/2026\tAccounts Payable\tApple Store\t-1.00\tTEST-A\n"
        # This account doesn't exist in the seeded chart.
        "SPL\tBILL\t05/01/2026\tHovercraft Repairs\tApple Store\t1.00\n"
        "ENDTRNS\n"
    )
    parsed = parse_iif(bad_iif)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["bills"] == 0
    assert len(result["errors"]) == 1
    assert "Hovercraft Repairs" in result["errors"][0]["message"]
    assert "not found" in result["errors"][0]["message"].lower()
    assert db_session.query(Bill).count() == 0


def test_iif_import_bill_unbalanced_block_rejected(db_session, seed_accounts):
    """TRNS + SPL must sum to zero. If they don't, refuse rather than
    posting an unbalanced bill — an unbalanced source block usually
    means a hand-edited IIF where a line got dropped."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.bills import Bill

    _seed_apple_vendor(db_session)

    # TRNS=-1, SPL=2 -> residual=1, rejected.
    unbalanced = (
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\n"
        "!ENDTRNS\n"
        "TRNS\tBILL\t05/01/2026\tAccounts Payable\tApple Store\t-1.00\tTEST-UNB\n"
        "SPL\tBILL\t05/01/2026\tOffice Supplies\tApple Store\t2.00\n"
        "ENDTRNS\n"
    )
    parsed = parse_iif(unbalanced)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["bills"] == 0
    assert len(result["errors"]) == 1
    assert "sum to zero" in result["errors"][0]["message"].lower()
    assert db_session.query(Bill).count() == 0


def test_iif_import_bill_dedupes_on_docnum(db_session, seed_accounts):
    """Same (vendor, bill_number) twice must result in one bill row.
    Idempotent re-runs are required so the user can re-import after
    fixing earlier failures without double-counting."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.bills import Bill

    _seed_apple_vendor(db_session)

    parsed = parse_iif(BILL_IIF)
    import_transactions(db_session, parsed["TRNS"])
    db_session.commit()
    assert db_session.query(Bill).count() == 2

    # Re-import the same IIF — no new rows, no errors.
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()
    assert result["imported"]["bills"] == 0
    assert result["errors"] == []
    assert db_session.query(Bill).count() == 2


# ============================================================================
# DEPOSIT import tests
# ============================================================================

DEPOSIT_IIF = (
    "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO\n"
    "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO\n"
    "!ENDTRNS\n"
    # TRNS positive on bank, SPL negative on income — the inverse of BILL.
    "TRNS\tDEPOSIT\t05/02/2026\tChecking\t\t150.00\tDEP-001\tConsulting income deposit\n"
    "SPL\tDEPOSIT\t05/02/2026\tService Income\t\t-150.00\tConsulting fees\n"
    "ENDTRNS\n"
)


def test_iif_import_deposit_happy_path(db_session, seed_accounts):
    """Deposit creates a journal-only Transaction with source_type='deposit',
    DR bank, CR income — same shape as the manual Make Deposits route."""
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.transactions import Transaction, TransactionLine

    parsed = parse_iif(DEPOSIT_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["deposits"] == 1, result
    assert result["errors"] == [], result["errors"]

    txn = (
        db_session.query(Transaction)
        .filter_by(source_type="deposit", reference="DEP-001")
        .first()
    )
    assert txn is not None
    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
    assert len(lines) == 2

    bank_line = next(l for l in lines if l.account.name == "Checking")
    income_line = next(l for l in lines if l.account.name == "Service Income")
    assert bank_line.debit == Decimal("150.00") and bank_line.credit == 0
    assert income_line.credit == Decimal("150.00") and income_line.debit == 0


def test_iif_import_deposit_missing_account_rejected(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.transactions import Transaction

    bad_iif = (
        "!TRNS\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\n"
        "!SPL\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\n"
        "!ENDTRNS\n"
        "TRNS\tDEPOSIT\t05/02/2026\tNonexistent Bank\t\t100.00\tDEP-X\n"
        "SPL\tDEPOSIT\t05/02/2026\tService Income\t\t-100.00\n"
        "ENDTRNS\n"
    )
    parsed = parse_iif(bad_iif)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["deposits"] == 0
    assert len(result["errors"]) == 1
    assert "Nonexistent Bank" in result["errors"][0]["message"]
    # No partial Transaction row was committed.
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 0


def test_iif_import_deposit_dedupes_on_docnum(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.transactions import Transaction

    parsed = parse_iif(DEPOSIT_IIF)
    import_transactions(db_session, parsed["TRNS"])
    db_session.commit()
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 1

    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()
    assert result["imported"]["deposits"] == 0
    assert db_session.query(Transaction).filter_by(source_type="deposit").count() == 1


# ============================================================================
# import_all integration: counts roll up correctly into the result schema
# ============================================================================


def test_import_all_reports_bills_and_deposits_in_result(db_session, seed_accounts):
    """The route returns the result dict directly to the UI; the UI
    enumerates Bills/Deposits rows. Pin that the orchestrator
    populates both keys so the UI never sees Imported 0 again."""
    from app.services.iif_import import import_all

    # Seed Apple Store so the BILL portion succeeds.
    from app.models.contacts import Vendor

    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()

    # Combined IIF: 2 bills + 1 deposit.
    combined = BILL_IIF + DEPOSIT_IIF
    result = import_all(db_session, combined)

    assert result["bills"] == 2, result
    assert result["deposits"] == 1, result
    assert result["errors"] == [], result["errors"]


def test_reimport_reports_duplicates_skipped(db_session, seed_accounts):
    """Re-running the same BILL/DEPOSIT IIF reports dedup hits explicitly
    instead of silently importing nothing."""
    from app.models.contacts import Vendor
    from app.services.iif_import import import_all

    db_session.add(Vendor(name="Apple Store", is_active=True))
    db_session.commit()

    combined = BILL_IIF + DEPOSIT_IIF
    first = import_all(db_session, combined)
    assert first["bills"] == 2
    assert first["deposits"] == 1
    assert first["duplicates_skipped"] == 0

    second = import_all(db_session, combined)
    assert second["bills"] == 0
    assert second["deposits"] == 0
    assert second["duplicates_skipped"] == 3


# ---------------------------------------------------------------------------
# CASH SALE (sales receipts)
# ---------------------------------------------------------------------------

CASH_SALE_IIF = (
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tPAYMETH\n"
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tINVITEM\tQNTY\tPRICE\n"
    "!ENDTRNS\n"
    "TRNS\t1\tCASH SALE\t2026-04-02\tChecking\tWalkup Wanda\t108.75\tSR-100\tCash\n"
    "SPL\t2\tCASH SALE\t2026-04-02\tService Income\tWalkup Wanda\t-100.00\t\t1\t100.00\n"
    "SPL\t3\tCASH SALE\t2026-04-02\tSales Tax Payable\tWalkup Wanda\t-8.75\t\t\t\n"
    "ENDTRNS\n"
)

# Counter sale: no customer name, no document number.
ANON_CASH_SALE_IIF = (
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tPAYMETH\n"
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tINVITEM\tQNTY\tPRICE\n"
    "!ENDTRNS\n"
    "TRNS\t1\tCASH SALE\t2026-04-03\tChecking\t\t25.00\t\tCash\n"
    "SPL\t2\tCASH SALE\t2026-04-03\tService Income\t\t-25.00\t\t1\t25.00\n"
    "ENDTRNS\n"
)


def test_iif_import_cash_sale_creates_paid_receipt(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment, PaymentAllocation
    from app.models.contacts import Customer
    from app.models.transactions import TransactionLine

    db_session.add(Customer(name="Walkup Wanda", is_active=True))
    db_session.commit()

    parsed = parse_iif(CASH_SALE_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["sales_receipts"] == 1, result
    invoice = db_session.query(Invoice).filter_by(invoice_number="SR-100").first()
    assert invoice is not None
    assert invoice.is_sales_receipt is True
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")
    assert invoice.balance_due == Decimal("0")

    payment = db_session.query(Payment).first()
    assert payment.amount == Decimal("108.75")
    assert payment.method == "Cash"
    checking = seed_accounts["1000"]
    assert payment.deposit_to_account_id == checking.id
    alloc = db_session.query(PaymentAllocation).first()
    assert alloc.invoice_id == invoice.id
    assert alloc.amount == Decimal("108.75")

    # Both journals balance; the payment journal debits Checking.
    for txn_id in (invoice.transaction_id, payment.transaction_id):
        assert txn_id is not None
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
        dr = sum((Decimal(str(line.debit)) for line in lines), Decimal("0"))
        cr = sum((Decimal(str(line.credit)) for line in lines), Decimal("0"))
        assert dr == cr == Decimal("108.75")
    debit_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=payment.transaction_id, account_id=checking.id)
        .first()
    )
    assert Decimal(str(debit_line.debit)) == Decimal("108.75")


def test_iif_import_anonymous_cash_sale_uses_walk_in_customer(
    db_session, seed_accounts
):
    from app.services.iif_import import (
        WALK_IN_CUSTOMER_NAME,
        import_transactions,
        parse_iif,
    )
    from app.models.invoices import Invoice
    from app.models.contacts import Customer

    parsed = parse_iif(ANON_CASH_SALE_IIF)
    result = import_transactions(db_session, parsed["TRNS"])
    db_session.commit()

    assert result["imported"]["sales_receipts"] == 1, result
    assert any(WALK_IN_CUSTOMER_NAME in w for w in result["warnings"])

    walk_in = db_session.query(Customer).filter_by(name=WALK_IN_CUSTOMER_NAME).first()
    assert walk_in is not None
    invoice = db_session.query(Invoice).first()
    assert invoice.customer_id == walk_in.id
    # Unnumbered receipt got the next invoice number from the numbering service
    assert invoice.invoice_number


def test_iif_reimport_cash_sale_skips_duplicates(db_session, seed_accounts):
    from app.services.iif_import import parse_iif, import_transactions
    from app.models.invoices import Invoice
    from app.models.contacts import Customer

    db_session.add(Customer(name="Walkup Wanda", is_active=True))
    db_session.commit()

    combined = parse_iif(CASH_SALE_IIF + ANON_CASH_SALE_IIF)
    first = import_transactions(db_session, combined["TRNS"])
    db_session.commit()
    assert first["imported"]["sales_receipts"] == 2

    second = import_transactions(db_session, combined["TRNS"])
    db_session.commit()
    assert second["imported"]["sales_receipts"] == 0
    assert second["imported"]["duplicates_skipped"] == 2
    assert db_session.query(Invoice).count() == 2
