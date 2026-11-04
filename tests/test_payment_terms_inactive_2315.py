"""An inactive payment term is not applied by name (2.31.5 gate W-102, #664).

The forms offer only active terms; the API matched typed terms to the whole
list, so an invoice or bill posted with an inactive term's name was tied to
it and given its discount. A new document (invoice, bill, recurring or
memorized schedule) naming one is now refused, 422 naming the term and that
it is inactive. A document saved under the term before it went inactive
keeps its tie: re-saving it, its terms sent back or retyped, is not refused.
"""

import pytest

SENTENCE = (
    '"2% 10 Net 30" is inactive on the terms list, so nothing new can be '
    "saved under it. Choose other terms, or an administrator can make it "
    "active again under Settings > Payment Terms."
)


@pytest.fixture
def term(client):
    r = client.post(
        "/api/payment-terms",
        json={
            "name": "2% 10 Net 30",
            "net_days": 30,
            "discount_percent": "2",
            "discount_days": 10,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _deactivate(client, term):
    r = client.put(f"/api/payment-terms/{term['id']}", json={"is_active": False})
    assert r.status_code == 200, r.text


def _invoice(client, customer_id, terms="2% 10 Net 30"):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-10-05",
            "terms": terms,
            "lines": [{"description": "Service", "quantity": 1, "rate": "100.00"}],
        },
    )


@pytest.fixture
def vendor(client, seed_accounts):
    r = client.post(
        "/api/vendors",
        json={
            "name": "Gulf Supply",
            "terms": "Net 30",
            "default_expense_account_id": seed_accounts["6000"].id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, number, terms="2% 10 Net 30"):
    return client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": number,
            "date": "2026-10-05",
            "terms": terms,
            "lines": [{"description": "Lumber", "quantity": 1, "rate": "500.00"}],
        },
    )


def test_a_new_invoice_naming_an_inactive_term_is_refused(
    client, seed_accounts, seed_customer, term
):
    _deactivate(client, term)
    r = _invoice(client, seed_customer.id)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == SENTENCE
    # matched as the list matches: case and spacing aside
    assert _invoice(client, seed_customer.id, "2% 10 net30").status_code == 422
    assert client.get("/api/invoices").json() == []


def test_a_new_bill_naming_an_inactive_term_is_refused(
    client, seed_accounts, vendor, term
):
    _deactivate(client, term)
    r = _bill(client, vendor["id"], "B-1")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == SENTENCE


def test_an_invoice_saved_under_it_before_keeps_its_tie(
    client, seed_accounts, seed_customer, term
):
    r = _invoice(client, seed_customer.id)
    assert r.status_code == 201, r.text
    inv = r.json()
    _deactivate(client, term)
    # sent back unchanged, as the form sends it
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "2% 10 Net 30"})
    assert r.status_code == 200, r.text
    # retyped in another case: the same entry it is already tied to
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "2% 10 NET 30"})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/invoices/{inv['id']}").json()
    assert got["discount_date"] == "2026-10-15"


def test_an_invoice_retyped_onto_an_inactive_term_is_refused(
    client, seed_accounts, seed_customer, term
):
    inv = _invoice(client, seed_customer.id, "Net 30").json()
    _deactivate(client, term)
    r = client.put(f"/api/invoices/{inv['id']}", json={"terms": "2% 10 Net 30"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == SENTENCE


def test_a_bill_saved_under_it_before_keeps_its_tie(
    client, seed_accounts, vendor, term
):
    r = _bill(client, vendor["id"], "B-2")
    assert r.status_code == 201, r.text
    bill = r.json()
    _deactivate(client, term)
    r = client.put(f"/api/bills/{bill['id']}", json={"terms": "2% 10 Net 30"})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert got["discount_date"] == "2026-10-15"


def test_a_new_recurring_schedule_naming_an_inactive_term_is_refused(
    client, seed_accounts, seed_customer, term
):
    _deactivate(client, term)
    r = client.post(
        "/api/recurring",
        json={
            "customer_id": seed_customer.id,
            "frequency": "monthly",
            "start_date": "2026-11-01",
            "terms": "2% 10 Net 30",
            "lines": [{"description": "Retainer", "quantity": 1, "rate": "100.00"}],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == SENTENCE
