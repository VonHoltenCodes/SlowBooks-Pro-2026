"""Sales receipts — the one-screen invoice+payment endpoint and QBO import.

A sales receipt is stored as an Invoice flagged is_sales_receipt plus a
Payment for the full total. These tests pin: both documents and their
journal entries post atomically, validation rejects inputs that would
strand a half-written receipt, the list filter separates receipts from
regular invoices, and the QBO SalesReceipt importer produces the same
document pair with id-mapped dedup.
"""

from decimal import Decimal
from types import SimpleNamespace


def _post_receipt(client, customer_id, **overrides):
    body = {
        "customer_id": customer_id,
        "date": "2026-04-01",
        "tax_rate": "0.0875",
        "method": "Cash",
        "lines": [
            {
                "description": "Widget",
                "quantity": "1",
                "rate": "100.00",
                "line_order": 0,
            }
        ],
    }
    body.update(overrides)
    return client.post("/api/sales-receipts", json=body)


def _sum_debits_credits(db_session, txn_id):
    from app.models.transactions import TransactionLine

    lines = db_session.query(TransactionLine).filter_by(transaction_id=txn_id).all()
    return (
        sum((Decimal(str(line.debit)) for line in lines), Decimal("0")),
        sum((Decimal(str(line.credit)) for line in lines), Decimal("0")),
    )


def test_create_sales_receipt_posts_paid_invoice_and_payment(
    client, db_session, seed_accounts, seed_customer
):
    r = _post_receipt(client, seed_customer.id)
    assert r.status_code == 201, r.text
    data = r.json()

    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment, PaymentAllocation

    invoice = db_session.query(Invoice).filter_by(id=data["invoice"]["id"]).first()
    assert invoice.is_sales_receipt is True
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")
    assert invoice.amount_paid == Decimal("108.75")
    assert invoice.balance_due == Decimal("0.00")

    payment = db_session.query(Payment).filter_by(id=data["payment"]["id"]).first()
    assert payment.amount == Decimal("108.75")
    assert payment.method == "Cash"
    alloc = db_session.query(PaymentAllocation).filter_by(payment_id=payment.id).first()
    assert alloc.invoice_id == invoice.id
    assert alloc.amount == Decimal("108.75")

    # Both journal entries exist and balance
    assert invoice.transaction_id is not None
    dr, cr = _sum_debits_credits(db_session, invoice.transaction_id)
    assert dr == cr == Decimal("108.75")
    assert payment.transaction_id is not None
    dr, cr = _sum_debits_credits(db_session, payment.transaction_id)
    assert dr == cr == Decimal("108.75")

    # Default deposit: the payment journal debits Undeposited Funds (1200)
    from app.models.transactions import TransactionLine

    uf = seed_accounts["1200"]
    debit_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=payment.transaction_id, account_id=uf.id)
        .first()
    )
    assert debit_line is not None
    assert Decimal(str(debit_line.debit)) == Decimal("108.75")


def test_create_sales_receipt_with_explicit_deposit_account(
    client, db_session, seed_accounts, seed_customer
):
    checking = seed_accounts["1000"]
    r = _post_receipt(
        client, seed_customer.id, deposit_to_account_id=checking.id, tax_rate="0"
    )
    assert r.status_code == 201, r.text

    from app.models.payments import Payment
    from app.models.transactions import TransactionLine

    payment = db_session.query(Payment).filter_by(id=r.json()["payment"]["id"]).first()
    assert payment.deposit_to_account_id == checking.id
    debit_line = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=payment.transaction_id, account_id=checking.id)
        .first()
    )
    assert Decimal(str(debit_line.debit)) == Decimal("100.00")


def test_zero_total_sales_receipt_rejected_before_writing(
    client, db_session, seed_accounts, seed_customer
):
    r = _post_receipt(
        client,
        seed_customer.id,
        tax_rate="0",
        lines=[{"description": "Freebie", "quantity": "1", "rate": "0"}],
    )
    assert r.status_code == 400

    from app.models.invoices import Invoice

    assert db_session.query(Invoice).count() == 0


