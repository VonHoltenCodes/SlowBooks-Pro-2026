# ============================================================================
# Purchase Orders — CRUD + convert to bill
# Feature 6: Non-posting vendor documents
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine, POStatus
from app.models.contacts import Vendor
from app.schemas.purchase_orders import (
    POConvertToBill,
    POCreate,
    POResponse,
    POUpdate,
)
from app.services.accounting import _q, compute_line_totals
from app.services.numbering import next_po_number
from app.services.purchase_posting import (
    expense_account_for,
    spread,
    unit_cost_with_tax,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase_orders"])


@router.get("", response_model=list[POResponse])
def list_pos(
    vendor_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load to avoid N+1 on .vendor and .lines during model_validate.
    q = db.query(PurchaseOrder).options(
        joinedload(PurchaseOrder.vendor),
        selectinload(PurchaseOrder.lines),
    )
    if vendor_id:
        q = q.filter(PurchaseOrder.vendor_id == vendor_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    pos = q.order_by(PurchaseOrder.date.desc()).offset(skip).limit(limit).all()
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

    vendor_id = vendor.id
    vendor_name = vendor.name
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    po = None
    po_number = None
    last_err = None
    # Same race as create_invoice / create_estimate: next_po_number is MAX+1
    # without a row-level lock, so two concurrent creates can collide on the
    # po_number UNIQUE constraint. Retry on IntegrityError.
    for _ in range(10):
        po_number = next_po_number(db)
        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor_id,
            date=data.date,
            expected_date=data.expected_date,
            ship_to=data.ship_to,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=data.notes,
            job_id=data.job_id,
        )
        db.add(po)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "po_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            po = None
    if po is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique PO number; please retry.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = PurchaseOrderLine(
            purchase_order_id=po.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            job_id=line_data.job_id,
            cost_code_id=line_data.cost_code_id,
            line_order=line_data.line_order or i,
        )
        db.add(line)

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    resp.vendor_name = vendor_name
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
        db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.purchase_order_id == po_id
        ).delete()
        for i, line_data in enumerate(data.lines):
            amt = _q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate)))
            db.add(
                PurchaseOrderLine(
                    purchase_order_id=po_id,
                    item_id=line_data.item_id,
                    description=line_data.description,
                    quantity=line_data.quantity,
                    rate=line_data.rate,
                    amount=amt,
                    job_id=line_data.job_id,
                    cost_code_id=line_data.cost_code_id,
                    line_order=line_data.line_order or i,
                )
            )
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
def convert_to_bill(
    po_id: int,
    data: Optional[POConvertToBill] = None,
    db: Session = Depends(get_db),
):
    """Convert a PO to a bill — creates bill with PO's line items AND the
    corresponding double-entry journal + inventory movements.

    Pre-Phase-11 this function created an orphan Bill row with no JE at all
    (expense + AP side were both missing). That was a silent accounting bug.

    Each line posts where Enter Bill would post it (see
    services/purchase_posting.py): the account chosen for it in the To Bill
    dialog (``lines: [{line_id, account_id}]``), else its item's expense
    account, else the vendor's default — and a line none of them names is
    refused rather than booked to account 6000 (Advertising). Tax on the PO
    is part of the lines' cost, never a debit to Sales Tax Payable. The bill
    takes the vendor's terms and a due date from them.
    """
    from app.models.bills import Bill, BillLine, BillStatus
    from app.models.items import Item as ItemModel
    from app.routes.invoices.helpers import _due_date_from_terms
    from app.services.accounting import create_journal_entry, get_ap_account_id
    from app.services.closing_date import check_closing_date
    from app.services.inventory_service import (
        get_inventory_asset_account_id,
        record_purchase,
    )

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    # Posts a JE dated to po.date; closing-date enforcement must cover this
    # path too, otherwise an operator can reach into a closed period by
    # converting an old PO without ever creating a bill directly.
    check_closing_date(db, po.date)
    if po.status == POStatus.CLOSED:
        raise HTTPException(status_code=400, detail="PO already closed")
    if Decimal(str(po.total or 0)) <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{po.po_number} comes to $0.00, so there is nothing to bill. "
                "Enter the prices on its lines, then turn it into a bill."
            ),
        )

    chosen: dict[int, int] = {}
    po_line_ids = {ln.id for ln in po.lines}
    for pick in data.lines if data else []:
        if pick.line_id not in po_line_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Line id {pick.line_id} is not a line of {po.po_number}. "
                    "Open the order again and choose the accounts from there."
                ),
            )
        chosen[pick.line_id] = pick.account_id

    vendor = po.vendor
    terms = (vendor.terms if vendor else None) or "Net 30"
    ap_id = get_ap_account_id(db)  # a missing A/P account is said first (#119)

    # Round per line so the bill's JE debit matches the rounded AP credit
    # rebuilt from po.total below.
    lines = list(po.lines)
    amounts = [_q(Decimal(str(ln.quantity)) * Decimal(str(ln.rate))) for ln in lines]
    tax_amount = _q(po.tax_amount or 0)
    tax_shares = spread(tax_amount, amounts)

    # Where each line posts, decided before anything is written so a
    # refused line leaves no bill behind.
    plan = []
    for i, poline in enumerate(lines):
        amt = amounts[i]
        item = (
            db.query(ItemModel).filter(ItemModel.id == poline.item_id).first()
            if poline.item_id
            else None
        )
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
        elif amt > 0 or chosen.get(poline.id):
            posting_acct = expense_account_for(
                db,
                line_no=i + 1,
                description=poline.description,
                account_id=chosen.get(poline.id),
                item=item,
                vendor=vendor,
                fix_hint="Choose an account for it when you turn the order into a bill",
            )
        else:
            posting_acct = (item.expense_account_id if item else None) or (
                vendor.default_expense_account_id if vendor else None
            )
        plan.append((poline, item, amt, tax_shares[i], posting_acct))

    bill = Bill(
        bill_number=f"BILL-{po.po_number}",
        vendor_id=po.vendor_id,
        status=BillStatus.UNPAID,
        po_id=po.id,
        date=po.date,
        # The vendor's terms, and the due date they give — a converted bill
        # had "Net 30" and no due date, so it never aged (macbase1 F10).
        terms=terms,
        due_date=_due_date_from_terms(po.date, terms),
        subtotal=po.subtotal,
        tax_rate=po.tax_rate,
        tax_amount=tax_amount,
        total=po.total,
        balance_due=po.total,
        notes=f"From {po.po_number}",
        job_id=po.job_id,
    )
    db.add(bill)
    db.flush()

    journal_lines: list[dict] = []
    inv_receipts: list[tuple] = []
    for poline, item, amt, share, posting_acct in plan:
        if item and item.track_inventory and poline.quantity and poline.quantity > 0:
            inv_receipts.append(
                (
                    item,
                    Decimal(str(poline.quantity)),
                    unit_cost_with_tax(amt, share, poline.quantity, poline.rate),
                )
            )
        db.add(
            BillLine(
                bill_id=bill.id,
                job_id=poline.job_id or po.job_id,
                cost_code_id=poline.cost_code_id,
                item_id=poline.item_id,
                account_id=posting_acct,
                description=poline.description,
                quantity=poline.quantity,
                rate=poline.rate,
                amount=poline.amount,
                line_order=poline.line_order,
            )
        )
        if amt > 0 and posting_acct:
            journal_lines.append(
                {
                    "account_id": posting_acct,
                    "debit": amt + share,
                    "credit": Decimal("0"),
                    "description": poline.description or "",
                    "job_id": poline.job_id or po.job_id,
                    "cost_code_id": poline.cost_code_id,
                }
            )

    if journal_lines:
        journal_lines.append(
            {
                "account_id": ap_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(bill.total)),
                "description": f"From PO {po.po_number}",
            }
        )
        txn = create_journal_entry(
            db,
            po.date,
            f"Bill {bill.bill_number} - {vendor.name if vendor else ''}",
            journal_lines,
            source_type="bill",
            source_id=bill.id,
            job_id=po.job_id,
        )
        bill.transaction_id = txn.id

    # Write inventory movement rows for tracked receipts
    for item, qty, unit_cost in inv_receipts:
        record_purchase(
            db,
            item,
            quantity=qty,
            unit_cost=unit_cost,
            source_type="bill",
            source_id=bill.id,
            memo=f"PO {po.po_number}",
            post_journal=False,
            txn_date=po.date,
        )

    po.status = POStatus.CLOSED
    db.commit()
    return {"bill_id": bill.id, "message": f"Bill created from {po.po_number}"}
