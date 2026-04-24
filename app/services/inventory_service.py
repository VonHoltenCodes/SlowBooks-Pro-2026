# ============================================================================
# Phase 11: Inventory tracking with weighted-average cost.
#
# Design goals:
#   - Only items with Item.track_inventory=True flow through here. Services,
#     labor, and non-inventory items bypass these hooks entirely (so the old
#     invoice/bill posting semantics are unchanged for them).
#   - Every quantity change appends an InventoryMovement row. Qty and avg_cost
#     on the Item are denormalized caches kept in sync with the ledger.
#   - Weighted-average cost is recomputed on purchases:
#         new_avg = ((old_qty * old_avg) + (received_qty * received_cost))
#                   / (old_qty + received_qty)
#   - Sales post COGS at the current avg_cost (not the invoice rate, not the
#     item's stored cost). This is the standard perpetual-inventory approach
#     and keeps COGS honest when purchase costs move over time.
#   - Journal entries are:
#       Purchase (via bill):  DR Inventory Asset   CR Accounts Payable
#                             (the bill already posts the AP side; we add
#                              the inventory-side JE separately so services
#                              on the same bill still hit their expense accts)
#       Sale (via invoice):   DR COGS              CR Inventory Asset
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.models.items import Item, InventoryMovement, MovementType
from app.models.accounts import Account, AccountType
from app.services.accounting import create_journal_entry

QTY_Q = Decimal("0.0001")
COST_Q = Decimal("0.0001")
MONEY_Q = Decimal("0.01")


def _q(value, exp=MONEY_Q) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def get_inventory_asset_account_id(db: Session, item: Item) -> Optional[int]:
    """Resolve the inventory asset account for an item.

    Priority: item.asset_account_id → account #1300 (seeded "Inventory") → None.
    """
    if item.asset_account_id:
        return item.asset_account_id
    acc = db.query(Account).filter(Account.account_number == "1300").first()
    return acc.id if acc else None


def get_cogs_account_id(db: Session) -> Optional[int]:
    """Resolve the COGS account. First COGS-typed account wins; fallback to #5000."""
    acc = (
        db.query(Account)
        .filter(Account.account_type == AccountType.COGS)
        .order_by(Account.account_number)
        .first()
    )
    if acc:
        return acc.id
    acc = db.query(Account).filter(Account.account_number == "5000").first()
    return acc.id if acc else None


def _append_movement(
    db: Session,
    item: Item,
    movement_type: MovementType,
    quantity: Decimal,
    unit_cost: Decimal,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
    transaction_id: Optional[int] = None,
    memo: Optional[str] = None,
) -> InventoryMovement:
    """Write one movement row and update the denormalized Item cache.

    Caller is responsible for posting any corresponding journal entries.
    """
    # Update running balances BEFORE writing the row so balance_* reflects
    # the post-movement state (standard ledger convention).
    old_qty = Decimal(str(item.quantity_on_hand or 0))
    old_avg = Decimal(str(item.avg_cost or 0))
    new_qty = old_qty + quantity

    # Weighted average cost only changes on positive movements (receipts).
    # Sales, returns-out, and adjustments-out leave avg_cost unchanged —
    # you don't revalue remaining stock when you ship it.
    if quantity > 0 and new_qty > 0:
        new_avg = _q(
            ((old_qty * old_avg) + (quantity * unit_cost)) / new_qty,
            COST_Q,
        )
    elif new_qty == 0:
        new_avg = Decimal("0")
    else:
        new_avg = old_avg

    item.quantity_on_hand = new_qty
    item.avg_cost = new_avg

    mv = InventoryMovement(
        item_id=item.id,
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        balance_qty=new_qty,
        balance_avg_cost=new_avg,
        source_type=source_type,
        source_id=source_id,
        transaction_id=transaction_id,
        memo=memo,
    )
    db.add(mv)
    db.flush()
    return mv


