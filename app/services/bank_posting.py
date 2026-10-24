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
from app.models.banking import BankTransaction, Reconciliation
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
from app.services.entry_text import BANK_ENTRY_HEADER


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


def bank_entry_lines(
    account: Account,
    amount: Decimal,
    category: Account,
    payee: str | None = None,
    memo: str | None = None,
    splits: list | None = None,
) -> tuple[str, list[dict]]:
    """A register entry's description and journal lines, for posting one
    and for rewriting one in place (#388). amount < 0: DR category / CR
    account (money out, or a card charge). amount > 0: DR account / CR
    category (money in, or a card payment). With `splits` ([(account,
    amount, memo[, {"class_id", "job_id"}]), …] adding up to `amount`),
    the other side is several lines (#330). Each part's own sign says which way it posts: a negative
    part debits its account, a positive part credits it, whichever way the
    entry goes. So a card processor's deposit is the gross as income
    (+100.00) and the fee as an expense (−3.00), the bank taking the net
    97.00 (#387); and a check with a refund netted against it (−620.00 and
    +120.00) is a −500.00 check. A part of zero is refused. A bank or card
    category is a transfer, which is not an entry of this shape: the caller
    decides that first."""
    amount = _q(amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Amount must not be zero")
    if category.id == account.id:
        raise HTTPException(
            status_code=400, detail="Category must differ from the account itself"
        )
    line_desc = memo or payee or None
    # The header carries the payee, or the sentinel "Bank entry" when none was
    # typed — never the memo. A memo-only entry (memo on the lines, sentinel on
    # the header) is then told apart from a payee-only one (payee on both), so
    # it reopens with the memo in Memo, not in Payee (#388 review).
    description = payee or BANK_ENTRY_HEADER
    if splits:
        lines = []
        for cat, part, part_memo, *dims in splits:
            part = _q(part)
            if part == 0:
                raise HTTPException(
                    status_code=400,
                    detail="A split line of 0.00 posts nothing; enter its share or remove the line",
                )
            line = {
                "account_id": cat.id,
                "debit": -part if part < 0 else Decimal("0"),
                "credit": part if part > 0 else Decimal("0"),
                "description": part_memo or line_desc,
            }
            # a line's own class and job, when it names them (an optional
            # fourth item {"class_id", "job_id"}); else the entry's
            for key, value in (dims[0] if dims else {}).items():
                if value is not None:
                    line[key] = value
            lines.append(line)
        lines.append(
            {
                "account_id": account.id,
                "debit": amount if amount > 0 else Decimal("0"),
                "credit": -amount if amount < 0 else Decimal("0"),
                "description": line_desc,
            }
        )
        return description, lines
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
    return description, lines


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
    splits: list | None = None,
) -> Transaction:
    """A register entry (the lines: `bank_entry_lines`). A category that is
    itself a bank/card account is a transfer."""
    amount = _q(amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Amount must not be zero")
    if category.id == account.id:
        raise HTTPException(
            status_code=400, detail="Category must differ from the account itself"
        )
    if not splits and category.bank_kind:
        if amount < 0:
            return post_transfer(
                db, txn_date, account, category, -amount, memo, reference
            )
        return post_transfer(db, txn_date, category, account, amount, memo, reference)
    description, lines = bank_entry_lines(
        account, amount, category, payee, memo, splits
    )
    return create_journal_entry(
        db,
        txn_date,
        description,
        lines,
        source_type="bank_entry",
        source_id=source_id,
        reference=reference or "",
        class_id=class_id,
        job_id=job_id,
    )


def reconciled_on(db: Session, txn: Transaction) -> Reconciliation | None:
    """The completed reconciliation holding a line of this entry (the latest
    statement, when more than one), or None."""
    ids = {ln.reconciliation_id for ln in txn.lines if ln.reconciliation_id}
    if not ids:
        return None
    return (
        db.query(Reconciliation)
        .filter(Reconciliation.id.in_(ids))
        .order_by(Reconciliation.statement_date.desc(), Reconciliation.id.desc())
        .first()
    )


def _pair_lines(old: list, new: list[dict]) -> list:
    """Each new line's old counterpart (or None), for a rewrite in place
    (#388): first the line with the same account and amount, then the one
    on the same account, then the remaining old lines in order. `old` is
    TransactionLine rows; `new` the line dicts about to be written."""
    pool = list(old)
    out: list = [None] * len(new)

    def take(i, test):
        hit = next((o for o in pool if test(o, new[i])), None)
        if hit is not None:
            pool.remove(hit)
            out[i] = hit

    for test in (
        lambda o, n: o.account_id == n["account_id"]
        and _q(o.debit or 0) == _q(n["debit"])
        and _q(o.credit or 0) == _q(n["credit"]),
        lambda o, n: o.account_id == n["account_id"],
        lambda o, n: True,
    ):
        for i in range(len(new)):
            if out[i] is None:
                take(i, test)
    return out


def _same_money(old, n: dict) -> bool:
    return (
        old is not None
        and old.account_id == n["account_id"]
        and _q(old.debit or 0) == _q(n["debit"])
        and _q(old.credit or 0) == _q(n["credit"])
    )


def replace_bank_entry(
    db: Session,
    txn: Transaction,
    account: Account,
    txn_date: date,
    amount: Decimal,
    category: Account,
    payee: str | None = None,
    memo: str | None = None,
    reference: str | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
    splits: list | None = None,
) -> list[dict]:
    """Rewrite a register entry in place (#388): the Transaction keeps its
    id (so its links, attachments, statement matches and audit trail stay
    on the one record).

    When every line keeps its account and amount (a payee, memo, reference,
    date, class or job fixed), the header and lines are updated where they
    are, and a save that changes nothing writes nothing. Otherwise the
    lines are replaced the way a bill's or an invoice's are on edit (#289):
    balances reversed, each old line a DELETE in the audit log, each new
    line an INSERT, the header an UPDATE. A line whose account and amount
    did not change keeps its cleared mark and its reconciliation, and the
    statement line matched to it follows to the new row; one that changed
    loses them, and its statement line goes back to review uncategorised,
    so "Add all categorised" cannot post it a second time.

    A line's own class or job — one set apart from the entry's, as an
    imported check's lines can be — stays with the line (its counterpart
    by account, else by order) unless the split names another; a line that
    followed the entry's class or job follows the new one.

    Returns the old lines that lost a cleared mark, so the caller can say
    so."""
    from app.models.transactions import TransactionLine
    from app.routes.invoices.helpers import _reverse_and_delete_journal
    from app.services.classes_service import default_function_of

    description, lines = bank_entry_lines(
        account, amount, category, payee, memo, splits
    )
    old_lines = list(txn.lines)
    pairs = _pair_lines(old_lines, lines)
    for n, o in zip(lines, pairs):
        for key, header_old, header_new in (
            ("class_id", txn.class_id, class_id),
            ("job_id", txn.job_id, job_id),
        ):
            if n.get(key) is not None:
                continue  # the split named it
            own = getattr(o, key) if o is not None else None
            n[key] = own if own is not None and own != header_old else header_new

    if all(_same_money(o, n) for o, n in zip(pairs, lines)) and len(lines) == len(
        old_lines
    ):
        # nothing moves: update the header and the lines where they are
        check_closing_date(db, txn_date)
        txn.date = txn_date
        txn.description = description
        txn.reference = reference or ""
        txn.class_id = class_id
        txn.job_id = job_id
        fn_cache: dict = {}
        for n, o in zip(lines, pairs):
            o.description = n.get("description")
            if o.class_id != n["class_id"]:
                o.class_id = n["class_id"]
                o.function = default_function_of(db, n["class_id"], fn_cache)
            o.job_id = n["job_id"]
        db.flush()
        return []

    old = [
        {
            "id": o.id,
            "account_id": o.account_id,
            "account_name": o.account.name if o.account else "",
            "debit": _q(o.debit or 0),
            "credit": _q(o.credit or 0),
            "cleared": bool(o.cleared),
            "reconciliation_id": o.reconciliation_id,
        }
        for o in old_lines
    ]
    keep = {
        i: next(x for x in old if x["id"] == o.id)
        for i, (o, n) in enumerate(zip(pairs, lines))
        if _same_money(o, n)
    }
    # The statement lines matched to this entry point at its lines; detach
    # them before the old lines go (PostgreSQL, and SQLite with foreign
    # keys on, refuse to delete a row still referenced), and relink below.
    links = {
        bt.transaction_line_id: bt
        for bt in db.query(BankTransaction).filter(
            BankTransaction.transaction_line_id.in_([o["id"] for o in old])
        )
    }
    for bt in links.values():
        bt.transaction_line_id = None
    db.flush()
    _reverse_and_delete_journal(db, txn.id)
    db.expire(txn, ["lines"])
    create_journal_entry(
        db,
        txn_date,
        description,
        lines,
        source_type="bank_entry",
        source_id=txn.source_id,
        reference=reference or "",
        class_id=class_id,
        job_id=job_id,
        existing_transaction=txn,
        posted_by=txn.posted_by,
    )
    db.flush()
    new_rows = (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn.id)
        .order_by(TransactionLine.id)
        .all()
    )
    for i, same in keep.items():
        ln = new_rows[i]
        ln.cleared = same["cleared"]
        ln.reconciliation_id = same["reconciliation_id"]
        bt = links.pop(same["id"], None)
        if bt is not None:
            bt.transaction_line_id = ln.id
    for bt in links.values():
        # back to review, and not ready to post: its category was for the
        # posting it was added as, which still stands (re-posting it would
        # count the bank line twice)
        bt.transaction_id = None
        bt.match_status = "unmatched"
        bt.category_account_id = None
    db.flush()
    db.refresh(txn)
    kept = {v["id"] for v in keep.values()}
    return [
        o
        for o in old
        if o["id"] not in kept and (o["cleared"] or o["reconciliation_id"])
    ]


