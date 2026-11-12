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
#   - A count below zero (stock sold ahead of its receipt) is carried at the
#     average of the time. Stock coming in fills that hole first, and the
#     difference between what the units sold ahead were costed at and what
#     they cost now goes to cost of goods under the document that brought
#     the stock in (#647's receiving side, W-60); what is left past the hole
#     is stock at its own price. _append_at_cost posts it; _append_movement
#     never posts anything.
# ============================================================================

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.items import (
    TRACKABLE_ITEM_TYPES,
    InventoryMovement,
    Item,
    MovementType,
)
from app.models.accounts import Account, AccountType
from app.models.transactions import TransactionLine
from app.services.accounting import _q, create_journal_entry, quantize_to

logger = logging.getLogger(__name__)

COST_Q = Decimal("0.0001")
# The memo of a reversal piece that matched no sale posting (a legacy or
# hand-made ledger): it names nothing, and must not count as pre-release
# unattributed quantity on a later reversal either.
_BEYOND_POSTINGS_MEMO = "Sale reversal (beyond postings)"


def get_inventory_asset_account_id(db: Session, item: Item) -> Optional[int]:
    """Resolve the inventory asset account for an item.

    Priority: item.asset_account_id → account #1300 (seeded "Inventory") → None.
    """
    if item.asset_account_id:
        return item.asset_account_id
    acc = db.query(Account).filter(Account.account_number == "1300").first()
    return acc.id if acc else None


def get_cogs_account_id(db: Session, item: Optional[Item] = None) -> Optional[int]:
    """Resolve the COGS account for an item's sale.

    Priority: the item's own Expense or COGS Account when it is COGS-typed
    (#622: it used to be ignored, so every sale landed on the first COGS
    account, 5000, and the P&L showed "Direct postings to Cost of Goods Sold"
    instead of the sub-account) → the first COGS-typed account → #5000.
    An item pointed at an expense-typed account keeps the fallback: a sale's
    cost side belongs in cost of goods, not operating expense. An account the
    user has made inactive is not reopened by a sale either.
    """
    if item is not None and item.expense_account_id:
        own = db.get(Account, item.expense_account_id)
        if own is not None and own.account_type == AccountType.COGS and own.is_active:
            return own.id
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


def _sale_entry_accounts(db: Session, movement: InventoryMovement) -> tuple:
    """(cogs_account_id, asset_account_id) of the entry a sale movement
    posted: the COGS-typed debit line and the asset-typed credit line. Either
    is None when the movement posted no entry or the line is not there."""
    if not movement.transaction_id:
        return None, None

    rows = (
        db.query(
            TransactionLine.account_id, TransactionLine.debit, Account.account_type
        )
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(TransactionLine.transaction_id == movement.transaction_id)
        .all()
    )
    cogs_id = next(
        (a for a, debit, t in rows if debit and debit > 0 and t == AccountType.COGS),
        None,
    )
    asset_id = next(
        (
            a
            for a, debit, t in rows
            if not (debit and debit > 0) and t == AccountType.ASSET
        ),
        None,
    )
    return cogs_id, asset_id


