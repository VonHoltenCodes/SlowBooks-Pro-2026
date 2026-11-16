"""Home cards after the 2.32.0 round-10 gate.

#958 (W-166): the Recent card's rows carry each document's face, as the
Overdue card's do, so the page's label branch has something to print.

#960 (NEW-243): only a nonprofit has pledges; a business counts an invoice
flagged as a pledge among its invoices."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus


def _invoice(db, customer_id, number, amount, *, pledge=False, past_due=True):
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        customer_id=customer_id,
        status=InvoiceStatus.SENT,
        date=today - timedelta(days=40),
        due_date=today - timedelta(days=10 if past_due else -10),
        total=Decimal(amount),
        balance_due=Decimal(amount),
        is_pledge=pledge,
    )
    db.add(inv)
    db.commit()
    return inv


def _customer(db, name):
    c = Customer(name=name, is_active=True)
    db.add(c)
    db.commit()
    return c


def test_a_business_counts_a_flagged_invoice_among_its_invoices(
    client, db_session, seed_accounts
):
    c = _customer(db_session, "Ada Okafor")
    _invoice(db_session, c.id, "3001", "222.00", pledge=True)
    d = client.get("/api/dashboard/data?ids=receivables,recent_invoices").json()
    assert d["receivables"]["overdue_count"] == 1
    assert d["receivables"]["overdue_pledges"] == 0
    assert d["receivables"]["overdue_invoices"] == 1
    assert [i["label"] for i in d["recent_invoices"]["items"]] == ["Invoice"]


def test_a_nonprofit_counts_the_same_invoice_as_a_pledge_and_faces_the_recent_rows(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    c = _customer(db_session, "Grant Foundation")
    _invoice(db_session, c.id, "P-1", "100.00", pledge=True)
    _invoice(db_session, c.id, "F-1", "83.00")
    d = client.get("/api/dashboard/data?ids=receivables,recent_invoices").json()
    assert d["receivables"]["overdue_count"] == 2
    assert d["receivables"]["overdue_pledges"] == 1
    assert d["receivables"]["overdue_invoices"] == 1
    labels = {i["invoice_number"]: i["label"] for i in d["recent_invoices"]["items"]}
    assert labels == {"P-1": "Pledge", "F-1": "Invoice"}
