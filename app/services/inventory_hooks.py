# ============================================================================
# Phase 11 (post-audit fix): Centralized inventory hooks.
#
# Every path that creates or modifies an invoice/bill/credit-memo goes through
# these helpers so nobody forgets to post the inventory movement + COGS JE.
#
# Callers: invoices.create/update/void/duplicate, estimates.convert_to_invoice,
# credit_memos.create/void, recurring_service.generate_due_invoices, iif_import,
# bills.create/update/void, vendor_credits.create/void.
#
# Design rule: all helpers are NO-OPS if track_inventory=False on the item,
# so they're safe to call unconditionally for every line.
# ============================================================================

import logging
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.items import InventoryMovement, Item, MovementType
from app.models.transactions import Transaction
from app.services.accounting import _q, create_journal_entry, reversing_lines
from app.services.inventory_service import (
    receipts_left,
    record_sale,
    return_cost_accounts,
    reverse_at_cost,
    reverse_sale,
    sale_pieces,
    _append_at_cost,
)

logger = logging.getLogger(__name__)


def _get_item(db: Session, item_id: Optional[int]) -> Optional[Item]:
    if not item_id:
        return None
    return db.query(Item).filter(Item.id == item_id).first()


def post_sale_for_invoice(
    db: Session, invoice, txn_date=None, post_journal: bool = True
) -> None:
    """Iterate an invoice's lines and record_sale for every inventory item.

    Safe to call on any invoice — non-inventory items are silently skipped.
    Uses invoice.date as the transaction date so the COGS posting lands in
    the same accounting period as the sale. `post_journal=False` moves the
    stock without the COGS entry (record_sale).
    """
    date_to_use = txn_date or invoice.date
    ref = getattr(invoice, "invoice_number", None) or f"inv#{invoice.id}"
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_from_db

    terms = terms_from_db(db)
    for line in invoice.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            record_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice",
                source_id=invoice.id,
                memo=f"{document_label(invoice, terms)} {ref}",
                txn_date=date_to_use,
                post_journal=post_journal,
            )


def reverse_sale_for_invoice(
    db: Session,
    invoice,
    txn_date=None,
    memo: str = "Sale reversal",
) -> None:
    """Reverse every SALE movement originally booked against this invoice.

    Used by void_invoice and (via update_invoice) when an edit needs to
    recompute from scratch. Passes original_source to reverse_sale so
    historical unit_cost is used for the reversal JE — keeps the GL balanced
    even if avg_cost has drifted.
    """
    date_to_use = txn_date or invoice.date
    for line in invoice.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            reverse_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=date_to_use,
            )


def _memo_ref(credit_memo) -> str:
    return getattr(credit_memo, "memo_number", None) or f"cm#{credit_memo.id}"