def _pool_after(
    old_qty: Decimal, old_avg: Decimal, quantity: Decimal, unit_cost: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """What a movement leaves of an item's pool: (quantity, average cost,
    residue).

    A receipt re-averages what it joins. A sale, or an adjustment out at the
    average, leaves the average alone: shipping stock does not revalue what
    stays. A take-out at a cost of its own (a return's void at the return's
    cost, #641; a receipt's void at what it was received at, #646; a return
    to the vendor at what its credit takes off Inventory, W-74) takes that
    much value out of the pool, and what remains re-averages, so the
    ledger's Inventory and quantity x average stay one figure (W-50, 2.31.0
    gate).

    The residue is the value a movement leaves between the two where no
    average can carry it (#647). For such a take-out: the pool's value less
    the cost taken out when the count lands on zero (the average goes to
    zero with it), the shortfall when the cost taken out is more than the
    pool held (the average is held at zero rather than going below it), and
    the cost's distance from the average when the count is below zero (the
    average is kept). For stock coming in while the count is below zero
    (W-60, 2.31.1 gate): the units sold ahead were costed at the average of
    the time, and the stock fills that hole first, so the residue is the
    hole filled times the cost's distance from that average; what is left
    past the hole is stock at its own cost, the new average (the count
    landing on zero zeroes the average, as a take-out's does). Positive,
    the ledger holds more than quantity x average; negative, less. Zero for
    every other movement: the rounding of a four-place average is not a
    residue, and a receipt into stock on hand blends in.
    """
    new_qty = old_qty + quantity
    residue = Decimal("0")
    if quantity > 0 and old_qty < 0:
        filled = min(quantity, -old_qty)
        residue = filled * (unit_cost - old_avg)
        if new_qty > 0:
            new_avg = quantize_to(unit_cost, COST_Q)
        elif new_qty == 0:
            new_avg = Decimal("0")
        else:
            new_avg = old_avg
    elif quantity > 0 and new_qty > 0:
        new_avg = quantize_to(
            ((old_qty * old_avg) + (quantity * unit_cost)) / new_qty,
            COST_Q,
        )
    elif new_qty == 0:
        new_avg = Decimal("0")
        if quantity < 0:
            residue = (old_qty * old_avg) - (abs(quantity) * unit_cost)
    elif quantity < 0 and new_qty > 0 and old_qty > 0 and unit_cost != old_avg:
        value_left = (old_qty * old_avg) - (abs(quantity) * unit_cost)
        new_avg = quantize_to(max(value_left, Decimal("0")) / new_qty, COST_Q)
        if value_left < 0:
            residue = value_left
    else:
        new_avg = old_avg
        if quantity < 0 and new_qty < 0:
            residue = abs(quantity) * (old_avg - unit_cost)
    return new_qty, new_avg, residue


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
    reverses_movement_id: Optional[int] = None,
) -> InventoryMovement:
    """Write one movement row and update the denormalized Item cache.

    Caller is responsible for posting any corresponding journal entries.
    """
    # Update running balances BEFORE writing the row so balance_* reflects
    # the post-movement state (standard ledger convention). The average is
    # _pool_after's; the residue it reports is _append_at_cost's to post,
    # never this row's.
    old_qty = Decimal(str(item.quantity_on_hand or 0))
    old_avg = Decimal(str(item.avg_cost or 0))
    new_qty, new_avg, _residue = _pool_after(old_qty, old_avg, quantity, unit_cost)

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
        reverses_movement_id=reverses_movement_id,
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

    Either way the entry carries quantity x unit_cost on Inventory. A
    receipt into a count below zero fills the hole first, and what the
    units sold ahead cost beyond what they were costed at is posted to the
    item's cost of goods account under (source_type, source_id), dated
    txn_date (_append_at_cost, W-60), so afterwards Inventory in the ledger
    reads quantity x average cost.

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
                {
                    "account_id": asset_id,
                    "debit": _q(quantity * unit_cost),
                    "credit": Decimal("0"),
                    "description": f"Inventory: {item.name}",
                },
                {
                    "account_id": ap_acc.id,
                    "debit": Decimal("0"),
                    "credit": _q(quantity * unit_cost),
                    "description": f"A/P: {item.name}",
                },
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

    return _append_at_cost(
        db,
        item,
        MovementType.PURCHASE,
        quantity=quantity,
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        txn_date=txn_date,
        transaction_id=txn_id,
        memo=memo,
    )


