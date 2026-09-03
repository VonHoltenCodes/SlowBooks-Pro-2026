# ============================================================================
# Enter Sales Receipts — invoice + payment recorded as one step, for
# point-of-sale style transactions where payment happens at the sale.
#
# Composes the existing invoice and payment routes rather than re-posting
# journals itself, so numbering, closing-date checks, FX, inventory/COGS,
# and void semantics stay identical to documents entered separately.
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.invoices import Invoice
from app.routes.invoices.crud import create_invoice, get_invoice
from app.routes.invoices.helpers import _compute_totals
from app.routes.invoices.lifecycle import void_invoice
from app.routes.payments import create_payment
from app.schemas.invoices import InvoiceCreate
from app.schemas.payments import PaymentAllocationCreate, PaymentCreate
from app.schemas.sales_receipts import SalesReceiptCreate, SalesReceiptResponse

router = APIRouter(prefix="/api/sales-receipts", tags=["sales-receipts"])


@router.post("", response_model=SalesReceiptResponse, status_code=201)
def create_sales_receipt(data: SalesReceiptCreate, db: Session = Depends(get_db)):
    # Validate up front so nothing is written on inputs the payment step
    # would reject after the invoice already exists: a $0 payment is
    # refused by the payments route, and a bad deposit account would only
    # surface when its journal posts.
    _, _, total = _compute_totals(data.lines, data.tax_rate)
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail="Sales receipt total must be positive; add at least one "
            "line with an amount",
        )
    if data.deposit_to_account_id is not None:
        acct = (
            db.query(Account).filter(Account.id == data.deposit_to_account_id).first()
        )
        if not acct:
            raise HTTPException(status_code=404, detail="Deposit account not found")

    inv_resp = create_invoice(
        InvoiceCreate(
            customer_id=data.customer_id,
            date=data.date,
            due_date=data.date,
            terms="Due on Receipt",
            tax_rate=data.tax_rate,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            lines=data.lines,
        ),
        db,
    )
    invoice = db.query(Invoice).filter(Invoice.id == inv_resp.id).first()
    invoice.is_sales_receipt = True
    db.commit()

    try:
        pay_resp = create_payment(
            PaymentCreate(
                customer_id=data.customer_id,
                date=data.date,
                amount=inv_resp.total,
                method=data.method,
                check_number=data.check_number,
                reference=data.reference,
                deposit_to_account_id=data.deposit_to_account_id,
                currency=data.currency,
                exchange_rate=data.exchange_rate,
                allocations=[
                    PaymentAllocationCreate(
                        invoice_id=inv_resp.id, amount=inv_resp.total
                    )
                ],
            ),
            db,
        )
    except Exception:
        # The invoice committed but its payment didn't: void the invoice so
        # a failed receipt never leaves a stray open balance, then surface
        # the original error.
        db.rollback()
        try:
            void_invoice(inv_resp.id, db)
        except Exception:
            pass
        raise

    return SalesReceiptResponse(invoice=get_invoice(inv_resp.id, db), payment=pay_resp)