def test_unknown_deposit_account_rejected_before_writing(
    client, db_session, seed_accounts, seed_customer
):
    r = _post_receipt(client, seed_customer.id, deposit_to_account_id=999999)
    assert r.status_code == 404

    from app.models.invoices import Invoice

    assert db_session.query(Invoice).count() == 0


def test_unknown_customer_rejected(client, seed_accounts):
    r = _post_receipt(client, 999999)
    assert r.status_code == 404


def test_list_filter_separates_receipts_from_invoices(
    client, seed_accounts, seed_customer
):
    receipt_id = _post_receipt(client, seed_customer.id).json()["invoice"]["id"]
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [{"description": "Consulting", "quantity": "1", "rate": "50"}],
        },
    )
    assert r.status_code == 201
    invoice_id = r.json()["id"]

    receipts = client.get("/api/invoices?is_sales_receipt=true").json()
    assert [i["id"] for i in receipts] == [receipt_id]
    invoices = client.get("/api/invoices?is_sales_receipt=false").json()
    assert [i["id"] for i in invoices] == [invoice_id]
    # No filter -> everything, as before
    all_ids = {i["id"] for i in client.get("/api/invoices").json()}
    assert all_ids == {receipt_id, invoice_id}


# ---------------------------------------------------------------------------
# QBO SalesReceipt import
# ---------------------------------------------------------------------------


def _fake_qbo_receipt(**overrides):
    receipt = SimpleNamespace(
        Id="SR-77",
        SyncToken="0",
        DocNumber="SR-1001",
        CustomerRef=SimpleNamespace(value="99", name="Walkup Wanda"),
        TotalAmt=108.75,
        TxnDate="2026-04-01",
        TxnTaxDetail=SimpleNamespace(TotalTax=8.75),
        DepositToAccountRef=None,
        PaymentMethodRef={"name": "Cash"},
        PaymentRefNum="REG-1",
        CustomerMemo={"value": "counter sale"},
        Line=[
            SimpleNamespace(
                DetailType="SalesItemLineDetail",
                SalesItemLineDetail=SimpleNamespace(
                    ItemRef=None, Qty=1, UnitPrice=100.00
                ),
                Amount=100.00,
                Description="Widget",
            )
        ],
    )
    for key, val in overrides.items():
        setattr(receipt, key, val)
    return receipt


def _wire_fake_qbo(monkeypatch, receipts):
    from app.services import qbo_import
    import quickbooks.objects.salesreceipt as sr_mod

    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: None)
    monkeypatch.setattr(
        sr_mod.SalesReceipt, "all", classmethod(lambda cls, qb=None: list(receipts))
    )


def test_qbo_import_sales_receipts(db_session, seed_accounts, monkeypatch):
    from app.models.contacts import Customer
    from app.models.invoices import Invoice, InvoiceStatus
    from app.models.payments import Payment, PaymentAllocation
    from app.services import qbo_import
    from app.services.qbo_common import get_mapping_by_qbo_id

    db_session.add(Customer(name="Walkup Wanda", is_active=True))
    db_session.commit()

    _wire_fake_qbo(monkeypatch, [_fake_qbo_receipt()])
    result = qbo_import.import_sales_receipts(db_session)
    db_session.commit()
    assert result["imported"] == 1, result
    assert result["errors"] == []

    invoice = db_session.query(Invoice).filter_by(invoice_number="SR-1001").first()
    assert invoice is not None
    assert invoice.is_sales_receipt is True
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_amount == Decimal("8.75")
    assert invoice.total == Decimal("108.75")
    assert invoice.balance_due == Decimal("0")
    assert len(invoice.lines) == 1
    assert invoice.lines[0].description == "Widget"

    payment = db_session.query(Payment).first()
    assert payment.amount == Decimal("108.75")
    assert payment.method == "Cash"
    assert payment.reference == "REG-1"
    alloc = db_session.query(PaymentAllocation).first()
    assert alloc.invoice_id == invoice.id

    assert get_mapping_by_qbo_id(db_session, "sales_receipt", "SR-77") is not None


