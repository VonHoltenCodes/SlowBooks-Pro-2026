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
from app.services.accounting import compute_line_totals

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
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    po = PurchaseOrder(
        po_number=po_number, vendor_id=data.vendor_id, date=data.date,
        expected_date=data.expected_date, ship_to=data.ship_to,
        subtotal=subtotal, tax_rate=data.tax_rate, tax_amount=tax_amount,
        total=total, notes=data.notes,
    )
    db.add(po)
    db.flush()

    for i, line_data in enumerate(data.lines):
        line = PurchaseOrderLine(
            purchase_order_id=po.id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate)),
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
        for i, line_data in enumerate(data.lines):
            amt = Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
            db.add(PurchaseOrderLine(
                purchase_order_id=po_id, item_id=line_data.item_id,
                description=line_data.description, quantity=line_data.quantity,
                rate=line_data.rate, amount=amt, line_order=line_data.line_order or i,
            ))
        tax_rate = data.tax_rate if data.tax_rate is not None else po.tax_rate
        subtotal, tax_amount, total = compute_line_totals(data.lines, tax_rate)
        po.subtotal = subtotal
        po.tax_amount = tax_amount
        po.total = total

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    if po.vendor:
        resp.vendor_name = po.vendor.name
    return resp


@router.post("/{po_id}/convert-to-bill")
def convert_to_bill(po_id: int, db: Session = Depends(get_db)):
    """Convert a PO to a bill — creates bill with PO's line items AND the
    corresponding double-entry journal + inventory movements.

    Pre-Phase-11 this function created an orphan Bill row with no JE at all
    (expense + AP side were both missing). That was a silent accounting bug.
    """
    from app.models.bills import Bill, BillLine, BillStatus
    from app.models.accounts import Account
    from app.models.items import Item as ItemModel
    from app.services.accounting import create_journal_entry
    from app.services.inventory_service import (
        get_inventory_asset_account_id, record_purchase,
    )

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if po.status == POStatus.CLOSED:
        raise HTTPException(status_code=400, detail="PO already closed")

    vendor = po.vendor
    bill = Bill(
        bill_number=f"BILL-{po.po_number}", vendor_id=po.vendor_id, status=BillStatus.UNPAID,
        po_id=po.id, date=po.date, terms="Net 30",
        subtotal=po.subtotal, tax_rate=po.tax_rate, tax_amount=po.tax_amount,
        total=po.total, balance_due=po.total, notes=f"From {po.po_number}",
    )
    db.add(bill)
    db.flush()

    # Build the same journal-line structure bills.create_bill uses so the
    # PO→Bill path produces a fully balanced, inventory-aware JE.
    default_expense = db.query(Account).filter(Account.account_number == "6000").first()
    default_expense_id = default_expense.id if default_expense else None
    ap_acct = db.query(Account).filter(Account.account_number == "2000").first()

    journal_lines: list[dict] = []
    inv_receipts: list[tuple] = []
    for poline in po.lines:
        amt = Decimal(str(poline.quantity)) * Decimal(str(poline.rate))
        item = db.query(ItemModel).filter(ItemModel.id == poline.item_id).first() if poline.item_id else None

        if item and item.track_inventory:
            posting_acct = get_inventory_asset_account_id(db, item)
            if not posting_acct:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Item '{item.name}' is inventory-tracked but has no "
                        "asset account and no #1300 is seeded."
                    ),
                )
            if poline.quantity and poline.quantity > 0:
                inv_receipts.append((
                    item,
                    Decimal(str(poline.quantity)),
                    Decimal(str(poline.rate)),
                ))
        else:
            posting_acct = None
            if item and item.expense_account_id:
                posting_acct = item.expense_account_id
            elif vendor and vendor.default_expense_account_id:
                posting_acct = vendor.default_expense_account_id
            else:
                posting_acct = default_expense_id

        db.add(BillLine(
            bill_id=bill.id, item_id=poline.item_id, account_id=posting_acct,
            description=poline.description,
            quantity=poline.quantity, rate=poline.rate, amount=poline.amount,
            line_order=poline.line_order,
        ))
        if amt > 0 and posting_acct:
            journal_lines.append({
                "account_id": posting_acct,
                "debit": amt, "credit": Decimal("0"),
                "description": poline.description or "",
            })

    if bill.tax_amount and bill.tax_amount > 0:
        tax_acct = db.query(Account).filter(Account.account_number == "2200").first()
        if tax_acct:
            journal_lines.append({
                "account_id": tax_acct.id,
                "debit": Decimal(str(bill.tax_amount)),
                "credit": Decimal("0"),
                "description": "Sales tax on bill",
            })

    if ap_acct and journal_lines:
        journal_lines.append({
            "account_id": ap_acct.id,
            "debit": Decimal("0"),
            "credit": Decimal(str(bill.total)),
            "description": f"From PO {po.po_number}",
        })
        txn = create_journal_entry(
            db, po.date, f"Bill {bill.bill_number} - {vendor.name if vendor else ''}",
            journal_lines, source_type="bill", source_id=bill.id,
        )
        bill.transaction_id = txn.id

    # Write inventory movement rows for tracked receipts
    for item, qty, unit_cost in inv_receipts:
        record_purchase(
            db, item, quantity=qty, unit_cost=unit_cost,
            source_type="bill", source_id=bill.id,
            memo=f"PO {po.po_number}",
            post_journal=False,
            txn_date=po.date,
        )

    po.status = POStatus.CLOSED
    db.commit()
    return {"bill_id": bill.id, "message": f"Bill created from {po.po_number}"}