def record_opening_quantity(
    db: Session,
    item: Item,
    quantity: Decimal,
    unit_cost: Decimal,
    as_of=None,
) -> Optional[InventoryMovement]:
    """The stock an item starts with, as QuickBooks books it: one movement
    of `quantity` at `unit_cost`, and DR Inventory (the item's asset
    account, else 1300) / CR 3900 Opening Balance Equity for qty x cost,
    dated `as_of` (today when not given), source_type "item_opening" (its
    own type, so a fixed asset with the same id is not mistaken for it).

    A zero cost moves the stock with no journal entry: there is no value
    to carry. An item that already has movements gets nothing, so the
    opening quantity cannot post twice. Only a stock item posts (a service
    or labor item with the flag forced on does not). The closing date is
    enforced by create_journal_entry. Returns the movement, or None when
    nothing posted.
    """
    if not item.track_inventory or item.item_type not in TRACKABLE_ITEM_TYPES:
        return None
    quantity = Decimal(str(quantity or 0))
    unit_cost = Decimal(str(unit_cost or 0))
    if quantity <= 0:
        return None
    if item.id is not None and (
        db.query(InventoryMovement.id)
        .filter(InventoryMovement.item_id == item.id)
        .first()
        is not None
    ):
        return None

    from datetime import date as _date

    from app.services import control_accounts
    from app.services.accounting import get_opening_balance_equity_id

    txn_id = None
    amount = _q(quantity * unit_cost)
    if amount > 0:
        asset_id = item.asset_account_id or control_accounts.resolve(db, "1300")
        txn = create_journal_entry(
            db,
            as_of or _date.today(),
            f"Opening quantity — {item.name}",
            [
                {
                    "account_id": asset_id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": f"Inventory: {item.name}",
                },
                {
                    "account_id": get_opening_balance_equity_id(db),
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": f"Opening quantity: {item.name}",
                },
            ],
            source_type="item_opening",
            source_id=item.id,
        )
        txn_id = txn.id

    return _append_movement(
        db,
        item,
        MovementType.ADJUSTMENT,
        quantity=quantity,
        unit_cost=unit_cost,
        source_type="item_opening",
        source_id=item.id,
        transaction_id=txn_id,
        memo="Opening quantity",
    )


