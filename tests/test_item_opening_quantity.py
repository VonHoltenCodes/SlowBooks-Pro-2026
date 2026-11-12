"""An item created with stock on hand posts that stock (SlowBooks-Pro-Testing
#101, found in PNWImport's fork).

Before, POST /api/items with track_inventory and quantity_on_hand 10 wrote
10 onto the item and nothing else: no movement, no journal entry. The stock
never reached the balance sheet, and a later sale posted COGS against value
that was never booked. Now it posts as QuickBooks does: one movement at the
item's cost and DR Inventory / CR Opening Balance Equity for qty x cost.
"""

from datetime import date
from decimal import Decimal

from app.models.accounts import Account
from app.models.items import InventoryMovement, Item
from app.models.transactions import Transaction, TransactionLine


def _create(client, seed_accounts, name="Widget", qty="10", cost="4.00", **extra):
    body = {
        "name": name,
        "item_type": "product",
        "rate": "25.00",
        "cost": cost,
        "track_inventory": True,
        "quantity_on_hand": qty,
        "asset_account_id": seed_accounts["1300"].id,
        "is_taxable": False,
        **extra,
    }
    r = client.post("/api/items", json=body)
    return r


def _bs_amount(client, number):
    bs = client.get("/api/reports/balance-sheet").json()
    rows = bs["assets"] + bs["liabilities"] + bs["equity"]
    return sum(r["amount"] for r in rows if r.get("account_number") == number)


def _lines(db_session, txn_id):
    out = {}
    for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn_id):
        num = db_session.get(Account, ln.account_id).account_number
        out[num] = (Decimal(str(ln.debit)), Decimal(str(ln.credit)))
    return out


def test_tracked_item_with_quantity_posts_movement_and_opening_entry(
    client, db_session, seed_accounts
):
    before = _bs_amount(client, "1300")
    r = _create(client, seed_accounts)
    assert r.status_code == 201, r.text
    item = r.json()
    assert Decimal(item["quantity_on_hand"]) == 10
    assert Decimal(item["avg_cost"]) == Decimal("4.00")

    mvs = db_session.query(InventoryMovement).filter_by(item_id=item["id"]).all()
    assert len(mvs) == 1
    mv = mvs[0]
    assert mv.quantity == 10 and mv.unit_cost == Decimal("4.00")
    assert mv.source_type == "item_opening" and mv.transaction_id

    txn = db_session.get(Transaction, mv.transaction_id)
    assert txn.source_type == "item_opening" and txn.source_id == item["id"]
    assert txn.date == date.today()
    lines = _lines(db_session, txn.id)
    obe = db_session.query(Account).filter_by(name="Opening Balance Equity").one()
    assert lines["1300"] == (Decimal("40.00"), Decimal("0"))
    assert lines[obe.account_number] == (Decimal("0"), Decimal("40.00"))

    assert _bs_amount(client, "1300") == before + 40


def test_zero_cost_moves_stock_without_a_journal_entry(
    client, db_session, seed_accounts
):
    r = _create(client, seed_accounts, name="Freebie", cost="0")
    assert r.status_code == 201, r.text
    mvs = db_session.query(InventoryMovement).filter_by(item_id=r.json()["id"]).all()
    assert len(mvs) == 1 and mvs[0].quantity == 10
    assert mvs[0].transaction_id is None
    assert (
        db_session.query(Transaction).filter_by(source_type="item_opening").count() == 0
    )


def test_untracked_item_keeps_its_quantity_as_given(client, db_session, seed_accounts):
    r = _create(client, seed_accounts, name="Bulk Gravel", track_inventory=False)
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["quantity_on_hand"]) == 10
    assert db_session.query(InventoryMovement).count() == 0
    assert db_session.query(Transaction).count() == 0


def test_negative_starting_quantity_is_refused(client, db_session, seed_accounts):
    r = _create(client, seed_accounts, name="Backorder", qty="-2")
    assert r.status_code == 400
    assert db_session.query(Item).filter_by(name="Backorder").count() == 0


def test_closing_date_refuses_the_opening_entry(client, db_session, seed_accounts):
    r = client.put("/api/settings", json={"closing_date": date.today().isoformat()})
    assert r.status_code == 200, r.text
    r = _create(client, seed_accounts, name="Late Widget")
    assert r.status_code in (400, 403, 409), r.text
    assert db_session.query(Item).filter_by(name="Late Widget").count() == 0


def test_update_leaves_on_hand_alone(client, db_session, seed_accounts):
    item = _create(client, seed_accounts).json()
    r = client.put(f"/api/items/{item['id']}", json={"description": "blue"})
    assert r.status_code == 200, r.text
    assert (
        db_session.query(InventoryMovement).filter_by(item_id=item["id"]).count() == 1
    )


