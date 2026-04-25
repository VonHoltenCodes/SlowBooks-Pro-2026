# ============================================================================
# Phase 11 (post-audit fix): Centralized inventory hooks.
#
# Every path that creates or modifies an invoice/bill/credit-memo goes through
# these helpers so nobody forgets to post the inventory movement + COGS JE.
#
# Callers: invoices.create/update/void/duplicate, estimates.convert_to_invoice,
# credit_memos.create, recurring_service.generate_due_invoices, iif_import,
# qbo_import, bills.create/void.
#
# Design rule: all helpers are NO-OPS if track_inventory=False on the item,
# so they're safe to call unconditionally for every line.
# ============================================================================

from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.models.items import Item, InventoryMovement, MovementType
from app.services.inventory_service import (
    record_sale,
    record_purchase,
    reverse_sale,
    _append_movement,
)


def _get_item(db: Session, item_id: Optional[int]) -> Optional[Item]:
    if not item_id:
        return None
    return db.query(Item).filter(Item.id == item_id).first()


def post_sale_for_invoice(db: Session, invoice, txn_date=None) -> None:
    """Iterate an invoice's lines and record_sale for every inventory item.

    Safe to call on any invoice — non-inventory items are silently skipped.
    Uses invoice.date as the transaction date so the COGS posting lands in
    the same accounting period as the sale.
    """
    date_to_use = txn_date or invoice.date
    ref = getattr(invoice, "invoice_number", None) or f"inv#{invoice.id}"
    for line in invoice.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            record_sale(
                db, item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice", source_id=invoice.id,
                memo=f"Invoice {ref}",
                txn_date=date_to_use,
            )


def reverse_sale_for_invoice(
    db: Session, invoice, txn_date=None, memo: str = "Sale reversal",
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
                db, item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void", source_id=invoice.id,
                original_source_type="invoice", original_source_id=invoice.id,
                txn_date=date_to_use,
            )


def post_return_for_credit_memo(db: Session, credit_memo, txn_date=None) -> None:
    """A credit memo returns stock. For each inventory line, add qty back at
    the item's current avg_cost (approximation — original sale's cost isn't
    reliably linkable across invoices)."""
    _ = txn_date or credit_memo.date  # reserved: future COGS-reversal JE
    ref = getattr(credit_memo, "credit_memo_number", None) or f"cm#{credit_memo.id}"
    for line in credit_memo.lines:
        item = _get_item(db, line.item_id)
        if item and item.track_inventory:
            unit_cost = Decimal(str(item.avg_cost or 0))
            _append_movement(
                db, item, MovementType.RETURN_IN,
                quantity=Decimal(str(line.quantity)),
                unit_cost=unit_cost,
                source_type="credit_memo", source_id=credit_memo.id,
                memo=f"Return: {ref}",
            )


# ---------------------------------------------------------------------------
# Diff-based reconciliation for PUT /api/invoices/{id}
# ---------------------------------------------------------------------------


def reconcile_invoice_inventory_delta(
    db: Session,
    invoice,
    old_line_snapshot: list[dict[str, Any]],
    txn_date=None,
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
        old_qty[item_id] = old_qty.get(item_id, Decimal("0")) + Decimal(str(row["quantity"]))

    # Aggregate new qty per item_id
    new_qty: dict[int, Decimal] = {}
    for line in invoice.lines:
        if not line.item_id:
            continue
        new_qty[line.item_id] = new_qty.get(line.item_id, Decimal("0")) + Decimal(str(line.quantity))

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
                db, item,
                quantity=delta,
                source_type="invoice", source_id=invoice.id,
                memo=f"Edit +{delta}",
                txn_date=date_to_use,
            )
        elif delta < 0:
            # Qty reduced — reverse the difference
            reverse_sale(
                db, item,
                quantity=-delta,
                source_type="invoice_edit", source_id=invoice.id,
                original_source_type="invoice", original_source_id=invoice.id,
                txn_date=date_to_use,
            )


def snapshot_invoice_lines(invoice) -> list[dict[str, Any]]:
    """Helper: capture the (item_id, quantity) of each line BEFORE an edit
    rebuilds them. Used by reconcile_invoice_inventory_delta."""
    return [
        {"item_id": line.item_id, "quantity": Decimal(str(line.quantity))}
        for line in invoice.lines
    ]