def record_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    memo: Optional[str] = None,
    txn_date=None,
    post_journal: bool = True,
) -> Optional[InventoryMovement]:
    """Record an inventory ship-out. Uses the current weighted-avg cost for
    the COGS journal entry (DR COGS / CR Inventory Asset).

    Returns the movement row (or None if the item isn't inventory-tracked).
    Allows negative on-hand balances — some businesses back-date bills after
    invoicing. Watch your low-stock report.

    `post_journal=False` moves the stock without the COGS entry, for a sale
    whose cost is already in the books another way (a QuickBooks Online sale
    the QBO ledger import has posted, cost of goods included).
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    unit_cost = Decimal(str(item.avg_cost or 0))
    cogs_amount = _q(quantity * unit_cost)

    txn_id = None
    if cogs_amount > 0 and post_journal:
        asset_id = get_inventory_asset_account_id(db, item)
        cogs_id = get_cogs_account_id(db, item)
        if asset_id and cogs_id:
            from datetime import date as _date

            lines = [
                {
                    "account_id": cogs_id,
                    "debit": cogs_amount,
                    "credit": Decimal("0"),
                    "description": f"COGS: {item.name}",
                },
                {
                    "account_id": asset_id,
                    "debit": Decimal("0"),
                    "credit": cogs_amount,
                    "description": f"Inventory: {item.name}",
                },
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
        db,
        item,
        MovementType.SALE,
        quantity=-quantity,  # store as negative
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        transaction_id=txn_id,
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
    expense account if #5900 exists, otherwise to COGS, and what the pool
    cannot carry of a unit_cost of its own (a count up into a count below
    zero fills the hole first, W-60; a count down at a cost of its own that
    empties the shelf, #647) goes to the item's cost of goods account under
    the adjustment (_append_at_cost).
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
        # An adjustment is not a sale; its offset stays 5900, else the chart's
        # first COGS account, as before (#622 changes sales and their reversals).
        offset_id = adj_acc.id if adj_acc else get_cogs_account_id(db)
        if asset_id and offset_id:
            from datetime import date as _date

            if quantity_delta > 0:
                # stock up: DR Inventory / CR Adjustment
                lines = [
                    {
                        "account_id": asset_id,
                        "debit": amount,
                        "credit": Decimal("0"),
                        "description": f"Inventory adj: {item.name}",
                    },
                    {
                        "account_id": offset_id,
                        "debit": Decimal("0"),
                        "credit": amount,
                        "description": "Inventory adjustment gain",
                    },
                ]
            else:
                # stock down: DR Adjustment / CR Inventory
                lines = [
                    {
                        "account_id": offset_id,
                        "debit": amount,
                        "credit": Decimal("0"),
                        "description": "Inventory adjustment loss",
                    },
                    {
                        "account_id": asset_id,
                        "debit": Decimal("0"),
                        "credit": amount,
                        "description": f"Inventory adj: {item.name}",
                    },
                ]
            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"Inventory adjustment — {item.name}",
                lines,
                source_type="adjustment",
                source_id=item.id,
            )
            txn_id = txn.id

    if not post_journal:
        return _append_movement(
            db,
            item,
            MovementType.ADJUSTMENT,
            quantity=quantity_delta,
            unit_cost=unit_cost,
            source_type="adjustment",
            source_id=item.id,
            transaction_id=txn_id,
            memo=memo,
        )
    return _append_at_cost(
        db,
        item,
        MovementType.ADJUSTMENT,
        quantity=quantity_delta,
        unit_cost=unit_cost,
        source_type="adjustment",
        source_id=item.id,
        txn_date=txn_date,
        transaction_id=txn_id,
        memo=memo,
        entry_memo="Inventory adjustment",
    )


def _returned_against(db: Session, sale_ids: list) -> dict:
    """Per sale posting id: the quantity that credit memo returns naming it
    have put back, net of the voids that took those returns out again (a
    memo's void names each RETURN_IN it reverses, #641)."""
    out = {sale_id: Decimal("0") for sale_id in sale_ids}
    returns = (
        db.query(
            InventoryMovement.id,
            InventoryMovement.reverses_movement_id,
            InventoryMovement.quantity,
        )
        .filter(
            InventoryMovement.movement_type == MovementType.RETURN_IN,
            InventoryMovement.reverses_movement_id.in_(sale_ids),
        )
        .all()
    )
    if not returns:
        return out
    voided = dict(
        db.query(
            InventoryMovement.reverses_movement_id,
            func.coalesce(func.sum(InventoryMovement.quantity), 0),
        )
        .filter(
            InventoryMovement.movement_type == MovementType.VOID,
            InventoryMovement.reverses_movement_id.in_([r[0] for r in returns]),
        )
        .group_by(InventoryMovement.reverses_movement_id)
        .all()
    )
    for return_id, sale_id, quantity in returns:
        out[sale_id] += Decimal(str(quantity or 0)) + Decimal(
            str(voided.get(return_id, 0) or 0)
        )
    return out


def _sale_postings_left(
    db: Session,
    item: Item,
    original_source_type: str,
    original_source_id: int,
    count_returns: bool = False,
) -> list:
    """The SALE movements a document posted for the item, newest first, each
    with the quantity it has left: [(movement, left), ...], empty when the
    document posted none.

    An invoice can hold more than one SALE movement for the item (each
    quantity-up edit adds one) and, since #622, they can sit on different
    accounts at different costs. Each posting's remainder is what earlier
    reversals that name it left; reversals from before the link existed
    name nothing, so their total is taken off the oldest postings first
    (such a reversal predates every posting made after it, so it can only
    have consumed the postings of its day; postings were all on 5000 back
    then, so for an install with no newer posting nothing changes). With
    `count_returns`, a credit memo's return that names the posting (#641)
    counts as taken too, net of a void of that memo.
    """
    originals = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.item_id == item.id,
            InventoryMovement.source_type == original_source_type,
            InventoryMovement.source_id == original_source_id,
            InventoryMovement.movement_type == MovementType.SALE,
        )
        .order_by(InventoryMovement.id.desc())
        .all()
    )
    if not originals:
        return []
    void_q = db.query(func.coalesce(func.sum(InventoryMovement.quantity), 0)).filter(
        InventoryMovement.item_id == item.id,
        InventoryMovement.movement_type == MovementType.VOID,
    )
    unattributed = Decimal(
        str(
            void_q.filter(
                InventoryMovement.source_id == original_source_id,
                InventoryMovement.source_type.in_(
                    (f"{original_source_type}_void", f"{original_source_type}_edit")
                ),
                InventoryMovement.reverses_movement_id.is_(None),
                or_(
                    InventoryMovement.memo.is_(None),
                    InventoryMovement.memo != _BEYOND_POSTINGS_MEMO,
                ),
            ).scalar()
            or 0
        )
    )
    taken = {
        mv.id: Decimal(
            str(
                void_q.filter(InventoryMovement.reverses_movement_id == mv.id).scalar()
                or 0
            )
        )
        for mv in originals
    }
    if count_returns:
        for sale_id, returned in _returned_against(db, list(taken)).items():
            taken[sale_id] += returned
    left = {}
    for mv in reversed(originals):
        room = max(abs(Decimal(str(mv.quantity))) - taken[mv.id], Decimal("0"))
        legacy = min(room, unattributed)
        unattributed -= legacy
        left[mv.id] = room - legacy
    return [(mv, left[mv.id]) for mv in originals]


