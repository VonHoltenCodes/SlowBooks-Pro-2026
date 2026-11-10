"""A payment amount and each allocation amount are in whole cents (#747).

The payment APIs took amounts finer than a cent and rounded each one on its
own when stored. A one-cent payment applied as two allocations of 0.005 to
one invoice passed "allocations exceed payment" (0.005 + 0.005 = 0.01) and
stored 0.01 twice: two cents applied from one. Voiding it backed out the
stored two cents, and the invoice ended a cent short of where it started.
A single amount drifted too: 1234.50499999999999999999 booked 1234.51 as a
JSON number and 1234.50 as a string, and 0.004 booked a 0.00 payment. Such
an amount is now refused with a sentence naming the field, on every route
that takes one, before anything is written.
"""

from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.payments import Payment, PaymentAllocation


@pytest.fixture
def acme(db_session, seed_accounts):
    c = Customer(name="Acme Framing", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


@pytest.fixture
def vendor(client, seed_accounts):
    r = client.post("/api/vendors", json={"name": "Harbor Supply"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _invoice(client, cid, amount="100.00"):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": "2026-11-01",
            "tax_rate": 0,
            "lines": [{"description": "Framing", "quantity": 1, "rate": amount}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert client.post(f"/api/invoices/{inv['id']}/send").status_code == 200
    return inv


def _bill(client, seed_accounts, vendor_id, amount="100.00"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "bill_number": "B-747",
            "date": "2026-11-01",
            "lines": [
                {
                    "description": "Lumber",
                    "amount": amount,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _pay(client, cid, amount, allocations):
    return client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": "2026-11-09",
            "amount": amount,
            "allocations": allocations,
        },
    )


def _message(r):
    assert r.status_code == 422, r.text
    return r.json()["detail"][0]["message"]


def _paid(client, inv):
    got = client.get(f"/api/invoices/{inv['id']}").json()
    return Decimal(got["amount_paid"]), Decimal(got["balance_due"])


def test_two_half_cent_allocations_are_refused_and_nothing_is_written(
    client, db_session, acme
):
    """The issue's repro: a one-cent payment as two allocations of 0.005."""
    inv = _invoice(client, acme)
    before = _paid(client, inv)
    r = _pay(
        client,
        acme,
        "0.01",
        [
            {"invoice_id": inv["id"], "amount": "0.005"},
            {"invoice_id": inv["id"], "amount": "0.005"},
        ],
    )
    assert _message(r) == (
        "Allocation 1: An allocation amount is in whole cents; 0.005 isn't."
    )
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert db_session.query(PaymentAllocation).count() == 0
    assert _paid(client, inv) == before


@pytest.mark.parametrize(
    "amount, shown",
    [
        ("1234.50499999999999999999", "1234.50499999999999999999"),
        (1234.505, "1234.505"),
        ("0.004", "0.004"),
        (0.004, "0.004"),
    ],
)
def test_a_sub_cent_payment_amount_is_refused_as_a_string_or_a_number(
    client, db_session, acme, amount, shown
):
    r = _pay(client, acme, amount, [])
    assert _message(r) == f"A payment amount is in whole cents; {shown} isn't."
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0


def test_the_issues_number_and_string_are_both_refused(client, acme):
    """1234.50499999999999999999 as a JSON number (the reader makes it
    1234.505) and as a string: neither is booked."""
    for raw in ("1234.50499999999999999999", '"1234.50499999999999999999"'):
        r = client.post(
            "/api/payments",
            content=(
                '{"customer_id": %d, "date": "2026-11-09", "amount": %s}' % (acme, raw)
            ),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422, (raw, r.text)


@pytest.mark.parametrize("amount", ["25.10", 25.1, "25.100", 25, "1E+1"])
def test_a_whole_cent_amount_is_still_accepted(client, acme, amount):
    inv = _invoice(client, acme)
    r = _pay(client, acme, amount, [{"invoice_id": inv["id"], "amount": amount}])
    assert r.status_code == 201, r.text
    paid = Decimal(r.json()["amount"])
    assert paid == Decimal(str(amount))
    assert _paid(client, inv)[0] == paid


def test_apply_refuses_a_sub_cent_allocation(client, db_session, acme):
    inv = _invoice(client, acme)
    r = _pay(client, acme, "10.00", [])
    assert r.status_code == 201, r.text
    pay = r.json()
    r = client.post(
        f"/api/payments/{pay['id']}/apply",
        json={"allocations": [{"invoice_id": inv["id"], "amount": "0.005"}]},
    )
    assert _message(r) == (
        "Allocation 1: An allocation amount is in whole cents; 0.005 isn't."
    )
    db_session.expire_all()
    assert db_session.query(PaymentAllocation).count() == 0
    assert _paid(client, inv) == (Decimal("0"), Decimal("100.00"))


def test_replace_refuses_a_sub_cent_amount(client, db_session, acme):
    inv = _invoice(client, acme)
    r = _pay(client, acme, "10.00", [{"invoice_id": inv["id"], "amount": "10.00"}])
    assert r.status_code == 201, r.text
    pay = r.json()
    r = client.post(
        f"/api/payments/{pay['id']}/replace",
        json={
            "customer_id": acme,
            "date": "2026-11-09",
            "amount": "10.005",
            "allocations": [{"invoice_id": inv["id"], "amount": "10.00"}],
        },
    )
    assert _message(r) == "A payment amount is in whole cents; 10.005 isn't."
    db_session.expire_all()
    assert db_session.query(Payment).count() == 1
    assert not db_session.query(Payment).one().is_voided
    assert _paid(client, inv) == (Decimal("10.00"), Decimal("90.00"))


def _bill_pay(client, vendor_id, amount, allocations, path="/api/bill-payments"):
    return client.post(
        path,
        json={
            "vendor_id": vendor_id,
            "date": "2026-11-09",
            "amount": amount,
            "method": "check",
            "allocations": allocations,
        },
    )


@pytest.mark.parametrize("amount", ["50.005", 50.005])
def test_a_sub_cent_bill_payment_amount_is_refused(
    client, db_session, seed_accounts, vendor, amount
):
    from app.models.bills import BillPayment

    bill = _bill(client, seed_accounts, vendor["id"])
    r = _bill_pay(client, vendor["id"], amount, [{"bill_id": bill["id"], "amount": 50}])
    assert _message(r) == "A bill payment amount is in whole cents; 50.005 isn't."
    db_session.expire_all()
    assert db_session.query(BillPayment).count() == 0


@pytest.mark.parametrize("amount", ["0.005", 0.005])
def test_two_half_cent_bill_allocations_are_refused(
    client, db_session, seed_accounts, vendor, amount
):
    from app.models.bills import BillPayment

    bill = _bill(client, seed_accounts, vendor["id"])
    r = _bill_pay(
        client,
        vendor["id"],
        "0.01",
        [
            {"bill_id": bill["id"], "amount": amount},
            {"bill_id": bill["id"], "amount": amount},
        ],
    )
    assert _message(r) == (
        "Allocation 1: An allocation amount is in whole cents; 0.005 isn't."
    )
    db_session.expire_all()
    assert db_session.query(BillPayment).count() == 0
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("100.00")


def test_bill_payment_replace_refuses_a_sub_cent_allocation(
    client, db_session, seed_accounts, vendor
):
    from app.models.bills import BillPayment

    bill = _bill(client, seed_accounts, vendor["id"])
    r = _bill_pay(client, vendor["id"], 40, [{"bill_id": bill["id"], "amount": 40}])
    assert r.status_code == 201, r.text
    pay = r.json()
    r = _bill_pay(
        client,
        vendor["id"],
        40,
        [{"bill_id": bill["id"], "amount": 39.999}],
        path=f"/api/bill-payments/{pay['id']}/replace",
    )
    assert _message(r) == (
        "Allocation 1: An allocation amount is in whole cents; 39.999 isn't."
    )
    db_session.expire_all()
    assert db_session.query(BillPayment).count() == 1
    assert not db_session.query(BillPayment).one().is_voided


def test_a_whole_cent_bill_payment_is_still_accepted(client, seed_accounts, vendor):
    bill = _bill(client, seed_accounts, vendor["id"])
    r = _bill_pay(
        client, vendor["id"], "33.30", [{"bill_id": bill["id"], "amount": 33.3}]
    )
    assert r.status_code == 201, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert Decimal(got["balance_due"]) == Decimal("66.70")


@pytest.mark.parametrize("amount", ["0.005", 0.005])
def test_batch_payment_refuses_a_sub_cent_allocation(client, db_session, acme, amount):
    inv = _invoice(client, acme)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-11-09",
            "method": "check",
            "allocations": [
                {"customer_id": acme, "invoice_id": inv["id"], "amount": amount},
                {"customer_id": acme, "invoice_id": inv["id"], "amount": amount},
            ],
        },
    )
    assert _message(r) == (
        "Allocation 1: An allocation amount is in whole cents; 0.005 isn't."
    )
    db_session.expire_all()
    assert db_session.query(Payment).count() == 0
    assert _paid(client, inv) == (Decimal("0"), Decimal("100.00"))


def test_batch_payment_still_takes_whole_cents(client, db_session, acme):
    inv = _invoice(client, acme)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-11-09",
            "method": "check",
            "allocations": [
                {"customer_id": acme, "invoice_id": inv["id"], "amount": 12.34}
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert _paid(client, inv) == (Decimal("12.34"), Decimal("87.66"))
