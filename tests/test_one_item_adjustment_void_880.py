"""A one-item adjustment's journal entry is not voided from the journal (#880).

POST /api/items/{id}/adjust posts an entry under source type "adjustment"
(source_id = the item). Voiding it from the journal reversed the ledger while
the item's count and movement stayed, so count and ledger disagreed. The
journal's Void now refuses it with a sentence, as it refuses the multi-item
adjustment's entry (#801); a reversing adjustment is the way back.
"""

from decimal import Decimal

from sqlalchemy import func

from app.models.items import InventoryMovement, Item
from app.models.transactions import Transaction, TransactionLine


def _item(client, seed_accounts, name="Widget", qty="10", cost="4.00"):
    r = client.post(
        "/api/items",
        json={
            "name": name,
            "item_type": "product",
            "rate": "25.00",
            "cost": cost,
            "track_inventory": True,
            "quantity_on_hand": qty,
            "asset_account_id": seed_accounts["1300"].id,
            "is_taxable": False,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _count(db_session, item_id):
    db_session.expire_all()
    return Decimal(str(db_session.get(Item, item_id).quantity_on_hand))


def _ledger(db_session, account):
    db_session.expire_all()
    debit, credit = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == account.id)
        .one()
    )
    return Decimal(str(debit)) - Decimal(str(credit))


def _movements(db_session, item_id):
    db_session.expire_all()
    return db_session.query(InventoryMovement).filter_by(item_id=item_id).count()


def _adjust(client, item_id, delta):
    r = client.post(f"/api/items/{item_id}/adjust", json={"quantity_delta": delta})
    assert r.status_code == 200, r.text
    return r.json()


REFUSAL = (
    "This is the item Widget's inventory adjustment entry. It can't be voided "
    "here, since the count it moved would stay; post a reversing adjustment on "
    "the Items page instead (Adjust Inventory: Remove or Add the same "
    "quantity), which keeps the count, the movement and the ledger together."
)


def test_the_entry_refuses_the_void_and_nothing_moves(
    client, db_session, seed_accounts
):
    w = _item(client, seed_accounts)
    mv = _adjust(client, w["id"], -3)
    txn = db_session.get(Transaction, mv["transaction_id"])
    assert txn.source_type == "adjustment" and txn.source_id == w["id"]

    count, moves = _count(db_session, w["id"]), _movements(db_session, w["id"])
    ledger = _ledger(db_session, seed_accounts["1300"])
    assert count == Decimal("7") and ledger == Decimal("28.00")

    r = client.post(f"/api/journal/{txn.id}/void")
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL

    assert _count(db_session, w["id"]) == count
    assert _movements(db_session, w["id"]) == moves
    assert _ledger(db_session, seed_accounts["1300"]) == ledger
    assert (
        db_session.query(Transaction)
        .filter(Transaction.source_id == txn.id, Transaction.source_type.like("%void"))
        .count()
        == 0
    )


def test_a_reversing_adjustment_brings_count_and_ledger_back(
    client, db_session, seed_accounts
):
    w = _item(client, seed_accounts)
    _adjust(client, w["id"], -3)
    _adjust(client, w["id"], 3)
    assert _count(db_session, w["id"]) == Decimal("10")
    assert _ledger(db_session, seed_accounts["1300"]) == Decimal("40.00")


def test_an_ordinary_journal_entry_still_voids(client, db_session, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-09-02",
            "description": "Accrue materials",
            "lines": [
                {"account_id": seed_accounts["6900"].id, "debit": 100, "credit": 0},
                {"account_id": seed_accounts["2500"].id, "debit": 0, "credit": 100},
            ],
        },
    )
    assert r.status_code == 201, r.text
    v = client.post(f"/api/journal/{r.json()['id']}/void")
    assert v.status_code == 200, v.text


def test_the_multi_item_refusal_still_holds(client, db_session, seed_accounts):
    w = _item(client, seed_accounts)
    b = _item(client, seed_accounts, "Bolt", "20", "0.50")
    r = client.post(
        "/api/inventory/adjustments",
        json={
            "memo": "Count",
            "lines": [
                {"item_id": w["id"], "quantity_delta": 2},
                {"item_id": b["id"], "quantity_delta": -2},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    adj = r.json()
    v = client.post(f"/api/journal/{adj['transaction_id']}/void")
    assert v.status_code == 400
    assert v.json()["detail"] == (
        f"This is inventory adjustment #{adj['id']}'s entry. It can't be voided "
        "here, since the counts it moved would stay; correct them with another "
        "inventory adjustment."
    )