def sale_pieces(
    db: Session,
    item: Item,
    quantity: Decimal,
    original_source_type: Optional[str] = None,
    original_source_id: Optional[int] = None,
    count_returns: bool = False,
) -> list:
    """How `quantity` coming back comes off the sale postings of a document:
    one piece per posting, newest first, as (quantity, unit_cost,
    cogs_account_id, asset_account_id, sale movement), at the posting's own
    cost and against the accounts its entry used (None for either when it
    posted none). What the postings cannot cover, or all of it when no
    document is named, is a last piece at the current avg_cost with no
    accounts and no movement, for the caller to place. Used by a sale's
    reversal (reverse_sale) and a credit memo's return (#641).
    """
    pieces = []
    remaining = quantity
    postings = []
    if original_source_type and original_source_id:
        postings = _sale_postings_left(
            db, item, original_source_type, original_source_id, count_returns
        )
        for mv, left in postings:
            if remaining <= 0:
                break
            if left <= 0:
                continue
            take = min(left, remaining)
            cogs_id, asset_id = _sale_entry_accounts(db, mv)
            pieces.append(
                (take, Decimal(str(mv.unit_cost or 0)), cogs_id, asset_id, mv)
            )
            remaining -= take
    if remaining > 0:
        if postings:
            logger.warning(
                "%s of %s #%s for item %s exceeds its sale postings; "
                "the remainder is costed at avg_cost",
                remaining,
                original_source_type,
                original_source_id,
                item.id,
            )
        pieces.append((remaining, Decimal(str(item.avg_cost or 0)), None, None, None))
    return pieces


def return_cost_accounts(db: Session, item: Item, piece) -> tuple:
    """(asset_account_id, cogs_account_id) a returned piece's cost goes back
    on (#641): the accounts the sale posting's entry used; a posting that
    put no entry on the books could only have gone to the chart's COGS
    account, never the item's (as reverse_sale falls back); a piece with no
    posting (no invoice named, or more than its postings cover) goes where
    a sale of the item would post today. Either may be None."""
    _quantity, _cost, cogs_id, asset_id, sale = piece
    asset_id = asset_id or get_inventory_asset_account_id(db, item)
    if sale is None:
        return asset_id, get_cogs_account_id(db, item)
    return asset_id, cogs_id or get_cogs_account_id(db)


