# ============================================================================
# Receive Payments — the allocation loop applies a payment across open
# invoices; exact Decimal math throughout.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.payments import Payment, PaymentAllocation
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Customer
from app.services.sub_customers import pays_for, refuse_unless_pays_for
from app.models.transactions import Transaction
from app.schemas.payments import (
    PaymentApply,
    PaymentCreate,
    PaymentDiscountAdd,
    PaymentDiscountResponse,
    PaymentResponse,
)
from app.services.accounting import (
    _q,
    all_lines,
    create_journal_entry,
    get_ar_account_id,
    get_undeposited_funds_id,
    reversing_lines,
    void_line_memo,
)
from app.services.closing_date import check_closing_date
from app.services.payments.clearing import (
    DISPLAY_NAMES,
    PROVIDER_NAMES,
    clearing_balance,
    existing_clearing_account,
    is_fee_line,
)
from app.services import cash_basis, request_keys
from app.services.audit import _actor

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
    resp.discounts = _discounts(payment)
    return resp


def _discounts(payment: Payment) -> list[PaymentDiscountResponse]:
    """The early-payment discounts taken with the payment (#664): the
    credit memos it posted, applied to their invoices."""
    from sqlalchemy.orm import object_session

    from app.models.credit_memos import CreditMemo, CreditMemoStatus

    db = object_session(payment)
    if db is None:
        return []
    rows = []
    for cm in (
        db.query(CreditMemo)
        .filter(
            CreditMemo.discount_payment_id == payment.id,
            CreditMemo.status != CreditMemoStatus.VOID,
        )
        .order_by(CreditMemo.id)
    ):
        inv = cm.original_invoice
        rows.append(
            PaymentDiscountResponse(
                invoice_id=cm.original_invoice_id,
                invoice_number=inv.invoice_number if inv else None,
                amount=cm.total,
                credit_memo_id=cm.id,
                memo_number=cm.memo_number,
                override=bool(cm.discount_override),
                account_id=(
                    next(
                        (ln.account_id for ln in cm.transaction.lines if ln.debit > 0),
                        None,
                    )
                    if cm.transaction
                    else None
                ),
            )
        )
    return rows


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
    payments = (
        q.order_by(Payment.date.desc(), Payment.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
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
        provider = payment.method if payment.method in PROVIDER_NAMES else None
        if provider:
            resp.has_provider_fee = any(
                is_fee_line(ln.description, provider)
                for ln in all_lines(db, payment.transaction_id)
            )
    return resp


@router.post("", response_model=PaymentResponse, status_code=201)
def create_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    request: Request = None,
    response: Response = None,
):
    check_closing_date(db, data.date)
    # A retry of a request whose response was lost must not book the
    # payment twice (#292): an Idempotency-Key header names the intended
    # payment, and its answer is kept with it.
    replay = request_keys.reserve(
        db, request, "POST /api/payments", data.model_dump(), _actor(db)
    )
    if replay is not None and not isinstance(replay, request_keys.Reservation):
        return replay
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Reject non-positive amounts at the boundary; otherwise create_journal_entry
    # raises ValueError on the AR/bank line, which the framework surfaces as a
    # 500. Refunds belong in a credit memo, not a negative-amount payment.
    from app.services.currency import (
        document_currency,
        fx_gain_loss_account_id,
        home_currency,
        resolve_rate,
        to_home,
    )

    pay_currency, pay_rate = resolve_rate(db, data.currency, data.exchange_rate)

    if data.amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Payment amount must be positive; use a credit memo for refunds",
        )
    # The early-payment discounts the customer took (#664): a discount
    # comes with a payment on its invoice, said by the invoice's number.
    from app.services import early_pay_discounts

    for a in data.allocations:
        if a.amount <= 0 and a.discount > 0:
            inv = db.get(Invoice, a.invoice_id)
            early_pay_discounts.refuse_unpaid_discount(
                "invoice",
                inv.invoice_number if inv else str(a.invoice_id),
                a.amount,
                a.discount,
            )
    if any(a.amount <= 0 for a in data.allocations):
        raise HTTPException(
            status_code=400,
            detail="Allocation amounts must be positive",
        )

    def _number(invoice_id):
        inv = db.get(Invoice, invoice_id)
        return inv.invoice_number if inv else invoice_id

    early_pay_discounts.refuse_doubled(
        data.allocations, "invoice_id", "invoice", _number
    )
    # and where they post, checked before anything is written
    discount_acct = early_pay_discounts.discount_account(
        db,
        data.discount_account_id,
        [a.discount for a in data.allocations],
        data.allow_discount_override,
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
        # The payer's own invoice, or one of a sub-customer billed with
        # it (#638): a property's payment pays its tenants' invoices.
        if not pays_for(db, data.customer_id, invoice):
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
    # (invoice, discount, why it was taken against the terms) to post
    discounts: list[tuple] = []
    from app.routes._roles import is_admin

    may_override = request is None or is_admin(request)
    # Apply allocations to the invoices locked and validated above.
    for alloc_data, invoice in zip(data.allocations, allocated_invoices):
        if alloc_data.amount > invoice.balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Allocation {alloc_data.amount} exceeds invoice {invoice.invoice_number} balance {invoice.balance_due}",
            )
        discount = _q(alloc_data.discount)
        if discount > 0 and alloc_data.amount + discount > invoice.balance_due:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"${_q(alloc_data.amount):,.2f} paid and a ${discount:,.2f} "
                    f"discount is more than the ${invoice.balance_due:,.2f} still "
                    f"due on invoice {invoice.invoice_number}."
                ),
            )
        # the discount the invoice's own terms offer, or the override
        # (an administrator's, recorded with the reason)
        against = early_pay_discounts.check_against_terms(
            db,
            invoice,
            "invoice",
            invoice.invoice_number,
            discount,
            invoice.balance_due,
            data.date,
            data.allow_discount_override,
            may_override,
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
        if discount > 0:
            early_pay_discounts.refuse_foreign(
                invoice,
                inv_currency,
                home_currency(db),
                invoice.invoice_number,
                "invoice",
            )
            discounts.append((invoice, discount, against))

    # The discounts post once every row has passed (#665): a refused row
    # never leaves a numbered memo behind to be named and rolled back.
    for invoice, discount, against in discounts:
        early_pay_discounts.post_customer_discount(
            db, payment, invoice, discount, discount_acct, against
        )
    early_pay_discounts.record_override(
        payment, [a for _i, _d, a in discounts if a], _actor(db)
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
        # The cash-basis reading of this payment (#483): the invoices'
        # income, tax and cost lines prorated by what was applied, the
        # remainder as unapplied cash income, balanced on A/R.
        journal_lines += cash_basis.customer_payment_cash_lines(
            db,
            ar_id,
            list(zip(allocated_invoices, ar_home_credits)),
            to_home(unallocated, pay_rate),
            f"Payment from {customer.name}",
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

    db.flush()
    db.refresh(payment)
    resp = _response(payment, customer.name)
    request_keys.finish(db, replay, 201, resp.model_dump(mode="json"), response)
    db.commit()
    return resp


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
    if any(_q(a.amount) <= 0 for a in data.allocations):
        raise HTTPException(
            status_code=400, detail="Allocation amounts must be positive"
        )
    if any(a.discount for a in data.allocations):
        raise HTTPException(
            status_code=400,
            detail=(
                "An early-payment discount is taken when the payment is "
                "received, not when its remainder is applied."
            ),
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
    applied_now: list[tuple] = []  # (invoice, home amount) for the cash basis
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
        # A payment pays its own customer's invoices only (#189), and
        # those of a sub-customer billed with it (#638).
        refuse_unless_pays_for(db, payment.customer_id, invoice, "payment")
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
        applied_now.append(
            (invoice, to_home(amount, Decimal(str(invoice.exchange_rate or 1))))
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

    # The cash basis (#483): the remainder was Unapplied Cash Payment
    # Income on the payment's date; now that it pays these invoices, move
    # it to their income. Cash lines only — the accrual reading sees nothing.
    _reclass_unapplied(db, payment, applied_now)

    db.commit()
    db.refresh(payment)
    return _response(payment)


@router.post("/{payment_id}/discount", response_model=PaymentResponse)
def add_discount(
    payment_id: int,
    data: PaymentDiscountAdd,
    request: Request = None,
    response: Response = None,
    db: Session = Depends(get_db),
):
    """Take an early-payment discount with a payment already recorded
    (#665): the customer paid 98 of 100 within the window and the 2 was not
    taken then. The discount is checked against the invoice's terms on the
    payment's date and its balance before the payment, posts as one taken
    with the payment would (a credit memo tied to it, voided with it), and
    is dated as the payment is.

    Claimed before anything is read (#665: four at once each posted a
    memo): an Idempotency-Key is reserved, then the payment's row is
    written, so a second request waits for the first to commit and then
    reads its discount and is refused; a retry with the same key answers
    with the first response."""
    from app.routes._roles import is_admin
    from app.services import early_pay_discounts
    from app.services.currency import document_currency, home_currency

    replay = request_keys.reserve(
        db,
        request,
        f"POST /api/payments/{payment_id}/discount",
        data.model_dump(),
        _actor(db),
    )
    if replay is not None and not isinstance(replay, request_keys.Reservation):
        return replay
    _claim(db, Payment, payment_id)
    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.is_voided:
        raise HTTPException(
            status_code=400, detail="This payment is void, so no discount goes with it."
        )
    check_closing_date(db, payment.date)
    acct = early_pay_discounts.discount_account(
        db, data.discount_account_id, [data.discount], data.allow_discount_override
    )
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == data.invoice_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    number = invoice.invoice_number
    paid = next((a for a in payment.allocations if a.invoice_id == invoice.id), None)
    if paid is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This payment paid nothing on invoice {number}; a discount is "
                "taken with the payment that paid it."
            ),
        )
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail=f"Invoice {number} is void.")
    discount = _q(data.discount)
    if discount > invoice.balance_due:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A ${discount:,.2f} discount is more than the "
                f"${invoice.balance_due:,.2f} still due on invoice {number}."
            ),
        )
    early_pay_discounts.refuse_foreign(
        invoice, document_currency(invoice, db), home_currency(db), number, "invoice"
    )
    # its balance before this payment, as when the discount was suggested
    before = _q(Decimal(str(invoice.balance_due)) + Decimal(str(paid.amount)))
    against = early_pay_discounts.check_against_terms(
        db,
        invoice,
        "invoice",
        number,
        discount,
        before,
        payment.date,
        data.allow_discount_override,
        request is None or is_admin(request),
    )
    early_pay_discounts.post_customer_discount(
        db, payment, invoice, discount, acct, against
    )
    early_pay_discounts.record_override(
        payment, [against] if against else [], _actor(db)
    )
    db.flush()
    db.refresh(payment)
    resp = _response(payment)
    request_keys.finish(db, replay, 200, resp.model_dump(mode="json"), response)
    db.commit()
    return resp


