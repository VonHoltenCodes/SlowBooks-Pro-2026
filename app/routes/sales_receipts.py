# ============================================================================
# Enter Sales Receipts — invoice + payment recorded as one step, for
# point-of-sale style transactions where payment happens at the sale.
#
# Composes the existing invoice and payment routes rather than re-posting
# journals itself, so numbering, closing-date checks, FX, inventory/COGS,
# and void semantics stay identical to documents entered separately.
# ============================================================================

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.contacts import Customer
from app.models.invoices import Invoice
from app.routes.invoices.crud import create_invoice, get_invoice, list_invoices
from app.routes.invoices.helpers import _compute_totals
from app.routes.invoices.lifecycle import void_invoice
from app.routes.payments import create_payment
from app.schemas.invoices import InvoiceCreate, InvoiceResponse
from app.schemas.payments import PaymentAllocationCreate, PaymentCreate
from app.schemas.sales_receipts import SalesReceiptCreate, SalesReceiptResponse
from app.services.donor_documents import document_label
from app.services.jobs_service import refuse_other_customers_jobs
from app.services.terminology import terms_from_db
from app.services.undeposited_funds import refuse_inactive_deposit_account

router = APIRouter(prefix="/api/sales-receipts", tags=["sales-receipts"])

# Settings row holding the walk-in customer's id, so a renamed walk-in
# customer ("Cash Sales") is still the one counter sales go to.
WALK_IN_SETTING = "walk_in_customer_id"


def walk_in_customer(db: Session) -> Customer:
    """The customer a counter sale is recorded against when none is picked.

    A sales receipt required a customer, so a bakery selling a loaf over the
    counter had to invent one (2.17.3 exploratory, macbase1 suggestion S-c).
    This is an ordinary customer, created the first time it is needed, so
    the sale posts, deposits and reports like any other: its receipts are
    paid in full and it never carries a balance.
    """
    from app.services.settings_service import get_setting_raw, set_setting
    from app.services.terminology import terms_from_db

    raw = (get_setting_raw(db, WALK_IN_SETTING) or "").strip()
    if raw.isdigit():
        found = db.get(Customer, int(raw))
        if found is not None:
            return found
    name = "Anonymous Donor" if terms_from_db(db).is_nonprofit else "Walk-in Customer"
    found = db.query(Customer).filter(func.lower(Customer.name) == name.lower()).first()
    if found is None:
        found = Customer(
            name=name,
            terms="Due on Receipt",
            is_active=True,
            notes="Counter sales entered without a customer.",
        )
        db.add(found)
        db.flush()
    set_setting(db, WALK_IN_SETTING, str(found.id))
    return found


@router.get("", response_model=list[InvoiceResponse])
def list_sales_receipts(
    customer_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Sales receipts are invoices paid on the spot; this is the invoice
    list filtered to them, so an API client (or agent) can enumerate
    receipts without knowing the flag. Found missing by the BYO-AI seed."""
    return list_invoices(
        customer_id=customer_id, is_sales_receipt=True, skip=skip, limit=limit, db=db
    )


@router.post("", response_model=SalesReceiptResponse, status_code=201)
def create_sales_receipt(data: SalesReceiptCreate, db: Session = Depends(get_db)):
    """Enter a sales receipt: the invoice and its full payment in one step.
    Answers `{invoice, payment, customer_name, warnings}`: the invoice as
    GET /api/invoices/{id} answers it, the payment, the customer's name as
    the list answers it, and a sentence for each stock item the sale took
    past what was on hand."""
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
        # nor an account deactivated in the chart (#741)
        refuse_inactive_deposit_account(db, acct.id)

    customer_id = data.customer_id
    if customer_id is None:
        customer_id = walk_in_customer(db).id
    # The job is this customer's — the walk-in customer's, for a counter
    # sale — before anything is written (NEW-36); the invoice step would
    # refuse it too, but as an invoice.
    words = terms_from_db(db)
    refuse_other_customers_jobs(
        db,
        customer_id,
        data.job_id,
        data.lines,
        document_label(SimpleNamespace(is_sales_receipt=True), words).lower(),
        words,
    )

    inv_resp = create_invoice(
        InvoiceCreate(
            # paid as it is made, so it owes nothing: the credit limit is
            # not asked of a sales receipt (#256)
            allow_over_credit_limit=True,
            customer_id=customer_id,
            date=data.date,
            due_date=data.date,
            terms="Due on Receipt",
            tax_rate=data.tax_rate,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            fair_value_amount=data.fair_value_amount,
            fair_value_description=data.fair_value_description,
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
                customer_id=customer_id,
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

    # the invoice step's stock warnings (#642) ride on the receipt's answer
    invoice_out = get_invoice(inv_resp.id, db)
    return SalesReceiptResponse(
        invoice=invoice_out,
        payment=pay_resp,
        customer_name=invoice_out.customer_name,
        warnings=inv_resp.warnings,
    )
