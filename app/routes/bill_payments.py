# ============================================================================
# Bill Payments — pay bills (AP), DR AP (2000), CR Bank
# Feature 1 continued: Pay Bills workflow
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.bills import Bill, BillStatus, BillPayment, BillPaymentAllocation
from app.models.contacts import Vendor
from app.schemas.bills import (
    BillPaymentCreate,
    BillPaymentDiscountAdd,
    BillPaymentDiscountResponse,
    BillPaymentMemoUpdate,
    BillPaymentResponse,
)
from app.services.accounting import (
    _q,
    all_lines,
    create_journal_entry,
    get_ap_account_id,
    get_checking_account_id,
    void_line_memo,
)
from app.services import cash_basis
from app.services.check_numbers import refuse_duplicate
from app.services.closing_date import check_closing_date
from app.services import request_keys
from app.services.audit import _actor

router = APIRouter(prefix="/api/bill-payments", tags=["bill_payments"])


@router.get("", response_model=list[BillPaymentResponse])
def list_bill_payments(
    vendor_id: int = None, bill_id: int = None, db: Session = Depends(get_db)
):
    """`?bill_id=` lists the payments applied to one bill (its view offers
    Void on each — a paid bill's payment could not be voided on screen)."""
    q = db.query(BillPayment).options(
        joinedload(BillPayment.vendor), selectinload(BillPayment.allocations)
    )
    if vendor_id:
        q = q.filter(BillPayment.vendor_id == vendor_id)
    if bill_id:
        q = q.filter(
            BillPayment.id.in_(
                db.query(BillPaymentAllocation.bill_payment_id).filter(
                    BillPaymentAllocation.bill_id == bill_id
                )
            )
        )
    payments = q.order_by(BillPayment.date.desc(), BillPayment.id.desc()).all()
    results = []
    for p in payments:
        resp = BillPaymentResponse.model_validate(p)
        if p.vendor:
            resp.vendor_name = p.vendor.name
        results.append(resp)
    return results