def _claim(db: Session, model, row_id: int) -> None:
    """Write the row before anything is read (#665). On SQLite the write
    opens the transaction and takes the database's write lock, so a second
    request waits here for the first to commit and reads what it posted;
    on PostgreSQL it is the row's lock. The value is left as it was."""
    from sqlalchemy.exc import OperationalError

    try:
        db.query(model).filter(model.id == row_id).update(
            {model.discount_override: model.discount_override},
            synchronize_session=False,
        )
    except OperationalError as e:
        if "locked" in str(e.orig).lower():
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another discount on this payment is being saved; try "
                    "again in a moment."
                ),
            ) from e
        raise


def _paid_out_warning(db, lines, provider) -> str | None:
    """A sentence when this payment's net (what it added to the provider's
    clearing account) is no longer all there, which means part of it has
    already been paid out to the bank (#584). Per-payment payout tracking does not exist
    yet, so this is a warning, not a block."""
    if not provider:
        return None
    clearing = existing_clearing_account(db, provider)  # reading creates nothing
    if clearing is None:
        return None
    net_in = sum(
        (Decimal(str(ln.debit)) - Decimal(str(ln.credit)))
        for ln in lines
        if ln.account_id == clearing.id
    )
    if net_in <= 0 or clearing_balance(db, provider) >= net_in:
        return None
    return (
        f"This payment looks already paid out by {DISPLAY_NAMES[provider]}: "
        f"{DISPLAY_NAMES[provider]} Clearing now runs short by the amount "
        "reversed. Check the payout, and record the refund or dispute taken "
        "from the next one."
    )


