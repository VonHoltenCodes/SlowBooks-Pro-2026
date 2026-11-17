"""A sale ahead of its receipt is costed at a figure of its own (#1008):
the latest purchase cost, else the item's cost; the hole carries it, and the
receipt that fills the hole posts only the distance, so the ledger's
Inventory equals quantity x average throughout."""

from datetime import date
from decimal import Decimal

from app.models.items import Item, ItemType
from app.services.inventory_service import (
    inventory_ledger_balance,
    provisional_sale_cost,
    record_purchase,
    record_sale,
)


def _item(db, accounts, cost="7.50"):
    it = Item(
        name="Gizmo",
        item_type=ItemType.PRODUCT,
        rate=Decimal("25.00"),
        cost=Decimal(cost),
        income_account_id=accounts["4000"].id if "4000" in accounts else None,
        track_inventory=True,
        asset_account_id=accounts["1300"].id,
        is_taxable=False,
    )
    db.add(it)
    db.commit()
    db.refresh(it)
    return it


def _cogs(db, accounts):
    from sqlalchemy import func

    from app.models.transactions import TransactionLine
    from app.services.inventory_service import get_cogs_account_id

    cogs_id = get_cogs_account_id(db)
    d, c = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == cogs_id)
        .one()
    )
    return Decimal(str(d)) - Decimal(str(c))


def _tied(db, it):
    db.refresh(it)
    ledger = inventory_ledger_balance(db, [it])
    pool = (Decimal(str(it.quantity_on_hand)) * Decimal(str(it.avg_cost))).quantize(
        Decimal("0.01")
    )
    assert ledger == pool, (ledger, pool, it.quantity_on_hand, it.avg_cost)
    return ledger


def test_an_oversold_sale_is_costed_at_the_latest_purchase_cost_and_the_receipt_settles_it(
    db_session, seed_accounts
):
    it = _item(db_session, seed_accounts)
    record_purchase(
        db_session,
        it,
        Decimal("2"),
        Decimal("10.00"),
        "bill",
        1,
        txn_date=date(2026, 3, 1),
    )
    record_sale(db_session, it, Decimal("2"), "invoice", 1, txn_date=date(2026, 3, 2))
    db_session.refresh(it)
    assert Decimal(str(it.quantity_on_hand)) == 0 and Decimal(str(it.avg_cost)) == 0
    assert provisional_sale_cost(db_session, it) == Decimal("10.0000")
    # sell 3 ahead of the next bill: costed at the latest purchase cost, not $0
    cogs_before = _cogs(db_session, seed_accounts)
    record_sale(db_session, it, Decimal("3"), "invoice", 2, txn_date=date(2026, 3, 5))
    assert _cogs(db_session, seed_accounts) - cogs_before == Decimal("30.00")
    assert _tied(db_session, it) == Decimal("-30.00")
    assert Decimal(str(it.avg_cost)) == Decimal("10.0000")
    # the bill arrives at 12: the hole's 3 are settled (6 more to cost of goods), 2 stay at 12
    cogs_before = _cogs(db_session, seed_accounts)
    record_purchase(
        db_session,
        it,
        Decimal("5"),
        Decimal("12.00"),
        "bill",
        2,
        txn_date=date(2026, 3, 9),
    )
    assert _cogs(db_session, seed_accounts) - cogs_before == Decimal("6.00")
    db_session.refresh(it)
    assert Decimal(str(it.quantity_on_hand)) == 2 and Decimal(
        str(it.avg_cost)
    ) == Decimal("12.0000")
    assert _tied(db_session, it) == Decimal("24.00")


def test_a_sale_with_no_history_uses_the_items_cost(db_session, seed_accounts):
    it = _item(db_session, seed_accounts, cost="7.50")
    assert provisional_sale_cost(db_session, it) == Decimal("7.5000")
    cogs_before = _cogs(db_session, seed_accounts)
    record_sale(db_session, it, Decimal("4"), "invoice", 3, txn_date=date(2026, 3, 5))
    assert _cogs(db_session, seed_accounts) - cogs_before == Decimal("30.00")
    assert _tied(db_session, it) == Decimal("-30.00")
    record_purchase(
        db_session,
        it,
        Decimal("4"),
        Decimal("7.50"),
        "bill",
        3,
        txn_date=date(2026, 3, 9),
    )
    assert _tied(db_session, it) == Decimal("0.00")


def test_units_already_sold_ahead_at_zero_are_weighted_into_the_hole(
    db_session, seed_accounts
):
    # a book from before #1008: 15 sold ahead at $0, then a sale at a real cost
    it = _item(db_session, seed_accounts, cost="0")
    record_sale(db_session, it, Decimal("15"), "invoice", 4, txn_date=date(2026, 3, 1))
    db_session.refresh(it)
    assert Decimal(str(it.avg_cost)) == 0 and _tied(db_session, it) == Decimal("0.00")
    it.cost = Decimal("10.00")
    db_session.commit()
    record_sale(db_session, it, Decimal("3"), "invoice", 5, txn_date=date(2026, 3, 5))
    assert _tied(db_session, it) == Decimal("-30.00")
    # the receipt fills the whole hole (18) at 12 and posts the distance
    cogs_before = _cogs(db_session, seed_accounts)
    record_purchase(
        db_session,
        it,
        Decimal("18"),
        Decimal("12.00"),
        "bill",
        4,
        txn_date=date(2026, 3, 9),
    )
    assert _cogs(db_session, seed_accounts) - cogs_before == Decimal("186.00")
    assert _tied(db_session, it) == Decimal("0.00")


def test_a_sale_from_stock_on_hand_is_unchanged(db_session, seed_accounts):
    it = _item(db_session, seed_accounts)
    record_purchase(
        db_session,
        it,
        Decimal("10"),
        Decimal("4.00"),
        "bill",
        5,
        txn_date=date(2026, 3, 1),
    )
    cogs_before = _cogs(db_session, seed_accounts)
    record_sale(db_session, it, Decimal("4"), "invoice", 6, txn_date=date(2026, 3, 2))
    assert _cogs(db_session, seed_accounts) - cogs_before == Decimal("16.00")
    assert _tied(db_session, it) == Decimal("24.00")