def test_qbo_import_sales_receipts_dedups_on_rerun(
    db_session, seed_accounts, monkeypatch
):
    from app.models.contacts import Customer
    from app.models.invoices import Invoice
    from app.services import qbo_import

    db_session.add(Customer(name="Walkup Wanda", is_active=True))
    db_session.commit()

    _wire_fake_qbo(monkeypatch, [_fake_qbo_receipt()])
    assert qbo_import.import_sales_receipts(db_session)["imported"] == 1
    db_session.commit()
    assert qbo_import.import_sales_receipts(db_session)["imported"] == 0
    db_session.commit()
    assert db_session.query(Invoice).count() == 1


def test_qbo_import_sales_receipt_without_customer_reports_error(
    db_session, seed_accounts, monkeypatch
):
    from app.services import qbo_import

    _wire_fake_qbo(
        monkeypatch,
        [_fake_qbo_receipt(CustomerRef=SimpleNamespace(value="1", name="Nobody"))],
    )
    result = qbo_import.import_sales_receipts(db_session)
    assert result["imported"] == 0
    assert result["errors"] and "Customer not found" in result["errors"][0]["message"]


# ---------------------------------------------------------------------------
# PDF / print artifact (#60): a receipt must not render as an invoice
# ---------------------------------------------------------------------------


def test_sales_receipt_pdf_is_a_receipt_not_an_invoice(
    client, seed_accounts, seed_customer
):
    sr = _post_receipt(client, seed_customer.id).json()
    inv_id = sr["invoice"]["id"]

    r = client.get(f"/api/invoices/{inv_id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert f"SalesReceipt_{sr['invoice']['invoice_number']}.pdf" in r.headers.get(
        "content-disposition", ""
    )

    # The print preview renders the same template as HTML, so the document
    # semantics can be asserted as text.
    html = client.get(f"/api/invoices/{inv_id}/print-preview").text
    assert "SALES RECEIPT" in html
    assert "Balance Due" not in html
    assert "Due Date" not in html
    assert "Terms" not in html
    assert "Sold To" in html
    assert "Test Customer" in html


def test_invoice_pdf_keeps_invoice_semantics(client, seed_accounts, seed_customer):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [{"description": "Consulting", "quantity": "1", "rate": "50"}],
        },
    )
    inv = r.json()

    r = client.get(f"/api/invoices/{inv['id']}/pdf")
    assert r.status_code == 200
    assert f"Invoice_{inv['invoice_number']}.pdf" in r.headers.get(
        "content-disposition", ""
    )

    html = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "INVOICE" in html
    assert "SALES RECEIPT" not in html
    assert "Due Date" in html
    assert "Bill To" in html
    # Regression for the bare BILL TO block: the customer name must print
    # even when the callers don't stamp customer_name onto the ORM object.
    assert "Test Customer" in html


def test_pdf_template_prints_name_via_customer_relationship(
    client, db_session, seed_accounts, seed_customer
):
    """The /pdf route renders the ORM object directly (no customer_name
    attribute) — the template must fall back to inv.customer.name."""
    sr = _post_receipt(client, seed_customer.id).json()

    from app.models.invoices import Invoice
    from app.services.pdf_service import _jinja_env

    inv = db_session.query(Invoice).filter_by(id=sr["invoice"]["id"]).first()
    assert not hasattr(inv, "customer_name")
    html = _jinja_env.get_template("invoice_pdf.html").render(inv=inv, company={})
    assert "Test Customer" in html
    assert "SALES RECEIPT" in html