def _reclass_unapplied(db: Session, payment: Payment, applied) -> None:
    """Move the applied part of the remainder from Unapplied Cash Payment
    Income to the invoices' income, as cash lines appended to the payment's
    own entry (#483): the cash was in hand on the payment's date, the
    period's cash-basis net income does not change, only the account, and
    the void reverses the entry whole."""
    if not applied or not payment.transaction_id:
        return
    txn = db.get(Transaction, payment.transaction_id)
    if txn is None:
        return
    customer = db.query(Customer).filter(Customer.id == payment.customer_id).first()
    cname = customer.name if customer else "customer"
    ar_id = get_ar_account_id(db)
    lines = []
    for invoice, home in applied:
        lines += cash_basis.apply_reclass_lines(
            db, ar_id, invoice, home, f"Payment from {cname} applied"
        )
    cash_basis.append_cash_lines(db, txn, lines)


@router.post("/{payment_id}/void", response_model=PaymentResponse)
def void_payment(
    payment_id: int, fee_refunded: bool = False, db: Session = Depends(get_db)
):
    """Void a payment — reverses journal entry and restores invoice balances.

    An online payment posted to a provider's clearing account is reversed
    against that account, never the bank, so a refund or dispute comes out of
    what the provider holds (#584). The processing fee the provider kept
    stays booked as an expense unless `fee_refunded` says the provider gave it
    back. A payment already paid out can't be told apart per payment yet, so
    the void goes through and the response carries a warning.

    A deposit a payment application applied is voided with that application."""
    from app.models.pay_applications import PayApplication

    applied = (
        db.query(PayApplication)
        .filter(
            PayApplication.deposit_payment_id == payment_id,
            PayApplication.status == "approved",
        )
        .first()
    )
    if applied is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This payment applies a deposit to payment application "
                f"{applied.number}; void the application to undo it."
            ),
        )
    return void_payment_unchecked(payment_id, db, fee_refunded)


