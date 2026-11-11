"""A bill line posts to a balance-sheet account (#766).

A loan payment entered as a bill, the way QuickBooks' Enter Bills allows:
the principal against the loan (a long-term liability) and the interest
against an expense. The server took such a line already (#745 showed the
API doing it for expenses); the Account picker on the bill, expense and
card-charge forms now offers those accounts too, under Other accounts
(tests/js/purchase_accounts_other_766.test.cjs). This is the posting end:
the bill, its payment, and the edit that keeps the account.
"""

from datetime import date
from decimal import Decimal

from app.models.contacts import Vendor


def _tb(client):
    tb = client.get(
        "/api/reports/trial-balance?start_date=2000-01-01&end_date=2099-12-31"
    ).json()
    return {i["account_number"]: Decimal(str(i["net_balance"])) for i in tb["items"]}


def _vendor(db_session, seed_accounts):
    v = Vendor(
        name="First Ridge Bank",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v


def test_a_loan_payment_bill_splits_between_the_loan_and_the_interest(
    client, db_session, seed_accounts
):
    v = _vendor(db_session, seed_accounts)
    loan, interest = seed_accounts["2400"], seed_accounts["6000"]
    before = _tb(client)
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": date.today().isoformat(),
            "lines": [
                {"description": "Principal", "account_id": loan.id, "amount": 500},
                {"description": "Interest", "account_id": interest.id, "amount": 20},
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    assert Decimal(str(bill["total"])) == Decimal("520")
    # each line keeps the account it was given, the liability included
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert {ln["description"]: ln["account_id"] for ln in got["lines"]} == {
        "Principal": loan.id,
        "Interest": interest.id,
    }
    after = _tb(client)
    # (the trial balance's net is debit less credit) the principal is a 500
    # debit on the loan, which it owes 500 less; the interest a 20 expense;
    # and the vendor is owed 520
    assert after["2400"] - before.get("2400", Decimal(0)) == Decimal("500")
    assert after["6000"] - before.get("6000", Decimal(0)) == Decimal("20")
    assert after["2000"] - before.get("2000", Decimal(0)) == Decimal("-520")
    # paying the bill clears the payable from the bank; the loan stays paid down
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": date.today().isoformat(),
            "amount": 520,
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill["id"], "amount": 520}],
        },
    )
    assert r.status_code == 201, r.text
    paid = _tb(client)
    assert paid["2000"] == before.get("2000", Decimal(0))
    assert paid["1000"] - before.get("1000", Decimal(0)) == Decimal("-520")
    assert paid["2400"] - before.get("2400", Decimal(0)) == Decimal("500")
    assert client.get(f"/api/bills/{bill['id']}").json()["status"] == "paid"


def test_editing_the_bill_keeps_the_loan_account_on_its_line(
    client, db_session, seed_accounts
):
    v = _vendor(db_session, seed_accounts)
    loan, interest = seed_accounts["2400"], seed_accounts["6000"]
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": date.today().isoformat(),
            "lines": [
                {"description": "Principal", "account_id": loan.id, "amount": 500},
                {"description": "Interest", "account_id": interest.id, "amount": 20},
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    # a memo change on the interest line re-posts the bill with the same accounts
    lines = [
        {"description": "Principal", "account_id": loan.id, "amount": 500},
        {"description": "Interest, November", "account_id": interest.id, "amount": 20},
    ]
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert {ln["description"]: ln["account_id"] for ln in got["lines"]} == {
        "Principal": loan.id,
        "Interest, November": interest.id,
    }
    tb = _tb(client)
    assert tb["2400"] == Decimal("500") and tb["2000"] == Decimal("-520")
