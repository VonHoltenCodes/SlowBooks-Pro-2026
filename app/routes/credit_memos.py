# ============================================================================
# Credit Memos — issue credits, apply to invoices
# Feature 5: DR Income, DR Sales Tax, CR AR — reverses invoice entry
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.credit_memos import CreditMemo, CreditMemoLine, CreditMemoStatus, CreditApplication
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.models.items import Item
from app.schemas.email import DocumentEmailRequest
from app.schemas.credit_memos import (
    CreditMemoCreate, CreditMemoResponse, CreditApplicationCreate, CreditMemoUpdate,
)
from app.services.accounting import (
    create_journal_entry, reverse_journal_entry, get_ar_account_id,
    get_default_income_account_id, get_gst_account_id,
)
from app.services.closing_date import check_closing_date
from app.services.email_service import render_document_email, send_document_email
from app.services.gst_calculations import calculate_document_gst, prices_include_gst
from app.services.gst_lines import resolve_gst_line_inputs, resolve_line_gst, stored_gst_line_inputs
from app.services.pdf_service import generate_credit_memo_pdf
from app.routes.settings import _get_all as get_settings
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/credit-memos", tags=["credit_memos"])


def _next_cm_number(db: Session) -> str:
    last = db.query(sqlfunc.max(CreditMemo.memo_number)).scalar()
    if last and last.replace("CM-", "").isdigit():
        num = int(last.replace("CM-", "")) + 1
        return f"CM-{num:04d}"
    return "CM-0001"


def _post_credit_memo_journal(db: Session, cm: CreditMemo, customer: Customer, lines, gst_totals):
    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_gst_account_id(db)
    journal_lines = []

    for i, line_data in enumerate(lines):
        amt = gst_totals.lines[i].net_amount
        if amt <= 0:
            continue
        income_id = default_income_id
        if getattr(line_data, "item_id", None):
            item = db.query(Item).filter(Item.id == line_data.item_id).first()
            if item and item.income_account_id:
                income_id = item.income_account_id
        if income_id:
            journal_lines.append({
                "account_id": income_id, "debit": amt, "credit": Decimal("0"),
                "description": getattr(line_data, "description", "") or "",
            })

    if gst_totals.tax_amount > 0 and tax_account_id:
        journal_lines.append({
            "account_id": tax_account_id, "debit": gst_totals.tax_amount, "credit": Decimal("0"),
            "description": "GST credit",
        })

    if ar_id and journal_lines:
        journal_lines.append({
            "account_id": ar_id, "debit": Decimal("0"), "credit": gst_totals.total,
            "description": f"Credit Memo {cm.memo_number}",
        })
        txn = create_journal_entry(
            db, cm.date, f"Credit Memo {cm.memo_number} - {customer.name}",
            journal_lines, source_type="credit_memo", source_id=cm.id,
            reference=cm.memo_number,
        )
        cm.transaction_id = txn.id
        return txn
    return None


def _replace_credit_memo_lines(db: Session, cm_id: int, lines_data, gst_totals):
    db.query(CreditMemoLine).filter(CreditMemoLine.credit_memo_id == cm_id).delete()
    for i, line_data in enumerate(lines_data):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        db.add(CreditMemoLine(
            credit_memo_id=cm_id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=gst_totals.lines[i].net_amount,
            gst_code=gst_code, gst_rate=gst_rate,
            line_order=getattr(line_data, "line_order", None) or i,
        ))


def _credit_memo_has_applications(cm: CreditMemo) -> bool:
    return Decimal(str(cm.amount_applied or 0)) > 0 or bool(cm.applications)


