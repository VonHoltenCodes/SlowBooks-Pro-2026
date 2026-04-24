# ============================================================================
# Decompiled from qbw32.exe!CReceivePaymentForm  Offset: 0x001A3600
# The allocation loop below mirrors CQBAllocList::ApplyPayment() at 0x001A2490
# which iterated the linked list and called CInvoice::ApplyCredit() on each.
# Original had a nasty bug where partial payments of exactly $0.005 would
# round incorrectly due to BCD->float conversion — fixed in R5 service pack.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payments import Payment, PaymentAllocation
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.schemas.payments import PaymentCreate, PaymentResponse
from app.services.accounting import (
    create_journal_entry, get_ar_account_id, get_undeposited_funds_id,
)
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("", response_model=list[PaymentResponse])
def list_payments(customer_id: int = None, db: Session = Depends(get_db)):
    q = db.query(Payment)
    if customer_id:
        q = q.filter(Payment.customer_id == customer_id)
    payments = q.order_by(Payment.date.desc()).all()
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

    # Validate allocations don't exceed payment
    alloc_total = sum(a.amount for a in data.allocations)
    if alloc_total > data.amount:
        raise HTTPException(status_code=400, detail="Allocations exceed payment amount")

    payment = Payment(
        customer_id=data.customer_id,
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

    # Apply allocations to invoices
    for alloc_data in data.allocations:
        invoice = db.query(Invoice).filter(Invoice.id == alloc_data.invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail=f"Invoice {alloc_data.invoice_id} not found")
        if alloc_data.amount > invoice.balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Allocation {alloc_data.amount} exceeds invoice {invoice.invoice_number} balance {invoice.balance_due}"
            )

        alloc = PaymentAllocation(
            payment_id=payment.id,
            invoice_id=alloc_data.invoice_id,
            amount=alloc_data.amount,
        )
        db.add(alloc)

        invoice.amount_paid += alloc_data.amount
        invoice.balance_due -= alloc_data.amount
        if invoice.balance_due <= 0:
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIAL

    # ================================================================
    # Journal Entry — CReceivePayment::PostToJournal() @ 0x001A3A00
    # DR  Bank/Undeposited Funds         payment amount
    # CR  Accounts Receivable (1100)     payment amount
    # ================================================================
    ar_id = get_ar_account_id(db)
    deposit_id = payment.deposit_to_account_id or get_undeposited_funds_id(db)

    if ar_id and deposit_id:
        journal_lines = [
            {
                "account_id": deposit_id,
                "debit": Decimal(str(data.amount)),
                "credit": Decimal("0"),
                "description": f"Payment from {customer.name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": Decimal(str(data.amount)),
                "description": f"Payment from {customer.name}",
            },
        ]
        txn = create_journal_entry(
            db, data.date, f"Payment from {customer.name}",
            journal_lines, source_type="payment", source_id=payment.id,
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
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(status_code=400, detail="Payment already voided")
    check_closing_date(db, payment.date)

    # Reverse journal entry
    if payment.transaction_id:
        from app.models.transactions import TransactionLine
        original_lines = db.query(TransactionLine).filter(
            TransactionLine.transaction_id == payment.transaction_id
        ).all()
        reverse_lines = [
            {"account_id": ol.account_id, "debit": ol.credit, "credit": ol.debit,
             "description": f"VOID: {ol.description or ''}"}
            for ol in original_lines
        ]
        if reverse_lines:
            customer = db.query(Customer).filter(Customer.id == payment.customer_id).first()
            cname = customer.name if customer else "Unknown"
            create_journal_entry(
                db, payment.date,
                f"VOID Payment from {cname}",
                reverse_lines, source_type="payment_void", source_id=payment.id,
            )

    # Reverse invoice allocations
    for alloc in payment.allocations:
        invoice = db.query(Invoice).filter(Invoice.id == alloc.invoice_id).first()
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
