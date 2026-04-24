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
    lines = db_session.query(TransactionLine).filter_by(
        transaction_id=invoice.transaction_id,
    ).all()
    total_dr = sum((Decimal(str(l.debit)) for l in lines), Decimal("0"))
    total_cr = sum((Decimal(str(l.credit)) for l in lines), Decimal("0"))
    assert total_dr == total_cr == Decimal("108.75")


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
