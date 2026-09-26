"""Undeposited Funds as a list of items waiting for the bank.

A payment received into Undeposited Funds (1200) debits it; Make Deposits
moves a batch of those to a bank account with one entry (DR bank, CR
1200), as QuickBooks does. Until 2.17.4 the deposit forgot which payments
it took. Nothing then stopped a deposited payment — even one whose deposit
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
- a deposit that names no items — made before 2.17.4, imported from
  QuickBooks, or posted by hand — is netted against the OLDEST waiting
  items, which is how a person deposits.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.payments import Payment
from app.models.transactions import Transaction, TransactionLine
from app.services.control_accounts import MissingControlAccount

ZERO = Decimal("0")

# A reversal whose source_id is the id of the TRANSACTION it reverses (the
# house void_document() convention and the journal's own void). A payment's
# reversal points at the payment instead; Payment.is_voided says it.
_VOIDS_KEYED_BY_TRANSACTION = ("manual_void", "bank_entry_void", "deposit_void")


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
    uf_id = uf_id or uf_account_id(db)
    if not uf_id:
        return []
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
    return waiting


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


def refuse_void_if_deposited(db: Session, payment: Payment) -> None:
    """A payment whose money has gone to the bank can't simply be voided:
    the reversal would take it out of Undeposited Funds a second time.
    Raise a 400 that says what to do instead; return when it may go."""
    txn = payment.transaction or (
        db.get(Transaction, payment.transaction_id) if payment.transaction_id else None
    )
    if txn is None:
        return
    if reconciled(txn):
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
    uf_id = uf_account_id(db)
    lines = [
        ln for ln in txn.lines if uf_id and ln.account_id == uf_id and ln.debit > 0
    ]
    if not lines:
        return
    deposits = {ln.deposit_transaction_id for ln in lines if ln.deposit_transaction_id}
    if deposits:
        deps = [db.get(Transaction, i) for i in sorted(deposits)]
        deps = [d for d in deps if d is not None]
        live = [d for d in deps if d.id not in dead_transaction_ids(db, deps)]
        for dep in live:
            if reconciled(dep):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This payment is in {describe_deposit(db, dep)}, which "
                        "has been reconciled with a bank statement, so it can't "
                        "be voided. If the check bounced, charge the customer "
                        "again with a new invoice."
                    ),
                )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This payment is in {describe_deposit(db, dep)}. Void that "
                    "deposit on the Make Deposits page first (its other payments "
                    "go back on the list to deposit again), then void this "
                    "payment."
                ),
            )
    # A deposit made before deposits kept their list: the payment is
    # deposited when the netting has used it up.
    waiting = {ln.id for ln, _ in waiting_items(db, uf_id)}
    if any(ln.id not in waiting for ln in lines):
        raise HTTPException(
            status_code=400,
            detail=(
                "This payment has already been deposited: it is no longer on "
                "the Make Deposits list. Void the deposit that took it (Make "
                "Deposits, Recent deposits) first, then void this payment."
            ),
        )
