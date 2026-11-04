# ============================================================================
# Unified Search — expands global search to all entity types
# Feature 4: server-side ILIKE across customers, vendors, items, classes,
# accounts, invoices, estimates, payments — 5 results per category
#
# A query that reads as an amount ("612.30", "$1,234.50", "612") also
# finds the documents for that amount, to the cent: invoices and bills by
# total or balance due, sales receipts, credit memos and estimates by
# total, payments by amount. Searching 612.30 found nothing (2.17.3
# exploratory test, W-L8).
#
# Transactions (#695): QuickBooks Desktop's Find reaches every posting by
# its memo, reference number or amount. A check by its number, a journal
# entry or register entry by its memo, an expense by its reference, a bill
# payment by its check number are found here, each opening its document as
# the report drill-downs do (a bank entry, its register).
# ============================================================================

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.bills import Bill, BillPayment, BillStatus
from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditMemo
from app.models.items import Item
from app.models.invoices import Invoice, InvoiceStatus
from app.models.estimates import Estimate
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.bank_register import (
    payees_for,
    references_for,
    source_link,
    voided_transaction_ids,
)

router = APIRouter(prefix="/api/search", tags=["search"])

LIMIT_PER = 5

_AMOUNT_RE = re.compile(r"\$?\s*(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?")
_HALF_CENT = Decimal("0.005")


def _amount(q: str):
    """The query as an amount to the cent, or None when it is not one."""
    text = q.strip()
    if not _AMOUNT_RE.fullmatch(text):
        return None
    return Decimal(text.replace("$", "").replace(",", "").strip()).quantize(
        Decimal("0.01")
    )


def _is_amount(column, amount):
    """column equals the amount to the cent. A half-cent window rather than
    ==, so a value SQLite keeps as a float still matches exactly the cents
    typed — and never a neighbouring cent."""
    if amount is None:
        return false()
    return column.between(amount - _HALF_CENT, amount + _HALF_CENT)


