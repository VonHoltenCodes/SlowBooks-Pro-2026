"""The bank register IS the general ledger account (issue #114).

One query shape — every journal line on one account with a natural-balance
running balance — serves the register, the reports drill-down and the
reconciliation candidates. Balances shown anywhere for a bank or card
account come from here, never from a stored number.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.bills import BillPayment
from app.models.contacts import Customer, Vendor
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine

DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS}

ZERO = Decimal("0")


def is_debit_normal(account: Account) -> bool:
    return account.account_type in DEBIT_NORMAL


def money_text(value) -> str:
    """$1,234.56 / -$1,234.56 — money as a sentence shows it."""
    amount = Decimal(str(value or 0))
    return f"{'-' if amount < 0 else ''}${abs(amount):,.2f}"


def date_text(value: date) -> str:
    """Sep 26, 2026 — a date as a sentence shows it."""
    return f"{value:%b} {value.day}, {value.year}"


def gl_balances(db: Session, account_ids) -> dict[int, Decimal]:
    """Natural-signed balance per account from the ledger lines, in one
    grouped query. Missing ids balance to zero."""
    ids = [int(i) for i in account_ids]
    if not ids:
        return {}
    rows = (
        db.query(
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id.in_(ids))
        .group_by(TransactionLine.account_id)
        .all()
    )
    kinds = {
        a.id: is_debit_normal(a)
        for a in db.query(Account).filter(Account.id.in_(ids)).all()
    }
    out = {i: ZERO for i in ids}
    for account_id, dr, cr in rows:
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        out[account_id] = (dr - cr) if kinds.get(account_id, True) else (cr - dr)
    return out


def gl_balance(db: Session, account_id: int) -> Decimal:
    return gl_balances(db, [account_id]).get(account_id, ZERO)


def balance_as_of(db: Session, account: Account, as_of: date) -> tuple[Decimal, int]:
    """The account's natural-signed balance from every line dated on or
    before `as_of`, and how many lines that is — "does the ledger already
    carry this account on that date, and with what?"."""
    dr, cr, n = (
        db.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
            func.count(TransactionLine.id),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == account.id)
        .filter(Transaction.date <= as_of)
        .one()
    )
    dr, cr = Decimal(str(dr)), Decimal(str(cr))
    balance = (dr - cr) if is_debit_normal(account) else (cr - dr)
    return balance, int(n or 0)


def voided_transaction_ids(db: Session, txn_ids) -> set[int]:
    """Ids among `txn_ids` that a `<type>_void` reversal points at. Voided-
    ness is derived from the ledger, never a flag (the expenses convention)."""
    ids = [int(i) for i in txn_ids]
    if not ids:
        return set()
    rows = (
        db.query(Transaction.source_id)
        .filter(Transaction.source_type.like("%\\_void", escape="\\"))
        .filter(Transaction.source_id.in_(ids))
        .all()
    )
    return {r[0] for r in rows}


_LINKS = {
    "invoice": "/#/invoices/{id}",
    "bill": "/#/bills/{id}",
    "payment": "/#/payments/{id}",
    "bill_payment": "/#/bill-payments/{id}",
    "vendor_credit": "/#/vendor-credits/{id}",
    "journal": "/#/journal/{id}",
    "manual_journal": "/#/journal/{id}",
    "manual": "/#/journal/{txn}",
    "expense": "/#/expenses/{txn}",
    "deposit": "/#/deposits/{txn}",
    "cc_charge": "/#/cc-charges/{txn}",
    "transfer": "/#/banking/transfers/{txn}",
    "bank_entry": "/#/journal/{txn}",
    "opening_balance": "/#/journal/{txn}",
}


def source_link(txn: Transaction) -> str | None:
    """Where the SPA can jump to for this posting. Documents link by their
    own id; postings that are their own document link to the journal view."""
    pattern = _LINKS.get(txn.source_type or "")
    if not pattern:
        return None
    if "{id}" in pattern:
        return pattern.format(id=txn.source_id) if txn.source_id else None
    return pattern.format(txn=txn.id)


