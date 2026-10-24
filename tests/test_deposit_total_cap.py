"""A typed deposit total is capped at Undeposited Funds (#494).

Make Deposits with no payments ticked took `total` as given and posted it,
so a $5,000 figure banked against a single $100 payment waiting to be
deposited. The ticked-payments path already refused a total that disagreed
with what was waiting; the no-ticket path now caps the typed total at the
Undeposited Funds balance the same way.
"""

from app.models.contacts import Customer


def _customer(db_session, name="Acme Diner"):
    c = Customer(name=name, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _receive_payment(client, customer_id, amount, date="2026-09-10"):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer_id,
            "date": date,
            "amount": amount,
            "method": "Check",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _checking_account_id(seed_accounts):
    for acct in seed_accounts.values():
        if acct.bank_kind == "bank":
            return acct.id
    raise AssertionError("no bank account in seed chart")


def test_typed_total_above_undeposited_funds_is_refused(
    client, db_session, seed_accounts
):
    cust = _customer(db_session)
    _receive_payment(client, cust.id, 100)  # only $100 waiting in UF
    bank_id = _checking_account_id(seed_accounts)

    r = client.post(
        "/api/deposits",
        json={"deposit_to_account_id": bank_id, "date": "2026-09-15", "total": 5000},
    )
    assert r.status_code == 400, r.text
    assert "Undeposited" in r.json()["detail"]


def test_typed_total_within_undeposited_funds_posts(client, db_session, seed_accounts):
    cust = _customer(db_session)
    _receive_payment(client, cust.id, 100)
    bank_id = _checking_account_id(seed_accounts)

    r = client.post(
        "/api/deposits",
        json={"deposit_to_account_id": bank_id, "date": "2026-09-15", "total": 100},
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 100.0
