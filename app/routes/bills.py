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
from app.schemas.bills import BillCreate, BillResponse, BillUpdate
from app.services.accounting import (
    create_journal_entry, reverse_journal_entry, get_gst_account_id,
    get_ap_account_id, get_default_expense_account_id,
)
from app.services.closing_date import check_closing_date
from app.services.auth import require_permissions
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst, stored_gst_line_inputs

router = APIRouter(prefix="/api/bills", tags=["bills"])


def _post_bill_journal(db: Session, bill: Bill, vendor: Vendor, lines, gst_totals):
    default_expense_id = get_default_expense_account_id(db)
    journal_lines = []

    for i, line_data in enumerate(lines):
        line_amount = gst_totals.lines[i].net_amount
        if line_amount <= 0:
            continue
        expense_acct = getattr(line_data, "account_id", None)
        if not expense_acct and getattr(line_data, "item_id", None):
            item = db.query(Item).filter(Item.id == line_data.item_id).first()
            if item and item.expense_account_id:
                expense_acct = item.expense_account_id
        if not expense_acct:
            expense_acct = default_expense_id
        if not expense_acct:
            continue
        journal_lines.append({
            "account_id": expense_acct,
            "debit": line_amount, "credit": Decimal("0"),
            "description": getattr(line_data, "description", "") or "",
        })

    if gst_totals.tax_amount > 0:
        tax_account_id = get_gst_account_id(db)
        if tax_account_id:
            journal_lines.append({
                "account_id": tax_account_id,
                "debit": gst_totals.tax_amount, "credit": Decimal("0"),
                "description": "GST on bill",
            })

    ap_id = get_ap_account_id(db)
    if ap_id and journal_lines:
        journal_lines.append({
            "account_id": ap_id,
            "debit": Decimal("0"), "credit": gst_totals.total,
            "description": f"Bill {bill.bill_number} - {vendor.name}",
        })
        txn = create_journal_entry(
            db, bill.date, f"Bill {bill.bill_number} - {vendor.name}",
            journal_lines, source_type="bill", source_id=bill.id,
            reference=bill.bill_number,
        )
        bill.transaction_id = txn.id
        return txn
    return None


def _replace_bill_lines(db: Session, bill_id: int, lines_data, gst_totals):
    db.query(BillLine).filter(BillLine.bill_id == bill_id).delete()
    default_expense_id = get_default_expense_account_id(db)
    for i, line_data in enumerate(lines_data):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        expense_acct = getattr(line_data, "account_id", None)
        if not expense_acct and getattr(line_data, "item_id", None):
            item = db.query(Item).filter(Item.id == line_data.item_id).first()
            if item and item.expense_account_id:
                expense_acct = item.expense_account_id
        if not expense_acct:
            expense_acct = default_expense_id
        db.add(BillLine(
            bill_id=bill_id, item_id=line_data.item_id, account_id=expense_acct,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=gst_totals.lines[i].net_amount,
            gst_code=gst_code, gst_rate=gst_rate,
            line_order=getattr(line_data, "line_order", None) or i,
        ))


def _bill_has_payments(bill: Bill) -> bool:
    return Decimal(str(bill.amount_paid or 0)) > 0 or bool(bill.payment_allocations)


@router.get("", response_model=list[BillResponse])
def list_bills(vendor_id: int = None, status: str = None, db: Session = Depends(get_db), auth=Depends(require_permissions("purchasing.view"))):
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
def get_bill(bill_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("purchasing.view"))):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp


@router.post("", response_model=BillResponse, status_code=201)
def create_bill(data: BillCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("purchasing.manage"))):
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

    gst_inputs = resolve_gst_line_inputs(db, data.lines)
    gst_totals = calculate_document_gst(
        gst_inputs,
        prices_include_gst=prices_include_gst(db),
        gst_context="purchase",
    )

    bill = Bill(
        bill_number=data.bill_number, vendor_id=data.vendor_id, date=data.date,
        due_date=due_date, terms=data.terms, ref_number=data.ref_number, po_id=data.po_id,
        subtotal=gst_totals.subtotal, tax_rate=gst_totals.effective_tax_rate, tax_amount=gst_totals.tax_amount,
        total=gst_totals.total, balance_due=gst_totals.total, notes=data.notes,
    )
    db.add(bill)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        amt = gst_totals.lines[i].net_amount
        expense_acct = line_data.account_id
        if not expense_acct and line_data.item_id:
            item = db.query(Item).filter(Item.id == line_data.item_id).first()
            if item and item.expense_account_id:
                expense_acct = item.expense_account_id
        if not expense_acct:
            expense_acct = get_default_expense_account_id(db)

        db.add(BillLine(
            bill_id=bill.id, item_id=line_data.item_id, account_id=expense_acct,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=amt, gst_code=gst_code, gst_rate=gst_rate,
            line_order=line_data.line_order or i,
        ))

    _post_bill_journal(db, bill, vendor, data.lines, gst_totals)

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    resp.vendor_name = vendor.name
    return resp


@router.put("/{bill_id}", response_model=BillResponse)
def update_bill(bill_id: int, data: BillUpdate, db: Session = Depends(get_db), auth=Depends(require_permissions("purchasing.manage"))):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit voided bill")

    old_transaction_id = bill.transaction_id
    old_date = bill.date
    update_values = data.model_dump(exclude_unset=True, exclude={"lines"})
    new_date = update_values.get("date", old_date)
    financial_change = data.lines is not None or new_date != old_date

    if financial_change:
        check_closing_date(db, old_date)
        if new_date != old_date:
            check_closing_date(db, new_date)
        if _bill_has_payments(bill):
            raise HTTPException(status_code=400, detail="Cannot change financial fields on a bill with payments applied")

    for key, val in update_values.items():
        setattr(bill, key, val)

    if financial_change:
        if data.lines is not None:
            gst_inputs = resolve_gst_line_inputs(db, data.lines)
            gst_totals = calculate_document_gst(
                gst_inputs,
                prices_include_gst=prices_include_gst(db),
                gst_context="purchase",
            )
            _replace_bill_lines(db, bill.id, data.lines, gst_totals)
        else:
            gst_totals = calculate_document_gst(
                stored_gst_line_inputs(db, bill.lines),
                prices_include_gst=prices_include_gst(db),
                gst_context="purchase",
            )

        bill.subtotal = gst_totals.subtotal
        bill.tax_rate = gst_totals.effective_tax_rate
        bill.tax_amount = gst_totals.tax_amount
        bill.total = gst_totals.total
        bill.balance_due = gst_totals.total - bill.amount_paid

        if old_transaction_id:
            reverse_journal_entry(
                db,
                old_transaction_id,
                old_date,
                f"Reversal Bill {bill.bill_number}",
                source_type="bill_reversal",
                source_id=bill.id,
                reference=bill.bill_number,
            )

        posting_vendor = db.query(Vendor).filter(Vendor.id == bill.vendor_id).first()
        _post_bill_journal(db, bill, posting_vendor, bill.lines, gst_totals)

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp


@router.post("/{bill_id}/void", response_model=BillResponse)
def void_bill(bill_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("purchasing.manage"))):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="Bill already voided")
    check_closing_date(db, bill.date)

    if bill.transaction_id:
        reverse_journal_entry(
            db,
            bill.transaction_id,
            bill.date,
            f"VOID Bill {bill.bill_number}",
            source_type="bill_void",
            source_id=bill.id,
        )

    bill.status = BillStatus.VOID
    bill.balance_due = Decimal("0")
    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp
