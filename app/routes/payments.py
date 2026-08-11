# ============================================================================
# Receive Payments — the allocation loop applies a payment across open
# invoices; exact Decimal math throughout.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.payments import Payment, PaymentAllocation
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.schemas.payments import PaymentCreate, PaymentResponse
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_undeposited_funds_id,
)
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("", response_model=list[PaymentResponse])
def list_payments(
    customer_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load to avoid N+1 on .customer and .allocations during model_validate.
    q = db.query(Payment).options(
        joinedload(Payment.customer),
        selectinload(Payment.allocations),
    )
    if customer_id:
        q = q.filter(Payment.customer_id == customer_id)
    payments = q.order_by(Payment.date.desc()).offset(skip).limit(limit).all()
    results = []
    for p in payments:
        resp = PaymentResponse.model_validate(p)
        if p.customer:
            resp.customer_name = p.customer.name
        results.append(resp)
    return results


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    resp = PaymentResponse.model_validate(payment)
    if payment.customer:
        resp.customer_name = payment.customer.name
    return resp


@router.post("", response_model=PaymentResponse, status_code=201)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Reject non-positive amounts at the boundary; otherwise create_journal_entry
    # raises ValueError on the AR/bank line, which the framework surfaces as a
    # 500. Refunds belong in a credit memo, not a negative-amount payment.
    from app.services.currency import (
        document_currency,
        fx_gain_loss_account_id,
        resolve_rate,
        to_home,
    )

    pay_currency, pay_rate = resolve_rate(db, data.currency, data.exchange_rate)

    if data.amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Payment amount must be positive; use a credit memo for refunds",
        )
    if any(a.amount <= 0 for a in data.allocations):
        raise HTTPException(
            status_code=400,
            detail="Allocation amounts must be positive",
        )

    # Validate allocations don't exceed payment
    alloc_total = sum(a.amount for a in data.allocations)
    if alloc_total > data.amount:
        raise HTTPException(status_code=400, detail="Allocations exceed payment amount")

    payment = Payment(
        customer_id=data.customer_id,
        currency=pay_currency,
        exchange_rate=pay_rate,
        date=data.date,
        amount=data.amount,
        method=data.method,
        check_number=data.check_number,
        reference=data.reference,
        deposit_to_account_id=data.deposit_to_account_id,
        notes=data.notes,
    )
    db.add(payment)
    db.flush()

    # Home-currency value of A/R relieved per allocation (at each
    # invoice's booked rate) — the FX gain/loss basis.
    ar_home_credits: list[Decimal] = []
    # Apply allocations to invoices
    for alloc_data in data.allocations:
        # Lock the invoice row for the read-check-write so two concurrent
        # payments to the same invoice can't both pass the balance check and
        # over-apply (driving balance_due negative). No-op on SQLite; real
        # row lock on Postgres.
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == alloc_data.invoice_id)
            .with_for_update()
            .first()
        )
        if not invoice:
            raise HTTPException(
                status_code=404, detail=f"Invoice {alloc_data.invoice_id} not found"
            )
        if alloc_data.amount > invoice.balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Allocation {alloc_data.amount} exceeds invoice {invoice.invoice_number} balance {invoice.balance_due}",
            )

        inv_currency = document_currency(invoice, db)
        if inv_currency != pay_currency:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Payment currency {pay_currency} does not match invoice "
                    f"{invoice.invoice_number} currency {inv_currency}; pay each "
                    f"currency with a separate payment"
                ),
            )
        ar_home_credits.append(
            to_home(alloc_data.amount, invoice.exchange_rate or Decimal("1"))
        )
        alloc = PaymentAllocation(
            payment_id=payment.id,
            invoice_id=alloc_data.invoice_id,
            amount=alloc_data.amount,
        )
        db.add(alloc)

        invoice.amount_paid += alloc_data.amount
        invoice.balance_due -= alloc_data.amount
        # Only an exact zero is PAID; a negative balance means something
        # over-applied — surface it rather than masking corruption as PAID.
        if invoice.balance_due < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Allocation drives invoice {invoice.invoice_number} balance negative",
            )
        invoice.status = (
            InvoiceStatus.PAID if invoice.balance_due == 0 else InvoiceStatus.PARTIAL
        )

    # ================================================================
    # Journal Entry
    # DR  Bank/Undeposited Funds         payment amount
    # CR  Accounts Receivable (1100)     payment amount
    # ================================================================
    ar_id = get_ar_account_id(db)
    deposit_id = payment.deposit_to_account_id or get_undeposited_funds_id(db)

    if ar_id and deposit_id:
        # Cash received, in home currency at the payment-date rate.
        cash_home = to_home(data.amount, pay_rate)
        # A/R relieved at each invoice's BOOKED rate; any unallocated
        # remainder (a prepayment credit) has no booking rate yet, so it
        # relieves at the payment rate.
        unallocated = Decimal(str(data.amount)) - Decimal(str(alloc_total))
        ar_home = sum(ar_home_credits, Decimal("0")) + to_home(unallocated, pay_rate)
        journal_lines = [
            {
                "account_id": deposit_id,
                "debit": cash_home,
                "credit": Decimal("0"),
                "description": f"Payment from {customer.name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": ar_home,
                "description": f"Payment from {customer.name}",
            },
        ]
        # Realized FX: cash settled at a different rate than the invoice
        # was booked at. Credit = gain, debit = loss (expense account).
        residual = cash_home - ar_home
        if residual != 0:
            fx_id = fx_gain_loss_account_id(db)
            journal_lines.append(
                {
                    "account_id": fx_id,
                    "debit": -residual if residual < 0 else Decimal("0"),
                    "credit": residual if residual > 0 else Decimal("0"),
                    "description": f"Realized FX on payment from {customer.name}",
                }
            )
        txn = create_journal_entry(
            db,
            data.date,
            f"Payment from {customer.name}",
            journal_lines,
            source_type="payment",
            source_id=payment.id,
            reference=data.reference or data.check_number or "",
        )
        payment.transaction_id = txn.id

    db.commit()
    db.refresh(payment)
    resp = PaymentResponse.model_validate(payment)
    resp.customer_name = customer.name
    return resp


