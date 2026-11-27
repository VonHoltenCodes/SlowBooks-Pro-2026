"""#1237: payments deposited to a liability account.

A professional-services firm holds retainers in a liability ("Retainer
Payable") and applies them to invoices as payments deposited to it; a
construction company's pay application does the same with its deposit
applied to 2210 Customer Deposits. The server has always taken such a
payment and posts it DR the liability / CR A/R.

Edit (Replace) opened Record Payment with the payment's own Deposit To, but
the form listed bank accounts only and a fixed set of methods, so Save sent
a blank Deposit To and no method: the replacement sat in Undeposited Funds
and the liability was overstated. The form now keeps both
(tests/js/retainer_deposit_to_1237.test.cjs renders it); these replay what
it sends and check the books.
"""

from decimal import Decimal

import pytest

from app.models.accounts import Account
from app.models.payments import Payment
from app.models.transactions import TransactionLine
from app.services.undeposited_funds import waiting_items


def _ok(r, codes=(200, 201)):
    assert r.status_code in codes, r.text
    return r.json()


def _liability(client, number, name):
    return _ok(
        client.post(
            "/api/accounts",
            json={"account_number": number, "name": name, "account_type": "liability"},
        )
    )["id"]


def _invoice(client, customer_id, rate="300.00"):
    return _ok(
        client.post(
            "/api/invoices",
            json={
                "customer_id": customer_id,
                "date": "2026-10-15",
                "tax_rate": 0,
                "lines": [{"description": "Design work", "quantity": 1, "rate": rate}],
            },
        )
    )


def _hold(client, seed_accounts, account_id, amount="500.00"):
    """Money taken up front: DR Checking / CR the liability."""
    _ok(
        client.post(
            "/api/journal",
            json={
                "date": "2026-10-01",
                "description": "Retainer received",
                "lines": [
                    {
                        "account_id": seed_accounts["1000"].id,
                        "debit": amount,
                        "credit": "0",
                    },
                    {"account_id": account_id, "debit": "0", "credit": amount},
                ],
            },
        )
    )


def _lines(db_session, payment):
    return {
        (ln.account_id, Decimal(str(ln.debit)), Decimal(str(ln.credit)))
        for ln in db_session.query(TransactionLine).filter_by(
            transaction_id=payment.transaction_id
        )
    }


# --- Commit 1: Edit keeps the payment's own Deposit To and Method ---------


@pytest.mark.parametrize(
    "number,name,method",
    [
        ("2290", "Retainer Payable", "Retainer"),
        ("2210", "Customer Deposits", "deposit"),
    ],
)
def test_an_edit_keeps_a_liability_deposit_to_and_its_method(
    client, db_session, seed_accounts, seed_customer, number, name, method
):
    acct = _liability(client, number, name)
    _hold(client, seed_accounts, acct)
    inv = _invoice(client, seed_customer.id)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "300.00",
        "method": method,
        "deposit_to_account_id": acct,
        "allocations": [{"invoice_id": inv["id"], "amount": "300.00"}],
    }
    first = _ok(client.post("/api/payments", json=body))
    # what the corrected form sends on Save: every field as Edit filled it
    # in, the account and method kept, the memo fixed
    body["notes"] = "typo fixed"
    new = _ok(client.post(f"/api/payments/{first['id']}/replace", json=body))
    assert new["id"] != first["id"]
    assert new["deposit_to_account_id"] == acct
    assert new["method"] == method
    db_session.expire_all()
    pay = db_session.get(Payment, new["id"])
    assert _lines(db_session, pay) == {
        (acct, Decimal("300.00"), Decimal("0.00")),
        (seed_accounts["1100"].id, Decimal("0.00"), Decimal("300.00")),
    }
    # nothing waits on Make Deposits, and the liability still holds 200
    assert not [t for _ln, t in waiting_items(db_session) if t.id == pay.transaction_id]
    assert _ok(client.get("/api/deposits/pending")) == []
    assert db_session.get(Account, acct).balance == Decimal("200.00")


def test_what_the_old_form_sent_moved_it_to_undeposited_funds(
    client, db_session, seed_accounts, seed_customer
):
    """The server keeps whatever it is sent: the blank the old form sent
    is Undeposited Funds. This is why the form must send the account."""
    acct = _liability(client, "2290", "Retainer Payable")
    _hold(client, seed_accounts, acct)
    inv = _invoice(client, seed_customer.id)
    body = {
        "customer_id": seed_customer.id,
        "date": "2026-10-20",
        "amount": "300.00",
        "method": "Retainer",
        "deposit_to_account_id": acct,
        "allocations": [{"invoice_id": inv["id"], "amount": "300.00"}],
    }
    first = _ok(client.post("/api/payments", json=body))
    body.update(deposit_to_account_id=None, method=None)
    new = _ok(client.post(f"/api/payments/{first['id']}/replace", json=body))
    assert new["deposit_to_account_id"] is None
    db_session.expire_all()
    assert db_session.get(Account, acct).balance == Decimal("500.00")
    assert len(_ok(client.get("/api/deposits/pending"))) == 1