def payees_for(db: Session, txns) -> dict[int, str]:
    """Best-known counterparty per transaction id, in batches: the vendor
    behind an expense or bill payment, the customer behind a payment, else
    the entry's own description."""
    by_type = defaultdict(list)
    for t in txns:
        by_type[t.source_type or ""].append(t)
    out = {}
    vendor_ids = {t.source_id for t in by_type.get("expense", []) if t.source_id}
    bp_ids = {t.source_id for t in by_type.get("bill_payment", []) if t.source_id}
    pay_ids = {t.source_id for t in by_type.get("payment", []) if t.source_id}
    vendors = (
        {v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids))}
        if vendor_ids
        else {}
    )
    if bp_ids:
        bps = db.query(BillPayment).filter(BillPayment.id.in_(bp_ids)).all()
        bp_vendor = {
            v.id: v.name
            for v in db.query(Vendor).filter(Vendor.id.in_({b.vendor_id for b in bps}))
        }
        bp_names = {b.id: bp_vendor.get(b.vendor_id, "") for b in bps}
    else:
        bp_names = {}
    if pay_ids:
        pays = db.query(Payment).filter(Payment.id.in_(pay_ids)).all()
        customers = {
            c.id: c.name
            for c in db.query(Customer).filter(
                Customer.id.in_({p.customer_id for p in pays})
            )
        }
        pay_names = {p.id: customers.get(p.customer_id, "") for p in pays}
    else:
        pay_names = {}
    for t in txns:
        name = ""
        if t.source_type == "expense":
            name = vendors.get(t.source_id, "") or (t.description or "").removeprefix(
                "Expense: "
            )
        elif t.source_type == "bill_payment":
            name = bp_names.get(t.source_id, "")
        elif t.source_type == "payment":
            name = pay_names.get(t.source_id, "")
        if not name:
            name = (t.description or "").removeprefix("CC Charge: ").strip()
        out[t.id] = name
    return out


def references_for(db: Session, txns) -> dict[int, str]:
    """The reference number to show per transaction id: the posting's own,
    else the check number its document carries.

    A bill payment keeps its check number on the payment, not on the
    journal entry, so check 1050 was missing from the register's REF #
    (exploratory 2.17.3, W-L6). Its void shows the same number."""
    out = {t.id: (t.reference or "") for t in txns}
    bp_ids = {
        t.source_id
        for t in txns
        if not out[t.id]
        and t.source_type in ("bill_payment", "bill_payment_void")
        and t.source_id
    }
    if bp_ids:
        checks = {
            bp.id: bp.check_number or ""
            for bp in db.query(BillPayment).filter(BillPayment.id.in_(bp_ids)).all()
        }
        for t in txns:
            if not out[t.id] and t.source_type in ("bill_payment", "bill_payment_void"):
                out[t.id] = checks.get(t.source_id, "")
    return out


def account_register(
    db: Session,
    account: Account,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Every ledger line on `account`, natural-balance running balance,
    period totals, and — when a start date is given — the balance carried
    in from before it. No dates = everything."""
    debit_normal = is_debit_normal(account)

    opening = ZERO
    if start:
        dr, cr = (
            db.query(
                func.coalesce(func.sum(TransactionLine.debit), 0),
                func.coalesce(func.sum(TransactionLine.credit), 0),
            )
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(TransactionLine.account_id == account.id)
            .filter(Transaction.date < start)
            .one()
        )
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        opening = (dr - cr) if debit_normal else (cr - dr)

    q = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == account.id)
    )
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    rows = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()

    txns = {txn.id: txn for _, txn in rows}
    voided = voided_transaction_ids(db, txns.keys())
    payees = payees_for(db, txns.values())
    refs = references_for(db, list(txns.values()))

    running = opening
    period_debit = ZERO
    period_credit = ZERO
    entries = []
    for tl, txn in rows:
        dr = tl.debit or ZERO
        cr = tl.credit or ZERO
        period_debit += dr
        period_credit += cr
        delta = (dr - cr) if debit_normal else (cr - dr)
        running += delta
        entries.append(
            {
                "line_id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "description": txn.description or tl.description or "",
                "payee": payees.get(txn.id, ""),
                "reference": refs.get(txn.id, ""),
                "debit": float(dr),
                "credit": float(cr),
                "amount": float(delta),
                "running_balance": float(running),
                "source_type": txn.source_type,
                "source_id": txn.source_id,
                "source_link": source_link(txn),
                "cleared": bool(tl.cleared),
                "reconciliation_id": tl.reconciliation_id,
                "voided": txn.id in voided
                or bool((txn.source_type or "").endswith("_void")),
            }
        )
    period_net = (
        (period_debit - period_credit)
        if debit_normal
        else (period_credit - period_debit)
    )
    return {
        "account": {
            "id": account.id,
            "number": account.account_number,
            "name": account.name,
            "type": account.account_type.value,
            "bank_kind": account.bank_kind,
            "natural_balance": "debit" if debit_normal else "credit",
        },
        "opening_balance": float(opening),
        "period_debit": float(period_debit),
        "period_credit": float(period_credit),
        "period_net": float(period_net),
        "entries": entries,
    }
