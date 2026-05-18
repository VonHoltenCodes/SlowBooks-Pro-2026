# ============================================================================
# Bills (Accounts Payable) — enter bills from vendors, track payables
# Feature 1: DR Expense, CR AP (2000) on create; DR AP, CR Bank on payment
# ============================================================================

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bills import Bill, BillLine, BillStatus
from app.models.contacts import Vendor
from app.models.items import Item
from app.models.accounts import Account
from app.schemas.bills import BillCreate, BillUpdate, BillResponse
from app.services.accounting import create_journal_entry, compute_line_totals
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/bills", tags=["bills"])


def _get_ap_account_id(db):
    acct = db.query(Account).filter(Account.account_number == "2000").first()
    return acct.id if acct else None


@router.get("", response_model=list[BillResponse])
def list_bills(vendor_id: int = None, status: str = None, db: Session = Depends(get_db)):
    q = db.query(Bill)
    if vendor_id:
        q = q.filter(Bill.vendor_id == vendor_id)
    if status:
        q = q.filter(Bill.status == status)
    bills = q.order_by(Bill.date.desc()).all()
    results = []
    for b in bills:
        resp = BillResponse.model_validate(b)
        if b.vendor:
            resp.vendor_name = b.vendor.name
        results.append(resp)
    return results


@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp


@router.post("", response_model=BillResponse, status_code=201)
def create_bill(data: BillCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    due_date = data.due_date
    if not due_date and data.terms:
        try:
            days = int(data.terms.lower().replace("net ", ""))
            due_date = data.date + timedelta(days=days)
        except ValueError:
            due_date = data.date + timedelta(days=30)

    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    bill = Bill(
        bill_number=data.bill_number, vendor_id=data.vendor_id, date=data.date,
        due_date=due_date, terms=data.terms, ref_number=data.ref_number, po_id=data.po_id,
        subtotal=subtotal, tax_rate=data.tax_rate, tax_amount=tax_amount,
        total=total, balance_due=total, notes=data.notes,
    )
    db.add(bill)
    db.flush()

    # Default expense account for lines without explicit account
    default_expense_id = db.query(Account).filter(Account.account_number == "6000").first()
    default_expense_id = default_expense_id.id if default_expense_id else None

    # Phase 11: track which lines are inventory receipts so we can write
    # InventoryMovement rows after the JE posts.
    from app.services.inventory_service import (
        get_inventory_asset_account_id,
        record_purchase,
    )
    inv_receipts = []  # [(item, quantity, unit_cost), ...]

    journal_lines = []
    for i, line_data in enumerate(data.lines):
        amt = Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
        item = None
        if line_data.item_id:
            item = db.query(Item).filter(Item.id == line_data.item_id).first()

        # Phase 11: for inventory-tracked items, the DR side goes to the
        # Inventory Asset account (NOT the expense account). The item will
        # move to COGS only when it's sold.
        if item and item.track_inventory:
            posting_acct = get_inventory_asset_account_id(db, item)
            if not posting_acct:
                # AUDIT FIX: don't silently drop the DR side when we can't
                # find an inventory asset account. Refuse the bill with a
                # clear error so the operator can fix the item's
                # asset_account_id or seed #1300.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Item '{item.name}' is inventory-tracked but has no "
                        "asset_account_id and no account #1300 (Inventory) is "
                        "seeded. Either set the item's inventory asset account "
                        "or add the Inventory account to the chart of accounts."
                    ),
                )
            if line_data.quantity > 0:
                inv_receipts.append((
                    item,
                    Decimal(str(line_data.quantity)),
                    Decimal(str(line_data.rate)),  # unit cost from the bill
                ))
        else:
            posting_acct = line_data.account_id
            if not posting_acct and item and item.expense_account_id:
                posting_acct = item.expense_account_id
            if not posting_acct and vendor.default_expense_account_id:
                posting_acct = vendor.default_expense_account_id
            if not posting_acct:
                posting_acct = default_expense_id

        db.add(BillLine(
            bill_id=bill.id, item_id=line_data.item_id, account_id=posting_acct,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=amt, line_order=line_data.line_order or i,
        ))

        if amt > 0 and posting_acct:
            journal_lines.append({
                "account_id": posting_acct,
                "debit": amt, "credit": Decimal("0"),
                "description": line_data.description or "",
            })

    # Tax line
    if tax_amount > 0:
        tax_acct = db.query(Account).filter(Account.account_number == "2200").first()
        if tax_acct:
            journal_lines.append({
                "account_id": tax_acct.id,
                "debit": tax_amount, "credit": Decimal("0"),
                "description": "Sales tax on bill",
            })

    # Credit AP
    ap_id = _get_ap_account_id(db)
    if ap_id and journal_lines:
        journal_lines.append({
            "account_id": ap_id,
            "debit": Decimal("0"), "credit": total,
            "description": f"Bill {data.bill_number} - {vendor.name}",
        })
        txn = create_journal_entry(
            db, data.date, f"Bill {data.bill_number} - {vendor.name}",
            journal_lines, source_type="bill", source_id=bill.id,
        )
        bill.transaction_id = txn.id

    # Phase 11: record inventory movements (no additional JE — the bill's
    # existing JE already debits the Inventory Asset account for these lines)
    for item, qty, unit_cost in inv_receipts:
        record_purchase(
            db, item, quantity=qty, unit_cost=unit_cost,
            source_type="bill", source_id=bill.id,
            memo=f"Bill {data.bill_number}",
            post_journal=False,
            txn_date=data.date,
        )

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    resp.vendor_name = vendor.name
    return resp


@router.post("/{bill_id}/void", response_model=BillResponse)
def void_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="Bill already voided")

    if bill.transaction_id:
        from app.models.transactions import TransactionLine
        original_lines = db.query(TransactionLine).filter(
            TransactionLine.transaction_id == bill.transaction_id
        ).all()
        reverse_lines = [
            {"account_id": ol.account_id, "debit": ol.credit, "credit": ol.debit,
             "description": f"VOID: {ol.description or ''}"}
            for ol in original_lines
        ]
        if reverse_lines:
            create_journal_entry(db, bill.date, f"VOID Bill {bill.bill_number}",
                                 reverse_lines, source_type="bill_void", source_id=bill.id)

    # Phase 11: reverse inventory receipts (the reversing JE already undoes
    # the Inventory Asset side; we just need the movement rows)
    from app.services.inventory_service import _append_movement
    from app.models.items import MovementType as _MovementType
    for line in bill.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory and line.quantity > 0:
            _append_movement(
                db, item, _MovementType.VOID,
                quantity=-Decimal(str(line.quantity)),
                unit_cost=Decimal(str(line.rate)),
                source_type="bill_void", source_id=bill.id,
                memo=f"VOID Bill {bill.bill_number}",
            )

    bill.status = BillStatus.VOID
    bill.balance_due = Decimal("0")
    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp
