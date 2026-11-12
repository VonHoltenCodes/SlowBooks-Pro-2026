"""Make Deposits lists only customer money; the start-up and the Inventory
Valuation say where a purchase line still sits on a control (#821).

Since 2.31.14 (#785) a bill, expense or card-charge line can't post to
Undeposited Funds or Inventory. A line saved before that keeps its account:
a bill line on 1200 is a debit there, which is what a payment waiting for the
bank looks like, so Make Deposits offered it and depositing it raised the
bank by money nobody paid (the 2.31.13 gate's W-143). A line on 1300 raised
the Balance Sheet's Inventory while the valuation, which counts items, did
not move. The lines are written here the way W-143 did: entered on an
ordinary account, then moved to the control with the ORM, since the API
refuses them now.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.bills import BillLine
from app.models.contacts import Customer, Vendor
from app.models.items import Item, ItemType
from app.models.transactions import Transaction, TransactionLine
from app.services import purchase_posting
from app.services.purchase_posting import (
    purchase_lines_on_controls,
    purchase_lines_sentence,
)

TODAY = date.today().isoformat()


@pytest.fixture
def vendor(db_session, seed_accounts):
    v = Vendor(
        name="Ridge Supply",
        is_active=True,
        default_expense_account_id=seed_accounts["6000"].id,
    )
    db_session.add(v)
    db_session.commit()
    return v


@pytest.fixture
def customer(db_session):
    c = Customer(name="Corner Diner", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _line(account, amount=100, description="Deposit"):
    return {
        "description": description,
        "account_id": account.id,
        "quantity": 1,
        "rate": amount,
    }


def _move_to(db_session, source_type, old_id, new_id, bill_id=None):
    """Move a saved purchase's line and its journal line from `old_id` to
    `new_id`, as a release before 2.31.14 let it be saved."""
    if bill_id is not None:
        db_session.query(BillLine).filter_by(bill_id=bill_id, account_id=old_id).update(
            {"account_id": new_id}
        )
    txn_ids = [
        t.id for t in db_session.query(Transaction).filter_by(source_type=source_type)
    ]
    db_session.query(TransactionLine).filter(
        TransactionLine.transaction_id.in_(txn_ids),
        TransactionLine.account_id == old_id,
    ).update({"account_id": new_id}, synchronize_session=False)
    db_session.commit()


def _bill_saved_on(client, db_session, seed_accounts, vendor, number, amount=100):
    """A bill whose Deposit line was saved on `number` before #785."""
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "date": TODAY,
            "bill_number": f"RS-{number}-{amount}",
            "lines": [
                _line(seed_accounts["6000"], 20, "Interest"),
                _line(seed_accounts["6100"], amount, "Deposit"),
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    _move_to(
        db_session,
        "bill",
        seed_accounts["6100"].id,
        seed_accounts[number].id,
        bill_id=bill["id"],
    )
    return client.get(f"/api/bills/{bill['id']}").json()


def _expense_saved_on(client, db_session, seed_accounts, number):
    r = client.post(
        "/api/expenses",
        json={
            "date": TODAY,
            "payee": "Ridge Supply",
            "paid_from_account_id": seed_accounts["1000"].id,
            "amount": "40.00",
            "expense_account_id": seed_accounts["6100"].id,
        },
    )
    assert r.status_code == 201, r.text
    _move_to(db_session, "expense", seed_accounts["6100"].id, seed_accounts[number].id)


def _charge_saved_on(client, db_session, seed_accounts, number):
    r = client.post(
        "/api/cc-charges",
        json={
            "date": TODAY,
            "payee": "Ridge Supply",
            "amount": "40",
            "account_id": seed_accounts["6100"].id,
        },
    )
    assert r.status_code in (200, 201), r.text
    _move_to(
        db_session, "cc_charge", seed_accounts["6100"].id, seed_accounts[number].id
    )


def _pending(client):
    r = client.get("/api/deposits/pending")
    assert r.status_code == 200, r.text
    return r.json()


# --- Make Deposits -----------------------------------------------------------


def test_make_deposits_lists_customer_money_and_never_a_purchase_line(
    client, db_session, seed_accounts, vendor, customer
):
    _bill_saved_on(client, db_session, seed_accounts, vendor, "1200")
    _expense_saved_on(client, db_session, seed_accounts, "1200")
    _charge_saved_on(client, db_session, seed_accounts, "1200")

    # a customer payment into Undeposited Funds
    r = client.post(
        "/api/payments",
        json={
            "customer_id": customer.id,
            "date": TODAY,
            "amount": "75.00",
            "method": "Check",
            "check_number": "4420",
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text
    payment_id = r.json()["id"]
    # a sales receipt into Undeposited Funds
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": customer.id,
            "date": TODAY,
            "method": "Cash",
            "lines": [{"description": "Service", "quantity": 1, "rate": "60.00"}],
        },
    )
    assert r.status_code == 201, r.text
    # a journal entry onto Undeposited Funds
    r = client.post(
        "/api/journal",
        json={
            "date": TODAY,
            "description": "Cash from the fair",
            "lines": [
                {
                    "account_id": seed_accounts["1200"].id,
                    "debit": 45,
                    "credit": 0,
                    "description": "cash",
                },
                {
                    "account_id": seed_accounts["4000"].id,
                    "debit": 0,
                    "credit": 45,
                    "description": "sales",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    je_txn = r.json()["id"]

    pending = _pending(client)
    by_source = sorted((p["source_type"], p["amount"]) for p in pending)
    assert by_source == [("manual", 45.0), ("payment", 60.0), ("payment", 75.0)]
    assert payment_id in {p["payment_id"] for p in pending}
    assert je_txn in {p["transaction_id"] for p in pending}
    assert not {p["source_type"] for p in pending} & {"bill", "expense", "cc_charge"}

    # depositing by amount can't take the purchase lines either: what waits
    # is the customer money, $180
    r = client.post(
        "/api/deposits",
        json={
            "date": TODAY,
            "deposit_to_account_id": seed_accounts["1000"].id,
            "total": 300,
        },
    )
    assert r.status_code == 400, r.text

    # the purchase lines stay on the Balance Sheet's Undeposited Funds
    on_uf = (
        db_session.query(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == seed_accounts["1200"].id,
            Transaction.source_type.in_(("bill", "expense", "cc_charge")),
        )
        .count()
    )
    assert on_uf == 3


def test_a_purchase_line_moved_off_undeposited_funds_still_saves(
    client, db_session, seed_accounts, vendor
):
    """The #785 edit rule: moving a line off a control is allowed."""
    bill = _bill_saved_on(client, db_session, seed_accounts, vendor, "1200")
    assert purchase_lines_on_controls(db_session) == 1
    lines = [
        {
            "id": ln["id"],
            "description": ln["description"],
            "account_id": (
                seed_accounts["6100"].id
                if ln["account_id"] == seed_accounts["1200"].id
                else ln["account_id"]
            ),
            "quantity": 1,
            "rate": ln["rate"],
        }
        for ln in bill["lines"]
    ]
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/bills/{bill['id']}").json()
    assert seed_accounts["1200"].id not in [ln["account_id"] for ln in got["lines"]]
    assert purchase_lines_on_controls(db_session) == 0
    assert _pending(client) == []


# --- the start-up count --------------------------------------------------------


def test_the_count_finds_purchase_lines_on_undeposited_funds_and_inventory(
    client, db_session, seed_accounts, vendor
):
    assert purchase_lines_on_controls(db_session) == 0
    _bill_saved_on(client, db_session, seed_accounts, vendor, "1200")
    assert purchase_lines_on_controls(db_session) == 1
    _bill_saved_on(client, db_session, seed_accounts, vendor, "1300", amount=70)
    _expense_saved_on(client, db_session, seed_accounts, "1300")
    _charge_saved_on(client, db_session, seed_accounts, "1200")
    assert purchase_lines_on_controls(db_session) == 4


def test_a_stocked_items_purchase_on_inventory_is_not_counted(
    client, db_session, seed_accounts, vendor
):
    item = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25.00"),
        cost=Decimal("10.00"),
        track_inventory=True,
        asset_account_id=seed_accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(item)
    db_session.commit()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "date": TODAY,
            "lines": [
                {
                    "item_id": item.id,
                    "description": "Stock",
                    "quantity": "5",
                    "rate": "10.00",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert purchase_lines_on_controls(db_session) == 0


def test_a_voided_bill_is_not_counted(client, db_session, seed_accounts, vendor):
    bill = _bill_saved_on(client, db_session, seed_accounts, vendor, "1200")
    r = client.post(f"/api/bills/{bill['id']}/void")
    assert r.status_code == 200, r.text
    assert purchase_lines_on_controls(db_session) == 0
    assert _pending(client) == []


def test_the_start_up_sentence():
    assert purchase_lines_sentence(0) is None
    assert purchase_lines_sentence(1) == (
        "1 purchase line sits on Undeposited Funds or Inventory; open each bill "
        "and move the line."
    )
    assert purchase_lines_sentence(3) == (
        "3 purchase lines sit on Undeposited Funds or Inventory; open each bill "
        "and move the line."
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_the_start_up_prints_the_count(monkeypatch, lifespan_client, capsys, count):
    monkeypatch.setattr(
        purchase_posting, "purchase_lines_on_controls", lambda db: count
    )
    with lifespan_client as c:
        assert c.get("/api/accounts").status_code == 200
    out = capsys.readouterr().out
    if count == 0:
        assert "on Undeposited Funds or Inventory" not in out
    else:
        assert purchase_lines_sentence(count) in out


# --- the Inventory Valuation -------------------------------------------------


def test_the_valuation_says_nothing_when_the_ledger_agrees(
    client, db_session, seed_accounts, vendor
):
    item = Item(
        name="Widget",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25.00"),
        cost=Decimal("10.00"),
        track_inventory=True,
        asset_account_id=seed_accounts["1300"].id,
        is_taxable=False,
    )
    db_session.add(item)
    db_session.commit()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor.id,
            "date": TODAY,
            "lines": [
                {
                    "item_id": item.id,
                    "description": "Stock",
                    "quantity": "5",
                    "rate": "10.00",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = client.get("/api/items/valuation").json()
    assert body["total_value"] == 50.0
    assert body["ledger_balance"] == 50.0
    assert body["ledger_note"] is None


def test_the_valuation_says_when_the_ledger_differs(
    client, db_session, seed_accounts, vendor
):
    _bill_saved_on(client, db_session, seed_accounts, vendor, "1300", amount=1250)
    body = client.get("/api/items/valuation").json()
    assert body["total_value"] == 0.0
    assert body["ledger_balance"] == 1250.0
    assert body["ledger_note"] == (
        "The ledger's Inventory balance is $1,250.00, the valuation $0.00; the "
        "difference is on postings that moved no item"
    )


def test_a_hand_journal_entry_to_inventory_is_caught_too(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": TODAY,
            "description": "Stock count",
            "lines": [
                {
                    "account_id": seed_accounts["1300"].id,
                    "debit": 0,
                    "credit": 30,
                    "description": "shrink",
                },
                {
                    "account_id": seed_accounts["6000"].id,
                    "debit": 30,
                    "credit": 0,
                    "description": "shrink",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = client.get("/api/items/valuation").json()
    assert body["ledger_note"] == (
        "The ledger's Inventory balance is -$30.00, the valuation $0.00; the "
        "difference is on postings that moved no item"
    )
