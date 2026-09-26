# ============================================================================
# Bill Payments — pay bills (AP), DR AP (2000), CR Bank
# Feature 1 continued: Pay Bills workflow
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.bills import Bill, BillStatus, BillPayment, BillPaymentAllocation
from app.models.contacts import Vendor
from app.models.accounts import Account
from app.schemas.bills import BillPaymentCreate, BillPaymentResponse
from app.services.accounting import create_journal_entry, get_ap_account_id
from app.services.closing_date import check_closing_date

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


@router.post("", response_model=BillPaymentResponse, status_code=201)
def create_bill_payment(data: BillPaymentCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    from app.services.currency import (
        document_currency,
        fx_gain_loss_account_id,
        resolve_rate,
        to_home,
    )

    pay_currency, pay_rate = resolve_rate(db, data.currency, data.exchange_rate)

    alloc_total = sum(a.amount for a in data.allocations)
    if alloc_total > data.amount:
        raise HTTPException(status_code=400, detail="Allocations exceed payment amount")

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
    )
    db.add(payment)
    db.flush()

    # Home-currency value of A/P relieved per allocation (at each bill's
    # booked rate) — the realized-FX basis, mirroring the A/R side.
    ap_home_debits: list[Decimal] = []
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

    # Journal: DR AP, CR Bank
    ap_id = get_ap_account_id(db)
    bank_id = data.pay_from_account_id
    if not bank_id:
        # Default to first checking account
        checking = db.query(Account).filter(Account.account_number == "1000").first()
        bank_id = checking.id if checking else None

    if ap_id and bank_id:
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
        txn = create_journal_entry(
            db,
            data.date,
            f"Bill payment to {vendor.name}",
            journal_lines,
            source_type="bill_payment",
            source_id=payment.id,
        )
        payment.transaction_id = txn.id

    db.commit()
    db.refresh(payment)
    resp = BillPaymentResponse.model_validate(payment)
    resp.vendor_name = vendor.name
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
