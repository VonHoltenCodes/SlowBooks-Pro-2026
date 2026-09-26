# ============================================================================
# Credit Memos — issue credits, apply to invoices
# Feature 5: DR Income, DR Sales Tax, CR AR — reverses invoice entry
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.transactions import Transaction
from app.routes._helpers import clamp_pagination
from app.models.credit_memos import (
    CreditMemo,
    CreditMemoLine,
    CreditMemoStatus,
    CreditApplication,
)
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.models.items import Item
from app.schemas.credit_memos import (
    CreditMemoCreate,
    CreditMemoResponse,
    CreditApplicationCreate,
)
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
    compute_line_totals,
    _q,
)
from app.routes.invoices.helpers import confirm_zero_total, resolve_line_taxable
from app.services.closing_date import check_closing_date
from app.services.numbering import next_credit_memo_number
from app.services.settings_service import get_all_settings as get_settings
from app.services.request_utils import content_disposition

router = APIRouter(prefix="/api/credit-memos", tags=["credit_memos"])


@router.get("", response_model=list[CreditMemoResponse])
def list_credit_memos(
    customer_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load .customer and .lines to avoid N+1 during model_validate.
    q = db.query(CreditMemo).options(
        joinedload(CreditMemo.customer),
        selectinload(CreditMemo.lines),
    )
    if customer_id:
        q = q.filter(CreditMemo.customer_id == customer_id)
    if status:
        q = q.filter(CreditMemo.status == status)
    memos = q.order_by(CreditMemo.date.desc()).offset(skip).limit(limit).all()
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


def _memo_or_404(db: Session, cm_id: int) -> CreditMemo:
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    return cm


@router.get("/{cm_id}/pdf")
def credit_memo_pdf(cm_id: int, db: Session = Depends(get_db)):
    """The credit memo as a PDF, to send to the customer."""
    from app.services.pdf_service import generate_credit_memo_pdf

    cm = _memo_or_404(db, cm_id)
    pdf_bytes = generate_credit_memo_pdf(cm, get_settings(db))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"CreditMemo_{cm.memo_number}.pdf"
            )
        },
    )


@router.get("/{cm_id}/print-preview")
def credit_memo_print_preview(cm_id: int, db: Session = Depends(get_db)):
    """The same page as the PDF, opened with the browser's print dialog."""
    from app.services.pdf_service import _render

    cm = _memo_or_404(db, cm_id)
    html_str = _render("credit_memo_pdf.html", get_settings(db), cm=cm)
    html_str = html_str.replace(
        "</body>", "<script>window.onload=function(){window.print();}</script></body>"
    )
    return HTMLResponse(content=html_str)


@router.post("", response_model=CreditMemoResponse, status_code=201)
def create_credit_memo(data: CreditMemoCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # A memo that credits an invoice takes that invoice's tax rate unless
    # it states its own: the tax goes back the way it was charged. It
    # defaulted to 0%, so returned taxable goods were credited without
    # their tax unless the user remembered (2.17.3 exploratory W-L14, F14).
    tax_rate = data.tax_rate
    if data.original_invoice_id is not None:
        original = db.get(Invoice, data.original_invoice_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        if original.customer_id != data.customer_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invoice {original.invoice_number} belongs to a different "
                    "customer than this credit memo."
                ),
            )
        if tax_rate is None:
            tax_rate = original.tax_rate
    if tax_rate is None:
        tax_rate = 0

    # Tax follows the lines the way an invoice's does: an item's own flag,
    # and nothing at all for a non-taxable customer.
    resolve_line_taxable(db, data.lines, customer)
    subtotal, tax_amount, total = compute_line_totals(data.lines, tax_rate)
    confirm_zero_total(total, data.allow_zero_total, "credit memo")

    cm = None
    # Retry the number assignment a few times — next_credit_memo_number is just
    # MAX+1 (no row-level lock), so two concurrent creates can both compute
    # the same number and one will hit the credit_memos.memo_number UNIQUE
    # constraint at flush (mirrors the invoice-number retry).
    for _ in range(10):
        cm = CreditMemo(
            memo_number=next_credit_memo_number(db),
            customer_id=data.customer_id,
            date=data.date,
            original_invoice_id=data.original_invoice_id,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            total=total,
            balance_remaining=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            status=CreditMemoStatus.ISSUED,
        )
        db.add(cm)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "memo_number" not in str(e.orig).lower():
                raise
            db.rollback()
            cm = None
    if cm is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a credit memo number — please retry.",
        )

    ar_id = get_ar_account_id(db)
    default_income_id = get_default_income_account_id(db)
    tax_account_id = get_sales_tax_account_id(db)
    journal_lines = []

    for i, line_data in enumerate(data.lines):
        # Round per line to match the rounded `total` used for the A/R credit
        # below — otherwise sub-cent rates make the JE unbalanced and 500
        # (same class as the invoice JE rounding bug).
        amt = _q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate)))
        db.add(
            CreditMemoLine(
                credit_memo_id=cm.id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=amt,
                line_order=line_data.line_order or i,
            )
        )
        if amt > 0:
            income_id = default_income_id
            if line_data.item_id:
                item = db.query(Item).filter(Item.id == line_data.item_id).first()
                if item and item.income_account_id:
                    income_id = item.income_account_id
            if income_id:
                journal_lines.append(
                    {
                        "account_id": income_id,
                        "debit": amt,
                        "credit": Decimal("0"),
                        "description": line_data.description or "",
                    }
                )

    # DR Sales Tax Payable if tax
    if tax_amount > 0 and tax_account_id:
        journal_lines.append(
            {
                "account_id": tax_account_id,
                "debit": tax_amount,
                "credit": Decimal("0"),
                "description": "Sales tax credit",
            }
        )

    # CR Accounts Receivable
    if ar_id and journal_lines:
        journal_lines.append(
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": total,
                "description": f"Credit Memo {cm.memo_number}",
            }
        )
        txn = create_journal_entry(
            db,
            data.date,
            f"Credit Memo {cm.memo_number} - {customer.name}",
            journal_lines,
            source_type="credit_memo",
            source_id=cm.id,
            class_id=cm.class_id,
        )
        cm.transaction_id = txn.id

    # Phase 11 (audit fix): a credit memo for returned inventory goods must
    # put the qty back on the shelf. This only adds quantity (no cost basis
    # JE) since the income-side reversal already covered the P&L impact.
    db.flush()
    db.refresh(cm)
    from app.services.inventory_hooks import post_return_for_credit_memo

    post_return_for_credit_memo(db, cm, txn_date=data.date)

    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = customer.name
    return resp