def record_purchase(
    db: Session,
    item: Item,
    quantity: Decimal,
    unit_cost: Decimal,
    source_type: str,
    source_id: int,
    memo: Optional[str] = None,
    post_journal: bool = True,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Record an inventory receipt. Posts DR Inventory / CR Accounts Payable
    when post_journal=True. Callers that already include the inventory line
    in their own balanced JE should pass post_journal=False.

    Returns the movement row (or None if the item isn't inventory-tracked).
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    unit_cost = Decimal(str(unit_cost))
    if quantity <= 0:
        return None

    txn_id = None
    if post_journal:
        asset_id = get_inventory_asset_account_id(db, item)
        ap_acc = db.query(Account).filter(Account.account_number == "2000").first()
        if asset_id and ap_acc:
            from datetime import date as _date
            lines = [
                {"account_id": asset_id, "debit": _q(quantity * unit_cost), "credit": Decimal("0"),
                 "description": f"Inventory: {item.name}"},
                {"account_id": ap_acc.id, "debit": Decimal("0"), "credit": _q(quantity * unit_cost),
                 "description": f"A/P: {item.name}"},
            ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"Inventory receipt — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    return _append_movement(
        db, item, MovementType.PURCHASE,
        quantity=quantity, unit_cost=unit_cost,
        source_type=source_type, source_id=source_id, transaction_id=txn_id,
        memo=memo,
    )


def record_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    memo: Optional[str] = None,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Record an inventory ship-out. Uses the current weighted-avg cost for
    the COGS journal entry (DR COGS / CR Inventory Asset).

    Returns the movement row (or None if the item isn't inventory-tracked).
    Allows negative on-hand balances — some businesses back-date bills after
    invoicing. Watch your low-stock report.
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    unit_cost = Decimal(str(item.avg_cost or 0))
    cogs_amount = _q(quantity * unit_cost)

    txn_id = None
    if cogs_amount > 0:
        asset_id = get_inventory_asset_account_id(db, item)
        cogs_id = get_cogs_account_id(db)
        if asset_id and cogs_id:
            from datetime import date as _date
            lines = [
                {"account_id": cogs_id, "debit": cogs_amount, "credit": Decimal("0"),
                 "description": f"COGS: {item.name}"},
                {"account_id": asset_id, "debit": Decimal("0"), "credit": cogs_amount,
                 "description": f"Inventory: {item.name}"},
            ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"COGS — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    return _append_movement(
        db, item, MovementType.SALE,
        quantity=-quantity,  # store as negative
        unit_cost=unit_cost,
        source_type=source_type, source_id=source_id, transaction_id=txn_id,
        memo=memo,
    )


def record_adjustment(
    db: Session,
    item: Item,
    quantity_delta: Decimal,
    unit_cost: Optional[Decimal] = None,
    memo: Optional[str] = None,
    post_journal: bool = True,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Manual inventory adjustment (count correction, shrinkage, etc.).

    quantity_delta can be positive (increase stock) or negative (decrease).
    If post_journal=True, books a one-sided JE to an "Inventory Adjustment"
    expense account if #5900 exists, otherwise to COGS.
    """
    if not item.track_inventory:
        return None

    quantity_delta = Decimal(str(quantity_delta))
    if quantity_delta == 0:
        return None

    if unit_cost is None:
        unit_cost = Decimal(str(item.avg_cost or 0))
    else:
        unit_cost = Decimal(str(unit_cost))

    amount = _q(abs(quantity_delta) * unit_cost)
    txn_id = None

    if post_journal and amount > 0:
        asset_id = get_inventory_asset_account_id(db, item)
        adj_acc = db.query(Account).filter(Account.account_number == "5900").first()
        offset_id = adj_acc.id if adj_acc else get_cogs_account_id(db)
        if asset_id and offset_id:
            from datetime import date as _date
            if quantity_delta > 0:
                # stock up: DR Inventory / CR Adjustment
                lines = [
                    {"account_id": asset_id, "debit": amount, "credit": Decimal("0"),
                     "description": f"Inventory adj: {item.name}"},
                    {"account_id": offset_id, "debit": Decimal("0"), "credit": amount,
                     "description": f"Inventory adjustment gain"},
                ]
            else:
                # stock down: DR Adjustment / CR Inventory
                lines = [
                    {"account_id": offset_id, "debit": amount, "credit": Decimal("0"),
                     "description": f"Inventory adjustment loss"},
                    {"account_id": asset_id, "debit": Decimal("0"), "credit": amount,
                     "description": f"Inventory adj: {item.name}"},
                ]
            txn = create_journal_entry(
                db, txn_date or _date.today(),
                f"Inventory adjustment — {item.name}",
                lines,
                source_type="adjustment",
                source_id=item.id,
            )
            txn_id = txn.id

    return _append_movement(
        db, item, MovementType.ADJUSTMENT,
        quantity=quantity_delta, unit_cost=unit_cost,
        source_type="adjustment", source_id=item.id, transaction_id=txn_id,
        memo=memo,
    )


def reverse_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    txn_date=None,
) -> Optional[InventoryMovement]:
    """Reverse a previous sale (invoice void). Puts stock back at the current
    avg_cost and reverses the COGS entry.
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    unit_cost = Decimal(str(item.avg_cost or 0))
    amount = _q(quantity * unit_cost)

    txn_id = None
    if amount > 0:
        asset_id = get_inventory_asset_account_id(db, item)
        cogs_id = get_cogs_account_id(db)
        if asset_id and cogs_id:
            from datetime import date as _date
            lines = [
                {"account_id": asset_id, "debit": amount, "credit": Decimal("0"),
                 "description": f"Inventory reversal: {item.name}"},
                {"account_id": cogs_id, "debit": Decimal("0"), "credit": amount,
                 "description": f"COGS reversal: {item.name}"},
            ]
            txn = create_journal_entry(
                db, txn_date or _date.today(),
                f"COGS reversal — {item.name}",
                lines,
                source_type=source_type, source_id=source_id,
            )
            txn_id = txn.id

    return _append_movement(
        db, item, MovementType.VOID,
        quantity=quantity, unit_cost=unit_cost,
        source_type=source_type, source_id=source_id, transaction_id=txn_id,
        memo="Sale reversal",
    )


def current_valuation(db: Session) -> dict:
    """Return aggregate inventory valuation: total_value, item_count, low_stock_count."""
    items = db.query(Item).filter(Item.track_inventory == True, Item.is_active == True).all()  # noqa
    total_value = Decimal("0")
    item_count = 0
    low_stock = 0
    for it in items:
        qty = Decimal(str(it.quantity_on_hand or 0))
        avg = Decimal(str(it.avg_cost or 0))
        total_value += qty * avg
        item_count += 1
        if Decimal(str(it.reorder_point or 0)) > 0 and qty <= Decimal(str(it.reorder_point)):
            low_stock += 1
    return {
        "total_value": float(_q(total_value)),
        "item_count": item_count,
        "low_stock_count": low_stock,
    }