def void_payment_unchecked(
    payment_id: int, db: Session, fee_refunded: bool = False, commit: bool = True
):
    """The void itself, without the payment-application guard (a payment
    application voiding its own deposit payment calls this). With
    `commit=False` the work is flushed and left for the caller to commit, so
    it can be undone with the caller's."""
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
    payout_warning = None
    # Money already taken to the bank in a deposit (or received straight
    # into a bank account that has since been reconciled) can't just be
    # reversed out of Undeposited Funds: that drove 1200 negative while the
    # deposit still claimed the money (explore 2.17.3, W-H4). A sales
    # receipt is voided through here too, so the same rule covers it.
    from app.services import qbo_documents
    from app.services.undeposited_funds import refuse_void_if_deposited

    # A payment the QuickBooks Online import created came in through the
    # ledger import's posting, not one of its own.
    refuse_void_if_deposited(db, payment, qbo_documents.money_in_posting(db, payment))
    qbo_documents.void_payment_import_posting(db, payment)
    # The QuickBooks Online sales receipt this payment is the payment half
    # of, if any: it is voided with it (below), as the Sales Receipts page
    # does, so it is never left open with nothing in A/R.
    receipt = qbo_documents.sales_receipt_of(db, payment)

    # Reverse journal entry — every line, the cash-basis ones included,
    # each undone on its own basis (#483)
    if payment.transaction_id:
        original_lines = all_lines(db, payment.transaction_id)
        provider = payment.method if payment.method in PROVIDER_NAMES else None
        # judged on the whole entry: what this payment added to clearing
        payout_warning = _paid_out_warning(db, original_lines, provider)
        if provider and not fee_refunded:
            # the provider kept its fee: leave those two lines standing
            original_lines = [
                ol for ol in original_lines if not is_fee_line(ol.description, provider)
            ]
        reverse_lines = [
            {
                "account_id": ol.account_id,
                "debit": ol.credit,
                "credit": ol.debit,
                "description": void_line_memo(ol.description),
                "class_id": ol.class_id,
                "job_id": ol.job_id,
                "cost_code_id": ol.cost_code_id,
                "cost_type": ol.cost_type,
                "function": ol.function,
                "basis": ol.basis,
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
            reversing_lines(all_lines(db, applied_fx.id)),
            source_type="payment_apply_void",
            source_id=applied_fx.id,
            reference=applied_fx.reference or "",
        )

    # The discounts taken with it go first (#664), then its own amounts.
    from app.services.early_pay_discounts import void_customer_discounts

    void_customer_discounts(db, payment)

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
    if commit:
        db.commit()
    else:
        db.flush()
    if receipt is not None:
        from app.routes.invoices.lifecycle import void_invoice

        void_invoice(receipt.id, db)
    db.refresh(payment)
    resp = _response(payment)
    resp.warning = payout_warning
    return resp