@router.post("/{payment_id}/void", response_model=PaymentResponse)
def void_payment(payment_id: int, db: Session = Depends(get_db)):
    """Void a payment — reverses journal entry and restores invoice balances"""
    # Row-lock the payment so two concurrent voids can't both pass the
    # is_voided guard and post duplicate reversing JEs.
    payment = (
        db.query(Payment).filter(Payment.id == payment_id).with_for_update().first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(status_code=400, detail="Payment already voided")
    check_closing_date(db, payment.date)

    # Reverse journal entry
    if payment.transaction_id:
        from app.models.transactions import TransactionLine

        original_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == payment.transaction_id)
            .all()
        )
        reverse_lines = [
            {
                "account_id": ol.account_id,
                "debit": ol.credit,
                "credit": ol.debit,
                "description": f"VOID: {ol.description or ''}",
            }
            for ol in original_lines
        ]
        if reverse_lines:
            customer = (
                db.query(Customer).filter(Customer.id == payment.customer_id).first()
            )
            cname = customer.name if customer else "Unknown"
            create_journal_entry(
                db,
                payment.date,
                f"VOID Payment from {cname}",
                reverse_lines,
                source_type="payment_void",
                source_id=payment.id,
            )

    # Reverse invoice allocations. Lock each invoice row so a concurrent
    # create_payment / second void can't race the read-modify-write of
    # amount_paid and balance_due.
    for alloc in payment.allocations:
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == alloc.invoice_id)
            .with_for_update()
            .first()
        )
        if invoice:
            invoice.amount_paid -= alloc.amount
            invoice.balance_due += alloc.amount
            if invoice.balance_due >= invoice.total:
                invoice.status = InvoiceStatus.SENT
            elif invoice.amount_paid > 0:
                invoice.status = InvoiceStatus.PARTIAL
            else:
                invoice.status = InvoiceStatus.SENT

    payment.is_voided = True
    db.commit()
    db.refresh(payment)
    resp = PaymentResponse.model_validate(payment)
    if payment.customer:
        resp.customer_name = payment.customer.name
    return resp
