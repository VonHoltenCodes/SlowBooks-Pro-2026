"""Make Deposits' lines for money that was never invoiced (#792).

A check for a reimbursement, a vendor's refund or a rebate goes on the same
slip as the day's customer checks. Each line names the account the money
goes to — income, an expense being reimbursed, an asset or a liability —
and who it came from, its check number, method, memo, class and job.

The deposit's own journal entry carries the money: DR bank for the whole
deposit (the payments taken plus these lines), CR Undeposited Funds for the
payments, and CR each line's account with the line's customer, class and
job. The ``deposit_other_lines`` rows keep the line as the slip prints it.
On the cash basis an income line is cash received on the deposit's date: a
deposit's lines count on both bases (cash_basis.default_basis_for), and the
money never passed through an invoice.

Refused, in a sentence, before anything posts: a bank or card account (that
is a transfer), Undeposited Funds (a payment waiting there is ticked, not
typed), Accounts Receivable (a customer's invoice is paid through Receive
Payment) and Accounts Payable (a vendor's refund goes to what the bill was
for); an account, customer or vendor made inactive (unless the deposit
being edited already has it); a line of zero or less.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor
from app.models.deposit_lines import DepositOtherLine
from app.models.jobs import Job
from app.services.accounting import _q
from app.services.control_accounts import MissingControlAccount
from app.services.dimensions import require_dimensions

ZERO = Decimal("0")


def _control_id(db: Session, getter) -> Optional[int]:
    try:
        return getter(db)
    except MissingControlAccount:
        return None


def _refusal(n: int, text: str) -> HTTPException:
    return HTTPException(
        status_code=400, detail=f"Other deposits, line {n}: {text} Nothing was saved."
    )


def _account_problem(acct: Account, uf_id, ar_id, ap_id) -> Optional[str]:
    """Why `acct` takes no deposit line, or None when it may."""
    detail = str(acct.detail_type or "")
    if acct.bank_kind == "bank":
        return (
            f"{acct.name} is a bank account. Money moved between bank "
            "accounts is a transfer (Banking, Transfer Funds), not a deposit line."
        )
    if acct.bank_kind == "credit_card":
        return (
            f"{acct.name} is a credit card account. A payment to the card is "
            "a transfer (Banking, Transfer Funds), not a deposit line."
        )
    if uf_id and acct.id == uf_id:
        return (
            f"{acct.name} is where payments wait for the bank; tick a waiting "
            "payment in the list above instead of typing it as a line."
        )
    if acct.id == ar_id or detail == "accounts_receivable":
        return (
            f"{acct.name} is Accounts Receivable. A customer paying an invoice "
            "is recorded with Receive Payment, and the payment is then ticked "
            "in the list above."
        )
    if acct.id == ap_id or detail == "accounts_payable":
        return (
            f"{acct.name} is Accounts Payable. A vendor's refund goes to the "
            "expense or asset account the bill was for."
        )
    return None


def validate(
    db: Session,
    lines: Iterable,
    uf_id: Optional[int],
    own: Optional[list[DepositOtherLine]] = None,
) -> list[dict]:
    """The other lines a request sends, checked and resolved, as the dicts
    `store` and `posting_lines` take. `own` are the lines the deposit being
    edited has: an account, customer or vendor one of them already carries
    may since have been made inactive and still be kept."""
    from app.services.accounting import get_ap_account_id, get_ar_account_id

    ar_id = _control_id(db, get_ar_account_id)
    ap_id = _control_id(db, get_ap_account_id)
    own = own or []
    own_accounts = {o.account_id for o in own}
    own_customers = {o.customer_id for o in own if o.customer_id}
    own_vendors = {o.vendor_id for o in own if o.vendor_id}
    out = []
    for n, line in enumerate(lines, start=1):
        amount = _q(line.amount)
        if amount <= 0:
            raise _refusal(
                n,
                "enter the amount that came in (more than $0.00), or remove the line.",
            )
        acct = db.get(Account, line.account_id)
        if acct is None:
            raise _refusal(n, "the account it names is not in the chart of accounts.")
        problem = _account_problem(acct, uf_id, ar_id, ap_id)
        if problem:
            raise _refusal(n, problem)
        if acct.is_active is False and acct.id not in own_accounts:
            raise _refusal(
                n,
                f"{acct.name} is inactive in the chart of accounts. Choose "
                "another account, or make it active again.",
            )
        if line.customer_id and line.vendor_id:
            raise _refusal(
                n, "it names both a customer and a vendor; choose the one it came from."
            )
        name = (line.received_from or "").strip()
        if line.customer_id:
            customer = db.get(Customer, line.customer_id)
            if customer is None:
                raise _refusal(n, "the customer it names is not on the customer list.")
            if customer.is_active is False and customer.id not in own_customers:
                raise _refusal(n, f"{customer.name} is inactive; choose another name.")
            name = customer.name
        if line.vendor_id:
            vendor = db.get(Vendor, line.vendor_id)
            if vendor is None:
                raise _refusal(n, "the vendor it names is not on the vendor list.")
            if vendor.is_active is False and vendor.id not in own_vendors:
                raise _refusal(n, f"{vendor.name} is inactive; choose another name.")
            name = vendor.name
        # a class must exist and be live, a job too, and a job named with a
        # customer must be that customer's (the register's rule, #606)
        require_dimensions(db, line.class_id, line.job_id, line.customer_id)
        out.append(
            {
                "account_id": acct.id,
                "account_name": acct.name,
                "amount": amount,
                "customer_id": line.customer_id or None,
                "vendor_id": line.vendor_id or None,
                # the typed name only when no customer or vendor is named
                "received_from": (
                    None
                    if (line.customer_id or line.vendor_id)
                    else (line.received_from or "").strip() or None
                ),
                "name": name,
                "memo": (line.memo or "").strip() or None,
                "check_number": (line.check_number or "").strip() or None,
                "method": (line.method or "").strip() or None,
                "class_id": line.class_id or None,
                "job_id": line.job_id or None,
            }
        )
    return out


def total(lines: list[dict]) -> Decimal:
    return _q(sum((ln["amount"] for ln in lines), ZERO))


def posting_lines(lines: list[dict], label: str) -> list[dict]:
    """One credit per line on its account, with the line's customer, class
    and job; its memo, else who it came from, as the ledger line's memo."""
    return [
        {
            "account_id": ln["account_id"],
            "debit": ZERO,
            "credit": ln["amount"],
            "description": ln["memo"]
            or (f"{label}: {ln['name']}" if ln["name"] else label),
            "class_id": ln["class_id"],
            "job_id": ln["job_id"],
            "customer_id": ln["customer_id"],
        }
        for ln in lines
    ]


