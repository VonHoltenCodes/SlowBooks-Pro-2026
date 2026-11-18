"""An invoice and the purchase orders that fill it (#986).

One invoice, many purchase orders: a reseller sells a mouse bought on one
vendor's PO and a monitor bought on another's, and each PO names the
invoice it was bought for (``purchase_orders.invoice_id``). A link is a
plain reference. Nothing posts, no total or balance changes, and voiding the
invoice leaves the links as they were (the PO still says which invoice it
was for). The link lives on the purchase order, so a PO that goes takes its
link with it.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models.invoices import Invoice
from app.models.purchase_orders import PurchaseOrder
from app.services.terminology import terms_from_db


def _status(value) -> str:
    return getattr(value, "value", value) or ""


def linked_purchase_orders(db: Session, invoice_id: int) -> list[dict]:
    """The purchase orders that fill an invoice, oldest first: what the
    invoice's Linked purchase orders panel lists."""
    pos = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.vendor))
        .filter(PurchaseOrder.invoice_id == invoice_id)
        .order_by(PurchaseOrder.date, PurchaseOrder.id)
        .all()
    )
    return [
        {
            "id": po.id,
            "po_number": po.po_number,
            "vendor_name": po.vendor.name if po.vendor else None,
            "status": _status(po.status),
            "total": po.total,
        }
        for po in pos
    ]


def invoice_to_fill(db: Session, invoice_id: int) -> Invoice:
    """The invoice a purchase order is being linked to, or a sentence why
    not: there is no such invoice, or it is void (a void invoice keeps the
    links it had, and takes no new one)."""
    terms = terms_from_db(db)
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(
            status_code=404, detail=terms.text("That invoice was not found.")
        )
    if _status(inv.status) == "void":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoice_void",
                "message": terms.text(
                    f"This invoice, {inv.invoice_number}, is void, so no "
                    "purchase order can be linked to it."
                ),
            },
        )
    if getattr(inv, "is_sales_receipt", False):
        # paid at the time of sale: the window offers no link on one and
        # draws no panel there, so the API refuses it too (#1018)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "sales_receipt",
                "message": terms.text(
                    f"Sales receipt {inv.invoice_number} carries no balance for "
                    "a purchase order to fill, so none can be linked to it."
                ),
            },
        )
    return inv


def refuse_linked_elsewhere(db: Session, po: PurchaseOrder, invoice_id: int) -> None:
    """A purchase order fills one invoice: linking it from a second
    invoice is refused with a sentence naming the first."""
    if po.invoice_id is None or po.invoice_id == invoice_id:
        return
    other = db.get(Invoice, po.invoice_id)
    num = f" {other.invoice_number}" if other else ""
    raise HTTPException(
        status_code=409,
        detail={
            "code": "po_linked_elsewhere",
            "message": terms_from_db(db).text(
                f"Purchase order {po.po_number} already fills invoice{num}. "
                "Unlink it there first."
            ),
        },
    )
