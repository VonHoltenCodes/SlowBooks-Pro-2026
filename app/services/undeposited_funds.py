"""Undeposited Funds as a list of items waiting for the bank.

A payment received into Undeposited Funds (1200) debits it; Make Deposits
moves a batch of those to a bank account with one entry (DR bank, CR
1200), as QuickBooks does. The deposit used to forget which payments it
took. Nothing then stopped a deposited payment — even one whose deposit
had been reconciled with a bank statement — from being voided: the void
credited 1200 a second time and drove it negative while the deposit still
claimed the money (explore 2.17.3, W-H4). The Make Deposits list, rebuilt
by netting every credit against every debit newest-first, also showed a
voided payment as waiting and hid a live one.

Now:

- an ITEM is a debit on 1200 from a transaction that is not void and is
  not itself a void's reversal;
- a deposit made from the list stamps each item's line with its journal
  entry (``TransactionLine.deposit_transaction_id``); voiding the deposit
  clears the stamp, and the payments are waiting again;
- a deposit that names no items — made before deposits kept their list,
  imported from QuickBooks, or posted by hand — is netted against the
  OLDEST waiting items, which is how a person deposits.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.control_accounts import MissingControlAccount

# Reversals whose source_id is the id of the TRANSACTION they reverse (the
# house void_document() convention and the journal's own void). A payment's
# reversal points at the payment instead; Payment.is_voided says it. The
# authoritative set now lives in app/services/voids.py (#448); kept under the
# old name so this module's callers stand.
from app.services.voids import TRANSACTION_KEYED_VOIDS as _VOIDS_KEYED_BY_TRANSACTION

ZERO = Decimal("0")

# The postings of a purchase (#821): a bill (bills, Bill Quick Entry, a
# memorized bill's entered bills, To Bill, an IIF bill), an expense, a card
# charge (the route and an IIF CREDIT CARD block), a check from a QuickBooks
# Desktop check register report, a vendor credit, and their voids. Since
# 2.31.14 (#785) none of them may put a line on 1200 or 1300; one saved
# before keeps it. A debit such a posting left on 1200 is not customer money
# and is never a payment waiting for deposit. Listed by exclusion: QuickBooks
# Online ledger imports post customer money to 1200 as qbo_ledger and
# qbo_journal, and an allowlist of payment and manual would hide it.
PURCHASE_SOURCES = frozenset(
    {
        "bill",
        "bill_edit",
        "bill_void",
        "expense",
        "expense_void",
        "cc_charge",
        "cc_charge_void",
        "check",
        "vendor_credit",
        "vendor_credit_void",
    }
)


def is_purchase_posting(txn: Transaction) -> bool:
    """True when `txn` is the posting of a purchase (PURCHASE_SOURCES)."""
    return (txn.source_type or "") in PURCHASE_SOURCES


def uf_account_id(db: Session) -> Optional[int]:
    """Undeposited Funds, or None for a chart without one (then nothing
    was ever received into it and there is nothing to guard)."""
    from app.services.accounting import get_undeposited_funds_id

    try:
        return get_undeposited_funds_id(db)
    except MissingControlAccount:
        return None


def dead_transaction_ids(db: Session, txns: Iterable[Transaction]) -> set[int]:
    """The ids among ``txns`` that are void, or are a void's reversal."""
    txns = list(txns)
    ids = {t.id for t in txns}
    if not ids:
        return set()
    dead = {t.id for t in txns if (t.source_type or "").endswith("_void")}
    dead |= {
        tid
        for (tid,) in db.query(Payment.transaction_id).filter(
            Payment.is_voided.is_(True), Payment.transaction_id.isnot(None)
        )
        if tid in ids
    }
    dead |= {
        sid
        for (sid,) in db.query(Transaction.source_id).filter(
            Transaction.source_type.in_(_VOIDS_KEYED_BY_TRANSACTION),
            Transaction.source_id.isnot(None),
        )
        if sid in ids
    }
    return dead


