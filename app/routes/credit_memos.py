# ============================================================================
# Credit Memos — issue credits, apply to invoices
# Feature 5: DR Income, DR Sales Tax, CR AR — reverses invoice entry
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.credit_memos import CreditMemo, CreditMemoLine, CreditMemoStatus, CreditApplication
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.models.items import Item
from app.schemas.credit_memos import CreditMemoCreate, CreditMemoResponse, CreditApplicationCreate
from app.services.accounting import (
    create_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_sales_tax_account_id,
)
from app.services.closing_date import check_closing_date
from app.services.gst_lines import resolve_line_gst

router = APIRouter(prefix="/api/credit-memos", tags=["credit_memos"])


def _next_cm_number(db: Session) -> str:
    last = db.query(sqlfunc.max(CreditMemo.memo_number)).scalar()
    if last and last.replace("CM-", "").isdigit():
        num = int(last.replace("CM-", "")) + 1
        return f"CM-{num:04d}"
    return "CM-0001"


@router.get("", response_model=list[CreditMemoResponse])
def list_credit_memos(customer_id: int = None, status: str = None, db: Session = Depends(get_db)):
    q = db.query(CreditMemo)
    if customer_id:
        q = q.filter(CreditMemo.customer_id == customer_id)
    if status:
        q = q.filter(CreditMemo.status == status)
    memos = q.order_by(CreditMemo.date.desc()).all()
    results = []
    for m in memos:
        resp = CreditMemoResponse.model_validate(m)
        if m.customer:
            resp.customer_name = m.customer.name
        results.append(resp)
    return results


@router.get("/{cm_id}", response_model=CreditMemoResponse)
def get_credit_memo(cm_id: int, db: Session = Depends(get_db)):
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    resp = CreditMemoResponse.model_validate(cm)
    if cm.customer:
        resp.customer_name = cm.customer.name
    return resp


@router.post("", response_model=CreditMemoResponse, status_code=201)
def create_credit_memo(data: CreditMemoCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    memo_number = _next_cm_number(db)
    subtotal = sum(Decimal(str(l.quantity)) * Decimal(str(l.rate)) for l in data.lines)
    tax_amount = subtotal * Decimal(str(data.tax_rate))
    total = subtotal + tax_amount

    cm = CreditMemo(
        memo_number=memo_number, customer_id=data.customer_id, date=data.date,
        original_invoice_id=data.original_invoice_id,
        subtotal=subtotal, tax_rate=data.tax_rate, tax_amount=tax_amount,
        total=total, balance_remaining=total, notes=data.notes,
        status=CreditMemoStatus.ISSUED,
    )
    db.add(cm)
    db.flush()

    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)
    journal_lines = []

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        amt = Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
        db.add(CreditMemoLine(
            credit_memo_id=cm.id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=amt, gst_code=gst_code, gst_rate=gst_rate,
            line_order=line_data.line_order or i,
        ))
        if amt > 0:
            income_id = default_income_id
            if line_data.item_id:
                item = db.query(Item).filter(Item.id == line_data.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            if income_id:
                journal_lines.append({
                    "account_id": income_id, "debit": amt, "credit": Decimal("0"),
                    "description": line_data.description or "",
                })

    # DR Sales Tax Payable if tax
    if tax_amount > 0 and tax_account_id:
        journal_lines.append({
            "account_id": tax_account_id, "debit": tax_amount, "credit": Decimal("0"),
            "description": "Sales tax credit",
        })

    # CR Accounts Receivable
    if ar_id and journal_lines:
        journal_lines.append({
            "account_id": ar_id, "debit": Decimal("0"), "credit": total,
            "description": f"Credit Memo {memo_number}",
        })
        txn = create_journal_entry(
            db, data.date, f"Credit Memo {memo_number} - {customer.name}",
            journal_lines, source_type="credit_memo", source_id=cm.id,
        )
        cm.transaction_id = txn.id

    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = customer.name
    return resp


@router.post("/{cm_id}/apply")
def apply_credit(cm_id: int, data: CreditApplicationCreate, db: Session = Depends(get_db)):
    """Apply credit memo to an invoice."""
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if cm.status == CreditMemoStatus.VOID:
        raise HTTPException(status_code=400, detail="Credit memo is voided")

    invoice = db.query(Invoice).filter(Invoice.id == data.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if Decimal(str(data.amount)) > cm.balance_remaining:
        raise HTTPException(status_code=400, detail="Amount exceeds credit balance")
    if Decimal(str(data.amount)) > invoice.balance_due:
        raise HTTPException(status_code=400, detail="Amount exceeds invoice balance")

    db.add(CreditApplication(
        credit_memo_id=cm.id, invoice_id=data.invoice_id, amount=data.amount,
    ))

    amount = Decimal(str(data.amount))
    cm.amount_applied += amount
    cm.balance_remaining -= amount
    if cm.balance_remaining <= 0:
        cm.status = CreditMemoStatus.APPLIED

    invoice.amount_paid += amount
    invoice.balance_due -= amount
    if invoice.balance_due <= 0:
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIAL

    db.commit()
    return {"message": f"Applied {data.amount} to invoice {invoice.invoice_number}"}
