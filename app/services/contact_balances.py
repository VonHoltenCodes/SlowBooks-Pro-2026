"""What a customer owes, and what is owed to a vendor — summed from the
documents every time it is read.

``Customer.balance`` and ``Vendor.balance`` are stored columns that no
posting path ever wrote (only the QuickBooks Online importer did), so the
Customer Center, the customer page, the vendor list and the CSV export all
read $0.00 while the ledger said otherwise (explore 2.17.3: Acme owed
$10,825,419.34 and showed $0.00). A stored figure has to be kept in step
by every path that posts, and drifts the first time one forgets; the open
documents ARE the sub-ledger, so the balance is summed from them, in home
currency, the way account 1100 / 2000 carries it:

    customer = open invoices' balance due
               − credit memos not yet applied
               − payments (or parts of payments) not yet applied
    vendor   = open bills' balance due
               − vendor credits not yet applied
               − bill payments (or parts) not yet applied

A foreign-currency document counts at the rate it was booked at (amount ×
exchange_rate), as its journal entry did; an unapplied payment at the rate
it was received at, which is what its unallocated remainder relieved.

Every function answers for many contacts in a constant number of grouped
queries, so a list page never runs one query per row.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.bills import Bill, BillPayment, BillPaymentAllocation, BillStatus
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation
from app.models.vendor_credits import VendorCredit, VendorCreditStatus
from app.services.accounting import _q

ZERO = Decimal("0")

# Beyond this many ids an IN list is no cheaper than reading every row, and
# a very long one can exceed a backend's bound-parameter limit.
_MAX_IN = 500


def home_amount(amount, rate) -> Decimal:
    """``amount`` in home currency at a document's booked rate (NULL = 1)."""
    return _q(Decimal(str(amount or 0)) * Decimal(str(rate or 1)))


def _ids(ids: Optional[Iterable[int]]) -> Optional[list[int]]:
    if ids is None:
        return None
    ids = sorted({int(i) for i in ids})
    return ids if len(ids) <= _MAX_IN else None


@dataclass(frozen=True)
class UnappliedPayment:
    """A live payment with money not yet applied to any invoice."""

    payment_id: int
    customer_id: int
    date: date
    currency: Optional[str]
    exchange_rate: Decimal
    amount: Decimal  # the whole payment, document currency
    unapplied: Decimal  # the part not on any invoice, document currency

    @property
    def unapplied_home(self) -> Decimal:
        return home_amount(self.unapplied, self.exchange_rate)


def _not_voided(column):
    return or_(column.is_(False), column.is_(None))


