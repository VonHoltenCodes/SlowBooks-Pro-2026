# ============================================================================
# Purchase Orders — CRUD + convert to bill
# Feature 6: Non-posting vendor documents
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine, POStatus
from app.models.contacts import Vendor
from app.schemas.purchase_orders import POCreate, POUpdate, POResponse
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase_orders"])


def _next_po_number(db: Session) -> str:
    last = db.query(sqlfunc.max(PurchaseOrder.po_number)).scalar()
    if last and last.replace("PO-", "").isdigit():
        num = int(last.replace("PO-", "")) + 1
        return f"PO-{num:04d}"
    return "PO-0001"


@router.get("", response_model=list[POResponse])
def list_pos(vendor_id: int = None, status: str = None, db: Session = Depends(get_db)):
    q = db.query(PurchaseOrder)
    if vendor_id:
        q = q.filter(PurchaseOrder.vendor_id == vendor_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    pos = q.order_by(PurchaseOrder.date.desc()).all()
    results = []
    for po in pos:
        resp = POResponse.model_validate(po)
        if po.vendor:
            resp.vendor_name = po.vendor.name
        results.append(resp)
    return results


@router.get("/{po_id}", response_model=POResponse)
def get_po(po_id: int, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    resp = POResponse.model_validate(po)
    if po.vendor:
        resp.vendor_name = po.vendor.name
    return resp


@router.post("", response_model=POResponse, status_code=201)
def create_po(data: POCreate, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    po_number = _next_po_number(db)
    gst_inputs = resolve_gst_line_inputs(db, data.lines)
    gst_totals = calculate_document_gst(
        gst_inputs,
        prices_include_gst=prices_include_gst(db),
        gst_context="purchase",
    )

    po = PurchaseOrder(
        po_number=po_number, vendor_id=data.vendor_id, date=data.date,
        expected_date=data.expected_date, ship_to=data.ship_to,
        subtotal=gst_totals.subtotal, tax_rate=gst_totals.effective_tax_rate, tax_amount=gst_totals.tax_amount,
        total=gst_totals.total, notes=data.notes,
    )
    db.add(po)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        line_total = gst_totals.lines[i]
        line = PurchaseOrderLine(
            purchase_order_id=po.id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=line_total.net_amount,
            gst_code=gst_code, gst_rate=gst_rate,
            line_order=line_data.line_order or i,
        )
        db.add(line)

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    resp.vendor_name = vendor.name
    return resp


@router.put("/{po_id}", response_model=POResponse)
def update_po(po_id: int, data: POUpdate, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    for key, val in data.model_dump(exclude_unset=True, exclude={"lines"}).items():
        if key == "status":
            setattr(po, key, POStatus(val))
        else:
            setattr(po, key, val)

    if data.lines is not None:
        db.query(PurchaseOrderLine).filter(PurchaseOrderLine.purchase_order_id == po_id).delete()
        gst_inputs = resolve_gst_line_inputs(db, data.lines)
        gst_totals = calculate_document_gst(
            gst_inputs,
            prices_include_gst=prices_include_gst(db),
            gst_context="purchase",
        )
        for i, line_data in enumerate(data.lines):
            gst_code, gst_rate = resolve_line_gst(db, line_data)
            amt = gst_totals.lines[i].net_amount
            db.add(PurchaseOrderLine(
                purchase_order_id=po_id, item_id=line_data.item_id,
                description=line_data.description, quantity=line_data.quantity,
                rate=line_data.rate, amount=amt, gst_code=gst_code, gst_rate=gst_rate,
                line_order=line_data.line_order or i,
            ))
        po.subtotal = gst_totals.subtotal
        po.tax_rate = gst_totals.effective_tax_rate
        po.tax_amount = gst_totals.tax_amount
        po.total = gst_totals.total

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    if po.vendor:
        resp.vendor_name = po.vendor.name
    return resp


@router.post("/{po_id}/convert-to-bill")
def convert_to_bill(po_id: int, db: Session = Depends(get_db)):
    """Convert a PO to a bill — creates bill with PO's line items."""
    from app.models.bills import Bill, BillLine, BillStatus

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if po.status == POStatus.CLOSED:
        raise HTTPException(status_code=400, detail="PO already closed")

    bill = Bill(
        bill_number=f"BILL-{po.po_number}", vendor_id=po.vendor_id, status=BillStatus.UNPAID,
        po_id=po.id, date=po.date, terms="Net 30",
        subtotal=po.subtotal, tax_rate=po.tax_rate, tax_amount=po.tax_amount,
        total=po.total, balance_due=po.total, notes=f"From {po.po_number}",
    )
    db.add(bill)
    db.flush()

    for poline in po.lines:
        db.add(BillLine(
            bill_id=bill.id, item_id=poline.item_id, description=poline.description,
            quantity=poline.quantity, rate=poline.rate, amount=poline.amount,
            gst_code=poline.gst_code, gst_rate=poline.gst_rate,
            line_order=poline.line_order,
        ))

    po.status = POStatus.CLOSED
    db.commit()
    return {"bill_id": bill.id, "message": f"Bill created from {po.po_number}"}