def reverse_sale(
    db: Session,
    item: Item,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    original_source_type: Optional[str] = None,
    original_source_id: Optional[int] = None,
    txn_date=None,
    post_journal: bool = True,
) -> Optional[InventoryMovement]:
    """Reverse a previous sale (invoice void).

    `post_journal=False` returns the stock without the cost entry, for a
    sale whose cost was carried by a posting being reversed with it (a
    QuickBooks Online sale's import posting).

    CRITICAL: we must reverse at the ORIGINAL sale's unit_cost, not the
    current avg_cost. If cost moved between sale and void, using the
    current cost would leave a permanent GL imbalance (the original COGS
    debit wouldn't match the reversal credit).

    Looks up the original SALE movement by (source_type, source_id) and
    uses its unit_cost. Falls back to current avg_cost if we can't find
    the original (legacy rows, manual corrections) but logs the fact.

    Stock coming back into a count below zero fills the hole first (W-60):
    what the units sold ahead were costed at beyond the sale's cost comes
    back out of the item's cost of goods account under the reversal
    (_append_at_cost), so Inventory reads quantity x average cost after.
    """
    if not item.track_inventory:
        return None

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        return None

    # The reversal comes off where the sale went on: one piece per sale
    # posting at its own cost, against its own accounts (sb-qa; sale_pieces
    # does the walk), and with no original to match, the current avg_cost
    # and the chart's accounts, as before. A credit memo's return (#641) is
    # not counted against the postings here: a sale voided after a return
    # still comes off its posting in full, at its cost, so the sale, the
    # return and their voids cancel to the cent.
    pieces = sale_pieces(db, item, quantity, original_source_type, original_source_id)

    txn_id = None
    if post_journal:
        fallback_asset = get_inventory_asset_account_id(db, item)
        # a sale that posted no entry could only have gone to the chart's
        # account, never the item's, so the fallback is item-less
        fallback_cogs = get_cogs_account_id(db)
        by_accounts = {}
        for q, c, cogs_id, asset_id, _mv in pieces:
            key = (asset_id or fallback_asset, cogs_id or fallback_cogs)
            by_accounts[key] = by_accounts.get(key, Decimal("0")) + _q(q * c)
        lines = []
        for (asset_id, cogs_id), piece_amount in by_accounts.items():
            if piece_amount <= 0:
                continue
            if not (asset_id and cogs_id):
                logger.warning(
                    "reverse_sale: no inventory or COGS account for a %s piece of "
                    "item %s; its stock is reversed but no entry is posted for it",
                    piece_amount,
                    item.id,
                )
                continue
            lines.append(
                {
                    "account_id": asset_id,
                    "debit": piece_amount,
                    "credit": Decimal("0"),
                    "description": f"Inventory reversal: {item.name}",
                }
            )
            lines.append(
                {
                    "account_id": cogs_id,
                    "debit": Decimal("0"),
                    "credit": piece_amount,
                    "description": f"COGS reversal: {item.name}",
                }
            )
        if lines:
            from datetime import date as _date

            txn = create_journal_entry(
                db,
                txn_date or _date.today(),
                f"COGS reversal — {item.name}",
                lines,
                source_type=source_type,
                source_id=source_id,
            )
            txn_id = txn.id

    last = None
    for q, c, _cogs, _asset, mv in pieces:
        piece = dict(
            quantity=q,
            unit_cost=c,
            source_type=source_type,
            source_id=source_id,
            transaction_id=txn_id,
            memo="Sale reversal" if mv is not None else _BEYOND_POSTINGS_MEMO,
            reverses_movement_id=mv.id if mv is not None else None,
        )
        if post_journal:
            last = _append_at_cost(
                db,
                item,
                MovementType.VOID,
                txn_date=txn_date,
                entry_memo="Sale reversal",
                **piece,
            )
        else:
            last = _append_movement(db, item, MovementType.VOID, **piece)
    return last


def receipts_left(db: Session, source_type: str, source_id: int) -> list:
    """The PURCHASE movements a document recorded, oldest first, each with
    the quantity it still has in stock: [(movement, left), ...], empty when
    it recorded none. A receipt's remainder is what the reversals naming it
    left (a bill edit's, a bill void's, #646). A reversal from before the
    link existed names nothing; its quantity comes off the oldest receipts
    of its item first, as _sale_postings_left charges a sale's, so a bill
    edited on an earlier release and voided now reverses only the receipt
    the edit left standing."""
    receipts = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == source_type,
            InventoryMovement.source_id == source_id,
            InventoryMovement.movement_type == MovementType.PURCHASE,
        )
        .order_by(InventoryMovement.id)
        .all()
    )
    if not receipts:
        return []
    taken = {
        receipt_id: Decimal(str(total or 0))
        for receipt_id, total in db.query(
            InventoryMovement.reverses_movement_id,
            func.coalesce(func.sum(InventoryMovement.quantity), 0),
        )
        .filter(
            InventoryMovement.movement_type == MovementType.VOID,
            InventoryMovement.reverses_movement_id.in_([r.id for r in receipts]),
        )
        .group_by(InventoryMovement.reverses_movement_id)
        .all()
    }
    unattributed = {
        item_id: abs(Decimal(str(total or 0)))
        for item_id, total in db.query(
            InventoryMovement.item_id,
            func.coalesce(func.sum(InventoryMovement.quantity), 0),
        )
        .filter(
            InventoryMovement.movement_type == MovementType.VOID,
            InventoryMovement.source_type.in_(
                (f"{source_type}_void", f"{source_type}_edit")
            ),
            InventoryMovement.source_id == source_id,
            InventoryMovement.reverses_movement_id.is_(None),
        )
        .group_by(InventoryMovement.item_id)
        .all()
    }
    out = []
    for mv in receipts:
        # a VOID's quantity is negative, so adding it is taking it off
        room = max(
            Decimal(str(mv.quantity)) + taken.get(mv.id, Decimal("0")), Decimal("0")
        )
        legacy = min(room, unattributed.get(mv.item_id, Decimal("0")))
        unattributed[mv.item_id] = unattributed.get(mv.item_id, Decimal("0")) - legacy
        out.append((mv, room - legacy))
    return out


