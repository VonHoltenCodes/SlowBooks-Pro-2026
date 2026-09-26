# ============================================================================
# Batch Payment Application — apply payments to multiple invoices at once
# Feature 7: Single transaction wrapping existing payment logic
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.schemas.common import StrictModel
from typing import Optional

from app.database import get_db
from app.models.payments import Payment, PaymentAllocation
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    get_undeposited_funds_id,
)
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/batch-payments", tags=["batch_payments"])


class BatchAllocation(StrictModel):
    customer_id: int
    invoice_id: int
    amount: float


class BatchPaymentCreate(StrictModel):
    date: str
    deposit_to_account_id: Optional[int] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    allocations: list[BatchAllocation] = []


@router.post("")
def create_batch_payment(data: BatchPaymentCreate, db: Session = Depends(get_db)):
    """One payment per customer, applied to the invoices chosen, in one
    commit: a refused line writes nothing for any customer.

    A batch payment has no currency or exchange rate of its own, so it is
    in the home currency, and Receive Payment's rule holds: a payment pays
    invoices in its own currency only ("Cross-currency allocations rejected
    with clear errors"). A EUR invoice in a batch was paid as if its euros
    were dollars — EUR 850 recorded as a $850 payment against A/R the
    invoice had booked at $935, with no conversion and no realized FX (found
    integrating the 2.17.3 exploratory fixes). It is now refused, naming the
    invoice; a foreign invoice is paid on its own, in its currency, at the
    rate the money came in at. Amounts must be positive, as for a payment."""
    from datetime import date as date_type

    from app.services.currency import document_currency, home_currency

    txn_date = date_type.fromisoformat(data.date)
    check_closing_date(db, txn_date)

    if not data.allocations:
        raise HTTPException(status_code=400, detail="No allocations provided")
    if any(Decimal(str(a.amount)) <= 0 for a in data.allocations):
        raise HTTPException(
            status_code=400, detail="Allocation amounts must be positive"
        )
    home = home_currency(db)

    # Group allocations by customer
    by_customer = {}
    for alloc in data.allocations:
        if alloc.customer_id not in by_customer:
            by_customer[alloc.customer_id] = []
        by_customer[alloc.customer_id].append(alloc)

    created_payments = []
    ar_id = get_ar_account_id(db)

    for customer_id, allocs in by_customer.items():
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(
                status_code=404, detail=f"Customer {customer_id} not found"
            )

        total = sum(a.amount for a in allocs)

        payment = Payment(
            customer_id=customer_id,
            currency=home,
            exchange_rate=Decimal("1"),
            date=txn_date,
            amount=total,
            method=data.method,
            reference=data.reference,
            deposit_to_account_id=data.deposit_to_account_id,
        )
        db.add(payment)
        db.flush()

        for alloc in allocs:
            # Row lock for the read-check-write, as create_payment does:
            # two concurrent batches against one invoice must not both
            # pass the balance check (GHSA-rm5h-555g-vpjj). No-op on
            # SQLite; real lock on Postgres.
            invoice = (
                db.query(Invoice)
                .filter(Invoice.id == alloc.invoice_id)
                .with_for_update()
                .first()
            )
            if not invoice:
                raise HTTPException(
                    status_code=404, detail=f"Invoice {alloc.invoice_id} not found"
                )
            # Each line's payment is recorded for its customer_id, so the
            # invoice must be that customer's (#189). The batch is one commit:
            # a refused line writes nothing for any customer.
            if invoice.customer_id != customer_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invoice {invoice.invoice_number} does not belong to "
                        f"customer {customer.name}."
                    ),
                )
            inv_currency = document_currency(invoice, db)
            if inv_currency != home:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invoice {invoice.invoice_number} is in {inv_currency}, "
                        f"and a batch payment is in {home}, so the batch can't "
                        "pay it. Take it out of the batch and record its payment "
                        f"on its own, in {inv_currency}."
                    ),
                )
            if Decimal(str(alloc.amount)) > invoice.balance_due:
                raise HTTPException(
                    status_code=400,
                    detail=f"Allocation exceeds balance on invoice {invoice.invoice_number}",
                )

            db.add(
                PaymentAllocation(
                    payment_id=payment.id,
                    invoice_id=alloc.invoice_id,
                    amount=alloc.amount,
                )
            )
            invoice.amount_paid += Decimal(str(alloc.amount))
            invoice.balance_due -= Decimal(str(alloc.amount))
            if invoice.balance_due < 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"Invoice {invoice.invoice_number} would be over-applied",
                )
            if invoice.balance_due <= 0:
                invoice.status = InvoiceStatus.PAID
            else:
                invoice.status = InvoiceStatus.PARTIAL

        # Journal entry
        deposit_id = data.deposit_to_account_id or get_undeposited_funds_id(db)
        if ar_id and deposit_id:
            journal_lines = [
                {
                    "account_id": deposit_id,
                    "debit": Decimal(str(total)),
                    "credit": Decimal("0"),
                    "description": f"Batch payment from {customer.name}",
                },
                {
                    "account_id": ar_id,
                    "debit": Decimal("0"),
                    "credit": Decimal(str(total)),
                    "description": f"Batch payment from {customer.name}",
                },
            ]
            txn = create_journal_entry(
                db,
                txn_date,
                f"Batch payment from {customer.name}",
                journal_lines,
                source_type="payment",
                source_id=payment.id,
            )
            payment.transaction_id = txn.id

        created_payments.append(
            {"payment_id": payment.id, "customer": customer.name, "amount": total}
        )

    db.commit()
    return {"payments_created": len(created_payments), "payments": created_payments}