@router.get("", response_model=list[CreditMemoResponse])
def list_credit_memos(customer_id: int = None, status: str = None, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.view"))):
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
def get_credit_memo(cm_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.view"))):
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    resp = CreditMemoResponse.model_validate(cm)
    if cm.customer:
        resp.customer_name = cm.customer.name
    return resp


@router.post("", response_model=CreditMemoResponse, status_code=201)
def create_credit_memo(data: CreditMemoCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    check_closing_date(db, data.date)

    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    memo_number = _next_cm_number(db)
    gst_inputs = resolve_gst_line_inputs(db, data.lines)
    gst_totals = calculate_document_gst(
        gst_inputs,
        prices_include_gst=prices_include_gst(db),
        gst_context="sales",
    )

    cm = CreditMemo(
        memo_number=memo_number, customer_id=data.customer_id, date=data.date,
        original_invoice_id=data.original_invoice_id,
        subtotal=gst_totals.subtotal, tax_rate=gst_totals.effective_tax_rate, tax_amount=gst_totals.tax_amount,
        total=gst_totals.total, balance_remaining=gst_totals.total, notes=data.notes,
        status=CreditMemoStatus.ISSUED,
    )
    db.add(cm)
    db.flush()

    for i, line_data in enumerate(data.lines):
        gst_code, gst_rate = resolve_line_gst(db, line_data)
        amt = gst_totals.lines[i].net_amount
        db.add(CreditMemoLine(
            credit_memo_id=cm.id, item_id=line_data.item_id,
            description=line_data.description, quantity=line_data.quantity,
            rate=line_data.rate, amount=amt, gst_code=gst_code, gst_rate=gst_rate,
            line_order=line_data.line_order or i,
        ))
    _post_credit_memo_journal(db, cm, customer, data.lines, gst_totals)

    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = customer.name
    return resp


@router.put("/{cm_id}", response_model=CreditMemoResponse)
def update_credit_memo(cm_id: int, data: CreditMemoUpdate, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if cm.status == CreditMemoStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit voided credit memo")

    old_transaction_id = cm.transaction_id
    old_date = cm.date
    update_values = data.model_dump(exclude_unset=True, exclude={"lines"})
    new_date = update_values.get("date", old_date)
    financial_change = data.lines is not None or new_date != old_date

    if financial_change:
        check_closing_date(db, old_date)
        if new_date != old_date:
            check_closing_date(db, new_date)
        if _credit_memo_has_applications(cm):
            raise HTTPException(status_code=400, detail="Cannot change financial fields on a credit memo with applications")

    for key, val in update_values.items():
        setattr(cm, key, val)

    if financial_change:
        if data.lines is not None:
            gst_inputs = resolve_gst_line_inputs(db, data.lines)
            gst_totals = calculate_document_gst(
                gst_inputs,
                prices_include_gst=prices_include_gst(db),
                gst_context="sales",
            )
            _replace_credit_memo_lines(db, cm.id, data.lines, gst_totals)
        else:
            gst_totals = calculate_document_gst(
                stored_gst_line_inputs(db, cm.lines),
                prices_include_gst=prices_include_gst(db),
                gst_context="sales",
            )

        cm.subtotal = gst_totals.subtotal
        cm.tax_rate = gst_totals.effective_tax_rate
        cm.tax_amount = gst_totals.tax_amount
        cm.total = gst_totals.total
        cm.balance_remaining = gst_totals.total - cm.amount_applied

        if old_transaction_id:
            reverse_journal_entry(
                db,
                old_transaction_id,
                old_date,
                f"Reversal Credit Memo {cm.memo_number}",
                source_type="credit_memo_reversal",
                source_id=cm.id,
                reference=cm.memo_number,
            )

        posting_customer = db.query(Customer).filter(Customer.id == cm.customer_id).first()
        _post_credit_memo_journal(db, cm, posting_customer, cm.lines, gst_totals)

    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    if cm.customer:
        resp.customer_name = cm.customer.name
    return resp


@router.get("/{cm_id}/pdf")
def credit_memo_pdf(cm_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.view"))):
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    company = get_settings(db)
    pdf_bytes = generate_credit_memo_pdf(cm, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=CreditMemo_{cm.memo_number}.pdf"},
    )


@router.post("/{cm_id}/email")
def email_credit_memo(cm_id: int, data: DocumentEmailRequest, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if cm.status == CreditMemoStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot email a void credit memo")
    company = get_settings(db)
    try:
        pdf_bytes = generate_credit_memo_pdf(cm, company)
        html_body = render_document_email(
            document_label="Credit Note",
            recipient_name=cm.customer.name if cm.customer else None,
            document_number=cm.memo_number,
            company_settings=company,
            amount=cm.total,
            action_label="Date",
            action_value=cm.date,
        )
        send_document_email(
            db,
            to_email=data.recipient,
            subject=data.subject or f"Credit Note #{cm.memo_number}",
            html_body=html_body,
            attachment_bytes=pdf_bytes,
            attachment_name=f"CreditMemo_{cm.memo_number}.pdf",
            entity_type="credit_memo",
            entity_id=cm.id,
        )
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")


@router.post("/{cm_id}/apply")
def apply_credit(cm_id: int, data: CreditApplicationCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("sales.manage"))):
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
