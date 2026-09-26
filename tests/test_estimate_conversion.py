"""Estimate -> invoice makes the invoice a new one would be (2.17.3
exploratory: macbase1 F21, skytech W-L10).

The converted invoice kept the estimate's date (QuickBooks dates it the day
it is converted), took the company's terms where a direct invoice takes the
customer's, printed "BILL TO" with no address because an estimate was never
addressed, and lost the default invoice notes. It is now dated today and due
by the customer's terms; the estimate is addressed to its customer when it
is saved, and the invoice takes the estimate's bill-to (else the
customer's), the customer's ship-to, and the estimate's notes (else the
default invoice notes).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest


@pytest.fixture
def customer(client):
    r = client.post(
        "/api/customers",
        json={
            "name": "Salt & Pine Catering Co.",
            "terms": "Net 15",
            "bill_address1": "12 Dock Rd",
            "bill_city": "Port Alder",
            "bill_state": "OR",
            "bill_zip": "97000",
            "ship_address1": "Pier 4",
            "ship_city": "Port Alder",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _estimate(client, customer_id, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-01-10",
        "tax_rate": 0,
        "lines": [{"description": "Wedding cake", "quantity": 1, "rate": 450}],
    }
    body.update(extra)
    r = client.post("/api/estimates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _convert(client, estimate_id):
    r = client.post(f"/api/estimates/{estimate_id}/convert")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_invoice_is_dated_today_and_due_by_the_customers_terms(
    client, seed_accounts, customer
):
    inv = _convert(client, _estimate(client, customer["id"])["id"])
    today = date.today()
    assert inv["date"] == today.isoformat()
    assert inv["terms"] == "Net 15"
    assert inv["due_date"] == (today + timedelta(days=15)).isoformat()


def test_due_on_receipt_is_due_today(client, seed_accounts):
    cust = client.post(
        "/api/customers", json={"name": "Counter Co", "terms": "Due on Receipt"}
    ).json()
    inv = _convert(client, _estimate(client, cust["id"])["id"])
    assert inv["due_date"] == date.today().isoformat()


def test_the_invoice_is_addressed_to_the_customer(
    client, db_session, seed_accounts, customer
):
    from app.models.estimates import Estimate

    est = _estimate(client, customer["id"])
    stored = db_session.get(Estimate, est["id"])
    assert (stored.bill_address1, stored.bill_city, stored.bill_zip) == (
        "12 Dock Rd",
        "Port Alder",
        "97000",
    )
    inv = _convert(client, est["id"])
    assert (inv["bill_address1"], inv["bill_city"], inv["bill_state"]) == (
        "12 Dock Rd",
        "Port Alder",
        "OR",
    )
    assert (inv["ship_address1"], inv["ship_city"]) == ("Pier 4", "Port Alder")
    page = client.get(f"/api/invoices/{inv['id']}/print-preview").text
    assert "12 Dock Rd" in page


def test_an_estimate_saved_without_an_address_takes_the_customers(
    client, db_session, seed_accounts, customer
):
    from app.models.estimates import Estimate

    est = _estimate(client, customer["id"])
    stored = db_session.get(Estimate, est["id"])
    for part in ("address1", "address2", "city", "state", "zip"):
        setattr(stored, f"bill_{part}", None)  # as every estimate before this
    db_session.commit()
    inv = _convert(client, est["id"])
    assert (inv["bill_address1"], inv["bill_zip"]) == ("12 Dock Rd", "97000")


def test_changing_the_customer_readdresses_the_estimate(
    client, db_session, seed_accounts, customer
):
    from app.models.estimates import Estimate

    other = client.post(
        "/api/customers",
        json={"name": "Tidewater Cafe", "bill_address1": "1 Harbor Way"},
    ).json()
    est = _estimate(client, customer["id"])
    r = client.put(f"/api/estimates/{est['id']}", json={"customer_id": other["id"]})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    stored = db_session.get(Estimate, est["id"])
    assert (stored.bill_address1, stored.bill_city) == ("1 Harbor Way", None)


def test_the_default_invoice_notes_apply(client, seed_accounts, customer):
    inv = _convert(client, _estimate(client, customer["id"])["id"])
    assert inv["notes"] == "Thank you for your business."
    kept = _convert(
        client, _estimate(client, customer["id"], notes="Delivered Saturday")["id"]
    )
    assert kept["notes"] == "Delivered Saturday"


def test_the_posting_is_dated_with_the_invoice(
    client, db_session, seed_accounts, customer
):
    from app.models.invoices import Invoice
    from app.models.transactions import Transaction

    inv = _convert(client, _estimate(client, customer["id"])["id"])
    txn = db_session.get(Transaction, db_session.get(Invoice, inv["id"]).transaction_id)
    assert txn.date == date.today()
    assert Decimal(str(inv["total"])) == Decimal("450.00")