def _uf_rows(db: Session, uf_id: int) -> list[tuple[TransactionLine, Transaction]]:
    return (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == uf_id)
        .order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .all()
    )


def waiting_items(
    db: Session, uf_id: Optional[int] = None
) -> list[tuple[TransactionLine, Transaction]]:
    """The 1200 debits still waiting for a deposit, oldest first."""
    return _waiting_and_pool(db, uf_id)[0]


def waiting_balance(db: Session, uf_id: Optional[int] = None) -> Decimal:
    """What is actually still waiting in Undeposited Funds.

    waiting_items() nets a lump deposit (one that named no items) only
    against WHOLE items, so after a partial lump deposit — $60 of a $100
    payment — the item still shows in full. The leftover of that deposit's
    credit is subtracted here, so a typed deposit can't take the $40 twice.
    """
    waiting, pool = _waiting_and_pool(db, uf_id)
    total = sum((Decimal(str(ln.debit)) for ln, _ in waiting), ZERO)
    return max(total - pool, ZERO)


def _waiting_and_pool(
    db: Session, uf_id: Optional[int] = None
) -> tuple[list[tuple[TransactionLine, Transaction]], Decimal]:
    """The waiting items, and what is left of the lump-deposit credits
    after netting them against whole items oldest first."""
    uf_id = uf_id or uf_account_id(db)
    if not uf_id:
        return [], ZERO
    rows = _uf_rows(db, uf_id)
    dead = dead_transaction_ids(db, (t for _, t in rows))
    # A deposit that named its items took exactly those; its credit is not
    # left over for the netting below.
    named = {
        ln.deposit_transaction_id
        for ln, _ in rows
        if ln.deposit_transaction_id and ln.deposit_transaction_id not in dead
    }
    items = [
        (ln, t)
        for ln, t in rows
        if ln.debit > 0
        and t.id not in dead
        and not (ln.deposit_transaction_id and ln.deposit_transaction_id not in dead)
    ]
    pool = sum(
        (
            Decimal(str(ln.credit))
            for ln, t in rows
            if ln.credit > 0 and t.id not in dead and t.id not in named
        ),
        ZERO,
    )
    waiting = []
    for ln, t in items:
        amount = Decimal(str(ln.debit))
        if pool >= amount:
            pool -= amount
            continue
        waiting.append((ln, t))
    return waiting, pool


def reconciled(txn: Transaction) -> bool:
    return any(ln.reconciliation_id for ln in txn.lines)


def describe_deposit(db: Session, txn: Transaction) -> str:
    """ "the deposit of 2026-09-26 to Checking (slip 1234, $812.20)"."""
    bank = next((ln for ln in txn.lines if ln.debit > 0), None)
    account = db.get(Account, bank.account_id) if bank else None
    amount = Decimal(str(bank.debit)) if bank else ZERO
    extra = f"${amount:,.2f}"
    if txn.reference:
        extra = f"slip {txn.reference}, {extra}"
    where = f" to {account.name}" if account else ""
    return f"the deposit of {txn.date.isoformat()}{where} ({extra})"


def _where_is(db: Session, payment: Payment, money_in=None):
    """Where a payment's money is: ("bank_reconciled", None) received
    straight into a bank account and on a reconciled statement;
    ("deposit", txn) in a deposit that named it; ("deposited", None) used up
    by a deposit that named no payments; (None, None) still waiting, or in
    a bank account not yet reconciled. `money_in` is the posting it came in
    by when that is not its own (a QuickBooks Online import posting)."""
    txn = (
        money_in
        or payment.transaction
        or (
            db.get(Transaction, payment.transaction_id)
            if payment.transaction_id
            else None
        )
    )
    if txn is None:
        return None, None
    if reconciled(txn):
        return "bank_reconciled", None
    uf_id = uf_account_id(db)
    lines = [
        ln for ln in txn.lines if uf_id and ln.account_id == uf_id and ln.debit > 0
    ]
    if not lines:
        return None, None
    deposits = {ln.deposit_transaction_id for ln in lines if ln.deposit_transaction_id}
    if deposits:
        deps = [db.get(Transaction, i) for i in sorted(deposits)]
        deps = [d for d in deps if d is not None]
        dead = dead_transaction_ids(db, deps)
        live = [d for d in deps if d.id not in dead]
        if live:
            return "deposit", live[0]
    waiting = {ln.id for ln, _ in waiting_items(db, uf_id)}
    if any(ln.id not in waiting for ln in lines):
        return "deposited", None
    return None, None