def stored(db: Session, deposit_id: int) -> list[DepositOtherLine]:
    return (
        db.query(DepositOtherLine)
        .filter(DepositOtherLine.deposit_transaction_id == deposit_id)
        .order_by(DepositOtherLine.position, DepositOtherLine.id)
        .all()
    )


def as_dicts(db: Session, rows: list[DepositOtherLine]) -> list[dict]:
    """Stored lines in the shape `validate` returns, for an edit that
    keeps them."""
    out = []
    for r in rows:
        out.append(
            {
                "account_id": r.account_id,
                "account_name": "",
                "amount": _q(r.amount),
                "customer_id": r.customer_id,
                "vendor_id": r.vendor_id,
                "received_from": r.received_from,
                "name": _name_of(db, r),
                "memo": r.memo,
                "check_number": r.check_number,
                "method": r.method,
                "class_id": r.class_id,
                "job_id": r.job_id,
            }
        )
    return out


def store(db: Session, deposit_id: int, lines: list[dict]) -> None:
    """Replace the deposit's other lines with `lines`, in order."""
    for old in stored(db, deposit_id):
        db.delete(old)
    db.flush()
    for i, ln in enumerate(lines):
        db.add(
            DepositOtherLine(
                deposit_transaction_id=deposit_id,
                position=i,
                account_id=ln["account_id"],
                amount=ln["amount"],
                customer_id=ln["customer_id"],
                vendor_id=ln["vendor_id"],
                received_from=ln["received_from"],
                memo=ln["memo"],
                check_number=ln["check_number"],
                method=ln["method"],
                class_id=ln["class_id"],
                job_id=ln["job_id"],
            )
        )
    db.flush()


def _name_of(db: Session, row: DepositOtherLine) -> str:
    if row.customer_id:
        c = db.get(Customer, row.customer_id)
        if c is not None:
            return c.name
    if row.vendor_id:
        v = db.get(Vendor, row.vendor_id)
        if v is not None:
            return v.name
    return row.received_from or ""


def describe(db: Session, rows: list[DepositOtherLine]) -> list[dict]:
    """The lines as the deposit's page and slip show them."""
    out = []
    for r in rows:
        acct = db.get(Account, r.account_id)
        cls = db.get(TxnClass, r.class_id) if r.class_id else None
        job = db.get(Job, r.job_id) if r.job_id else None
        out.append(
            {
                "id": r.id,
                "account_id": r.account_id,
                "account_name": acct.name if acct else "",
                "account_number": (acct.account_number or "") if acct else "",
                "customer_id": r.customer_id,
                "vendor_id": r.vendor_id,
                "received_from": _name_of(db, r),
                "memo": r.memo or "",
                "check_number": r.check_number or "",
                "method": r.method or "",
                "class_id": r.class_id,
                "class_name": cls.name if cls else "",
                "job_id": r.job_id,
                "job_name": job.full_name if job else "",
                "amount": float(_q(r.amount)),
            }
        )
    return out


def counts(db: Session, deposit_ids: list[int]) -> dict[int, int]:
    """How many other lines each deposit carries, one query for a page."""
    from sqlalchemy import func

    if not deposit_ids:
        return {}
    return dict(
        db.query(
            DepositOtherLine.deposit_transaction_id, func.count(DepositOtherLine.id)
        )
        .filter(DepositOtherLine.deposit_transaction_id.in_(deposit_ids))
        .group_by(DepositOtherLine.deposit_transaction_id)
        .all()
    )
