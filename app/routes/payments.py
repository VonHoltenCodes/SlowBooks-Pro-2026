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
from app.models.transactions import Transaction
from app.schemas.payments import PaymentApply, PaymentCreate, PaymentResponse
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_ar_account_id,
    get_undeposited_funds_id,
    reversing_lines,
)
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _response(payment: Payment, customer_name: str = None) -> PaymentResponse:
    """The payment as the screens read it: allocations by invoice NUMBER,
    and how much of it is not applied to any invoice yet (a credit the
    customer holds). Callers eager-load .allocations and their invoices."""
    resp = PaymentResponse.model_validate(payment)
    numbers = {a.id: a.invoice.invoice_number for a in payment.allocations if a.invoice}
    for a in resp.allocations:
        a.invoice_number = numbers.get(a.id)
    applied = sum((Decimal(str(a.amount)) for a in payment.allocations), Decimal("0"))
    resp.unapplied = (
        Decimal("0")
        if payment.is_voided
        else max(_q(Decimal(str(payment.amount)) - applied), Decimal("0"))
    )
    if customer_name is None and payment.customer:
        customer_name = payment.customer.name
    resp.customer_name = customer_name
    return resp


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
        selectinload(Payment.allocations).joinedload(PaymentAllocation.invoice),
    )
    if customer_id:
        q = q.filter(Payment.customer_id == customer_id)
    payments = q.order_by(Payment.date.desc()).offset(skip).limit(limit).all()
    return [_response(p) for p in payments]


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    resp = _response(payment)
    if not payment.is_voided and payment.transaction_id:
        from app.services.undeposited_funds import deposit_holding

        resp.deposited_in = deposit_holding(db, payment)
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

    # Validate every allocation owner before creating or applying the payment.
    allocated_invoices = []
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
        if invoice.customer_id != data.customer_id:
            raise HTTPException(
                status_code=400,
                detail=f"Invoice {invoice.invoice_number} does not belong to payment customer",
            )
        allocated_invoices.append(invoice)

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
    # Apply allocations to the invoices locked and validated above.
    for alloc_data, invoice in zip(data.allocations, allocated_invoices):
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
    return _response(payment, customer.name)