def test_a_sale_after_opening_posts_cogs_at_opening_cost(
    client, db_session, seed_accounts, seed_customer
):
    item = _create(client, seed_accounts).json()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": date.today().isoformat(),
            "terms": "Net 30",
            "tax_rate": "0",
            "lines": [
                {
                    "item_id": item["id"],
                    "description": "Sold",
                    "quantity": "3",
                    "rate": "25.00",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    sale = (
        db_session.query(InventoryMovement)
        .filter_by(item_id=item["id"], source_type="invoice")
        .one()
    )
    lines = _lines(db_session, sale.transaction_id)
    assert lines["5000"] == (Decimal("12.00"), Decimal("0"))
    assert lines["1300"] == (Decimal("0"), Decimal("12.00"))
    assert _bs_amount(client, "1300") == 28
    db_session.expire_all()
    assert db_session.get(Item, item["id"]).quantity_on_hand == 7


# --- Review of the PR (Trent, VonHoltenCodes): six follow-ups. ---


def _default_asset_type(client, db_session):
    # the type a new company gets at setup (a read never makes it: #662)
    from app.seed.fixed_assets import ensure_default_asset_type

    ensure_default_asset_type(db_session)
    db_session.commit()
    types = client.get("/api/fixed-assets/types").json()
    return types[0]


def test_opening_entry_does_not_block_a_fixed_asset_with_the_same_id(
    client, db_session, seed_accounts
):
    """Point 1: the opening entry posts source_type 'item_opening', not
    'opening_balance', so a fixed asset that happens to share the item's id
    is no longer read as already purchased and can still be posted."""
    item = _create(client, seed_accounts).json()

    atype = _default_asset_type(client, db_session)
    r = client.post(
        "/api/fixed-assets",
        json={
            "name": "Truck",
            "asset_type_id": atype["id"],
            "purchase_date": "2026-01-15",
            "purchase_price": "1000",
            "salvage_value": "0",
        },
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    # Fresh tables: both are id 1, the collision the reviewer hit.
    assert asset["id"] == item["id"]

    # The truck's purchase is not in the books yet, so posting it succeeds
    # (before the fix this said "already in the books").
    r = client.post(
        f"/api/fixed-assets/{asset['id']}/post-purchase",
        json={"method": "opening_balance", "as_of": "2026-01-15"},
    )
    assert r.status_code == 200, r.text


def _opening_txn(db_session, item_id):
    return (
        db_session.query(Transaction)
        .filter_by(source_type="item_opening", source_id=item_id)
        .one()
    )


def test_voiding_the_opening_entry_is_refused(client, db_session, seed_accounts):
    """Point 2: the void is refused with a clear message, so the GL value
    and the stock can't drift apart."""
    item = _create(client, seed_accounts).json()
    txn = _opening_txn(db_session, item["id"])
    r = client.post(f"/api/journal/{txn.id}/void")
    assert r.status_code == 400
    assert "inventory adjustment" in r.json()["detail"]
    # Nothing reversed: the one opening entry stands.
    assert (
        db_session.query(Transaction).filter_by(source_type="item_opening").count() == 1
    )


def test_opening_entry_is_listed_but_not_voidable(client, db_session, seed_accounts):
    """Point 2: the Journal page shows the entry (for the record) but marks
    it non-voidable, so the UI hides Void."""
    _create(client, seed_accounts)
    entries = client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
        "entries"
    ]
    opening = [e for e in entries if e["source_type"] == "item_opening"]
    assert len(opening) == 1
    assert opening[0]["voidable"] is False


def test_opening_entry_can_be_dated_with_as_of(client, db_session, seed_accounts):
    """Point 3: as_of dates the opening entry instead of always today."""
    r = _create(client, seed_accounts, name="Dated Widget", as_of="2025-12-31")
    assert r.status_code == 201, r.text
    txn = _opening_txn(db_session, r.json()["id"])
    assert txn.date == date(2025, 12, 31)


def test_a_service_item_with_tracking_on_posts_nothing(
    client, db_session, seed_accounts
):
    """Point 4: only a stock item posts; a service item with track_inventory
    forced on via the API moves no stock and posts no value."""
    r = client.post(
        "/api/items",
        json={
            "name": "Consulting",
            "item_type": "service",
            "rate": "100.00",
            "cost": "50.00",
            "track_inventory": True,
            "quantity_on_hand": "10",
            "is_taxable": False,
        },
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    assert db_session.query(InventoryMovement).filter_by(item_id=item_id).count() == 0
    assert (
        db_session.query(Transaction).filter_by(source_type="item_opening").count() == 0
    )


def test_a_renamed_3900_is_reused_not_duplicated(client, db_session, seed_accounts):
    """Point 5: Opening Balance Equity is found by account number 3900, so a
    chart that already holds it under a QuickBooks-style name is reused."""
    from app.models.accounts import AccountType

    renamed = Account(
        account_number="3900",
        name="Opening Bal Equity",
        account_type=AccountType.EQUITY,
        is_system=True,
        balance=Decimal("0"),
    )
    db_session.add(renamed)
    db_session.commit()

    item = _create(client, seed_accounts, name="Reuse Widget").json()
    txn = _opening_txn(db_session, item["id"])
    lines = _lines(db_session, txn.id)

    # Still only one account numbered 3900, and it carries the credit.
    assert db_session.query(Account).filter_by(account_number="3900").count() == 1
    assert lines["3900"] == (Decimal("0"), Decimal("40.00"))
    assert (
        db_session.query(Account).filter_by(name="Opening Balance Equity").count() == 0
    )


def test_a_non_equity_3900_is_not_taken_for_opening_balance_equity(
    client, db_session, seed_accounts
):
    """An imported chart may use 3900 for something that isn't equity; the
    opening value must not be credited to it."""
    from app.models.accounts import AccountType

    other = Account(
        account_number="3900",
        name="Miscellaneous Expense",
        account_type=AccountType.EXPENSE,
        balance=Decimal("0"),
    )
    db_session.add(other)
    db_session.commit()

    item = _create(client, seed_accounts, name="Other 3900 Widget").json()
    txn = _opening_txn(db_session, item["id"])
    credited = [
        db_session.get(Account, ln.account_id)
        for ln in db_session.query(TransactionLine).filter_by(transaction_id=txn.id)
        if ln.credit and ln.credit > 0
    ]
    assert [a.name for a in credited] == ["Opening Balance Equity"]
    assert credited[0].account_type == AccountType.EQUITY
