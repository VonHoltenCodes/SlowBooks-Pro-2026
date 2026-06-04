# ============================================================================
# Batch Payment Application — apply payments to multiple invoices at once
# Feature 7: Single transaction wrapping existing payment logic
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
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


class BatchAllocation(BaseModel):
    customer_id: int
    invoice_id: int
    amount: float


class BatchPaymentCreate(BaseModel):
    date: str
    deposit_to_account_id: Optional[int] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    allocations: list[BatchAllocation] = []


@router.post("")
def create_batch_payment(data: BatchPaymentCreate, db: Session = Depends(get_db)):
    from datetime import date as date_type

    txn_date = date_type.fromisoformat(data.date)
    check_closing_date(db, txn_date)

    if not data.allocations:
        raise HTTPException(status_code=400, detail="No allocations provided")

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
            date=txn_date,
            amount=total,
            method=data.method,
            reference=data.reference,
            deposit_to_account_id=data.deposit_to_account_id,
        )
        db.add(payment)
        db.flush()

        for alloc in allocs:
            invoice = db.query(Invoice).filter(Invoice.id == alloc.invoice_id).first()
            if not invoice:
                raise HTTPException(
                    status_code=404, detail=f"Invoice {alloc.invoice_id} not found"
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