@router.post("/{payment_id}/apply", response_model=PaymentResponse)
def apply_payment(payment_id: int, data: PaymentApply, db: Session = Depends(get_db)):
    """Apply the unapplied part of an earlier payment — an overpayment, a
    prepayment, or a payment recorded without choosing invoices — to the
    same customer's open invoices.

    Before this there was no way to: the credit sat on account 1100 while
    every invoice still read unpaid (explore 2.17.3, macbase1 F12). Moving
    money between the payment and an invoice changes which document it
    settles, not the ledger: the payment already credited A/R in full. A
    foreign-currency remainder relieved A/R at the payment's rate; settling
    an invoice booked at another rate posts the difference as realized FX,
    exactly as applying it on the day would have."""
    from app.services.currency import (
        document_currency,
        fx_gain_loss_account_id,
        to_home,
    )

    # Lock the payment for the read-check-write, as the void does: two
    # applies at once must not both spend the same remainder.
    payment = (
        db.query(Payment).filter(Payment.id == payment_id).with_for_update().first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(
            status_code=400, detail="This payment is void, so it has nothing to apply."
        )
    if any(a.amount <= 0 for a in data.allocations):
        raise HTTPException(
            status_code=400, detail="Allocation amounts must be positive"
        )
    applied = sum((Decimal(str(a.amount)) for a in payment.allocations), Decimal("0"))
    unapplied = _q(Decimal(str(payment.amount)) - applied)
    wanted = _q(sum((Decimal(str(a.amount)) for a in data.allocations), Decimal("0")))
    if wanted > unapplied:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Only ${unapplied:,.2f} of this payment is not applied yet; "
                f"lower the amounts (they add up to ${wanted:,.2f})."
            ),
        )

    pay_currency = document_currency(payment, db)
    pay_rate = Decimal(str(payment.exchange_rate or 1))
    fx_home = Decimal("0")  # A/R still to relieve (+) or over-relieved (−)
    latest = payment.date
    for alloc_data in data.allocations:
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
        # A payment pays its own customer's invoices only (#189).
        if invoice.customer_id != payment.customer_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invoice {invoice.invoice_number} belongs to a different "
                    "customer than this payment."
                ),
            )
        if invoice.status == InvoiceStatus.VOID:
            raise HTTPException(
                status_code=400,
                detail=f"Invoice {invoice.invoice_number} is void.",
            )
        amount = _q(alloc_data.amount)
        if amount > invoice.balance_due:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"${amount:,.2f} is more than the ${invoice.balance_due:,.2f} "
                    f"still due on invoice {invoice.invoice_number}."
                ),
            )
        inv_currency = document_currency(invoice, db)
        if inv_currency != pay_currency:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Payment currency {pay_currency} does not match invoice "
                    f"{invoice.invoice_number} currency {inv_currency}; apply "
                    "it to an invoice in the same currency"
                ),
            )
        fx_home += to_home(amount, Decimal(str(invoice.exchange_rate or 1))) - to_home(
            amount, pay_rate
        )
        latest = max(latest, invoice.date)
        db.add(
            PaymentAllocation(
                payment_id=payment.id, invoice_id=invoice.id, amount=amount
            )
        )
        invoice.amount_paid += amount
        invoice.balance_due -= amount
        invoice.status = (
            InvoiceStatus.PAID if invoice.balance_due == 0 else InvoiceStatus.PARTIAL
        )

    if fx_home != 0:
        customer = db.query(Customer).filter(Customer.id == payment.customer_id).first()
        cname = customer.name if customer else "customer"
        ar_id = get_ar_account_id(db)
        fx_id = fx_gain_loss_account_id(db)
        amt = abs(fx_home)
        loss = fx_home > 0  # the invoice was booked dearer than the cash
        lines = [
            {
                "account_id": fx_id if loss else ar_id,
                "debit": amt,
                "credit": Decimal("0"),
                "description": f"Realized FX on payment from {cname}",
            },
            {
                "account_id": ar_id if loss else fx_id,
                "debit": Decimal("0"),
                "credit": amt,
                "description": f"Realized FX on payment from {cname}",
            },
        ]
        create_journal_entry(
            db,
            latest,
            f"Realized FX on applying payment from {cname}",
            lines,
            source_type="payment_apply",
            source_id=payment.id,
            reference=payment.reference or payment.check_number or "",
        )

    db.commit()
    db.refresh(payment)
    return _response(payment)


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
    # Money already taken to the bank in a deposit (or received straight
    # into a bank account that has since been reconciled) can't just be
    # reversed out of Undeposited Funds: that drove 1200 negative while the
    # deposit still claimed the money (explore 2.17.3, W-H4). A sales
    # receipt is voided through here too, so the same rule covers it.
    from app.services.undeposited_funds import refuse_void_if_deposited

    refuse_void_if_deposited(db, payment)

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
        # A bank-feed line matched to this payment goes back to review.
        from app.services.bank_posting import release_statement_links

        if payment.transaction is not None:
            release_statement_links(db, payment.transaction)

    # Realized FX posted when the payment's remainder was applied later
    # (POST /apply) is part of the payment: undo it with the rest.
    for applied_fx in (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "payment_apply",
            Transaction.source_id == payment.id,
        )
        .all()
    ):
        create_journal_entry(
            db,
            applied_fx.date,
            f"VOID {applied_fx.description or ''}".strip(),
            reversing_lines(applied_fx.lines),
            source_type="payment_apply_void",
            source_id=applied_fx.id,
            reference=applied_fx.reference or "",
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
    return _response(payment)
