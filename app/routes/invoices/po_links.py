"""Link an invoice to the purchase orders that fill it (#986).

POST links a purchase order to the invoice, DELETE unlinks it. Each answers
the invoice's linked purchase orders as they now stand. Nothing posts and
no total changes: the link is the purchase order's ``invoice_id``, written
through the session so the audit log (and so each document's Activity)
records it. A read-only or accountant sign-in is refused by the role gate,
as on every write.
"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.purchase_orders import PurchaseOrder
from app.routes.invoices._router import router
from app.schemas.invoices import LinkedPurchaseOrder
from app.services.invoice_po_links import (
    invoice_to_fill,
    linked_purchase_orders,
    refuse_linked_elsewhere,
)
from app.services.terminology import terms_from_db


def _po(db: Session, po_id: int) -> PurchaseOrder:
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po


@router.post(
    "/{invoice_id}/purchase-orders/{po_id}",
    response_model=list[LinkedPurchaseOrder],
)
def link_purchase_order(invoice_id: int, po_id: int, db: Session = Depends(get_db)):
    """Link a purchase order to this invoice. A PO already filling another
    invoice is refused with a sentence; linking it again here is a no-op."""
    po = _po(db, po_id)
    if po.invoice_id == invoice_id:
        return linked_purchase_orders(db, invoice_id)
    invoice_to_fill(db, invoice_id)
    refuse_linked_elsewhere(db, po, invoice_id)
    po.invoice_id = invoice_id
    db.commit()
    return linked_purchase_orders(db, invoice_id)


@router.delete(
    "/{invoice_id}/purchase-orders/{po_id}",
    response_model=list[LinkedPurchaseOrder],
)
def unlink_purchase_order(invoice_id: int, po_id: int, db: Session = Depends(get_db)):
    """Unlink a purchase order from this invoice. A PO that fills another
    invoice (or none) is not this invoice's to unlink."""
    po = _po(db, po_id)
    if po.invoice_id != invoice_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "po_not_linked",
                "message": terms_from_db(db).text(
                    f"Purchase order {po.po_number} does not fill this invoice."
                ),
            },
        )
    po.invoice_id = None
    db.commit()
    return linked_purchase_orders(db, invoice_id)
