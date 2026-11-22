"""A nonprofit's Home count names what it counts (#952, 2.32.0 round 9,
NEW-237, the owner's call): pledges and invoices apart, since a program-fee
invoice is an Invoice on its own face and only a flagged pledge is a Pledge;
the Overdue card's rows carry each document's face."""

from datetime import date, timedelta
from decimal import Decimal

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus


def _past_due(db, customer_id, number, amount, *, pledge=False):
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        customer_id=customer_id,
        status=InvoiceStatus.SENT,
        date=today - timedelta(days=40),
        due_date=today - timedelta(days=10),
        total=Decimal(amount),
        balance_due=Decimal(amount),
        is_pledge=pledge,
    )
    db.add(inv)
    db.commit()
    return inv


def test_the_count_splits_pledges_from_invoices_on_a_nonprofit(
    client, db_session, seed_accounts
):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    donor = Customer(name="Grant Foundation", is_active=True)
    db_session.add(donor)
    db_session.commit()
    _past_due(db_session, donor.id, "P-1", "100.00", pledge=True)
    _past_due(db_session, donor.id, "P-2", "200.00", pledge=True)
    _past_due(db_session, donor.id, "F-1", "83.00")
    d = client.get("/api/dashboard/data?ids=receivables,overdue_invoices").json()
    assert d["receivables"]["overdue_count"] == 3
    assert d["receivables"]["overdue_pledges"] == 2
    assert d["receivables"]["overdue_invoices"] == 1
    labels = {i["invoice_number"]: i["label"] for i in d["overdue_invoices"]["items"]}
    assert labels == {"P-1": "Pledge", "P-2": "Pledge", "F-1": "Invoice"}


def test_a_business_reads_invoices_as_before(client, db_session, seed_accounts):
    cust = Customer(name="Ada Okafor", is_active=True)
    db_session.add(cust)
    db_session.commit()
    _past_due(db_session, cust.id, "3001", "222.00")
    d = client.get("/api/dashboard/data?ids=receivables,overdue_invoices").json()
    assert d["receivables"]["overdue_count"] == 1
    assert (
        d["receivables"]["overdue_pledges"] == 0
        and d["receivables"]["overdue_invoices"] == 1
    )
    assert d["overdue_invoices"]["items"][0]["label"] == "Invoice"


def test_the_page_words_the_split_only_on_a_nonprofit():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/dashboard.js").read_text(
        encoding="utf-8"
    )
    rec = js.split("receivables(d)")[1].split("active_customers(d)")[0]
    assert "Terms.isNonprofit() && (d.overdue_pledges || d.overdue_invoices)" in rec
    assert "word(d.overdue_pledges, 'pledge', 'pledges')" in rec
    assert "word(d.overdue_invoices, 'invoice', 'invoices')" in rec
    over = js.split("overdue_invoices(d)")[1].split("bank_balances(d)")[0]
    assert "Terms.isNonprofit() && i.label" in over
