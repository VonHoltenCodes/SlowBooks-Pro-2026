"""Source identities survive customer failures and project references import."""

from datetime import date
from decimal import Decimal

import pytest
from quickbooks.objects.invoice import Invoice as QBOInvoice
from quickbooks.objects.payment import Payment as QBOPayment
from quickbooks.objects.salesreceipt import SalesReceipt as QBOSalesReceipt

from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.payments import Payment
from app.models.qbo_mapping import QBOMapping
from app.services import qbo_import


@pytest.mark.parametrize(
    "entity, sdk, model",
    [
        ("invoices", QBOInvoice, Invoice),
        ("payments", QBOPayment, Payment),
        ("sales_receipts", QBOSalesReceipt, Invoice),
    ],
)
def test_project_customer_documents_resolve_parent_and_preserve_job(
    db_session, monkeypatch, entity, sdk, model
):
    customer = Customer(name="Parent customer")
    db_session.add(customer)
    db_session.flush()
    job = Job(name="PO 123 Line 3", customer_id=customer.id)
    db_session.add(job)
    db_session.flush()
    db_session.add(QBOMapping(entity_type="job", qbo_id="223", slowbooks_id=job.id))
    db_session.flush()
    source = sdk.from_json(
        {
            "Id": "6607",
            "DocNumber": "1373",
            "PaymentRefNum": "CHECK-9",
            "TxnDate": "2026-08-03",
            "DueDate": "2026-08-03",
            "TotalAmt": 10,
            "Balance": 10,
            "CustomerRef": {"value": "223", "name": "PO 123 Line 3"},
            "Line": [],
        }
    )
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    monkeypatch.setattr(qbo_import, "_all_qbo_objects", lambda cls, client: [source])
    result = getattr(qbo_import, f"import_{entity}")(db_session)
    db_session.flush()
    assert result == {"imported": 1, "errors": []}
    document = db_session.query(model).one()
    assert document.customer_id == customer.id
    assert document.date == date(2026, 8, 3)
    if model is Invoice:
        assert document.job_id == job.id
        assert document.total == Decimal("10")
    assert getattr(qbo_import, f"import_{entity}")(db_session) == {
        "imported": 0,
        "errors": [],
    }


@pytest.mark.parametrize(
    "entity, sdk",
    [
        ("invoices", QBOInvoice),
        ("payments", QBOPayment),
        ("sales_receipts", QBOSalesReceipt),
    ],
)
def test_missing_customer_identifies_source_document_and_reference(
    db_session, monkeypatch, entity, sdk
):
    source = sdk.from_json(
        {
            "Id": "6607",
            "DocNumber": "1373",
            "TxnDate": "2026-08-03",
            "TotalAmt": 10,
            "CustomerRef": {"value": "223", "name": "PO 123 Line 3"},
            "Line": [{"LinkedTxn": [{"TxnType": "Invoice", "TxnId": "4991"}]}],
        }
    )
    monkeypatch.setattr(qbo_import, "get_qbo_client", lambda db: object())
    monkeypatch.setattr(qbo_import, "_all_qbo_objects", lambda cls, client: [source])
    result = getattr(qbo_import, f"import_{entity}")(db_session)
    error = result["errors"][0]
    assert result["imported"] == 0
    assert error["qbo_id"] == "6607"
    assert error["code"] == "IMPORT_CUSTOMER_NOT_FOUND"
    assert error["document_number"] == "1373"
    assert "QBO #6607 (document 1373)" in error["message"]
    assert "CustomerRef QBO #223 (PO 123 Line 3)" in error["message"]
    assert "Invoice QBO #4991" in error["message"]
    assert "no local customer or project mapping" in error["message"]
    assert db_session.query(Invoice).count() == 0
    assert db_session.query(Payment).count() == 0
