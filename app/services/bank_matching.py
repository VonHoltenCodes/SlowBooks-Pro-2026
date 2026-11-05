"""Statement lines (bank feeds, OFX/CSV imports) are a review queue: each
one is matched to a posting the ledger already has, or added as a new
posting, or excluded (issue #114). Nothing posts silently.

Sign contract (see bank_posting): a statement amount > 0 is a DEBIT to
the bank/card ledger account, < 0 a CREDIT, whatever the account type.
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.contacts import Vendor
from app.models.banking import BankAccount, BankTransaction
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q
from app.services.bank_posting import post_bank_entry
from app.services.bank_register import (
    payees_for,
    references_for,
    source_link,
    voided_transaction_ids,
)
from app.services.closing_date import check_closing_date
from app.services.entry_text import BANK_ENTRY_HEADER, header_text

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
    refs = references_for(db, list({txn.id: txn for _, txn in rows}.values()))
    rows.sort(key=lambda r: (abs((r[1].date - on_date).days), r[1].id, r[0].id))
    return [
        {
            "line_id": tl.id,
            "transaction_id": txn.id,
            "date": txn.date.isoformat(),
            "days_off": abs((txn.date - on_date).days),
            "description": header_text(txn, tl.description),
            "payee": payees.get(txn.id, ""),
            "reference": refs.get(txn.id, ""),
            "source_type": txn.source_type,
            "source_kind": txn.source_kind,
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


def vendor_named(db: Session, name: str | None) -> Vendor | None:
    """The vendor whose name is `name`, ignoring capitals and the spaces
    around it: an active one first, else an inactive one (its history is
    still its own). None for an empty name or no such vendor."""
    key = (name or "").strip().lower()
    if not key:
        return None
    return (
        db.query(Vendor)
        .filter(func.lower(func.trim(Vendor.name)) == key)
        .order_by(Vendor.is_active.desc(), Vendor.id)
        .first()
    )


def _entry_vendor(
    db: Session, bt: BankTransaction, payee: str | None, vendor_id: int | None
) -> Vendor | None:
    """The vendor a feed line posts to (#491): the one asked for; else the
    one named by the bank rule the statement line follows (#493); else the
    vendor its payee names."""
    from app.services.bank_rules_engine import rule_for

    if vendor_id is not None:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor
    rule = rule_for(db, bt.payee)
    if rule is not None and rule.vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == rule.vendor_id).first()
        if vendor is not None:
            return vendor
    return vendor_named(db, payee)


def add(
    db: Session,
    bt: BankTransaction,
    category_account_id: int | None = None,
    payee: str | None = None,
    memo: str | None = None,
    class_id: int | None = None,
    job_id: int | None = None,
    vendor_id: int | None = None,
    splits: list | None = None,
    customer_id: int | None = None,
) -> BankTransaction:
    """Post the statement line as a register entry and link it. The entry
    names the vendor asked for, or the one whose name is the payee (#491);
    a payee no vendor has is left as text, and the review row offers to
    make it one (`attach_vendors`).

    With `splits` ([(account, amount, memo[, dims]), …] adding up to the
    statement amount, #584) the other side is several accounts — a provider
    payout clears its clearing account and books the fee. Splits replace the
    single category; the two are mutually exclusive."""
    acct = _feed_account(db, bt)
    if bt.transaction_line_id or bt.match_status == "added":
        raise HTTPException(
            status_code=400, detail="Statement line is already in the books"
        )
    if splits:
        if category_account_id:
            raise HTTPException(
                status_code=400,
                detail="A split line replaces the category; pick one, not both",
            )
        category = splits[0][0]  # for post_bank_entry's account/transfer checks
    else:
        cat_id = category_account_id or bt.category_account_id
        if not cat_id:
            raise HTTPException(
                status_code=400, detail="Pick a category (account) to add this line"
            )
        category = db.query(Account).filter(Account.id == cat_id).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category account not found")
    check_closing_date(db, bt.date)
    payee = payee or bt.payee
    vendor = _entry_vendor(db, bt, payee, vendor_id)
    if not splits and (class_id is None or job_id is None):
        # the class and job the statement line's rule names (#675), where
        # the caller named none; a split's lines carry their own
        from app.services.bank_rules_engine import rule_dimensions

        rule_class, rule_job = rule_dimensions(db, bt.payee)
        class_id = class_id if class_id is not None else rule_class
        job_id = job_id if job_id is not None else rule_job
    txn = post_bank_entry(
        db,
        acct,
        bt.date,
        bt.amount,
        category,
        payee=payee,
        memo=memo or bt.description,
        reference=bt.check_number,
        class_id=class_id,
        job_id=job_id,
        source_id=bt.id,
        splits=splits,
        customer_id=None if splits else customer_id,
    )
    if vendor is not None and txn.source_type == "bank_entry":
        # a transfer between two bank or card accounts has no vendor
        txn.vendor_id = vendor.id
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


def entry_vendors(db: Session, rows) -> dict[int, Vendor]:
    """The vendor on each added statement line's entry, by line id."""
    txn_ids = {r.transaction_id for r in rows if r.transaction_id}
    if not txn_ids:
        return {}
    pairs = (
        db.query(Transaction.id, Vendor)
        .join(Vendor, Transaction.vendor_id == Vendor.id)
        .filter(Transaction.id.in_(txn_ids))
        .all()
    )
    by_txn = dict(pairs)
    return {r.id: by_txn[r.transaction_id] for r in rows if r.transaction_id in by_txn}


def needs_vendor(db: Session, bt: BankTransaction) -> bool:
    """An added line whose entry is money out, has a payee and names no
    vendor: the one the review row offers to make a vendor of."""
    if bt.match_status != "added" or not bt.transaction_id or bt.amount >= 0:
        return False
    txn = db.query(Transaction).filter(Transaction.id == bt.transaction_id).first()
    return bool(
        txn
        and txn.source_type == "bank_entry"
        and txn.vendor_id is None
        and (txn.description or "").strip()
        and txn.description != BANK_ENTRY_HEADER
    )


def attach_vendors(db: Session, line_ids: list[int]) -> dict:
    """Name a vendor on the entries these added feed lines posted (#491):
    the vendor their payee names, made when there is none, the way the
    register's "Add X as a vendor?" makes one. A line not added from the
    feed, money in, a transfer or an entry that already names a vendor is
    left as it is. Returns the vendors made and the entries linked."""
    rows = (
        db.query(BankTransaction)
        .filter(BankTransaction.id.in_(line_ids or [-1]))
        .order_by(BankTransaction.id)
        .all()
    )
    created, linked = [], 0
    for bt in rows:
        if not needs_vendor(db, bt):
            continue
        txn = db.query(Transaction).get(bt.transaction_id)
        name = txn.description.strip()
        vendor = vendor_named(db, name)
        if vendor is None:
            vendor = Vendor(name=name)
            db.add(vendor)
            db.flush()
            created.append({"id": vendor.id, "name": vendor.name})
        txn.vendor_id = vendor.id
        linked += 1
    db.flush()
    return {"created": created, "linked": linked}


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
    """Back to To review: a line the user excluded, or one an import set
    aside as already in the books (bank_dedupe.IN_BOOKS)."""
    from app.services.bank_dedupe import EXCLUDED

    if bt.match_status not in EXCLUDED:
        raise HTTPException(status_code=400, detail="Statement line is not excluded")
    bt.match_status = "unmatched"
    return bt


def add_all(db: Session, bank_account: BankAccount, picks: dict | None = None) -> dict:
    """Add every unmatched line that already carries a category (from a
    rule or the user). Lines that cannot post are reported, not fatal.
    `picks` ({line id: {"class_id", "job_id", "vendor_id", "customer_id",
    "payee"}}, #671) is what each review row chose besides its category."""
    picks = picks or {}
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
    added, skipped, no_vendor = 0, [], []
    for bt in rows:
        try:
            with db.begin_nested():
                add(db, bt, **picks.get(bt.id, {}))
            added += 1
        except HTTPException as exc:
            skipped.append({"id": bt.id, "reason": exc.detail})
            continue
        if needs_vendor(db, bt):
            no_vendor.append(bt.id)
    # the lines whose payee is no vendor yet, for the page to offer them
    return {"added": added, "skipped": skipped, "without_vendor": no_vendor}