def _post_residue(
    db: Session,
    item: Item,
    residue: Decimal,
    source_type: str,
    source_id: int,
    txn_date,
    memo: Optional[str],
) -> Optional[int]:
    """Post what a movement at a cost of its own left between the ledger's
    Inventory and quantity x average (_pool_after's residue, #647), under the
    caller's source: Dr Cost of Goods Sold / Cr Inventory for value left
    with no stock to carry it (a take-out that empties the shelf, stock into
    a count below zero that cost more than the units sold ahead were costed
    at), Dr Inventory / Cr Cost of Goods Sold for the reverse (a take-out
    worth more than the stock held, stock into the hole that cost less). The
    entry is "{memo} - Inventory set to quantity x average cost: {item}",
    so a drill-down says why it is there. The cost side is the item's
    COGS account (its own, else the chart's) and the asset side the item's
    inventory account, where a sale of the item would post today. Returns
    the entry's id, or None when there is nothing to post or no account to
    post it to (logged, as reverse_sale logs a piece it cannot place)."""
    amount = _q(residue)
    if amount == 0:
        return None
    asset_id = get_inventory_asset_account_id(db, item)
    cogs_id = get_cogs_account_id(db, item)
    if not (asset_id and cogs_id):
        logger.warning(
            "%s #%s: no inventory or COGS account for item %s; the %s between "
            "Inventory and quantity x average cost is not posted",
            source_type,
            source_id,
            item.id,
            amount,
        )
        return None
    if amount > 0:
        lines = [
            {
                "account_id": cogs_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"COGS: {item.name}",
            },
            {
                "account_id": asset_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"Inventory: {item.name}",
            },
        ]
    else:
        lines = [
            {
                "account_id": asset_id,
                "debit": -amount,
                "credit": Decimal("0"),
                "description": f"Inventory: {item.name}",
            },
            {
                "account_id": cogs_id,
                "debit": Decimal("0"),
                "credit": -amount,
                "description": f"COGS: {item.name}",
            },
        ]
    from datetime import date as _date

    txn = create_journal_entry(
        db,
        txn_date or _date.today(),
        f"{memo or 'Inventory'} - Inventory set to quantity x average cost: "
        f"{item.name}",
        lines,
        source_type=source_type,
        source_id=source_id,
    )
    return txn.id


def reverse_at_cost(
    db: Session,
    item: Item,
    movement: InventoryMovement,
    quantity: Decimal,
    source_type: str,
    source_id: int,
    txn_date=None,
    transaction_id: Optional[int] = None,
    memo: Optional[str] = None,
) -> InventoryMovement:
    """Reverse `quantity` of a receipt or a return at the cost that movement
    carried: one VOID movement naming it (reverses_movement_id), linked to
    the entry the caller posted for the cost (`transaction_id`; a bill's
    void passes its reversing entry, an edit that re-posts the bill's own
    entry nothing). `quantity` is signed as the movement is: a receipt's or
    a customer's return is positive and goes back out of stock, a return to
    the vendor is negative and comes back in. The caller's entry mirrors
    what the movement put on Inventory, and what the pool cannot absorb of
    it (#647) is posted here under the caller's source (_post_residue), so
    afterwards Inventory in the ledger reads quantity x average cost, 0.00
    with nothing on hand. Used by a bill's void and edit
    (reverse_receipts_for_bill, #646), a credit memo's void
    (reverse_return_for_credit_memo, #641) and a vendor credit's void
    (reverse_returns_for_vendor_credit, W-74)."""
    return _append_at_cost(
        db,
        item,
        MovementType.VOID,
        quantity=-Decimal(str(quantity)),
        unit_cost=Decimal(str(movement.unit_cost or 0)),
        source_type=source_type,
        source_id=source_id,
        txn_date=txn_date,
        transaction_id=transaction_id,
        memo=memo,
        reverses_movement_id=movement.id,
    )


