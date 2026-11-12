"""A purchase line saved on a control before 2.31.14 (#821).

#785 refuses a new purchase line on Undeposited Funds or Inventory; a line
saved before keeps its account. Make Deposits no longer offers one on 1200
as a payment to deposit, the start-up pass counts them, and the inventory
valuation says when the ledger's Inventory differs from what the items are
worth.
"""

from decimal import Decimal

from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.undeposited_funds import (
    purchase_lines_on_controls,
    waiting_balance,
)
from tests.test_deposit_payments import _pending, _receive, acme  # noqa: F401
from tests.test_inventory import (
    _create_bill_for_inventory,
    _mk_tracked_item,
    _seed_vendor,
)
from tests.test_purchase_line_controls_785 import (  # noqa: F401  (vendor)
    TODAY,
    _expense,
    _saved_on_undeposited_funds,
    vendor,
)


def _move_expense_to(db_session, seed_accounts, number):
    """An expense entered on 6000, its line then moved to `number` with the
    ORM, the way a release before 2.31.14 left it."""
    txn_ids = [
        t.id for t in db_session.query(Transaction).filter_by(source_type="expense")
    ]
    db_session.query(TransactionLine).filter(
        TransactionLine.transaction_id.in_(txn_ids),
        TransactionLine.account_id == seed_accounts["6000"].id,
    ).update({"account_id": seed_accounts[number].id}, synchronize_session=False)
    db_session.commit()


def _journal(client, debit, credit, amount):
    r = client.post(
        "/api/journal",
        json={
            "date": TODAY,
            "description": "By hand",
            "lines": [
                {"account_id": debit.id, "debit": amount, "credit": 0},
                {"account_id": credit.id, "debit": 0, "credit": amount},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_make_deposits_offers_customer_money_and_no_purchase_line(
    client, db_session, seed_accounts, vendor, acme  # noqa: F811
):
    _saved_on_undeposited_funds(client, db_session, seed_accounts, vendor)
    assert _expense(client, seed_accounts).status_code == 201
    _move_expense_to(db_session, seed_accounts, "1200")
    payment = _receive(client, acme, 250, date=TODAY)
    entry = _journal(client, seed_accounts["1200"], seed_accounts["4000"], 70)

    listed = {p["transaction_id"] for p in _pending(client)}
    purchases = {
        t.id
        for t in db_session.query(Transaction).filter(
            Transaction.source_type.in_(("bill", "expense"))
        )
    }
    assert listed.isdisjoint(purchases)
    assert db_session.get(Payment, payment["id"]).transaction_id in listed
    assert entry["id"] in listed
    assert waiting_balance(db_session) == Decimal("320")


def test_the_start_up_count_takes_account_lines_and_not_item_lines(
    client, db_session, seed_accounts, vendor  # noqa: F811
):
    assert purchase_lines_on_controls(db_session) == 0
    # an inventory item's bill posts to 1300 by right
    item = _mk_tracked_item(db_session, seed_accounts)
    _create_bill_for_inventory(client, vendor.id, item.id, qty=2, unit_cost="10.00")
    assert purchase_lines_on_controls(db_session) == 0
    _saved_on_undeposited_funds(client, db_session, seed_accounts, vendor)
    assert _expense(client, seed_accounts).status_code == 201
    _move_expense_to(db_session, seed_accounts, "1300")
    assert purchase_lines_on_controls(db_session) == 2


def test_the_valuation_says_when_the_ledger_inventory_differs(
    client, db_session, seed_accounts
):
    item = _mk_tracked_item(db_session, seed_accounts)
    vendor = _seed_vendor(db_session)  # noqa: F811
    _create_bill_for_inventory(client, vendor.id, item.id, qty=5, unit_cost="10.00")
    body = client.get("/api/items/valuation").json()
    assert body["total_value"] == 50.0
    assert body["ledger_balance"] == 50.0
    assert body["difference"] == 0
    assert body["note"] is None

    _journal(client, seed_accounts["1300"], seed_accounts["3000"], 30)
    body = client.get("/api/items/valuation").json()
    assert body["total_value"] == 50.0
    assert body["ledger_balance"] == 80.0
    assert body["difference"] == 30.0
    assert body["note"] == (
        "The ledger's Inventory balance is $80.00, the valuation $50.00; the "
        "difference is on postings that moved no item."
    )