def post_return_for_credit_memo(db: Session, credit_memo, txn_date=None) -> None:
    """A credit memo returns stock: each inventory line's quantity goes back
    on the shelf, and the cost of what came back goes back with it, one
    journal entry per memo, DR Inventory Asset / CR COGS (#641), which the
    return movements link to (transaction_id).

    The cost is the original sale's when the memo names an invoice: the
    quantity comes off that invoice's sale postings the way a void does
    (sale_pieces, newest first, earlier reversals and returns already
    taken off), each piece at the posting's own cost and back onto the
    accounts its entry used, with one RETURN_IN movement per piece naming
    the posting. What the postings do not cover, or every line of a memo
    naming no invoice, is at the item's current avg_cost onto the item's
    (else the chart's) accounts, the cost the return already used for the
    quantity. The same item on two lines returns their total. A line whose
    item is not tracked, or of zero quantity, moves and posts nothing. A
    return into a count below zero fills the hole first; what the units
    sold ahead were costed at beyond the return's cost comes back out of
    the item's cost of goods account under the memo (W-60).
    """
    date_to_use = txn_date or credit_memo.date
    ref = _memo_ref(credit_memo)
    invoice_id = getattr(credit_memo, "original_invoice_id", None)

    wanted: dict = {}  # item_id -> [item, quantity], in line order
    for line in credit_memo.lines:
        item = _get_item(db, line.item_id)
        quantity = Decimal(str(line.quantity or 0))
        if not (item and item.track_inventory) or quantity <= 0:
            continue
        wanted.setdefault(item.id, [item, Decimal("0")])[1] += quantity

    returns = []  # (item, piece)
    for item, quantity in wanted.values():
        returns.extend(
            (item, piece)
            for piece in sale_pieces(
                db,
                item,
                quantity,
                "invoice" if invoice_id else None,
                invoice_id,
                count_returns=True,
            )
        )
    if not returns:
        return

    # One debit/credit pair per item and pair of accounts, in return order.
    amounts: dict = {}
    for item, piece in returns:
        amount = _q(piece[0] * piece[1])
        if amount <= 0:
            continue
        asset_id, cogs_id = return_cost_accounts(db, item, piece)
        if not (asset_id and cogs_id):
            logger.warning(
                "credit memo %s: no inventory or COGS account for a %s return of "
                "item %s; its stock is returned but no entry is posted for it",
                ref,
                amount,
                item.id,
            )
            continue
        key = (item.id, item.name, asset_id, cogs_id)
        amounts[key] = amounts.get(key, Decimal("0")) + amount
    lines = []
    for (_item_id, name, asset_id, cogs_id), amount in amounts.items():
        lines.append(
            {
                "account_id": asset_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"Inventory returned: {name}",
            }
        )
        lines.append(
            {
                "account_id": cogs_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"COGS returned: {name}",
            }
        )
    txn_id = None
    if lines:
        txn = create_journal_entry(
            db,
            date_to_use,
            f"Credit Memo {ref} - returned stock",
            lines,
            source_type="credit_memo",
            source_id=credit_memo.id,
        )
        txn_id = txn.id

    for item, (quantity, unit_cost, _cogs, _asset, sale) in returns:
        _append_at_cost(
            db,
            item,
            MovementType.RETURN_IN,
            quantity=quantity,
            unit_cost=unit_cost,
            source_type="credit_memo",
            source_id=credit_memo.id,
            txn_date=date_to_use,
            transaction_id=txn_id,
            memo=f"Return: {ref}",
            reverses_movement_id=sale.id if sale is not None else None,
        )


def reverse_return_for_credit_memo(db: Session, credit_memo, txn_date=None) -> None:
    """Void a credit memo's return (#641): the stock it put back goes out
    again and its cost entry is reversed, mirror-image. Every RETURN_IN
    movement the memo made gets a VOID movement of the same quantity at the
    same cost naming it (so the ledger nets to zero at the return's cost,
    as a sale's void does), and the entry the returns link to is reversed
    line for line under "credit_memo_void". A memo from before the entry
    existed only has its stock taken back out. What the pool cannot absorb
    of a return's cost (its void landing the count on zero, or worth more
    than the stock holds) is posted to the item's COGS account under the
    void (reverse_at_cost, #647). An item no longer tracked is left alone,
    as a sale's reversal leaves it.
    """
    date_to_use = txn_date or credit_memo.date
    ref = _memo_ref(credit_memo)
    returns = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "credit_memo",
            InventoryMovement.source_id == credit_memo.id,
            InventoryMovement.movement_type == MovementType.RETURN_IN,
        )
        .order_by(InventoryMovement.id)
        .all()
    )
    reversal_of: dict = {}  # return entry id -> reversing entry id
    for mv in returns:
        if not mv.transaction_id or mv.transaction_id in reversal_of:
            continue
        reversal_of[mv.transaction_id] = None
        txn = db.get(Transaction, mv.transaction_id)
        if txn is None:
            continue
        reversal_of[mv.transaction_id] = create_journal_entry(
            db,
            date_to_use,
            f"VOID Credit Memo {ref} - returned stock",
            reversing_lines(txn.lines),
            source_type="credit_memo_void",
            source_id=credit_memo.id,
        ).id
    for mv in returns:
        item = mv.item
        quantity = Decimal(str(mv.quantity or 0))
        if not (item and item.track_inventory) or quantity <= 0:
            continue
        reverse_at_cost(
            db,
            item,
            mv,
            quantity,
            source_type="credit_memo_void",
            source_id=credit_memo.id,
            txn_date=date_to_use,
            transaction_id=reversal_of.get(mv.transaction_id),
            memo=f"VOID Credit Memo {ref}",
        )


