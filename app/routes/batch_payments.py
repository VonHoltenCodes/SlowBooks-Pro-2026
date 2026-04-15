from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.schemas.batch_payments import BatchPaymentCreate
from app.services.accounting import create_journal_entry, get_ar_account_id, get_undeposited_funds_id
from app.services.auth import require_permissions
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/batch-payments", tags=["batch_payments"])


@router.post("")
def create_batch_payment(
    data: BatchPaymentCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("sales.manage")),
):
    if not data.allocations:
        raise HTTPException(status_code=400, detail="No allocations provided")

    ar_id = get_ar_account_id(db)
    created_payments = []

    grouped = {}
    for alloc in data.allocations:
        grouped.setdefault(alloc.customer_id, []).append(alloc)

    for customer_id, allocations in grouped.items():
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

        total = sum(Decimal(str(alloc.amount)) for alloc in allocations)
        txn_date = data.date or allocations[0].date
        check_closing_date(db, txn_date)
        payment = Payment(customer_id=customer_id, date=txn_date, amount=total, method=data.method, check_number=data.check_number, reference=data.reference, deposit_to_account_id=data.deposit_to_account_id, notes=data.notes)
        db.add(payment)
        db.flush()

        for alloc in allocations:
            invoice = db.query(Invoice).filter(Invoice.id == alloc.invoice_id).first()
            if not invoice:
                raise HTTPException(status_code=404, detail=f"Invoice {alloc.invoice_id} not found")
            if alloc.amount > invoice.balance_due:
                raise HTTPException(status_code=400, detail=f"Allocation exceeds balance on invoice {invoice.invoice_number}")
            db.add(PaymentAllocation(payment_id=payment.id, invoice_id=alloc.invoice_id, amount=alloc.amount))
            invoice.amount_paid += Decimal(str(alloc.amount))
            invoice.balance_due -= Decimal(str(alloc.amount))
            invoice.status = InvoiceStatus.PAID if invoice.balance_due <= 0 else InvoiceStatus.PARTIAL

        deposit_id = data.deposit_to_account_id or get_undeposited_funds_id(db)
        if ar_id and deposit_id:
            journal_lines = [
                {"account_id": deposit_id, "debit": Decimal(str(total)), "credit": Decimal("0"), "description": f"Batch payment from {customer.name}"},
                {"account_id": ar_id, "debit": Decimal("0"), "credit": Decimal(str(total)), "description": f"Batch payment from {customer.name}"},
            ]
            txn = create_journal_entry(db, txn_date, f"Batch payment from {customer.name}", journal_lines, source_type="payment", source_id=payment.id)
            payment.transaction_id = txn.id

        created_payments.append({"payment_id": payment.id, "customer": customer.name, "amount": total})

    db.commit()
    return {"payments_created": len(created_payments), "payments": created_payments}