@router.get("")
def unified_search(q: str = Query(min_length=2), db: Session = Depends(get_db)):
    pattern = f"%{q}%"
    amount = _amount(q)
    results = {}

    # Customers
    customers = (
        db.query(Customer)
        .filter(
            Customer.is_active,
            (
                Customer.name.ilike(pattern)
                | Customer.company.ilike(pattern)
                | Customer.email.ilike(pattern)
            ),
        )
        .limit(LIMIT_PER)
        .all()
    )
    if customers:
        results["customers"] = [
            {"id": c.id, "name": c.name, "company": c.company, "email": c.email}
            for c in customers
        ]

    # Vendors
    vendors = (
        db.query(Vendor)
        .filter(
            Vendor.is_active,
            (Vendor.name.ilike(pattern) | Vendor.company.ilike(pattern)),
        )
        .limit(LIMIT_PER)
        .all()
    )
    if vendors:
        results["vendors"] = [
            {"id": v.id, "name": v.name, "company": v.company} for v in vendors
        ]

    # Items
    items = (
        db.query(Item)
        .filter(
            Item.is_active,
            (Item.name.ilike(pattern) | Item.description.ilike(pattern)),
        )
        .limit(LIMIT_PER)
        .all()
    )
    if items:
        results["items"] = [
            {"id": i.id, "name": i.name, "item_type": i.item_type.value} for i in items
        ]

    # Classes (#234): a hit opens the class's own page. Archived ones are
    # found too — they still open, with their history.
    classes = (
        db.query(TxnClass)
        .filter(TxnClass.name.ilike(pattern))
        .order_by(TxnClass.is_archived, TxnClass.name)
        .limit(LIMIT_PER)
        .all()
    )
    if classes:
        results["classes"] = [
            {"id": c.id, "name": c.name, "is_archived": bool(c.is_archived)}
            for c in classes
        ]

    # Accounts (#240): by number or name; a hit opens the account's register
    # (the bank register for a bank or card account, the drill-down for the
    # rest). Inactive accounts are found too: their history still opens.
    accounts = (
        db.query(Account)
        .filter(Account.account_number.ilike(pattern) | Account.name.ilike(pattern))
        .order_by(Account.is_active.desc(), Account.account_number)
        .limit(LIMIT_PER)
        .all()
    )
    if accounts:
        results["accounts"] = [
            {
                "id": a.id,
                "account_number": a.account_number,
                "name": a.name,
                "account_type": a.account_type.value,
                "bank_kind": a.bank_kind,
                "is_active": bool(a.is_active),
            }
            for a in accounts
        ]

    # Invoices (a sales receipt is listed under its own heading below)
    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.is_sales_receipt.is_(False),
            Invoice.invoice_number.ilike(pattern)
            | _is_amount(Invoice.total, amount)
            | (
                (Invoice.status != InvoiceStatus.VOID)
                & _is_amount(Invoice.balance_due, amount)
            ),
        )
        .order_by(Invoice.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if invoices:
        results["invoices"] = [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "customer_name": inv.customer.name if inv.customer else "",
                "total": float(inv.total),
                "balance_due": float(inv.balance_due or 0),
                "status": inv.status.value,
            }
            for inv in invoices
        ]

    # Sales receipts
    receipts = (
        db.query(Invoice)
        .filter(
            Invoice.is_sales_receipt.is_(True),
            Invoice.invoice_number.ilike(pattern) | _is_amount(Invoice.total, amount),
        )
        .order_by(Invoice.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if receipts:
        results["sales_receipts"] = [
            {
                "id": r.id,
                "invoice_number": r.invoice_number,
                "customer_name": r.customer.name if r.customer else "",
                "total": float(r.total),
                "status": r.status.value,
            }
            for r in receipts
        ]

    # Bills
    bills = (
        db.query(Bill)
        .filter(
            Bill.bill_number.ilike(pattern)
            | _is_amount(Bill.total, amount)
            | ((Bill.status != BillStatus.VOID) & _is_amount(Bill.balance_due, amount))
        )
        .order_by(Bill.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if bills:
        results["bills"] = [
            {
                "id": b.id,
                "bill_number": b.bill_number,
                "vendor_name": b.vendor.name if b.vendor else "",
                "total": float(b.total),
                "status": b.status.value,
            }
            for b in bills
        ]

    # Credit memos
    memos = (
        db.query(CreditMemo)
        .filter(
            CreditMemo.memo_number.ilike(pattern) | _is_amount(CreditMemo.total, amount)
        )
        .order_by(CreditMemo.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if memos:
        results["credit_memos"] = [
            {
                "id": m.id,
                "memo_number": m.memo_number,
                "customer_name": m.customer.name if m.customer else "",
                "total": float(m.total),
                "status": m.status.value,
            }
            for m in memos
        ]

    # Estimates
    estimates = (
        db.query(Estimate)
        .filter(
            Estimate.estimate_number.ilike(pattern) | _is_amount(Estimate.total, amount)
        )
        .order_by(Estimate.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if estimates:
        results["estimates"] = [
            {
                "id": e.id,
                "estimate_number": e.estimate_number,
                "customer_name": e.customer.name if e.customer else "",
                "total": float(e.total),
                "status": e.status.value,
            }
            for e in estimates
        ]

    # Payments (search by reference/check number, or amount)
    payments = (
        db.query(Payment)
        .filter(
            (
                Payment.reference.ilike(pattern)
                | Payment.check_number.ilike(pattern)
                | _is_amount(Payment.amount, amount)
            )
        )
        .order_by(Payment.date.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if payments:
        results["payments"] = [
            {
                "id": p.id,
                "amount": float(p.amount),
                "date": p.date.isoformat(),
                "customer_name": p.customer.name if p.customer else "",
                "method": p.method,
            }
            for p in payments
        ]

    txns = _transactions(db, pattern, amount)
    if txns:
        results["transactions"] = txns

    return results


# Postings a section above already lists by number and amount: an invoice
# (a sales receipt's too), a bill, a credit memo, a customer payment.
_OWN_SECTION = ("invoice", "bill", "credit_memo", "payment")
# A void's reversal, an edit's or an applied payment's adjustment: not a
# transaction of its own (the voided one is listed, and says so).
_SIDE_POSTINGS = ("_void", "_edit", "_apply")


def _transactions(db: Session, pattern: str, amount):
    """Posted transactions whose memo, description, reference or check
    number holds the text, or whose total is the amount to the cent; newest
    first. Voided ones are found too, marked."""
    total = (
        select(func.coalesce(func.sum(TransactionLine.debit), 0))
        .where(TransactionLine.transaction_id == Transaction.id)
        .scalar_subquery()
    )
    line_memo = (
        select(TransactionLine.id)
        .where(
            TransactionLine.transaction_id == Transaction.id,
            TransactionLine.description.ilike(pattern),
        )
        .exists()
    )
    bp_check = select(BillPayment.id).where(BillPayment.check_number.ilike(pattern))
    kind = func.coalesce(Transaction.source_type, "")
    query = db.query(Transaction, total).filter(
        kind.notin_(_OWN_SECTION),
        *[~kind.endswith(end, autoescape=True) for end in _SIDE_POSTINGS],
        Transaction.description.ilike(pattern)
        | Transaction.memo.ilike(pattern)
        | Transaction.reference.ilike(pattern)
        | line_memo
        | ((kind == "bill_payment") & Transaction.source_id.in_(bp_check))
        | _is_amount(total, amount),
    )
    rows = (
        query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(LIMIT_PER)
        .all()
    )
    if not rows:
        return []
    txns = [t for t, _ in rows]
    ids = [t.id for t in txns]
    voided = voided_transaction_ids(db, ids)
    payees = payees_for(db, txns)
    refs = references_for(db, txns)
    # a bank entry opens its register: the bank or card account it is on
    registers = dict(
        db.query(TransactionLine.transaction_id, TransactionLine.account_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(
            TransactionLine.transaction_id.in_(
                [t.id for t in txns if t.source_type == "bank_entry"] or [0]
            ),
            Account.bank_kind.isnot(None),
        )
        .all()
    )
    return [
        {
            "id": t.id,
            "date": t.date.isoformat(),
            "source_type": t.source_type or "journal",
            "source_kind": t.source_kind,
            "reference": refs.get(t.id, ""),
            "description": t.description or "",
            "memo": t.memo or "",
            "payee": payees.get(t.id, ""),
            "total": float(tot or 0),
            "voided": t.id in voided,
            "link": source_link(t) or f"/#/journal/{t.id}",
            "register_account_id": registers.get(t.id),
        }
        for t, tot in rows
    ]
