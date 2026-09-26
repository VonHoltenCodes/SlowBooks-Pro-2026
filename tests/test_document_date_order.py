"""Dates that cannot be (2.17.3 exploratory, skytech W-L4).

An invoice due before its own date, and a recurring schedule that ends
before it starts, were both accepted and saved. Both are refused now with a
sentence naming the two dates and what to pick.
"""

import pytest


def _invoice(client, customer_id, **extra):
    body = {
        "customer_id": customer_id,
        "date": "2026-09-10",
        "tax_rate": 0,
        "lines": [{"description": "Banner", "quantity": 1, "rate": 120}],
    }
    body.update(extra)
    return client.post("/api/invoices", json=body)


def test_an_invoice_due_before_its_date_is_refused(
    client, db_session, seed_accounts, seed_customer
):
    from app.models.invoices import Invoice

    r = _invoice(client, seed_customer.id, due_date="2026-09-01")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "The due date (Sep 1, 2026) is before the invoice date (Sep 10, 2026). "
        "Pick a due date on or after the invoice date."
    )
    assert db_session.query(Invoice).count() == 0
    # due the same day is fine
    assert _invoice(client, seed_customer.id, due_date="2026-09-10").status_code == 201


def test_an_edit_cannot_put_the_due_date_before_the_date(
    client, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id).json()
    for change in ({"due_date": "2026-09-09"}, {"date": "2026-11-01"}):
        r = client.put(f"/api/invoices/{inv['id']}", json=change)
        assert r.status_code == 400, (change, r.text)
        assert "before the invoice date" in r.json()["detail"]
    after = client.get(f"/api/invoices/{inv['id']}").json()
    assert (after["date"], after["due_date"]) == ("2026-09-10", "2026-10-10")
    # moving both together, or clearing the due date, is fine
    r = client.put(
        f"/api/invoices/{inv['id']}",
        json={"date": "2026-11-01", "due_date": "2026-12-01"},
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/invoices/{inv['id']}", json={"due_date": None})
    assert r.status_code == 200 and r.json()["due_date"] == "2026-12-01"


@pytest.fixture
def schedule_body(seed_customer):
    return {
        "customer_id": seed_customer.id,
        "frequency": "monthly",
        "start_date": "2026-10-01",
        "lines": [{"description": "Retainer", "quantity": 1, "rate": 500}],
    }


def test_a_schedule_ending_before_it_starts_is_refused(
    client, db_session, seed_accounts, schedule_body
):
    from app.models.recurring import RecurringInvoice

    r = client.post("/api/recurring", json=dict(schedule_body, end_date="2026-09-01"))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "The end date (Sep 1, 2026) is before the start date (Oct 1, 2026). Pick "
        "an end date on or after the start date, or leave it blank for no end."
    )
    assert db_session.query(RecurringInvoice).count() == 0

    rec = client.post("/api/recurring", json=schedule_body).json()
    r = client.put(f"/api/recurring/{rec['id']}", json={"end_date": "2026-09-30"})
    assert r.status_code == 400, r.text
    r = client.put(f"/api/recurring/{rec['id']}", json={"end_date": "2027-09-30"})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/recurring/{rec['id']}", json={"end_date": None})
    assert r.status_code == 200 and r.json()["end_date"] is None
