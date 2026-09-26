"""Statement lines (bank feeds, OFX/CSV imports) are a review queue: each
one is matched to a posting the ledger already has, or added as a new
posting, or excluded (issue #114). Nothing posts silently.

Sign contract (see bank_posting): a statement amount > 0 is a DEBIT to
the bank/card ledger account, < 0 a CREDIT, whatever the account type.
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.banking import BankAccount, BankTransaction
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q
from app.services.bank_posting import post_bank_entry
from app.services.bank_register import payees_for, source_link, voided_transaction_ids
from app.services.closing_date import check_closing_date

AUTO_WINDOW_DAYS = 5
CANDIDATE_WINDOW_DAYS = 30


def _feed_account(db: Session, bt: BankTransaction) -> Account:
    ba = bt.bank_account or db.query(BankAccount).get(bt.bank_account_id)
    if not ba or not ba.account_id:
        raise HTTPException(
            status_code=400, detail="Link this feed to a ledger account first"
        )
    acct = db.query(Account).filter(Account.id == ba.account_id).first()
    if not acct or not acct.bank_kind:
        raise HTTPException(
            status_code=400,
            detail="The feed's ledger account is not a bank or card account",
        )
    return acct


def candidate_lines(
    db: Session,
    account_id: int,
    amount: Decimal,
    on_date: date,
    window_days: int = CANDIDATE_WINDOW_DAYS,
    exclude_line_ids=frozenset(),
) -> list[dict]:
    """Unlinked, unreconciled ledger lines on `account_id` with exactly this
    amount on the statement's side, within the window, nearest date first.
    Voided postings and their reversals never qualify."""
    amount = _q(amount)
    side = TransactionLine.debit if amount > 0 else TransactionLine.credit
    linked = exists().where(BankTransaction.transaction_line_id == TransactionLine.id)
    q = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == account_id)
        .filter(side == abs(amount))
        .filter(TransactionLine.reconciliation_id.is_(None))
        .filter(~linked)
        .filter(~Transaction.source_type.like("%\\_void", escape="\\"))
        .filter(Transaction.date >= on_date - timedelta(days=window_days))
        .filter(Transaction.date <= on_date + timedelta(days=window_days))
    )
    rows = [(tl, txn) for tl, txn in q.all() if tl.id not in exclude_line_ids]
    if not rows:
        return []
    voided = voided_transaction_ids(db, {txn.id for _, txn in rows})
    rows = [(tl, txn) for tl, txn in rows if txn.id not in voided]
    payees = payees_for(db, [txn for _, txn in rows])
    rows.sort(key=lambda r: (abs((r[1].date - on_date).days), r[1].id, r[0].id))
    return [
        {
            "line_id": tl.id,
            "transaction_id": txn.id,
            "date": txn.date.isoformat(),
            "days_off": abs((txn.date - on_date).days),
            "description": txn.description or tl.description or "",
            "payee": payees.get(txn.id, ""),
            "reference": txn.reference or "",
            "source_type": txn.source_type,
            "source_link": source_link(txn),
            "amount": float(amount),
            "cleared": bool(tl.cleared),
        }
        for tl, txn in rows
    ]


def _link(bt: BankTransaction, line: TransactionLine, status: str) -> None:
    bt.transaction_id = line.transaction_id
    bt.transaction_line_id = line.id
    bt.match_status = status
    line.cleared = True


def auto_match(db: Session, bank_account: BankAccount, rows) -> int:
    """Link each unmatched statement line to the one nearest ledger line
    with its amount within ±AUTO_WINDOW_DAYS. A check number narrows to
    lines carrying it as reference. Two candidates at the same distance is
    ambiguity, and ambiguity stays unmatched — no guessing."""
    if not bank_account.account_id:
        return 0
    consumed: set[int] = set()
    matched = 0
    for bt in sorted(rows, key=lambda r: (r.date, r.id)):
        if bt.match_status != "unmatched":
            continue
        cands = candidate_lines(
            db, bank_account.account_id, bt.amount, bt.date, AUTO_WINDOW_DAYS, consumed
        )
        if bt.check_number:
            by_ref = [c for c in cands if c["reference"] == bt.check_number]
            if by_ref:
                cands = by_ref
        if not cands:
            continue
        nearest = [c for c in cands if c["days_off"] == cands[0]["days_off"]]
        if len(nearest) != 1:
            continue
        line = db.query(TransactionLine).get(nearest[0]["line_id"])
        _link(bt, line, "auto")
        consumed.add(line.id)
        matched += 1
    return matched


def match(db: Session, bt: BankTransaction, line_id: int) -> BankTransaction:
    acct = _feed_account(db, bt)
    if bt.transaction_line_id:
        raise HTTPException(status_code=400, detail="Statement line is already matched")
    line = db.query(TransactionLine).filter(TransactionLine.id == line_id).first()
    if not line or line.account_id != acct.id:
        raise HTTPException(
            status_code=400, detail="That ledger line is not on this account"
        )
    amount = _q(bt.amount)
    have = line.debit if amount > 0 else line.credit
    if _q(have) != abs(amount):
        raise HTTPException(
            status_code=400,
            detail="Amounts differ; match needs the same amount on the same side",
        )
    if line.reconciliation_id:
        raise HTTPException(
            status_code=400, detail="That line is in a completed reconciliation"
        )
    taken = (
        db.query(BankTransaction.id)
        .filter(BankTransaction.transaction_line_id == line.id)
        .first()
    )
    if taken:
        raise HTTPException(
            status_code=400,
            detail="That ledger line is already matched to another statement line",
        )
    _link(bt, line, "manual")
    return bt


def unmatch(db: Session, bt: BankTransaction) -> BankTransaction:
    if bt.match_status == "added":
        raise HTTPException(
            status_code=400,
            detail="This line was posted from the feed; void the entry instead",
        )
    if not bt.transaction_line_id:
        raise HTTPException(status_code=400, detail="Statement line is not matched")
    line = db.query(TransactionLine).get(bt.transaction_line_id)
    if line is not None:
        if line.reconciliation_id:
            raise HTTPException(
                status_code=400, detail="That line is in a completed reconciliation"
            )
        line.cleared = False
    bt.transaction_id = None
    bt.transaction_line_id = None
    bt.match_status = "unmatched"
    return bt


def add(
    db: Session,
    bt: BankTransaction,
    category_account_id: int | None = None,
    payee: str | None = None,
    memo: str | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
) -> BankTransaction:
    """Post the statement line as a register entry and link it."""
    acct = _feed_account(db, bt)
    if bt.transaction_line_id or bt.match_status == "added":
        raise HTTPException(
            status_code=400, detail="Statement line is already in the books"
        )
    cat_id = category_account_id or bt.category_account_id
    if not cat_id:
        raise HTTPException(
            status_code=400, detail="Pick a category (account) to add this line"
        )
    category = db.query(Account).filter(Account.id == cat_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category account not found")
    check_closing_date(db, bt.date)
    txn = post_bank_entry(
        db,
        acct,
        bt.date,
        bt.amount,
        category,
        payee=payee or bt.payee,
        memo=memo or bt.description,
        reference=bt.check_number,
        class_id=class_id,
        job_id=job_id,
        source_id=bt.id,
    )
    db.flush()
    line = (
        db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == txn.id,
            TransactionLine.account_id == acct.id,
        )
        .first()
    )
    _link(bt, line, "added")
    if category_account_id:
        bt.category_account_id = category_account_id
    return bt


def set_category(
    db: Session, bt: BankTransaction, category_account_id: int | None
) -> BankTransaction:
    """Keep the category picked for a statement line in the review list.

    Picks used to live only in the page's dropdown until that line's own
    Add was pressed: Add all posted just the rule-categorised lines and a
    reload lost the rest (exploratory 2.17.3, W-M12). A pick is saved as it
    is made, so Add all and the next visit both see it."""
    if bt.transaction_line_id or bt.match_status == "added":
        raise HTTPException(
            status_code=400,
            detail="This statement line is already in the books; its category can't change here",
        )
    if category_account_id is None:
        bt.category_account_id = None
        return bt
    category = db.query(Account).filter(Account.id == category_account_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category account not found")
    ba = bt.bank_account or db.query(BankAccount).get(bt.bank_account_id)
    if ba and ba.account_id == category.id:
        raise HTTPException(
            status_code=400,
            detail="Pick a category other than the account the statement is for",
        )
    bt.category_account_id = category.id
    return bt


def exclude(bt: BankTransaction) -> BankTransaction:
    if bt.transaction_line_id:
        raise HTTPException(status_code=400, detail="Unmatch it first")
    bt.match_status = "excluded"
    return bt


def restore(bt: BankTransaction) -> BankTransaction:
    if bt.match_status != "excluded":
        raise HTTPException(status_code=400, detail="Statement line is not excluded")
    bt.match_status = "unmatched"
    return bt


def add_all(db: Session, bank_account: BankAccount) -> dict:
    """Add every unmatched line that already carries a category (from a
    rule or the user). Lines that cannot post are reported, not fatal."""
    rows = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == bank_account.id,
            BankTransaction.match_status == "unmatched",
            BankTransaction.category_account_id.isnot(None),
        )
        .order_by(BankTransaction.date, BankTransaction.id)
        .all()
    )
    added, skipped = 0, []
    for bt in rows:
        try:
            with db.begin_nested():
                add(db, bt)
            added += 1
        except HTTPException as exc:
            skipped.append({"id": bt.id, "reason": exc.detail})
    return {"added": added, "skipped": skipped}