def reverse_receipts_for_bill(
    db: Session,
    bill,
    source_type: str,
    txn_date=None,
    memo: Optional[str] = None,
    transaction_id: Optional[int] = None,
) -> None:
    """Take a bill's receipts back out of stock (#646): each PURCHASE
    movement the bill recorded, for what it still has in stock, at the cost
    it was received at (the line's rate with its share of the tax,
    purchase_posting.unit_cost_with_tax), one VOID movement per receipt
    naming it, the way a sale's void walks its postings (reverse_sale) and
    a memo's void its returns. Used by the void ("bill_void", with the
    reversing entry to link) and by an edit that re-posts the lines
    ("bill_edit"; the new quantities are received again after it).

    The journal side is the caller's: the void's reversing entry and the
    edit's re-post both reverse the bill's own Inventory lines, the very
    amounts the receipts were costed from, so Inventory in the ledger and
    quantity x average cost come out one figure. What the pool cannot absorb
    is posted to the item's COGS account under the same source
    (reverse_at_cost, #647). A receipt of an item no longer tracked is left
    alone, as a sale's reversal leaves it.

    `memo` names each reversal with the receipt it reverses in place of
    "{receipt}": the receipt's own memo ("Bill B-2 line 2" since W-68), else
    the bill's number, so "VOID {receipt}" reads "VOID Bill B-2 line 2" and
    the two reversals of a two-line bill no longer read the same.
    """
    date_to_use = txn_date or bill.date
    for receipt, left in receipts_left(db, "bill", bill.id):
        item = receipt.item
        if not (item and item.track_inventory) or left <= 0:
            continue
        label = receipt.memo or f"Bill {bill.bill_number}"
        reverse_at_cost(
            db,
            item,
            receipt,
            left,
            source_type=source_type,
            source_id=bill.id,
            txn_date=date_to_use,
            transaction_id=transaction_id,
            memo=memo.format(receipt=label) if memo else None,
        )


def reverse_returns_for_vendor_credit(
    db: Session,
    vendor_credit,
    txn_date=None,
    transaction_id: Optional[int] = None,
) -> None:
    """Put a vendor credit's returned stock back on the shelf (W-74, 2.31.1
    gate): each RETURN_OUT movement the credit recorded comes back at the
    cost it went out at (the line's rate with its share of the tax, what
    the credit took off Inventory), one VOID movement per return naming it
    and linked to the void's reversing entry (`transaction_id`), the way a
    bill's void walks its receipts. It used to put each line's quantity
    back at the rate alone, so a taxed credit's void left Inventory above
    quantity x average cost.

    The journal side is the caller's: the void's reversing entry mirrors
    the credit's own, the very amounts the returns were costed from. What
    the pool cannot absorb (stock coming back into a count below zero fills
    the hole first, W-60) is posted to the item's COGS account under
    "vendor_credit_void" (reverse_at_cost, #647), so Inventory reads
    quantity x average cost afterwards. A return of an item no longer
    tracked is left alone, as a sale's reversal leaves it. A credit has no
    edit, so each return is reversed whole, once.
    """
    date_to_use = txn_date or vendor_credit.date
    returns = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "vendor_credit",
            InventoryMovement.source_id == vendor_credit.id,
            InventoryMovement.movement_type == MovementType.RETURN_OUT,
        )
        .order_by(InventoryMovement.id)
        .all()
    )
    for mv in returns:
        item = mv.item
        quantity = Decimal(str(mv.quantity or 0))
        if not (item and item.track_inventory) or quantity >= 0:
            continue
        reverse_at_cost(
            db,
            item,
            mv,
            quantity,
            source_type="vendor_credit_void",
            source_id=vendor_credit.id,
            txn_date=date_to_use,
            transaction_id=transaction_id,
            # "VOID Vendor credit VC-0001 line 2": the return's own memo, so
            # each reversal names the line it reverses (W-68's rule for bills)
            memo="VOID " + (mv.memo or f"Vendor credit {vendor_credit.credit_number}"),
        )