@router.post("/{cm_id}/apply")
def apply_credit(
    cm_id: int, data: CreditApplicationCreate, db: Session = Depends(get_db)
):
    """Apply credit memo to an invoice."""
    # Lock both rows for the read-check-write. Two concurrent applies of the
    # same credit memo would otherwise both read the same balance_remaining
    # and both pass the check, double-spending the credit. No-op on SQLite;
    # real row lock on Postgres.
    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).with_for_update().first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if cm.status == CreditMemoStatus.VOID:
        raise HTTPException(status_code=400, detail="Credit memo is voided")

    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == data.invoice_id)
        .with_for_update()
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # A credit belongs to one customer: it pays down that customer's invoices
    # only (the payment-side rule, #189).
    if invoice.customer_id != cm.customer_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invoice {invoice.invoice_number} belongs to a different "
                "customer than this credit memo."
            ),
        )

    if Decimal(str(data.amount)) > cm.balance_remaining:
        raise HTTPException(status_code=400, detail="Amount exceeds credit balance")
    if Decimal(str(data.amount)) > invoice.balance_due:
        raise HTTPException(status_code=400, detail="Amount exceeds invoice balance")

    db.add(
        CreditApplication(
            credit_memo_id=cm.id,
            invoice_id=data.invoice_id,
            amount=data.amount,
        )
    )

    amount = Decimal(str(data.amount))
    cm.amount_applied += amount
    cm.balance_remaining -= amount
    if cm.balance_remaining < 0:
        raise HTTPException(status_code=400, detail="Amount exceeds credit balance")
    if cm.balance_remaining == 0:
        cm.status = CreditMemoStatus.APPLIED

    invoice.amount_paid += amount
    invoice.balance_due -= amount
    if invoice.balance_due < 0:
        raise HTTPException(status_code=400, detail="Amount exceeds invoice balance")
    invoice.status = (
        InvoiceStatus.PAID if invoice.balance_due == 0 else InvoiceStatus.PARTIAL
    )

    db.commit()
    return {"message": f"Applied {data.amount} to invoice {invoice.invoice_number}"}


@router.post("/{cm_id}/void", response_model=CreditMemoResponse)
def void_credit_memo(cm_id: int, db: Session = Depends(get_db)):
    """Reverse a credit memo: put every application back on its invoice,
    post the mirror-image entry, return any inventory the memo took back,
    and mark it void. This is also how a mistaken write-off is undone."""
    from app.models.items import MovementType
    from app.services.accounting import reversing_lines
    from app.services.inventory_service import _append_movement

    cm = db.query(CreditMemo).filter(CreditMemo.id == cm_id).with_for_update().first()
    if not cm:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    if cm.status == CreditMemoStatus.VOID:
        raise HTTPException(status_code=400, detail="Credit memo is already void")
    check_closing_date(db, cm.date)

    for app_row in list(cm.applications):
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == app_row.invoice_id)
            .with_for_update()
            .first()
        )
        if invoice:
            amt = Decimal(str(app_row.amount))
            invoice.amount_paid = Decimal(str(invoice.amount_paid or 0)) - amt
            invoice.balance_due = Decimal(str(invoice.balance_due or 0)) + amt
            if invoice.amount_paid > 0:
                invoice.status = InvoiceStatus.PARTIAL
            else:
                invoice.status = InvoiceStatus.SENT
        db.delete(app_row)

    if cm.transaction_id:
        txn = db.query(Transaction).filter(Transaction.id == cm.transaction_id).first()
        if txn is not None:
            create_journal_entry(
                db,
                cm.date,
                f"VOID Credit Memo {cm.memo_number}",
                reversing_lines(txn.lines),
                source_type="credit_memo_void",
                source_id=cm.id,
                class_id=cm.class_id,
                job_id=cm.job_id,
            )

    for line in cm.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory and line.quantity > 0:
            _append_movement(
                db,
                item,
                MovementType.VOID,
                quantity=-Decimal(str(line.quantity)),
                unit_cost=Decimal(str(item.avg_cost or 0)),
                source_type="credit_memo_void",
                source_id=cm.id,
                memo=f"VOID Credit Memo {cm.memo_number}",
            )

    cm.amount_applied = Decimal("0")
    cm.balance_remaining = Decimal("0")
    cm.status = CreditMemoStatus.VOID
    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = cm.customer.name if cm.customer else None
    return resp
