"""What was open on a past date: the invoices, bills, credits and payments an
aging report ages as of that date.

A document's stored balance is today's. To read it as of an earlier date,
start from today's balance and put back what came off it afterwards: the
payments dated after the date, the credit memos (write-offs and early-payment
discounts too) and vendor credits dated after it. A document that is void
today was net zero all along, because a void reverses on the document's own
date; one whose void entry is dated after the date was still open then.

Payments, credits and the documents they settle are never counted before the
later of the two dates, so the total equals account 1100 / 2000 on the
Balance Sheet for the same date. Amounts are in each document's own currency;
the caller converts at the document's booked rate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.bills import (
    Bill,
    BillPayment,
    BillPaymentAllocation,
    BillStatus,
)
from app.models.credit_memos import CreditApplication, CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction, TransactionLine
from app.models.vendor_credits import (
    VendorCredit,
    VendorCreditApplication,
    VendorCreditStatus,
)
from app.services.accounting import _q

ZERO = Decimal("0")


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _not_void(column):
    return or_(column.is_(False), column.is_(None))


_VOID = {Invoice: InvoiceStatus.VOID, Bill: BillStatus.VOID}


def _void_after(db: Session, source_type: str, model, as_of: date) -> set[int]:
    """Ids of voided `model` rows whose void entry is dated after `as_of`
    (the entry sits on the document's date, so this is empty unless an
    import or a repair dated it later)."""
    rows = (
        db.query(Transaction.source_id)
        .filter(Transaction.source_type == source_type, Transaction.date > as_of)
        .all()
    )
    ids = {r[0] for r in rows}
    if not ids:
        return set()
    return {
        r[0]
        for r in db.query(model.id)
        .filter(model.id.in_(ids), model.status == _VOID[model])
        .all()
    }


def late_fees(db: Session, invoice_ids=None) -> dict[int, list[tuple[date, Decimal]]]:
    """Invoice id -> [(date, amount)] of the late fees standing on it. A fee
    posts its own A/R debit (source_type "late_fee") and is also added to the
    invoice's total and balance; a fee voided in the journal is not standing."""
    from app.services.qbo_documents import REVERSALS

    q = db.query(Transaction).filter(Transaction.source_type == "late_fee")
    if invoice_ids is not None:
        q = q.filter(Transaction.source_id.in_(invoice_ids))
    out: dict[int, list[tuple[date, Decimal]]] = {}
    for txn in q.order_by(Transaction.date, Transaction.id):
        reversed_ = (
            db.query(Transaction.id)
            .filter(
                Transaction.source_type.in_(REVERSALS),
                Transaction.source_id == txn.id,
            )
            .first()
        )
        if reversed_ is not None:
            continue
        amount = _dec(
            db.query(func.sum(TransactionLine.debit))
            .filter(TransactionLine.transaction_id == txn.id)
            .scalar()
        )
        if amount > 0:
            out.setdefault(txn.source_id, []).append((txn.date, amount))
    return out


def standing_late_fee(db: Session, invoice_id: int) -> Decimal:
    """The late fees standing on one invoice, whatever their date."""
    return sum((a for _, a in late_fees(db, [invoice_id]).get(invoice_id, [])), ZERO)


def open_invoices_as_of(db: Session, as_of: date) -> list[tuple[Invoice, Decimal]]:
    """Every invoice dated on or before `as_of` with what was still owed on
    it then (its own currency), the open ones only."""
    later_paid = {
        iid: _dec(amt)
        for iid, amt in db.query(
            PaymentAllocation.invoice_id, func.sum(PaymentAllocation.amount)
        )
        .join(Payment, PaymentAllocation.payment_id == Payment.id)
        .filter(_not_void(Payment.is_voided), Payment.date > as_of)
        .group_by(PaymentAllocation.invoice_id)
        .all()
    }
    later_credited = {
        iid: _dec(amt)
        for iid, amt in db.query(
            CreditApplication.invoice_id, func.sum(CreditApplication.amount)
        )
        .join(CreditMemo, CreditApplication.credit_memo_id == CreditMemo.id)
        .filter(CreditMemo.status != CreditMemoStatus.VOID, CreditMemo.date > as_of)
        .group_by(CreditApplication.invoice_id)
        .all()
    }
    later = set(later_paid) | set(later_credited)
    voided_later = _void_after(db, "invoice_void", Invoice, as_of)

    q = db.query(Invoice).filter(Invoice.date <= as_of)
    live = and_(
        Invoice.status != InvoiceStatus.VOID,
        or_(Invoice.balance_due > 0, Invoice.id.in_(later)),
    )
    q = q.filter(or_(live, Invoice.id.in_(voided_later)))

    invoices = q.order_by(Invoice.date, Invoice.id).all()
    # A fee is in the invoice's total from the day it posts: before that day
    # the invoice owed without it, as 1100 did.
    fees = late_fees(db, [inv.id for inv in invoices])
    out = []
    for inv in invoices:
        if inv.status == InvoiceStatus.VOID:
            # open until its void entry, as booked: net of retainage held
            bal = (
                _dec(inv.total)
                - _dec(inv.retainage_held)
                + _dec(inv.retainage_released)
            )
        else:
            bal = (
                _dec(inv.balance_due)
                + later_paid.get(inv.id, ZERO)
                + later_credited.get(inv.id, ZERO)
            )
        bal -= sum((a for d, a in fees.get(inv.id, []) if d > as_of), ZERO)
        if bal > 0:
            out.append((inv, bal))
    return out