@router.get("/{bill_payment_id}", response_model=BillPaymentResponse)
def get_bill_payment(bill_payment_id: int, db: Session = Depends(get_db)):
    """One bill payment and the bills it paid. The bank register links a
    bill payment here (#/bill-payments/{id}); the link said "Page not
    found"."""
    p = (
        db.query(BillPayment)
        .options(joinedload(BillPayment.vendor), selectinload(BillPayment.allocations))
        .filter(BillPayment.id == bill_payment_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Bill payment not found")
    resp = BillPaymentResponse.model_validate(p)
    if p.vendor:
        resp.vendor_name = p.vendor.name
    resp.discounts = _discounts(db, p)
    return resp


def _discounts(db: Session, payment: BillPayment) -> list[BillPaymentDiscountResponse]:
    """The early-payment discounts taken with the payment (#664): the
    vendor credits it posted, applied to their bills."""
    from app.models.vendor_credits import VendorCredit, VendorCreditStatus

    rows = []
    for vc in (
        db.query(VendorCredit)
        .filter(
            VendorCredit.discount_bill_payment_id == payment.id,
            VendorCredit.status != VendorCreditStatus.VOID,
        )
        .order_by(VendorCredit.id)
    ):
        rows.append(
            BillPaymentDiscountResponse(
                bill_id=vc.original_bill_id,
                bill_number=vc.original_bill.bill_number if vc.original_bill else None,
                amount=vc.total,
                vendor_credit_id=vc.id,
                credit_number=vc.credit_number,
                account_id=vc.lines[0].account_id if vc.lines else None,
                override=bool(vc.discount_override),
            )
        )
    return rows


@router.post("", response_model=BillPaymentResponse, status_code=201)
def create_bill_payment(
    data: BillPaymentCreate,
    db: Session = Depends(get_db),
    request: Request = None,
    response: Response = None,
    allow_duplicate_check_number: bool = Query(False),
):
    check_closing_date(db, data.date)
    # A retry of a request whose response was lost must not pay the bill
    # twice (#292): an Idempotency-Key header names the intended payment
    replay = request_keys.reserve(
        db, request, "POST /api/bill-payments", data.model_dump(), _actor(db)
    )
    if replay is not None and not isinstance(replay, request_keys.Reservation):
        return replay

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # A Sub whose certificate of insurance had expired by the payment date is
    # not paid without an explicit override, which the payment records (#553).
    # A Sub with no COI on file only warns in Pay Bills; it is not blocked.
    coi_note = None
    if (
        vendor.vendor_type == "sub"
        and vendor.coi_expires
        and data.date > vendor.coi_expires
    ):
        if not data.coi_override:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{vendor.name}'s certificate of insurance expired on "
                    f"{vendor.coi_expires.isoformat()}. Get a current one, or "
                    "pay anyway with coi_override and the payment will record "
                    "that it was overridden."
                ),
            )
        coi_note = f"Paid with COI expired {vendor.coi_expires.isoformat()}"

    from app.services.currency import (
        document_currency,
        fx_gain_loss_account_id,
        home_currency,
        resolve_rate,
        to_home,
    )

    pay_currency, pay_rate = resolve_rate(db, data.currency, data.exchange_rate)

    alloc_total = sum(a.amount for a in data.allocations)
    if alloc_total > data.amount:
        raise HTTPException(status_code=400, detail="Allocations exceed payment amount")
    # The early-payment discounts we took (#664) and where they post,
    # checked before anything is written.
    from app.services import early_pay_discounts

    discount_acct = early_pay_discounts.discount_account(
        db,
        data.discount_account_id,
        [a.discount for a in data.allocations],
        data.allow_discount_override,
    )
    # a discount comes with a payment on its bill, said by the bill's number
    for a in data.allocations:
        if a.discount > 0 and a.amount <= 0:
            row = db.get(Bill, a.bill_id)
            early_pay_discounts.refuse_unpaid_discount(
                "bill",
                row.bill_number if row else str(a.bill_id),
                Decimal(str(a.amount)),
                a.discount,
            )

    def _number(bill_id):
        row = db.get(Bill, bill_id)
        return row.bill_number if row else bill_id

    early_pay_discounts.refuse_doubled(data.allocations, "bill_id", "bill", _number)

    # The accounts the journal needs, resolved before anything is written:
    # a missing one is a 409 naming it, with no payment, allocations or
    # bill balances left behind (#284: the default bank answered None
    # when 1000 was missing, and the payment committed without a journal).
    ap_id = get_ap_account_id(db)
    bank_id = data.pay_from_account_id or get_checking_account_id(db)
    # a check number the paying account already has (#481): after the
    # replay check, so a payment sent again answers with itself
    refuse_duplicate(db, bank_id, data.check_number, allow=allow_duplicate_check_number)

    payment = BillPayment(
        vendor_id=data.vendor_id,
        date=data.date,
        amount=data.amount,
        method=data.method,
        check_number=data.check_number,
        pay_from_account_id=data.pay_from_account_id,
        notes=data.notes,
        currency=pay_currency,
        exchange_rate=pay_rate,
        coi_override=coi_note is not None,
        coi_override_note=coi_note,
    )
    db.add(payment)
    db.flush()

    # Home-currency value of A/P relieved per allocation (at each bill's
    # booked rate) — the realized-FX basis, mirroring the A/R side.
    ap_home_debits: list[Decimal] = []
    applied_bills: list[tuple] = []  # (bill, home amount) for the cash basis
    # (bill, discount, why it was taken against the terms) to post
    discounts: list[tuple] = []
    from app.routes._roles import is_admin

    may_override = request is None or is_admin(request)
    for alloc_data in data.allocations:
        # Row lock for the read-check-write, as the void path below and
        # create_payment already do (GHSA-rm5h-555g-vpjj). No-op on SQLite.
        bill = (
            db.query(Bill)
            .filter(Bill.id == alloc_data.bill_id)
            .with_for_update()
            .first()
        )
        if not bill:
            raise HTTPException(
                status_code=404, detail=f"Bill {alloc_data.bill_id} not found"
            )
        # A payment to one vendor pays that vendor's bills only (the
        # customer-side rule, #189; vendor credits already check this).
        if bill.vendor_id != data.vendor_id:
            raise HTTPException(
                status_code=400,
                detail=f"Bill {bill.bill_number} belongs to a different vendor.",
            )
        if alloc_data.amount > float(bill.balance_due):
            raise HTTPException(
                status_code=400, detail="Allocation exceeds bill balance"
            )
        discount = _q(alloc_data.discount)
        paying = _q(Decimal(str(alloc_data.amount)))
        if discount > 0 and paying + discount > Decimal(str(bill.balance_due)):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"${paying:,.2f} paid and a ${discount:,.2f} discount is "
                    f"more than the ${Decimal(str(bill.balance_due)):,.2f} still "
                    f"due on bill {bill.bill_number}."
                ),
            )
        # the discount the bill's own terms offer, or the override
        # (an administrator's, recorded with the reason)
        against = early_pay_discounts.check_against_terms(
            db,
            bill,
            "bill",
            bill.bill_number,
            discount,
            bill.total,
            data.date,
            data.allow_discount_override,
            may_override,
        )

        bill_currency = document_currency(bill, db)
        if bill_currency != pay_currency:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Payment currency {pay_currency} does not match bill "
                    f"{bill.bill_number} currency {bill_currency}; pay each "
                    f"currency with a separate payment"
                ),
            )
        ap_home_debits.append(
            to_home(alloc_data.amount, bill.exchange_rate or Decimal("1"))
        )
        applied_bills.append((bill, ap_home_debits[-1]))

        db.add(
            BillPaymentAllocation(
                bill_payment_id=payment.id,
                bill_id=alloc_data.bill_id,
                amount=alloc_data.amount,
            )
        )

        bill.amount_paid += Decimal(str(alloc_data.amount))
        bill.balance_due -= Decimal(str(alloc_data.amount))
        if bill.balance_due < 0:
            raise HTTPException(
                status_code=409,
                detail=f"Bill {bill.bill_number} would be over-applied",
            )
        if bill.balance_due <= 0:
            bill.status = BillStatus.PAID
        else:
            bill.status = BillStatus.PARTIAL
        if discount > 0:
            early_pay_discounts.refuse_foreign(
                bill, bill_currency, home_currency(db), bill.bill_number, "bill"
            )
            discounts.append((bill, discount, against))

    # The discounts post once every row has passed (#665): a refused row
    # never leaves a numbered credit behind.
    for bill, discount, against in discounts:
        early_pay_discounts.post_vendor_discount(
            db, payment, bill, discount, discount_acct, against
        )
    early_pay_discounts.record_override(
        payment, [a for _b, _d, a in discounts if a], _actor(db)
    )

    # A payment of one bill carries that bill's memo, for the check's memo
    # line (#602); a memo typed on the payment wins, and a payment of
    # several bills leaves the memo for the person to type.
    if not (data.notes or "").strip() and len(applied_bills) == 1:
        payment.notes = (applied_bills[0][0].notes or "").strip() or None

    # Journal: DR AP, CR Bank — always; the accounts were resolved above
    if True:
        # Cash paid, in home currency at the payment-date rate; A/P
        # relieved at each bill's BOOKED rate. Unallocated remainder (a
        # vendor prepayment) has no booking rate yet, so it relieves at
        # the payment rate.
        cash_home = to_home(data.amount, pay_rate)
        unallocated = Decimal(str(data.amount)) - Decimal(str(alloc_total))
        ap_home = sum(ap_home_debits, Decimal("0")) + to_home(unallocated, pay_rate)
        journal_lines = [
            {
                "account_id": ap_id,
                "debit": ap_home,
                "credit": Decimal("0"),
                "description": f"Bill payment to {vendor.name}",
            },
            {
                "account_id": bank_id,
                "debit": Decimal("0"),
                "credit": cash_home,
                "description": f"Bill payment to {vendor.name}",
            },
        ]
        # Realized FX: settling A/P cheaper than booked = gain (credit),
        # dearer = loss (debit). Mirror of the customer-payment side.
        residual = ap_home - cash_home
        if residual != 0:
            fx_id = fx_gain_loss_account_id(db)
            journal_lines.append(
                {
                    "account_id": fx_id,
                    "debit": -residual if residual < 0 else Decimal("0"),
                    "credit": residual if residual > 0 else Decimal("0"),
                    "description": f"Realized FX on bill payment to {vendor.name}",
                }
            )
        # The cash-basis reading of this payment (#483): the bills' expense,
        # cost and inventory lines prorated by what was applied, the
        # remainder as unapplied cash expense, balanced on A/P.
        journal_lines += cash_basis.bill_payment_cash_lines(
            db,
            ap_id,
            applied_bills,
            to_home(unallocated, pay_rate),
            f"Bill payment to {vendor.name}",
        )
        txn = create_journal_entry(
            db,
            data.date,
            f"Bill payment to {vendor.name}",
            journal_lines,
            source_type="bill_payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.flush()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    resp.vendor_name = vendor.name
    resp.discounts = _discounts(db, payment)
    request_keys.finish(db, replay, 201, resp.model_dump(mode="json"), response)
    db.commit()
    return resp


@router.post("/{bill_payment_id}/discount", response_model=BillPaymentResponse)
def add_discount(
    bill_payment_id: int,
    data: BillPaymentDiscountAdd,
    request: Request = None,
    response: Response = None,
    db: Session = Depends(get_db),
):
    """Take an early-payment discount with a bill payment already recorded
    (#665), checked against the bill's terms on the payment's date and its
    balance before the payment, posted as one taken with it would be.
    Claimed before anything is read, and an Idempotency-Key honoured, as
    the customer side (#665)."""
    from app.routes._roles import is_admin
    from app.routes.payments import _claim
    from app.services import early_pay_discounts
    from app.services.currency import document_currency, home_currency

    # a query parameter this route doesn't take is refused, not ignored
    if request is not None and request.query_params:
        key = next(iter(request.query_params.keys()))
        raise HTTPException(
            status_code=422, detail=f"'{key}' is not a parameter this accepts."
        )
    replay = request_keys.reserve(
        db,
        request,
        f"POST /api/bill-payments/{bill_payment_id}/discount",
        data.model_dump(),
        _actor(db),
    )
    if replay is not None and not isinstance(replay, request_keys.Reservation):
        return replay
    _claim(db, BillPayment, bill_payment_id)
    payment = (
        db.query(BillPayment)
        .filter(BillPayment.id == bill_payment_id)
        .with_for_update()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Bill payment not found")
    if payment.is_voided:
        raise HTTPException(
            status_code=400, detail="This payment is void, so no discount goes with it."
        )
    check_closing_date(db, payment.date)
    acct = early_pay_discounts.discount_account(
        db, data.discount_account_id, [data.discount], data.allow_discount_override
    )
    bill = db.query(Bill).filter(Bill.id == data.bill_id).with_for_update().first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    number = bill.bill_number
    paid = next((a for a in payment.allocations if a.bill_id == bill.id), None)
    if paid is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This payment paid nothing on bill {number}; a discount is taken "
                "with the payment that paid it."
            ),
        )
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail=f"Bill {number} is void.")
    discount = _q(data.discount)
    if discount > Decimal(str(bill.balance_due)):
        raise HTTPException(
            status_code=400,
            detail=(
                f"A ${discount:,.2f} discount is more than the "
                f"${Decimal(str(bill.balance_due)):,.2f} still due on bill {number}."
            ),
        )
    early_pay_discounts.refuse_foreign(
        bill, document_currency(bill, db), home_currency(db), number, "bill"
    )
    against = early_pay_discounts.check_against_terms(
        db,
        bill,
        "bill",
        number,
        discount,
        bill.total,
        payment.date,
        data.allow_discount_override,
        request is None or is_admin(request),
    )
    early_pay_discounts.post_vendor_discount(db, payment, bill, discount, acct, against)
    early_pay_discounts.record_override(
        payment, [against] if against else [], _actor(db)
    )
    db.flush()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    resp.vendor_name = payment.vendor.name if payment.vendor else None
    resp.discounts = _discounts(db, payment)
    request_keys.finish(db, replay, 200, resp.model_dump(mode="json"), response)
    db.commit()
    return resp


