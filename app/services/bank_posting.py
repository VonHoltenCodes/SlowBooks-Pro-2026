"""Postings that start on the bank side: opening balances, register
entries, transfers, and the guards a void needs when it touches a line a
statement or a reconciliation has claimed (issue #114).

Sign contract, once: an amount > 0 DEBITS the bank/card account, < 0
CREDITS it — the same rule for an asset (bank) and a liability (card).
A −50 card charge credits the card (more owed); a +500 card payment
debits it. Display follows natural balance, so a card register shows the
amount owed as a positive number.
"""

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.banking import BankTransaction
from app.models.transactions import Transaction
from app.services.accounting import (
    _q,
    create_journal_entry,
    get_opening_balance_equity_id,
    reversing_lines,
)
from app.services.bank_register import (
    balance_as_of,
    date_text,
    is_debit_normal,
    money_text,
)
from app.services.closing_date import check_closing_date


def require_bank_account(db: Session, account_id: int) -> Account:
    acct = db.query(Account).filter(Account.id == account_id).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if not acct.bank_kind:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{acct.name} is not a bank or credit-card account. Set its kind "
                "under Chart of Accounts first."
            ),
        )
    return acct


def post_opening_balance(
    db: Session,
    account: Account,
    txn_date: date,
    amount: Decimal,
    description: str | None = None,
) -> Transaction:
    """The balance a bank or card account carries in, against 3900 Opening
    Balance Equity. `amount` is what the statement says: cash in the bank,
    or the amount owed on the card. (For an account the ledger already
    carries, `post_statement_balance` decides what, if anything, posts.)"""
    amount = _q(amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Opening balance is zero")
    obe = get_opening_balance_equity_id(db)
    bank_debit = amount if is_debit_normal(account) else -amount
    if bank_debit > 0:
        lines = [
            {"account_id": account.id, "debit": bank_debit, "credit": Decimal("0")},
            {"account_id": obe, "debit": Decimal("0"), "credit": bank_debit},
        ]
    else:
        lines = [
            {"account_id": obe, "debit": -bank_debit, "credit": Decimal("0")},
            {"account_id": account.id, "debit": Decimal("0"), "credit": -bank_debit},
        ]
    return create_journal_entry(
        db,
        txn_date,
        description or f"Opening balance: {account.name}",
        lines,
        source_type="opening_balance",
        reference="",
    )


def post_statement_balance(
    db: Session,
    account: Account,
    as_of: date,
    statement: Decimal,
    post_difference: bool = False,
) -> Transaction | None:
    """A new feed's "what the statement says on `as_of`".

    An account with nothing in the ledger on or before that date takes the
    whole figure as its opening balance. An account the ledger already
    carries does not: posting the statement again counted Savings twice
    ($300 from a transfer, then $300 more against 3900 — exploratory 2.17.3,
    W-H6). There the statement is compared with the ledger: equal posts
    nothing; different is refused, naming the ledger's balance, until the
    caller confirms — and then only the difference posts."""
    statement = _q(statement)
    ledger, lines = balance_as_of(db, account, as_of)
    if not lines:
        return post_opening_balance(db, account, as_of, statement)
    difference = _q(statement - ledger)
    if difference == 0:
        return None
    if not post_difference:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ledger_has_balance",
                "message": (
                    f"{account.name} already has a balance of {money_text(ledger)} "
                    f"in the books on {date_text(as_of)}, so the statement's "
                    f"{money_text(statement)} is not posted again. Post only the "
                    f"{money_text(difference)} difference as an opening balance "
                    f"adjustment, or enter {money_text(ledger)} to post nothing."
                ),
                "ledger_balance": str(_q(ledger)),
                "difference": str(difference),
            },
        )
    return post_opening_balance(
        db,
        account,
        as_of,
        difference,
        description=f"Opening balance adjustment: {account.name}",
    )


