"""Refusals that say which one, and in the same words (#813).

- A bill's and a bill payment's posting refused by the journal's Void named
  the kind ("Void the bill itself"), not which bill or which check.
- A bank rule's class and job were refused in words of its own ("Class not
  found", "pick a live one") where the feed and the transfers said "Class
  99999 not found" and "restore it under Classes or choose another".
- "N short" on a sale past on hand was the whole count below zero, not this
  sale's part of it."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.transactions import Transaction
from app.services.dimensions import require_dimensions
from app.services.inventory_hooks import oversell_refusal


def _posting(db_session, source_type, source_id):
    return (
        db_session.query(Transaction)
        .filter(
            Transaction.source_type == source_type,
            Transaction.source_id == source_id,
        )
        .one()
    )


def test_a_bills_and_a_bill_payments_void_refusal_name_the_document(
    client, db_session, seed_accounts
):
    vendor = client.post("/api/vendors", json={"name": "Harbor Supply"}).json()
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "V-5501",
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Supplies",
                    "amount": 100,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    bill = bill.json()
    pay = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-09-10",
            "amount": 100,
            "method": "check",
            "check_number": "2201",
            "allocations": [{"bill_id": bill["id"], "amount": 100}],
        },
    )
    assert pay.status_code == 201, pay.text
    pay = pay.json()

    r = client.post(f"/api/journal/{_posting(db_session, 'bill', bill['id']).id}/void")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "This is bill V-5501's posting, not a journal entry of its own. "
        "Void bill V-5501 itself; that reverses this posting with it."
    )
    r = client.post(
        f"/api/journal/{_posting(db_session, 'bill_payment', pay['id']).id}/void"
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "This is the posting of the bill payment by check 2201, not a journal "
        "entry of its own. Void the bill payment by check 2201 itself; that "
        "reverses this posting with it."
    )


def _shared_refusal(db_session, **dims):
    with pytest.raises(HTTPException) as caught:
        require_dimensions(db_session, **dims)
    return caught.value.status_code, caught.value.detail


def test_a_bank_rule_refuses_a_class_and_a_job_as_the_feed_does(
    client, db_session, seed_accounts, seed_customer
):
    base = {"name": "R", "pattern": "X"}
    for dims in ({"class_id": 99999}, {"job_id": 99999}):
        r = client.post("/api/bank-rules", json={**base, **dims})
        assert (r.status_code, r.json()["detail"]) == _shared_refusal(
            db_session, **dims
        )
    field = client.post("/api/classes", json={"name": "Field"}).json()
    client.put(f"/api/classes/{field['id']}", json={"is_archived": True})
    r = client.post("/api/bank-rules", json={**base, "class_id": field["id"]})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "Class Field is archived; restore it under Classes or choose another."
    )


def test_short_is_this_sales_part():
    widget = SimpleNamespace(name="Widget")
    # from 2 on hand, a sale of 6 is 4 short
    said = oversell_refusal(
        [{"item": widget, "on_hand": Decimal("2"), "after": Decimal("-4")}]
    )
    assert said.startswith("Widget: 2 on hand; this sale takes it to -4, 4 short.")
    # from -2, a sale of 3 is 3 short, not the 5 below zero
    said = oversell_refusal(
        [{"item": widget, "on_hand": Decimal("-2"), "after": Decimal("-5")}]
    )
    assert said.startswith("Widget: -2 on hand; this sale takes it to -5, 3 short.")