def deposit_holding(db: Session, payment: Payment) -> Optional[str]:
    """For the payment's page: "the deposit of … to Checking (…)", or
    "an earlier deposit", or None while it waits in Undeposited Funds."""
    kind, dep = _where_is(db, payment)
    if kind == "deposit":
        return describe_deposit(db, dep)
    if kind == "deposited":
        return "an earlier deposit"
    return None


def refuse_void_if_deposited(db: Session, payment: Payment, money_in=None) -> None:
    """A payment whose money has gone to the bank can't simply be voided:
    the reversal would take it out of Undeposited Funds a second time.
    Raise a 400 that says what to do instead; return when it may go."""
    kind, dep = _where_is(db, payment, money_in)
    if kind == "bank_reconciled":
        # Received straight into the bank, and that line is on a closed
        # bank statement.
        raise HTTPException(
            status_code=400,
            detail=(
                "This payment is on a bank statement that has been reconciled, "
                "so it can't be voided. If the check bounced, charge the "
                "customer again with a new invoice."
            ),
        )
    if kind == "deposit" and reconciled(dep):
        raise HTTPException(
            status_code=400,
            detail=(
                f"This payment is in {describe_deposit(db, dep)}, which has been "
                "reconciled with a bank statement, so it can't be voided. If "
                "the check bounced, charge the customer again with a new "
                "invoice."
            ),
        )
    if kind == "deposit":
        raise HTTPException(
            status_code=400,
            detail=(
                f"This payment is in {describe_deposit(db, dep)}. Void that "
                "deposit on the Make Deposits page first (its other payments "
                "go back on the list to deposit again), then void this payment."
            ),
        )
    if kind == "deposited":
        # A deposit made before deposits kept their list used it up.
        raise HTTPException(
            status_code=400,
            detail=(
                "This payment has already been deposited: it is no longer on "
                "the Make Deposits list. Void the deposit that took it first "
                "(Make Deposits, Recent deposits; or, for one brought in from "
                "QuickBooks Online, open it from the bank register), then void "
                "this payment."
            ),
        )


# The Deposit To account an edit already has (#741). A payment is edited by
# voiding it and posting its replacement through the same create, so the
# create alone can't tell a new payment from an edited one; the replace
# names the original's account here, and the replacement may keep it even
# when it was deactivated in the chart since.
_kept_deposit_account: ContextVar[Optional[int]] = ContextVar(
    "_kept_deposit_account", default=None
)


@contextmanager
def keeping_deposit_account(account_id: Optional[int]):
    """While open, `refuse_inactive_deposit_account` lets `account_id`
    through: the account of the record being edited."""
    token = _kept_deposit_account.set(account_id)
    try:
        yield
    finally:
        _kept_deposit_account.reset(token)


def refuse_inactive_deposit_account(db: Session, account_id: Optional[int]) -> None:
    """A new deposit, payment or sales receipt posted to an account
    deactivated in the chart is refused with a 422 naming it (#741): the
    pickers no longer offer one, and the API must not take one either. An
    edit keeps its own account (see `keeping_deposit_account`); a blank
    Deposit To (Undeposited Funds) is never refused, nor is Undeposited
    Funds itself, which is not a bank account."""
    if account_id is None or account_id == _kept_deposit_account.get():
        return
    acct = db.get(Account, account_id)
    if acct is None or acct.is_active is not False:
        return
    if acct.id == uf_account_id(db):
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"Deposit To: {acct.name} is inactive in the chart of accounts. "
            "Choose another account, or make it active again. Nothing was saved."
        ),
    )
