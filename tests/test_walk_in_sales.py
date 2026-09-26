"""A counter sale needs no customer (2.17.3 exploratory, macbase1
suggestion S-c).

A sales receipt required a customer, so a bakery selling a loaf over the
counter had to invent one for every sale. Left blank, a sales receipt is
now recorded against a built-in walk-in customer, created the first time it
is needed: an ordinary customer, so the sale posts, deposits and reports
like any other.
"""

from decimal import Decimal
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _receipt(client, **extra):
    body = {
        "date": "2026-09-26",
        "tax_rate": 0,
        "method": "Cash",
        "lines": [{"description": "Sourdough Loaf", "quantity": 1, "rate": 8.5}],
    }
    body.update(extra)
    return client.post("/api/sales-receipts", json=body)


def test_a_counter_sale_goes_to_the_walk_in_customer(client, db_session, seed_accounts):
    from app.models.contacts import Customer
    from app.models.invoices import Invoice, InvoiceStatus

    first = _receipt(client)
    assert first.status_code == 201, first.text
    second = _receipt(client)
    assert second.status_code == 201, second.text

    walk_ins = db_session.query(Customer).filter_by(name="Walk-in Customer").all()
    assert len(walk_ins) == 1
    walk_in = walk_ins[0]
    for r in (first, second):
        body = r.json()
        assert body["invoice"]["customer_name"] == "Walk-in Customer"
        assert body["payment"]["customer_id"] == walk_in.id
        inv = db_session.get(Invoice, body["invoice"]["id"])
        assert inv.status == InvoiceStatus.PAID and inv.balance_due == Decimal("0.00")
    assert walk_in.terms == "Due on Receipt"


def test_a_renamed_walk_in_customer_is_still_the_one(client, db_session, seed_accounts):
    from app.models.contacts import Customer

    first = _receipt(client).json()
    cid = first["payment"]["customer_id"]
    r = client.put(f"/api/customers/{cid}", json={"name": "Cash Sales"})
    assert r.status_code == 200, r.text
    again = _receipt(client).json()
    assert again["payment"]["customer_id"] == cid
    assert db_session.query(Customer).count() == 1


def test_a_nonprofits_is_an_anonymous_donor(client, db_session, seed_accounts):
    r = client.put("/api/settings", json={"company_type": "nonprofit"})
    assert r.status_code == 200, r.text
    r = _receipt(client)
    assert r.status_code == 201, r.text
    assert r.json()["invoice"]["customer_name"] == "Anonymous Donor"


def test_a_named_customer_still_works(client, seed_accounts, seed_customer):
    r = _receipt(client, customer_id=seed_customer.id)
    assert r.status_code == 201, r.text
    assert r.json()["invoice"]["customer_name"] == "Test Customer"


def test_the_form_offers_the_walk_in_customer_first():
    src = (JS / "sales_receipts.js").read_text(encoding="utf-8")
    select = src[src.index('<select name="customer_id" id="sr-customer-select"') :]
    select = select[: select.index("</select>")]
    assert " required" not in select
    assert '<option value="">${walkInLabel}</option>' in select
    save = src[src.index("    async save(e) {") :]
    assert (
        "customer_id: form.customer_id.value ? parseInt(form.customer_id.value) : null"
        in save
    )
    assert "if (form.customer_id.value === '__new__')" in save
