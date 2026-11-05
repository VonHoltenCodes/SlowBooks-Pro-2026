"""#709: Deposit To names Undeposited Funds.

Receive Payment's Deposit To offered "--" and the bank accounts; the blank
option is what Undeposited Funds means, but nothing said so. The forms
(payments.js, sales_receipts.js, batch_payments.js) now label it
"Undeposited Funds (deposit later)", first and the default, when the chart
has the 1200 control account; tests/js/deposit_to_undeposited.test.cjs
renders each. These pin the two things the label rests on: the account
list tells the page which account is Undeposited Funds, and a payment
saved with the blank option posts there. Nothing about the posting changed.
"""

from decimal import Decimal

from app.models.payments import Payment
from app.models.transactions import TransactionLine


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-11-02",
            "lines": [{"description": "Mowing", "quantity": 1, "rate": "250.00"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_the_account_list_names_the_undeposited_funds_control_account(
    client, seed_accounts
):
    """The forms look for the account by is_control and number 1200."""
    accounts = client.get("/api/accounts").json()
    uf = [a for a in accounts if a["is_control"] and a["account_number"] == "1200"]
    assert [a["name"] for a in uf] == ["Undeposited Funds"]
    assert uf[0]["bank_kind"] != "bank"  # not among the bank accounts listed after it


def test_a_payment_saved_with_the_blank_deposit_to_posts_to_undeposited_funds(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    r = client.post(
        "/api/payments",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-11-04",
            "amount": "250.00",
            # what the form sends for "Undeposited Funds (deposit later)"
            "deposit_to_account_id": None,
            "allocations": [{"invoice_id": inv["id"], "amount": "250.00"}],
        },
    )
    assert r.status_code == 201, r.text
    payment = db_session.get(Payment, r.json()["id"])
    assert payment.deposit_to_account_id is None
    debit = (
        db_session.query(TransactionLine)
        .filter_by(
            transaction_id=payment.transaction_id,
            account_id=seed_accounts["1200"].id,
        )
        .one()
    )
    assert Decimal(str(debit.debit)) == Decimal("250.00")
    # and it is waiting on Make Deposits
    waiting = client.get("/api/deposits/pending").json()
    assert any(
        Decimal(str(w.get("amount"))) == Decimal("250.00") for w in waiting
    ), waiting


def test_a_batch_payment_with_the_blank_deposit_to_posts_to_undeposited_funds(
    client, db_session, seed_accounts, seed_customer
):
    inv = _invoice(client, seed_customer.id)
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-11-04",
            "deposit_to_account_id": None,
            "method": "check",
            "allocations": [
                {
                    "customer_id": seed_customer.id,
                    "invoice_id": inv["id"],
                    "amount": "250.00",
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    lines = (
        db_session.query(TransactionLine)
        .filter_by(account_id=seed_accounts["1200"].id)
        .all()
    )
    assert sum((Decimal(str(x.debit)) for x in lines), Decimal("0")) == Decimal(
        "250.00"
    )