@router.put("/{bill_payment_id}/memo", response_model=BillPaymentResponse)
def update_bill_payment_memo(
    bill_payment_id: int, data: BillPaymentMemoUpdate, db: Session = Depends(get_db)
):
    """Set the payment's memo, which prints on its check's memo line (#602).
    Print Check saves the memo here first. Nothing is posted: the memo is
    not in the books, so a closed period does not refuse it."""
    payment = db.query(BillPayment).filter(BillPayment.id == bill_payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Bill payment not found")
    if payment.is_voided:
        raise HTTPException(status_code=400, detail="A void payment has no check.")
    payment.notes = (data.notes or "").strip() or None
    db.commit()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    if payment.vendor:
        resp.vendor_name = payment.vendor.name
    return resp


@router.post("/{bill_payment_id}/void", response_model=BillPaymentResponse)
def void_bill_payment(bill_payment_id: int, db: Session = Depends(get_db)):
    """Void a bill payment — reverses JE and restores bill balances.

    Mirror of /api/payments/{id}/void for the AP side. Posts a reversing
    journal entry dated to the original payment, walks each allocation,
    and restores the bill's amount_paid / balance_due / status to its
    pre-payment values.
    """
    # Row-lock the payment so two concurrent voids can't both pass the
    # is_voided guard and post duplicate reversing JEs.
    payment = (
        db.query(BillPayment)
        .filter(BillPayment.id == bill_payment_id)
        .with_for_update()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Bill payment not found")
    if payment.is_voided:
        raise HTTPException(status_code=400, detail="Bill payment already voided")
    check_closing_date(db, payment.date)
    # The bill's view offers Void on each payment now. A check that cleared
    # in a completed reconciliation stays put, as every other void refuses.
    if payment.transaction is not None:
        from app.services.bank_posting import assert_not_reconciled

        assert_not_reconciled(payment.transaction)

    if payment.transaction_id:
        # every line, the cash-basis ones included, each undone on its
        # own basis (#483)
        original_lines = all_lines(db, payment.transaction_id)
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
            vendor = db.query(Vendor).filter(Vendor.id == payment.vendor_id).first()
            vname = vendor.name if vendor else "Unknown"
            create_journal_entry(
                db,
                payment.date,
                f"VOID Bill payment to {vname}",
                reverse_lines,
                source_type="bill_payment_void",
                source_id=payment.id,
            )
        if payment.transaction is not None:
            from app.services.bank_posting import release_statement_links

            # A statement line matched to this check goes back to review.
            release_statement_links(db, payment.transaction)

    # The discounts taken with it go first (#664), then its own amounts.
    from app.services.early_pay_discounts import void_vendor_discounts

    void_vendor_discounts(db, payment)

    # Reverse allocations. Lock each bill row so a concurrent create or
    # second void can't race the read-modify-write of amount_paid /
    # balance_due / status.
    for alloc in payment.allocations:
        bill = db.query(Bill).filter(Bill.id == alloc.bill_id).with_for_update().first()
        if bill:
            bill.amount_paid -= alloc.amount
            bill.balance_due += alloc.amount
            if bill.balance_due >= bill.total:
                bill.status = BillStatus.UNPAID
            elif bill.amount_paid > 0:
                bill.status = BillStatus.PARTIAL
            else:
                bill.status = BillStatus.UNPAID

    payment.is_voided = True
    db.commit()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    if payment.vendor:
        resp.vendor_name = payment.vendor.name
    return resp