def _append_at_cost(
    db: Session,
    item: Item,
    movement_type: MovementType,
    quantity: Decimal,
    unit_cost: Decimal,
    source_type: str,
    source_id: int,
    txn_date=None,
    transaction_id: Optional[int] = None,
    memo: Optional[str] = None,
    reverses_movement_id: Optional[int] = None,
    entry_memo: Optional[str] = None,
) -> InventoryMovement:
    """One movement at a cost of its own, and what the pool cannot carry of
    it posted under the caller's source (#647, both ways): the residue
    _pool_after reports goes Dr Cost of Goods Sold / Cr Inventory or the
    reverse (_post_residue), so afterwards Inventory in the ledger reads
    quantity x average cost. The caller's own entry carries quantity x
    unit_cost on Inventory (a bill's lines, a vendor credit's, a void's
    reversing entry, a sale's reversal, a memo's return, an adjustment's).
    _append_movement stays journal-free; this is the one place a
    movement's residue is posted. `entry_memo` names the residue entry
    where the movement's memo would not (an adjustment's memo is the
    user's)."""
    _new_qty, _new_avg, residue = _pool_after(
        Decimal(str(item.quantity_on_hand or 0)),
        Decimal(str(item.avg_cost or 0)),
        quantity,
        unit_cost,
    )
    mv = _append_movement(
        db,
        item,
        movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        source_type=source_type,
        source_id=source_id,
        transaction_id=transaction_id,
        memo=memo,
        reverses_movement_id=reverses_movement_id,
    )
    _post_residue(
        db, item, residue, source_type, source_id, txn_date, entry_memo or memo
    )
    return mv


def current_valuation(db: Session) -> dict:
    """Return aggregate inventory valuation: total_value, item_count, low_stock_count."""
    items = db.query(Item).filter(Item.track_inventory, Item.is_active).all()  # noqa
    total_value = Decimal("0")
    item_count = 0
    low_stock = 0
    for it in items:
        qty = Decimal(str(it.quantity_on_hand or 0))
        avg = Decimal(str(it.avg_cost or 0))
        total_value += qty * avg
        item_count += 1
        if Decimal(str(it.reorder_point or 0)) > 0 and qty <= Decimal(
            str(it.reorder_point)
        ):
            low_stock += 1
    total_value = _q(total_value)
    ledger = inventory_ledger_balance(db, items)
    return {
        "total_value": float(total_value),
        "item_count": item_count,
        "low_stock_count": low_stock,
        "ledger_balance": float(ledger) if ledger is not None else None,
        "ledger_note": ledger_difference_note(ledger, total_value),
    }


def inventory_ledger_balance(db: Session, items) -> Optional[Decimal]:
    """The ledger's Inventory balance (#821): what 1300 Inventory and every
    tracked item's own asset account hold, debits less credits. None for a
    chart with neither."""
    ids = {get_inventory_asset_account_id(db, it) for it in items}
    inv = db.query(Account).filter(Account.account_number == "1300").first()
    if inv is not None:
        ids.add(inv.id)
    ids.discard(None)
    if not ids:
        return None
    debit, credit = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id.in_(ids))
        .one()
    )
    return _q(Decimal(str(debit)) - Decimal(str(credit)))


def ledger_difference_note(ledger: Optional[Decimal], valuation) -> Optional[str]:
    """The line under the valuation's totals when the ledger's Inventory
    balance is not what the items are worth (#821): a purchase line saved on
    Inventory before 2.31.14, or a hand journal entry to it, moves the
    ledger and no item. None when they agree."""
    if ledger is None:
        return None
    valuation = _q(valuation)
    if ledger == valuation:
        return None
    return (
        f"The ledger's Inventory balance is {_money(ledger)}, the valuation "
        f"{_money(valuation)}; the difference is on postings that moved no item"
    )


def _money(amount: Decimal) -> str:
    return f"-${-amount:,.2f}" if amount < 0 else f"${amount:,.2f}"