def oversold_by_reversal(db: Session, bill, lines=()) -> list[dict[str, Any]]:
    """The stock items a bill's void or edit takes past what is on hand
    (W-67, 2.31.1 gate), in oversold_items' shape. What the bill's receipts
    still have in stock comes out (reverse_receipts_for_bill); on an edit
    the new `lines` are received again, so only the net take per item is
    measured against the count, and a void or edit that lands exactly on
    zero is not past the stock. Reads the books and writes nothing, so the
    route can ask before anything is saved."""
    taking = [
        SimpleNamespace(item_id=receipt.item_id, quantity=left)
        for receipt, left in receipts_left(db, "bill", bill.id)
        if left > 0
    ]
    receiving = [
        line
        for line in lines or ()
        if getattr(line, "item_id", None)
        and Decimal(str(getattr(line, "quantity", 0) or 0)) > 0
    ]
    return oversold_items(db, taking, receiving)


# ---------------------------------------------------------------------------
# Diff-based reconciliation for PUT /api/invoices/{id}
# ---------------------------------------------------------------------------


def reconcile_invoice_inventory_delta(
    db: Session,
    invoice,
    old_line_snapshot: list[dict[str, Any]],
    txn_date=None,
    post_journal: bool = True,
) -> None:
    """After an invoice edit that replaced all InvoiceLines, compare the
    old snapshot against the new lines and post compensating movements:

      - If an item's qty increased by N (or a new line was added): record_sale(N)
      - If an item's qty decreased by N (or a line was removed):    reverse_sale(N)

    This is the ONLY correct way to keep the inventory ledger consistent
    with user edits without a full void+recreate.

    `old_line_snapshot` is a list of {"item_id": int, "quantity": Decimal}
    captured BEFORE the route's .delete() + rebuild of InvoiceLines.
    """
    date_to_use = txn_date or invoice.date

    # Aggregate old qty per item_id
    old_qty: dict[int, Decimal] = {}
    for row in old_line_snapshot:
        item_id = row.get("item_id")
        if not item_id:
            continue
        old_qty[item_id] = old_qty.get(item_id, Decimal("0")) + Decimal(
            str(row["quantity"])
        )

    # Aggregate new qty per item_id
    new_qty: dict[int, Decimal] = {}
    for line in invoice.lines:
        if not line.item_id:
            continue
        new_qty[line.item_id] = new_qty.get(line.item_id, Decimal("0")) + Decimal(
            str(line.quantity)
        )

    all_items = set(old_qty) | set(new_qty)
    for item_id in all_items:
        item = _get_item(db, item_id)
        if not (item and item.track_inventory):
            continue
        old_q = old_qty.get(item_id, Decimal("0"))
        new_q = new_qty.get(item_id, Decimal("0"))
        delta = new_q - old_q
        if delta > 0:
            # Additional qty sold
            record_sale(
                db,
                item,
                quantity=delta,
                source_type="invoice",
                source_id=invoice.id,
                memo=f"Edit +{delta}",
                txn_date=date_to_use,
                post_journal=post_journal,
            )
        elif delta < 0:
            # Qty reduced — reverse the difference
            reverse_sale(
                db,
                item,
                quantity=-delta,
                source_type="invoice_edit",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=date_to_use,
                post_journal=post_journal,
            )


# ---------------------------------------------------------------------------
# Selling past what is on hand (#642)
# ---------------------------------------------------------------------------


def _quantity_by_item(lines) -> dict[int, Decimal]:
    """The quantity of each item on these lines (the lines of one item added
    together); a line with no item is left out. Takes the lines of a
    request and the stored lines of an invoice alike: both carry item_id
    and quantity."""
    out: dict[int, Decimal] = {}
    for line in lines or ():
        item_id = getattr(line, "item_id", None)
        if not item_id:
            continue
        out[item_id] = out.get(item_id, Decimal("0")) + Decimal(
            str(getattr(line, "quantity", 0) or 0)
        )
    return out