def post_transfer(
    db: Session,
    txn_date: date,
    from_account: Account,
    to_account: Account,
    amount: Decimal,
    memo: str | None = None,
    reference: str | None = None,
) -> Transaction:
    """Money between two bank/card accounts: DR to, CR from. Paying a card
    is a transfer from the bank to the card."""
    amount = _q(amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Transfer amount must be positive")
    if from_account.id == to_account.id:
        raise HTTPException(
            status_code=400, detail="Transfer needs two different accounts"
        )
    for a in (from_account, to_account):
        if not a.bank_kind:
            raise HTTPException(
                status_code=400,
                detail=f"{a.name} is not a bank or credit-card account",
            )
    desc = f"Transfer: {from_account.name} → {to_account.name}"
    if memo:
        desc = f"{desc} — {memo}"
    return create_journal_entry(
        db,
        txn_date,
        desc,
        [
            {"account_id": to_account.id, "debit": amount, "credit": Decimal("0")},
            {"account_id": from_account.id, "debit": Decimal("0"), "credit": amount},
        ],
        source_type="transfer",
        reference=reference or "",
    )


def post_bank_entry(
    db: Session,
    account: Account,
    txn_date: date,
    amount: Decimal,
    category: Account,
    payee: str | None = None,
    memo: str | None = None,
    reference: str | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
    source_id: int | None = None,
) -> Transaction:
    """A register entry. amount < 0: DR category / CR account (money out,
    or a card charge). amount > 0: DR account / CR category (money in, or a
    card payment). A category that is itself a bank/card account is a
    transfer."""
    amount = _q(amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Amount must not be zero")
    if category.id == account.id:
        raise HTTPException(
            status_code=400, detail="Category must differ from the account itself"
        )
    if category.bank_kind:
        if amount < 0:
            return post_transfer(
                db, txn_date, account, category, -amount, memo, reference
            )
        return post_transfer(db, txn_date, category, account, amount, memo, reference)
    line_desc = memo or payee or None
    if amount < 0:
        lines = [
            {
                "account_id": category.id,
                "debit": -amount,
                "credit": Decimal("0"),
                "description": line_desc,
            },
            {
                "account_id": account.id,
                "debit": Decimal("0"),
                "credit": -amount,
                "description": line_desc,
            },
        ]
    else:
        lines = [
            {
                "account_id": account.id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": line_desc,
            },
            {
                "account_id": category.id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": line_desc,
            },
        ]
    return create_journal_entry(
        db,
        txn_date,
        payee or memo or "Bank entry",
        lines,
        source_type="bank_entry",
        source_id=source_id,
        reference=reference or "",
        class_id=class_id,
        job_id=job_id,
    )


def assert_not_reconciled(txn: Transaction) -> None:
    if any(ln.reconciliation_id for ln in txn.lines):
        raise HTTPException(
            status_code=400,
            detail="This entry is in a completed reconciliation and cannot be voided",
        )


def release_statement_links(db: Session, txn: Transaction) -> int:
    """A voided posting gives its statement lines back to the review queue
    and un-clears its bank lines."""
    line_ids = [ln.id for ln in txn.lines]
    n = 0
    if line_ids:
        for bt in (
            db.query(BankTransaction)
            .filter(BankTransaction.transaction_line_id.in_(line_ids))
            .all()
        ):
            bt.transaction_id = None
            bt.transaction_line_id = None
            bt.match_status = "unmatched"
            n += 1
    for ln in txn.lines:
        ln.cleared = False
    return n


def void_document(db: Session, txn: Transaction, void_source_type: str) -> Transaction:
    """The house void: keep the original, post its mirror image."""
    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)
    reversal = create_journal_entry(
        db,
        txn.date,
        f"VOID {txn.description or ''}".strip(),
        reversing_lines(txn.lines),
        source_type=void_source_type,
        source_id=txn.id,
        reference=txn.reference or "",
        class_id=txn.class_id,
        job_id=txn.job_id,
    )
    release_statement_links(db, txn)
    return reversal