def open_bills_as_of(
    db: Session, as_of: date, bill_ids: Optional[set[int]] = None
) -> list[tuple[Bill, Decimal]]:
    """Every bill dated on or before `as_of` with what was still owed on it
    then (its own currency), the open ones only. Drafts are not payables.
    `bill_ids` limits the answer to those bills."""
    later_paid = {
        bid: _dec(amt)
        for bid, amt in db.query(
            BillPaymentAllocation.bill_id, func.sum(BillPaymentAllocation.amount)
        )
        .join(BillPayment, BillPaymentAllocation.bill_payment_id == BillPayment.id)
        .filter(_not_void(BillPayment.is_voided), BillPayment.date > as_of)
        .group_by(BillPaymentAllocation.bill_id)
        .all()
    }
    later_credited = {
        bid: _dec(amt)
        for bid, amt in db.query(
            VendorCreditApplication.bill_id, func.sum(VendorCreditApplication.amount)
        )
        .join(
            VendorCredit,
            VendorCreditApplication.vendor_credit_id == VendorCredit.id,
        )
        .filter(
            VendorCredit.status != VendorCreditStatus.VOID, VendorCredit.date > as_of
        )
        .group_by(VendorCreditApplication.bill_id)
        .all()
    }
    later = set(later_paid) | set(later_credited)
    voided_later = _void_after(db, "bill_void", Bill, as_of)

    q = db.query(Bill).filter(Bill.date <= as_of)
    live = and_(
        Bill.status.notin_([BillStatus.VOID, BillStatus.DRAFT]),
        or_(Bill.balance_due > 0, Bill.id.in_(later)),
    )
    q = q.filter(or_(live, Bill.id.in_(voided_later)))
    if bill_ids is not None:
        q = q.filter(Bill.id.in_(bill_ids))

    out = []
    for bill in q.order_by(Bill.date, Bill.id):
        if bill.status == BillStatus.VOID:
            bal = _dec(bill.total) - _dec(bill.retainage_held)
        else:
            bal = (
                _dec(bill.balance_due)
                + later_paid.get(bill.id, ZERO)
                + later_credited.get(bill.id, ZERO)
            )
        if bal > 0:
            out.append((bill, bal))
    return out


def unapplied_credit_memos_as_of(
    db: Session, as_of: date
) -> list[tuple[CreditMemo, Decimal]]:
    """Credit memos dated on or before `as_of` with the part not yet applied
    to an invoice then (an application to an invoice dated after `as_of` was
    not made yet)."""
    later = {
        cid: _dec(amt)
        for cid, amt in db.query(
            CreditApplication.credit_memo_id, func.sum(CreditApplication.amount)
        )
        .join(Invoice, CreditApplication.invoice_id == Invoice.id)
        .filter(Invoice.date > as_of)
        .group_by(CreditApplication.credit_memo_id)
        .all()
    }
    out = []
    for cm in db.query(CreditMemo).filter(
        CreditMemo.status != CreditMemoStatus.VOID, CreditMemo.date <= as_of
    ):
        left = _dec(cm.balance_remaining) + later.get(cm.id, ZERO)
        if left > 0:
            out.append((cm, left))
    return out


def unapplied_payments_as_of(db: Session, as_of: date) -> list[tuple[Payment, Decimal]]:
    """Payments dated on or before `as_of` with the part not applied to an
    invoice then, in the payment's currency."""
    applied = {
        pid: _dec(amt)
        for pid, amt in db.query(
            PaymentAllocation.payment_id, func.sum(PaymentAllocation.amount)
        )
        .join(Invoice, PaymentAllocation.invoice_id == Invoice.id)
        .filter(Invoice.date <= as_of)
        .group_by(PaymentAllocation.payment_id)
        .all()
    }
    out = []
    for p in db.query(Payment).filter(
        _not_void(Payment.is_voided), Payment.date <= as_of
    ):
        left = _q(_dec(p.amount) - applied.get(p.id, ZERO))
        if left > 0:
            out.append((p, left))
    return out


def unapplied_vendor_credits_as_of(
    db: Session, as_of: date
) -> list[tuple[VendorCredit, Decimal]]:
    """Vendor credits dated on or before `as_of` with the part not applied to
    a bill then."""
    later = {
        vid: _dec(amt)
        for vid, amt in db.query(
            VendorCreditApplication.vendor_credit_id,
            func.sum(VendorCreditApplication.amount),
        )
        .join(Bill, VendorCreditApplication.bill_id == Bill.id)
        .filter(Bill.date > as_of)
        .group_by(VendorCreditApplication.vendor_credit_id)
        .all()
    }
    out = []
    for vc in db.query(VendorCredit).filter(
        VendorCredit.status != VendorCreditStatus.VOID, VendorCredit.date <= as_of
    ):
        left = _dec(vc.balance_remaining) + later.get(vc.id, ZERO)
        if left > 0:
            out.append((vc, left))
    return out


def unapplied_bill_payments_as_of(
    db: Session, as_of: date
) -> list[tuple[BillPayment, Decimal]]:
    """Bill payments dated on or before `as_of` with the part not applied to
    a bill then."""
    applied = {
        pid: _dec(amt)
        for pid, amt in db.query(
            BillPaymentAllocation.bill_payment_id,
            func.sum(BillPaymentAllocation.amount),
        )
        .join(Bill, BillPaymentAllocation.bill_id == Bill.id)
        .filter(Bill.date <= as_of)
        .group_by(BillPaymentAllocation.bill_payment_id)
        .all()
    }
    out = []
    for p in db.query(BillPayment).filter(
        _not_void(BillPayment.is_voided), BillPayment.date <= as_of
    ):
        left = _q(_dec(p.amount) - applied.get(p.id, ZERO))
        if left > 0:
            out.append((p, left))
    return out