def plain_quantity(value) -> str:
    """A quantity in plain figures for a sentence: 2, -4, 2.5; never a tail
    of zeros."""
    text = format(Decimal(str(value or 0)).normalize(), "f")
    return "0" if text in ("0", "-0") else text


def oversold_items(db: Session, lines, before=()) -> list[dict[str, Any]]:
    """The tracked items these lines take past what is on hand, as
    {"item", "on_hand", "after"}: the count now and the count the sale
    would leave. ``before`` is the invoice's stored lines when this is an
    edit, so only what the edit adds is taken off the count (the stored
    lines already came out of stock). A sale that lands exactly on zero is
    not past the stock. Reads the books and writes nothing, so a route can
    ask before anything is saved."""
    wanted = _quantity_by_item(lines)
    had = _quantity_by_item(before)
    out = []
    for item_id, quantity in wanted.items():
        taken = quantity - had.get(item_id, Decimal("0"))
        if taken <= 0:
            continue
        item = _get_item(db, item_id)
        if not (item and item.track_inventory):
            continue
        on_hand = Decimal(str(item.quantity_on_hand or 0))
        if taken <= on_hand:
            continue
        out.append({"item": item, "on_hand": on_hand, "after": on_hand - taken})
    return out


def oversell_warning(short: dict[str, Any], action: str = "sale") -> str:
    """One sentence for a sale past the stock, as the response carries it:
    'Widget: 2 on hand; this sale takes it to -4.' A bill's void or edit
    that takes stock already sold below zero says "this void" or "this
    edit" (W-67), and a vendor credit that returns more than is on hand
    "this credit" (W-74)."""
    return (
        f"{short['item'].name}: {plain_quantity(short['on_hand'])} on hand; "
        f"this {action} takes it to {plain_quantity(short['after'])}."
    )


# What to do about a refused sale, void or edit, by what was refused.
_OVERSELL_WAY_OUT = {
    "sale": (
        "Enter the receiving bill first, or turn off Refuse a sale of more "
        "than is on hand under Settings."
    ),
    "void": (
        "Void the sale that took the stock first, or turn off Refuse a sale "
        "of more than is on hand under Settings."
    ),
    "edit": (
        "Keep at least what has sold on the bill, void the sale that took it, "
        "or turn off Refuse a sale of more than is on hand under Settings."
    ),
    "slip": (
        "Receive the stock on a bill first, or turn off Refuse a sale of more "
        "than is on hand under Settings."
    ),
    "credit": (
        "Return no more than is on hand, enter the bill that received the "
        "stock first, or turn off Refuse a sale of more than is on hand under "
        "Settings."
    ),
}


def oversell_refusal(shorts: list[dict[str, Any]], action: str = "sale") -> str:
    """The sentence a refused sale (or a bill's refused void or edit, W-67,
    or a refused vendor credit, W-74) answers with when Settings says to
    refuse: each item, its count and how many short, and what to do.
    "Short" is this sale's part (#813): with the count already below zero,
    the shortfall from before is not this sale's, and the count it takes
    the item to says the whole of it."""
    parts = [
        f"{s['item'].name}: {plain_quantity(s['on_hand'])} on hand; this {action} "
        f"takes it to {plain_quantity(s['after'])}, "
        f"{plain_quantity(min(s['on_hand'] - s['after'], -s['after']))} short."
        for s in shorts
    ]
    return " ".join(parts) + " " + _OVERSELL_WAY_OUT[action]


def snapshot_invoice_lines(invoice) -> list[dict[str, Any]]:
    """Helper: capture the (item_id, quantity) of each line BEFORE an edit
    rebuilds them. Used by reconcile_invoice_inventory_delta."""
    return [
        {"item_id": line.item_id, "quantity": Decimal(str(line.quantity))}
        for line in invoice.lines
    ]