def unapplied_payments(
    db: Session,
    customer_ids: Optional[Iterable[int]] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[UnappliedPayment]:
    """Every non-void customer payment dated in [start, end] whose amount is
    more than what is allocated to invoices — a prepayment, an overpayment,
    or a payment recorded without applying it. One query."""
    applied = (
        db.query(
            PaymentAllocation.payment_id.label("payment_id"),
            func.sum(PaymentAllocation.amount).label("applied"),
        )
        .group_by(PaymentAllocation.payment_id)
        .subquery()
    )
    q = (
        db.query(
            Payment.id,
            Payment.customer_id,
            Payment.date,
            Payment.currency,
            Payment.exchange_rate,
            Payment.amount,
            func.coalesce(applied.c.applied, 0),
        )
        .outerjoin(applied, applied.c.payment_id == Payment.id)
        .filter(_not_voided(Payment.is_voided))
    )
    ids = _ids(customer_ids)
    if ids is not None:
        q = q.filter(Payment.customer_id.in_(ids))
    if start is not None:
        q = q.filter(Payment.date >= start)
    if end is not None:
        q = q.filter(Payment.date <= end)
    out = []
    wanted = None if customer_ids is None else {int(i) for i in customer_ids}
    for pid, cid, pdate, currency, rate, amount, applied_amt in q.order_by(
        Payment.date, Payment.id
    ):
        if wanted is not None and cid not in wanted:
            continue
        left = _q(Decimal(str(amount or 0)) - Decimal(str(applied_amt or 0)))
        if left > 0:
            out.append(
                UnappliedPayment(
                    payment_id=pid,
                    customer_id=cid,
                    date=pdate,
                    currency=currency,
                    exchange_rate=Decimal(str(rate or 1)),
                    amount=_q(amount),
                    unapplied=left,
                )
            )
    return out


def customer_balances(
    db: Session, customer_ids: Optional[Iterable[int]] = None
) -> dict[int, Decimal]:
    """Home-currency balance per customer id; ids with no open documents
    are absent (read them as zero)."""
    ids = _ids(customer_ids)
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)

    inv = db.query(
        Invoice.customer_id, Invoice.exchange_rate, func.sum(Invoice.balance_due)
    ).filter(Invoice.status != InvoiceStatus.VOID, Invoice.balance_due > 0)
    if ids is not None:
        inv = inv.filter(Invoice.customer_id.in_(ids))
    for cid, rate, total in inv.group_by(Invoice.customer_id, Invoice.exchange_rate):
        out[cid] += home_amount(total, rate)

    cm = db.query(
        CreditMemo.customer_id, func.sum(CreditMemo.balance_remaining)
    ).filter(
        CreditMemo.status != CreditMemoStatus.VOID, CreditMemo.balance_remaining > 0
    )
    if ids is not None:
        cm = cm.filter(CreditMemo.customer_id.in_(ids))
    for cid, total in cm.group_by(CreditMemo.customer_id):
        out[cid] -= _q(total)

    for p in unapplied_payments(db, customer_ids=ids):
        out[p.customer_id] -= p.unapplied_home

    return dict(out)


def customer_balance(db: Session, customer_id: int) -> Decimal:
    return customer_balances(db, [customer_id]).get(customer_id, ZERO)


def vendor_balances(
    db: Session, vendor_ids: Optional[Iterable[int]] = None
) -> dict[int, Decimal]:
    """Home-currency amount owed per vendor id (absent = zero)."""
    ids = _ids(vendor_ids)
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)

    bills = db.query(
        Bill.vendor_id, Bill.exchange_rate, func.sum(Bill.balance_due)
    ).filter(Bill.status != BillStatus.VOID, Bill.balance_due > 0)
    if ids is not None:
        bills = bills.filter(Bill.vendor_id.in_(ids))
    for vid, rate, total in bills.group_by(Bill.vendor_id, Bill.exchange_rate):
        out[vid] += home_amount(total, rate)

    vc = db.query(
        VendorCredit.vendor_id, func.sum(VendorCredit.balance_remaining)
    ).filter(
        VendorCredit.status != VendorCreditStatus.VOID,
        VendorCredit.balance_remaining > 0,
    )
    if ids is not None:
        vc = vc.filter(VendorCredit.vendor_id.in_(ids))
    for vid, total in vc.group_by(VendorCredit.vendor_id):
        out[vid] -= _q(total)

    applied = (
        db.query(
            BillPaymentAllocation.bill_payment_id.label("bill_payment_id"),
            func.sum(BillPaymentAllocation.amount).label("applied"),
        )
        .group_by(BillPaymentAllocation.bill_payment_id)
        .subquery()
    )
    bp = (
        db.query(
            BillPayment.vendor_id,
            BillPayment.exchange_rate,
            BillPayment.amount,
            func.coalesce(applied.c.applied, 0),
        )
        .outerjoin(applied, applied.c.bill_payment_id == BillPayment.id)
        .filter(_not_voided(BillPayment.is_voided))
    )
    if ids is not None:
        bp = bp.filter(BillPayment.vendor_id.in_(ids))
    for vid, rate, amount, applied_amt in bp:
        left = _q(Decimal(str(amount or 0)) - Decimal(str(applied_amt or 0)))
        if left > 0:
            out[vid] -= home_amount(left, rate)

    return dict(out)


def vendor_balance(db: Session, vendor_id: int) -> Decimal:
    return vendor_balances(db, [vendor_id]).get(vendor_id, ZERO)
