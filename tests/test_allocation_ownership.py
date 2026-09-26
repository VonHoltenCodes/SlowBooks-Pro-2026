"""A payment, credit or batch line pays down its own customer's (or vendor's)
documents only. #189 closed this for ordinary customer payments; applying a
credit memo, batch payments and bill payments had the same gap: each returned
success and reduced another customer's invoice or another vendor's bill."""

import pytest

from app.models.bills import Bill, BillPayment, BillPaymentAllocation
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditApplication, CreditMemo
from app.models.invoices import Invoice
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction, TransactionLine


def _snapshot(TestSession):
    # Fresh session: what was committed, not the request's identity map.
    with TestSession() as s:
        return {
            m.__tablename__: [
                dict(r)
                for r in s.execute(
                    m.__table__.select().order_by(m.__table__.c.id)
                ).mappings()
            ]
            for m in (
                Invoice,
                Payment,
                PaymentAllocation,
                CreditMemo,
                CreditApplication,
                Bill,
                BillPayment,
                BillPaymentAllocation,
                Transaction,
                TransactionLine,
            )
        }


@pytest.fixture
def two_customers(db_session):
    a, b = Customer(name="Alder Co", is_active=True), Customer(
        name="Birch Co", is_active=True
    )
    db_session.add_all([a, b])
    db_session.commit()
    return a.id, b.id


def _invoice(client, customer_id, rate=100):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-04-01",
            "lines": [{"description": "Service", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _credit_memo(client, customer_id, rate=50):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": customer_id,
            "date": "2026-04-02",
            "lines": [{"description": "Credit", "quantity": 1, "rate": rate}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_credit_memo_applies_only_to_its_customers_invoices(
    client, TestSession, seed_accounts, two_customers
):
    a, b = two_customers
    theirs, ours = _invoice(client, b), _invoice(client, a)
    cm = _credit_memo(client, a)
    before = _snapshot(TestSession)
    r = client.post(
        f"/api/credit-memos/{cm}/apply", json={"invoice_id": theirs, "amount": 50}
    )
    assert r.status_code == 400 and "different customer" in r.json()["detail"]
    assert _snapshot(TestSession) == before

    r = client.post(
        f"/api/credit-memos/{cm}/apply", json={"invoice_id": ours, "amount": 50}
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/invoices/{ours}").json()["balance_due"] == "50.00"


def test_a_batch_line_pays_only_its_customers_invoice(
    client, TestSession, seed_accounts, two_customers
):
    a, b = two_customers
    theirs, ours = _invoice(client, b), _invoice(client, a)
    before = _snapshot(TestSession)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-04-02",
            "allocations": [{"customer_id": a, "invoice_id": theirs, "amount": 60}],
        },
    )
    assert r.status_code == 400 and "does not belong to customer" in r.json()["detail"]
    assert _snapshot(TestSession) == before

    # A mixed batch is refused whole: the good line is not posted either.
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-04-02",
            "allocations": [
                {"customer_id": a, "invoice_id": ours, "amount": 60},
                {"customer_id": a, "invoice_id": theirs, "amount": 60},
            ],
        },
    )
    assert r.status_code == 400
    assert _snapshot(TestSession) == before

    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-04-02",
            "allocations": [
                {"customer_id": a, "invoice_id": ours, "amount": 60},
                {"customer_id": b, "invoice_id": theirs, "amount": 25},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert client.get(f"/api/invoices/{ours}").json()["balance_due"] == "40.00"
    assert client.get(f"/api/invoices/{theirs}").json()["balance_due"] == "75.00"


def test_a_bill_payment_pays_only_its_vendors_bills(
    client, db_session, TestSession, seed_accounts
):
    v1, v2 = Vendor(name="Cedar Supply", is_active=True), Vendor(
        name="Dogwood Parts", is_active=True
    )
    db_session.add_all([v1, v2])
    db_session.commit()
    expense = seed_accounts["6000"].id

    def bill(vendor_id):
        r = client.post(
            "/api/bills",
            json={
                "vendor_id": vendor_id,
                "date": "2026-04-01",
                "lines": [
                    {
                        "account_id": expense,
                        "description": "Parts",
                        "quantity": 1,
                        "rate": 100,
                    }
                ],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    theirs, ours = bill(v2.id), bill(v1.id)
    before = _snapshot(TestSession)
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v1.id,
            "date": "2026-04-02",
            "amount": 60,
            "allocations": [{"bill_id": theirs, "amount": 60}],
        },
    )
    assert r.status_code == 400 and "different vendor" in r.json()["detail"]
    assert _snapshot(TestSession) == before

    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v1.id,
            "date": "2026-04-02",
            "amount": 60,
            "allocations": [{"bill_id": ours, "amount": 60}],
        },
    )
    assert r.status_code == 201, r.text
    assert client.get(f"/api/bills/{ours}").json()["balance_due"] == "40.00"


def test_pay_bills_sends_one_payment_per_vendor():
    # Pay Bills lists every vendor's open bills. It used to send every ticked
    # bill as one payment to the first bill's vendor, so paying a CPA and a
    # supplier together recorded one payment to the CPA that also "paid" the
    # supplier's bill. It now groups by vendor; the server refuses the old shape.
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/bills.js").read_text(
        encoding="utf-8"
    )
    save = js[js.index("async savePay(e)") :]
    save = save[: save.index("\n    },")]
    assert "firstBill" not in save
    assert "byVendor" in save and "vendor_id: vendorId" in save
    assert 'data-vendor="${b.vendor_id}"' in js