def replace_journal_lines(
    db: Session,
    txn: Transaction,
    txn_date: date,
    description: str,
    lines: list[dict],
    *,
    source_type: str,
    reference: str | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
    is_adjusting: bool = False,
    posted_by: str | None = None,
) -> list[dict]:
    """Rewrite a journal entry in place (#388), the way `replace_bank_entry`
    rewrites a register entry — for a manual journal entry or a deposit
    opened from the register. The Transaction keeps its id (so its links,
    attachments, statement matches and audit trail stay on the one record);
    its lines are replaced the way a bill's are on edit (#289): balances
    reversed, each old line a DELETE in the audit log, each new line an
    INSERT, the header an UPDATE. A line whose account and amount did not
    change keeps its cleared mark and its reconciliation, and the statement
    line matched to it follows to the new row; one that changed loses them,
    its statement line going back to review. `lines` are the new line dicts
    (account_id, debit, credit, and any description/class/job the caller
    sets). Returns the old lines that lost a cleared or reconciled mark."""
    from app.models.transactions import TransactionLine
    from app.routes.invoices.helpers import _reverse_and_delete_journal
    from app.services.classes_service import default_function_of

    old_lines = list(txn.lines)
    pairs = _pair_lines(old_lines, lines)
    # A dimension the caller left off a line keeps what the paired old line
    # had (its counterpart by account, else by order): a class or job of its
    # own, else the entry's; the billable flag; the cost code; the nonprofit
    # function (#388 review — the same carry-over replace_bank_entry does).
    for n, o in zip(lines, pairs):
        for key, header_old, header_new in (
            ("class_id", txn.class_id, class_id),
            ("job_id", txn.job_id, job_id),
        ):
            if key in n:
                continue
            own = getattr(o, key) if o is not None else None
            n[key] = own if own is not None and own != header_old else header_new
        if "is_billable" not in n:
            n["is_billable"] = bool(o.is_billable) if o is not None else False
        if "cost_code_id" not in n:
            n["cost_code_id"] = o.cost_code_id if o is not None else None
        if "function" not in n and o is not None:
            n["function"] = o.function

    if all(_same_money(o, n) for o, n in zip(pairs, lines)) and len(lines) == len(
        old_lines
    ):
        # nothing moves: update the header and the lines where they are, so
        # a save that changes nothing writes nothing and the reconciled
        # lines keep their marks without a DELETE/INSERT churn (#388)
        check_closing_date(db, txn_date)
        txn.date = txn_date
        txn.description = description
        txn.reference = reference or ""
        txn.class_id = class_id
        txn.job_id = job_id
        txn.is_adjusting = bool(is_adjusting)
        if posted_by is not None:
            txn.posted_by = posted_by
        fn_cache: dict = {}
        for n, o in zip(lines, pairs):
            o.description = n.get("description") or ""
            o.is_billable = bool(n.get("is_billable"))
            o.cost_code_id = n.get("cost_code_id")
            o.job_id = n.get("job_id")
            if o.class_id != n.get("class_id"):
                o.class_id = n.get("class_id")
                o.function = default_function_of(db, n.get("class_id"), fn_cache)
            elif "function" in n:
                o.function = n["function"]
        db.flush()
        return []

    old = [
        {
            "id": o.id,
            "account_id": o.account_id,
            "account_name": o.account.name if o.account else "",
            "debit": _q(o.debit or 0),
            "credit": _q(o.credit or 0),
            "cleared": bool(o.cleared),
            "reconciliation_id": o.reconciliation_id,
        }
        for o in old_lines
    ]
    keep = {
        i: next(x for x in old if x["id"] == o.id)
        for i, (o, n) in enumerate(zip(pairs, lines))
        if _same_money(o, n)
    }
    # Detach the statement lines matched to this entry before the old lines
    # go (a referenced row cannot be deleted), and relink the kept ones.
    links = {
        bt.transaction_line_id: bt
        for bt in db.query(BankTransaction).filter(
            BankTransaction.transaction_line_id.in_([o["id"] for o in old])
        )
    }
    for bt in links.values():
        bt.transaction_line_id = None
    db.flush()
    _reverse_and_delete_journal(db, txn.id)
    db.expire(txn, ["lines"])
    create_journal_entry(
        db,
        txn_date,
        description,
        lines,
        source_type=source_type,
        source_id=txn.source_id,
        reference=reference or "",
        class_id=class_id,
        job_id=job_id,
        existing_transaction=txn,
        is_adjusting=bool(is_adjusting),
        posted_by=posted_by if posted_by is not None else txn.posted_by,
    )
    db.flush()
    new_rows = (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == txn.id)
        .order_by(TransactionLine.id)
        .all()
    )
    for i, same in keep.items():
        ln = new_rows[i]
        ln.cleared = same["cleared"]
        ln.reconciliation_id = same["reconciliation_id"]
        bt = links.pop(same["id"], None)
        if bt is not None:
            bt.transaction_line_id = ln.id
    for bt in links.values():
        bt.transaction_id = None
        bt.match_status = "unmatched"
        bt.category_account_id = None
    db.flush()
    db.refresh(txn)
    kept = {v["id"] for v in keep.values()}
    return [
        o
        for o in old
        if o["id"] not in kept and (o["cleared"] or o["reconciliation_id"])
    ]


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
