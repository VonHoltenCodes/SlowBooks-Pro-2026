# ============================================================================
# Bank accounts + reconciliation — toggle cleared items, then validate
# their sum matches the statement balance.
# ============================================================================

import re
from datetime import date
from types import SimpleNamespace
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.accounting import _q
from app.routes._helpers import clamp_pagination, reject_null_updates
from app.models.accounts import Account
from app.models.banking import (
    BankAccount,
    BankTransaction,
    Reconciliation,
    ReconciliationStatus,
)
from app.schemas.common import StrictModel
from app.schemas.banking import (
    BankAccountCreate,
    BankAccountUpdate,
    BankAccountResponse,
    BankEntryDetail,
    BankEntryResponse,
    BankEntryUpdate,
    BankSplit,
    BankTransactionCreate,
    BankTransactionResponse,
    LegacyBalancePost,
    ReconciliationCreate,
    ReconciliationUpdate,
    StatementAdd,
    StatementCategory,
    StatementMatch,
    ReconciliationResponse,
)
from app.models.transactions import Transaction
from app.services.audit import log_event
from app.services.bank_posting import (
    bank_entry_lines,
    post_bank_entry,
    post_opening_balance,
    post_statement_balance,
    reconciled_on,
    replace_bank_entry,
    require_bank_account,
    void_document,
)
from app.services.bank_register import (
    account_register,
    balance_as_of,
    date_text,
    gl_balance,
    gl_balances,
    voided_transaction_ids,
)
from app.services.closing_date import check_closing_date
from app.services.entry_text import BANK_ENTRY_HEADER

router = APIRouter(prefix="/api/banking", tags=["banking"])


# ---------------------------------------------------------------------------
# Bank accounts. The ledger account (bank_kind set) is the account; a
# BankAccount row is its feed/statement identity. Balances come from the
# ledger (issue #114).
# ---------------------------------------------------------------------------


def _feed_out(ba: BankAccount, balances: dict) -> dict:
    return {
        "id": ba.id,
        "name": ba.name,
        "account_id": ba.account_id,
        "account_name": ba.account.name if ba.account else None,
        "bank_kind": ba.account.bank_kind if ba.account else None,
        "bank_name": ba.bank_name,
        "last_four": ba.last_four,
        "balance": (
            balances.get(ba.account_id, Decimal("0")) if ba.account_id else Decimal("0")
        ),
        "legacy_balance": ba.legacy_balance,
        "is_active": bool(ba.is_active),
        "created_at": ba.created_at,
        "updated_at": ba.updated_at,
    }


def _feeds_out(db: Session, rows: list) -> list[dict]:
    balances = gl_balances(db, [b.account_id for b in rows if b.account_id])
    return [_feed_out(b, balances) for b in rows]


@router.get("/overview")
def banking_overview(db: Session = Depends(get_db)):
    """Every bank and card account with its ledger balance, its feed (if
    any), how many statement lines wait for review, and the last completed
    reconciliation."""
    accounts = (
        db.query(Account)
        .filter(Account.bank_kind.isnot(None), Account.is_active)
        .order_by(Account.account_number, Account.name)
        .all()
    )
    ids = [a.id for a in accounts]
    balances = gl_balances(db, ids)
    feeds = {
        b.account_id: b
        for b in db.query(BankAccount)
        .filter(BankAccount.is_active, BankAccount.account_id.in_(ids))
        .all()
    }
    review = dict(
        db.query(BankTransaction.bank_account_id, func.count(BankTransaction.id))
        .filter(BankTransaction.match_status == "unmatched")
        .group_by(BankTransaction.bank_account_id)
        .all()
    )
    last_recon = {}
    for r in (
        db.query(Reconciliation)
        .filter(Reconciliation.status == ReconciliationStatus.COMPLETED)
        .filter(Reconciliation.account_id.in_(ids))
        .order_by(Reconciliation.statement_date)
        .all()
    ):
        last_recon[r.account_id] = r.statement_date.isoformat()
    out = []
    for a in accounts:
        feed = feeds.get(a.id)
        out.append(
            {
                "account_id": a.id,
                "account_number": a.account_number,
                "name": a.name,
                "bank_kind": a.bank_kind,
                "balance": float(balances.get(a.id, Decimal("0"))),
                "to_review": int(review.get(feed.id, 0)) if feed else 0,
                "last_reconciled": last_recon.get(a.id),
                "feed": (
                    {
                        "bank_account_id": feed.id,
                        "name": feed.name,
                        "bank_name": feed.bank_name,
                        "last_four": feed.last_four,
                        "legacy_balance": (
                            float(feed.legacy_balance)
                            if feed.legacy_balance is not None
                            else None
                        ),
                    }
                    if feed
                    else None
                ),
            }
        )
    return out


@router.get("/accounts", response_model=list[BankAccountResponse])
def list_bank_accounts(db: Session = Depends(get_db)):
    rows = (
        db.query(BankAccount)
        .filter(BankAccount.is_active)
        .order_by(BankAccount.name)
        .all()
    )
    return _feeds_out(db, rows)


@router.get("/accounts/{account_id}", response_model=BankAccountResponse)
def get_bank_account(account_id: int, db: Session = Depends(get_db)):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return _feeds_out(db, [ba])[0]


def _reject_second_feed(db: Session, account_id: int, exclude_id: int | None = None):
    q = db.query(BankAccount).filter(
        BankAccount.account_id == account_id, BankAccount.is_active
    )
    if exclude_id is not None:
        q = q.filter(BankAccount.id != exclude_id)
    other = q.first()
    if other:
        raise HTTPException(
            status_code=409,
            detail=f"'{other.name}' is already the feed for this ledger account",
        )


@router.get("/ledger-balance")
def ledger_balance(account_id: int, as_of: date = None, db: Session = Depends(get_db)):
    """What the books already say about a bank or card account on a date —
    the New Bank Account form shows it beside the statement balance."""
    acct = require_bank_account(db, account_id)
    as_of = as_of or date.today()
    balance, lines = balance_as_of(db, acct, as_of)
    return {
        "account_id": acct.id,
        "account_name": acct.name,
        "as_of": as_of.isoformat(),
        "balance": float(balance),
        "has_postings": lines > 0,
    }


@router.post("/accounts", response_model=BankAccountResponse, status_code=201)
def create_bank_account(data: BankAccountCreate, db: Session = Depends(get_db)):
    acct = require_bank_account(db, data.account_id)
    _reject_second_feed(db, acct.id)
    if data.opening_balance:
        opening_date = data.opening_date or date.today()
        check_closing_date(db, opening_date)
        post_statement_balance(
            db, acct, opening_date, data.opening_balance, data.post_difference
        )
    ba = BankAccount(
        name=data.name,
        account_id=acct.id,
        bank_name=data.bank_name,
        last_four=data.last_four,
        legacy_balance=None,
    )
    db.add(ba)
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


@router.put("/accounts/{account_id}", response_model=BankAccountResponse)
def update_bank_account(
    account_id: int, data: BankAccountUpdate, db: Session = Depends(get_db)
):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    fields = reject_null_updates(BankAccount, data.model_dump(exclude_unset=True))
    if "account_id" in fields and fields["account_id"] != ba.account_id:
        linked = (
            db.query(BankTransaction.id)
            .filter(
                BankTransaction.bank_account_id == ba.id,
                BankTransaction.transaction_line_id.isnot(None),
            )
            .first()
        )
        if linked:
            raise HTTPException(
                status_code=400,
                detail="This feed already has matched statement lines; its ledger account cannot change",
            )
        acct = require_bank_account(db, fields["account_id"])
        _reject_second_feed(db, acct.id, exclude_id=ba.id)
    for key, val in fields.items():
        setattr(ba, key, val)
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


@router.post(
    "/accounts/{account_id}/post-legacy-balance", response_model=BankAccountResponse
)
def post_legacy_balance(
    account_id: int, data: LegacyBalancePost, db: Session = Depends(get_db)
):
    """The pre-2.10 register balance, posted once as the account's opening
    balance (against 3900), then cleared from the feed."""
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if ba.legacy_balance is None:
        raise HTTPException(status_code=400, detail="No pre-2.10 balance to post")
    if not ba.account_id:
        raise HTTPException(
            status_code=400, detail="Link this feed to a ledger account first"
        )
    acct = require_bank_account(db, ba.account_id)
    check_closing_date(db, data.date)
    if ba.legacy_balance != 0:
        post_opening_balance(db, acct, data.date, ba.legacy_balance)
    ba.legacy_balance = None
    db.commit()
    db.refresh(ba)
    return _feeds_out(db, [ba])[0]


# Bank Transactions
def _statement_out(rows: list, db: Session) -> list[dict]:
    ids = {r.category_account_id for r in rows if r.category_account_id}
    names = (
        {a.id: a.name for a in db.query(Account).filter(Account.id.in_(ids)).all()}
        if ids
        else {}
    )
    return [
        {
            "id": r.id,
            "bank_account_id": r.bank_account_id,
            "date": r.date,
            "amount": r.amount,
            "payee": r.payee,
            "description": r.description,
            "check_number": r.check_number,
            "category_account_id": r.category_account_id,
            "category_name": names.get(r.category_account_id),
            "match_status": r.match_status,
            "transaction_id": r.transaction_id,
            "transaction_line_id": r.transaction_line_id,
            "import_source": r.import_source,
            "reconciled": bool(r.reconciled),
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/transactions", response_model=list[BankTransactionResponse])
def list_bank_transactions(
    bank_account_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Statement lines (feeds and imports) — the review queue. `status`
    filters on match_status: unmatched | auto | manual | added | excluded."""
    skip, limit = clamp_pagination(skip, limit)
    q = db.query(BankTransaction)
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    if status:
        q = q.filter(BankTransaction.match_status == status)
    rows = (
        q.order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return _statement_out(rows, db)


def _statement_line(db: Session, txn_id: int) -> BankTransaction:
    bt = db.query(BankTransaction).filter(BankTransaction.id == txn_id).first()
    if not bt:
        raise HTTPException(status_code=404, detail="Statement line not found")
    return bt


@router.get("/transactions/{txn_id}/candidates")
def statement_candidates(txn_id: int, db: Session = Depends(get_db)):
    """Ledger lines this statement line could be matched to."""
    from app.services import bank_matching as m

    bt = _statement_line(db, txn_id)
    acct = m._feed_account(db, bt)
    return m.candidate_lines(db, acct.id, bt.amount, bt.date)


@router.post("/transactions/{txn_id}/match", response_model=BankTransactionResponse)
def statement_match(txn_id: int, data: StatementMatch, db: Session = Depends(get_db)):
    from app.services import bank_matching as m

    bt = m.match(db, _statement_line(db, txn_id), data.line_id)
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/{txn_id}/unmatch", response_model=BankTransactionResponse)
def statement_unmatch(txn_id: int, db: Session = Depends(get_db)):
    from app.services import bank_matching as m

    bt = m.unmatch(db, _statement_line(db, txn_id))
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/{txn_id}/add", response_model=BankTransactionResponse)
def statement_add(
    txn_id: int, data: StatementAdd = None, db: Session = Depends(get_db)
):
    """Post this statement line as a register entry and link it."""
    from app.services import bank_matching as m

    data = data or StatementAdd()
    bt = m.add(
        db,
        _statement_line(db, txn_id),
        category_account_id=data.category_account_id,
        payee=data.payee,
        memo=data.memo,
        class_id=data.class_id,
        job_id=data.job_id,
    )
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.patch("/transactions/{txn_id}", response_model=BankTransactionResponse)
def statement_set_category(
    txn_id: int, data: StatementCategory, db: Session = Depends(get_db)
):
    """Save the category picked for a statement line in the review list."""
    from app.services import bank_matching as m

    bt = m.set_category(db, _statement_line(db, txn_id), data.category_account_id)
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/{txn_id}/exclude", response_model=BankTransactionResponse)
def statement_exclude(txn_id: int, db: Session = Depends(get_db)):
    from app.services import bank_matching as m

    bt = m.exclude(_statement_line(db, txn_id))
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/{txn_id}/restore", response_model=BankTransactionResponse)
def statement_restore(txn_id: int, db: Session = Depends(get_db)):
    from app.services import bank_matching as m

    bt = m.restore(_statement_line(db, txn_id))
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/accounts/{account_id}/feed/add-all")
def feed_add_all(account_id: int, db: Session = Depends(get_db)):
    """Add every unmatched line that already carries a category."""
    from app.services import bank_matching as m

    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    out = m.add_all(db, ba)
    db.commit()
    return out


@router.post("/accounts/{account_id}/feed/auto-match")
def feed_auto_match(account_id: int, db: Session = Depends(get_db)):
    """Find matches for this feed's unmatched lines (what an import does
    on arrival, on demand)."""
    from app.services import bank_matching as m

    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    rows = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == ba.id,
            BankTransaction.match_status == "unmatched",
        )
        .all()
    )
    n = m.auto_match(db, ba, rows)
    db.commit()
    return {"matched": n}


def _entry_out(txn, account_id: int, db: Session, status: str = "recorded") -> dict:
    bank_line = next(ln for ln in txn.lines if ln.account_id == account_id)
    other = next((ln for ln in txn.lines if ln.account_id != account_id), bank_line)
    category = db.query(Account).filter(Account.id == other.account_id).first()
    amount = (bank_line.debit or Decimal("0")) - (bank_line.credit or Decimal("0"))
    return {
        "id": txn.id,
        "account_id": account_id,
        "date": txn.date,
        "amount": amount,
        "category_account_id": other.account_id,
        "category_name": category.name if category else "",
        "payee": txn.description or "",
        "description": bank_line.description or "",
        "reference": txn.reference or "",
        "source_type": txn.source_type or "",
        "status": status,
    }


def _resolve_account(db: Session, account_id, bank_account_id) -> Account:
    if account_id is None and bank_account_id is None:
        raise HTTPException(status_code=422, detail="account_id is required")
    if account_id is None:
        ba = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
        if not ba:
            raise HTTPException(status_code=404, detail="Bank account not found")
        if not ba.account_id:
            raise HTTPException(
                status_code=400, detail="Link this feed to a ledger account first"
            )
        account_id = ba.account_id
    return require_bank_account(db, account_id)


def _category_and_splits(db: Session, account: Account, data) -> tuple:
    """The other side of a register entry as the request names it: the
    category account, and the split lines ([(account, amount, memo), …])
    when there are any — the same checks for a new entry and an edit."""
    splits = None
    if data.splits:
        # a split entry (#330): every line's account, the amounts adding up
        # to the entry's, no bank or card account among them (a transfer is
        # one line, never a split). A line may go the other way from the
        # entry (#387): a processor's fee on a deposit, a refund netted
        # against a check. A line of zero says nothing and is refused.
        splits = []
        for part in data.splits:
            cat = (
                db.query(Account).filter(Account.id == part.category_account_id).first()
            )
            if not cat:
                raise HTTPException(
                    status_code=404,
                    detail=f"Split account {part.category_account_id} not found",
                )
            if cat.bank_kind:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{cat.name}' is a bank or card account; a transfer is its own entry, not a split line",
                )
            if cat.id == account.id:
                raise HTTPException(
                    status_code=400,
                    detail="A split line cannot post to the account itself",
                )
            if _q(part.amount) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="A split line of 0.00 posts nothing; enter its share or remove the line",
                )
            splits.append(
                (
                    cat,
                    part.amount,
                    part.description,
                    {"class_id": part.class_id, "job_id": part.job_id},
                )
            )
        total = sum((sp[1] for sp in splits), Decimal("0"))
        if _q(total) != _q(data.amount):
            raise HTTPException(
                status_code=400,
                detail=f"The split lines come to {_q(total):,.2f} and the entry is {_q(data.amount):,.2f}; they must agree",
            )
        category = splits[0][0]
    else:
        if data.category_account_id is None:
            raise HTTPException(status_code=422, detail="Category account is required")
        category = (
            db.query(Account).filter(Account.id == data.category_account_id).first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Category account not found")
    return category, splits


@router.post("/transactions", response_model=BankEntryResponse, status_code=201)
def create_bank_transaction(data: BankTransactionCreate, db: Session = Depends(get_db)):
    """A register entry posts to the ledger (issue #114): DR category /
    CR account for money out, the reverse for money in; a bank or card
    category makes it a transfer."""
    check_closing_date(db, data.date)
    account = _resolve_account(db, data.account_id, data.bank_account_id)
    category, splits = _category_and_splits(db, account, data)
    txn = post_bank_entry(
        db,
        account,
        data.date,
        data.amount,
        category,
        payee=data.payee,
        memo=data.description,
        reference=data.check_number,
        class_id=data.class_id,
        job_id=data.job_id,
        splits=splits,
    )
    db.commit()
    db.refresh(txn)
    return _entry_out(txn, account.id, db)


def _bank_entry(db: Session, txn_id: int) -> Transaction:
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == txn_id, Transaction.source_type == "bank_entry")
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Bank entry not found")
    return txn


def _bank_line(txn: Transaction):
    return next(
        (ln for ln in txn.lines if ln.account and ln.account.bank_kind), txn.lines[0]
    )


def _reconciled_sentence(recon) -> str:
    """What the edit form says about an entry a completed reconciliation
    holds (#388), when it opens and when a change is refused."""
    return (
        f"This entry was reconciled on the {date_text(recon.statement_date)} "
        "statement, so its amount and date stay as they are (a reconciled "
        "entry cannot be voided either). Its payee, memo, category, class "
        "and job can still be corrected."
    )


def _entry_detail(
    txn, account_id: int, db: Session, status: str = "recorded", uncleared=None
) -> dict:
    """The entry in the + Entry form's shape (#388): what `_entry_out`
    says, plus class, job, the split lines when there are several, and
    the statement it was reconciled on."""
    from app.models.classes import TxnClass

    out = _entry_out(txn, account_id, db, status)
    bank_line = next(ln for ln in txn.lines if ln.account_id == account_id)
    others = [ln for ln in txn.lines if ln.account_id != account_id]
    line_desc = bank_line.description or ""
    header = txn.description or ""
    # A payee-only entry carries the payee on both the header and the lines; a
    # memo-only one carries the sentinel "Bank entry" on the header and the
    # memo on the lines (bank_entry_lines). So the header being the sentinel
    # means no payee was typed, and whatever is on the lines is the Memo: both
    # reopen as they were saved — the memo in Memo, not in Payee (#388 review).
    if header == BANK_ENTRY_HEADER:
        out["payee"] = ""
        out["description"] = line_desc
    else:
        out["description"] = "" if line_desc == header else line_desc
    out["class_id"] = txn.class_id
    cls = db.get(TxnClass, txn.class_id) if txn.class_id else None
    out["class_name"] = cls.name if cls else None
    out["job_id"] = txn.job_id

    def own(ln, key):
        # a line's own class or job: one set apart from the entry's
        value = getattr(ln, key)
        return value if value is not None and value != getattr(txn, key) else None

    out["splits"] = (
        [
            {
                "category_account_id": ln.account_id,
                "amount": (ln.credit or Decimal("0")) - (ln.debit or Decimal("0")),
                "description": (
                    ln.description if (ln.description or "") != line_desc else None
                ),
                "class_id": own(ln, "class_id"),
                "job_id": own(ln, "job_id"),
            }
            for ln in others
        ]
        if len(others) > 1
        else None
    )
    recon = reconciled_on(db, txn)
    out["reconciled_on"] = recon.statement_date if recon else None
    out["reconciled_note"] = _reconciled_sentence(recon) if recon else None
    out["uncleared"] = [
        {
            "account_id": o["account_id"],
            "account_name": o["account_name"],
            "amount": str(o["debit"] - o["credit"]),
        }
        for o in (uncleared or [])
    ]
    out["note"] = (
        (
            "The "
            + ", ".join(o["account_name"] or "line" for o in uncleared)
            + (
                " line changed, so it is"
                if len(uncleared) == 1
                else " lines changed, so they are"
            )
            + " no longer marked cleared; tick "
            + ("it" if len(uncleared) == 1 else "them")
            + " again on the next reconciliation."
        )
        if uncleared
        else None
    )
    return out


@router.get("/entries/{txn_id}", response_model=BankEntryDetail)
def get_bank_entry(txn_id: int, db: Session = Depends(get_db)):
    """A register entry as the + Entry form takes it (#388), to open it
    for editing."""
    txn = _bank_entry(db, txn_id)
    status = "void" if txn.id in voided_transaction_ids(db, [txn.id]) else "recorded"
    return _entry_detail(txn, _bank_line(txn).account_id, db, status=status)


def _other_side(txn: Transaction, account_id: int) -> list:
    """The entry's lines off the bank account, as (account, signed amount)
    — what a change of category or split lines changes."""
    return sorted(
        (ln.account_id, _q((ln.credit or 0) - (ln.debit or 0)))
        for ln in txn.lines
        if ln.account_id != account_id
    )


@router.put("/entries/{txn_id}", response_model=BankEntryDetail)
def update_bank_entry(
    txn_id: int, data: BankEntryUpdate, db: Session = Depends(get_db)
):
    """Rewrite a register entry in place (#388): same journal entry, new
    date, amount, category or splits, payee, memo, reference, class and
    job (`replace_bank_entry`). A field the request leaves out keeps what
    the entry has. A void entry is not edited; an entry that would become
    a transfer is refused (void it and post the transfer). On an entry a
    completed reconciliation holds, the amount and date are refused (400:
    the reconciled bank line stays as it is), a payee, memo, reference,
    class or job fix goes through, and a change of category or split
    lines is refused with 409 and the statement date until the request
    says `confirm_reconciled`, then audited as such."""
    txn = _bank_entry(db, txn_id)
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(
            status_code=400,
            detail="This entry is void; a void entry is not edited. Enter it again.",
        )
    bank_line = _bank_line(txn)
    account = bank_line.account
    if data.account_id is not None or data.bank_account_id is not None:
        wanted = _resolve_account(db, data.account_id, data.bank_account_id)
        if wanted.id != account.id:
            raise HTTPException(
                status_code=400,
                detail=f"This entry stays on {account.name}. To move it to "
                f"{wanted.name}, void it and enter it there.",
            )
    # what the request leaves out, the entry keeps (#388 review): the form
    # sends what it shows, an API client may send only what it changes
    given = data.model_fields_set
    now = _entry_detail(txn, account.id, db)
    new_date = data.date if data.date is not None else txn.date
    new_amount = data.amount if data.amount is not None else now["amount"]
    if "splits" in given or "category_account_id" in given:
        category, splits = _category_and_splits(
            db,
            account,
            SimpleNamespace(
                splits=data.splits,
                category_account_id=data.category_account_id,
                amount=new_amount,
            ),
        )
    else:
        kept = [BankSplit(**sp) for sp in now["splits"]] if now["splits"] else None
        category, splits = _category_and_splits(
            db,
            account,
            SimpleNamespace(
                splits=kept,
                category_account_id=now["category_account_id"],
                amount=new_amount,
            ),
        )
    if not splits and category.bank_kind:
        raise HTTPException(
            status_code=400,
            detail=f"{category.name} is a bank or card account, so this would be "
            "a transfer. A transfer is its own entry: void this one and post the "
            "transfer.",
        )
    check_closing_date(db, txn.date)
    check_closing_date(db, new_date)
    recon = reconciled_on(db, txn)
    if recon is not None:
        # the reconciled bank line stays as the statement has it: a new
        # amount or date would take it off that statement (or keep it on
        # one it no longer belongs to), as a void would (#388 review)
        if new_date != txn.date or _q(new_amount) != _q(now["amount"]):
            raise HTTPException(
                status_code=400,
                detail=f"This entry was reconciled on the "
                f"{date_text(recon.statement_date)} statement, so its amount "
                "and date cannot be changed, just as it cannot be voided. "
                "Correct the difference with a new entry.",
            )
    _, new_lines = bank_entry_lines(
        account,
        new_amount,
        category,
        splits=splits,
    )
    moves = sorted(
        (ln["account_id"], _q(ln["credit"] - ln["debit"]))
        for ln in new_lines
        if ln["account_id"] != account.id
    ) != _other_side(txn, account.id)
    if recon is not None and moves and not data.confirm_reconciled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reconciled",
                "message": f"This entry was reconciled on the "
                f"{date_text(recon.statement_date)} statement. Changing its "
                "category moves its amount between accounts in a period "
                "already reconciled.",
                "statement_date": recon.statement_date.isoformat(),
            },
        )

    def keep(field, value):
        return getattr(data, field) if field in given else value

    uncleared = replace_bank_entry(
        db,
        txn,
        account,
        new_date,
        new_amount,
        category,
        payee=keep("payee", now["payee"] or None),
        memo=keep("description", now["description"] or None),
        reference=keep("check_number", txn.reference or None),
        class_id=keep("class_id", txn.class_id),
        job_id=keep("job_id", txn.job_id),
        splits=splits,
    )
    if recon is not None and moves:
        # the category change on a reconciled entry says so in the trail,
        # beside the rows the hook writes for the header and each line
        log_event(
            db,
            "transactions",
            txn.id,
            "UPDATE",
            old_values={"reconciled_on": recon.statement_date.isoformat()},
            new_values={
                "confirm_reconciled": True,
                "reconciliation_id": recon.id,
                "lines_uncleared": len(uncleared),
            },
            changed_fields=["confirm_reconciled"],
        )
    db.commit()
    db.refresh(txn)
    return _entry_detail(txn, account.id, db, uncleared=uncleared)


@router.post("/entries/{txn_id}/void", response_model=BankEntryResponse)
def void_bank_entry(txn_id: int, db: Session = Depends(get_db)):
    txn = _bank_entry(db, txn_id)
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Bank entry is already void")
    bank_line = _bank_line(txn)
    void_document(db, txn, "bank_entry_void")
    db.commit()
    db.refresh(txn)
    return _entry_out(txn, bank_line.account_id, db, status="void")


# ---------------------------------------------------------------------------
# Reconciliations — over the ledger account's lines (issue #114).
# ---------------------------------------------------------------------------


@router.get("/reconciliations", response_model=list[ReconciliationResponse])
def list_reconciliations(
    account_id: int = None,
    bank_account_id: int = None,
    status: Optional[ReconciliationStatus] = None,
    db: Session = Depends(get_db),
):
    """Every reconciliation, newest statement first; `status=completed`
    keeps the finished ones (the Report Center's list, #395)."""
    q = db.query(Reconciliation)
    if account_id:
        q = q.filter(Reconciliation.account_id == account_id)
    elif bank_account_id:
        q = q.filter(Reconciliation.bank_account_id == bank_account_id)
    if status is not None:
        q = q.filter(Reconciliation.status == status)
    rows = q.order_by(
        Reconciliation.statement_date.desc(), Reconciliation.id.desc()
    ).all()
    # A completed reconciliation whose ledger account is gone (one from
    # before 2.10) has no account to name and no report — its PDF is a raw
    # 400. The accountant's package skips it (reports/package.py: rc.report
    # raises, and the loop continues), and the Report Center's cross-account
    # list does too. A per-account query is left alone: it was asked for that
    # account, and an account-less row can't match an account_id anyway.
    if account_id is None and bank_account_id is None:
        rows = [r for r in rows if r.account is not None]
    return rows


@router.post("/reconciliations", response_model=ReconciliationResponse, status_code=201)
def create_reconciliation(data: ReconciliationCreate, db: Session = Depends(get_db)):
    """Start a reconciliation (409 with existing_id when one is open)."""
    from app.services import reconciliation as rc

    account = _resolve_account(db, data.account_id, data.bank_account_id)
    recon = rc.start(db, account.id, data.statement_date, data.statement_balance)
    db.commit()
    db.refresh(recon)
    return recon


def _recon(db: Session, recon_id: int) -> Reconciliation:
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return recon


@router.put("/reconciliations/{recon_id}", response_model=ReconciliationResponse)
def update_reconciliation(
    recon_id: int, data: ReconciliationUpdate, db: Session = Depends(get_db)
):
    """Correct the statement's ending balance or date while the
    reconciliation is in progress (#389); the next session's difference
    is recomputed from the lines already ticked. 400 once completed. The
    response reports any lines left ticked past the statement date, which
    carry to the next reconciliation still ticked (#400 review)."""
    from app.services import reconciliation as rc

    fields = reject_null_updates(Reconciliation, data.model_dump(exclude_unset=True))
    recon = rc.update(
        db,
        _recon(db, recon_id),
        statement_date=fields.get("statement_date"),
        statement_balance=fields.get("statement_balance"),
    )
    db.commit()
    db.refresh(recon)
    recon.ticked_after_date = rc.ticked_after_statement_date(db, recon)
    return recon


@router.get("/reconciliations/{recon_id}/transactions")
def get_reconciliation_transactions(recon_id: int, db: Session = Depends(get_db)):
    """The ledger lines on the account up to the statement date, cleared
    or not, with the statement / beginning / cleared / difference math."""
    from app.services import reconciliation as rc

    return rc.session(db, _recon(db, recon_id))


@router.post("/reconciliations/{recon_id}/toggle/{line_id}")
def toggle_cleared(recon_id: int, line_id: int, db: Session = Depends(get_db)):
    from app.services import reconciliation as rc

    line = rc.toggle(db, _recon(db, recon_id), line_id)
    db.commit()
    return {"id": line.id, "reconciled": bool(line.cleared)}


class ReconcileMarkAll(StrictModel):
    cleared: bool = True
    through: Optional[date] = None


@router.post("/reconciliations/{recon_id}/mark-all")
def mark_all_cleared(
    recon_id: int, data: ReconcileMarkAll, db: Session = Depends(get_db)
):
    """Tick (or, with `cleared: false`, untick) every line the session
    shows, or those up to `through` (#258). Answers with how many changed
    and the session's figures, so the page shows the new difference at
    once."""
    from app.services import reconciliation as rc

    recon = _recon(db, recon_id)
    changed = rc.mark_all(db, recon, data.cleared, data.through)
    db.commit()
    return {"changed": changed, **rc.session(db, recon)}


@router.post("/reconciliations/{recon_id}/complete")
def complete_reconciliation(recon_id: int, db: Session = Depends(get_db)):
    """Finish: the difference must be zero; the cleared lines are stamped."""
    from app.services import reconciliation as rc

    out = rc.complete(db, _recon(db, recon_id))
    db.commit()
    return out


@router.get("/reconciliations/{recon_id}/report")
def reconciliation_report(recon_id: int, db: Session = Depends(get_db)):
    """A completed reconciliation's report: beginning and ending balances,
    the items it cleared, what was outstanding on the statement date."""
    from app.services import reconciliation as rc

    return rc.report(db, _recon(db, recon_id))


@router.get("/reconciliations/{recon_id}/pdf")
def reconciliation_report_pdf(recon_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import Response

    from app.services import reconciliation as rc
    from app.services.pdf_service import generate_report_pdf
    from app.services.settings_service import get_all_settings

    data = rc.report(db, _recon(db, recon_id))
    pdf = generate_report_pdf(rc.report_sections(data), get_all_settings(db))
    label = re.sub(r"[^A-Za-z0-9_-]", "", data["account_number"] or "") or str(
        data["account_id"]
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="reconciliation_{label}_{data["statement_date"]}.pdf"'
            )
        },
    )


@router.delete("/reconciliations/{recon_id}")
def abandon_reconciliation(recon_id: int, db: Session = Depends(get_db)):
    from app.services import reconciliation as rc

    rc.abandon(db, _recon(db, recon_id))
    db.commit()
    return {"status": "abandoned", "reconciliation_id": recon_id}


@router.get("/check-register")
def check_register(
    account_id: int = None,
    start_date: date = None,
    end_date: date = None,
    class_id: int = None,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """The register: every ledger line on a bank or card account, natural-
    balance running balance (a card shows the amount owed positive), with
    the posting each row came from and whether it has cleared. With dates,
    the lines in them and the balance brought forward; with `class_id`,
    only that class's lines, the balance brought forward computed under
    the same test (#236). `balance` is always the account's whole ledger
    balance."""
    if not account_id:
        acct = (
            db.query(Account)
            .filter(Account.bank_kind == "bank", Account.is_active)
            .order_by(Account.account_number)
            .first()
        )
        if not acct:
            return {"account_id": None, "account_name": "", "entries": []}
        account_id = acct.id
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    cls = None
    if class_id is not None:
        from app.models.classes import TxnClass

        cls = db.get(TxnClass, class_id)
        if cls is None:
            raise HTTPException(status_code=404, detail="Class not found")
    reg = account_register(db, account, start_date, end_date, class_id=class_id)
    entries = []
    from app.routes._roles import current_role
    from app.models.users import ROLE_ACCOUNTANT

    is_accountant = current_role(request) == ROLE_ACCOUNTANT if request else False
    for e in reg["entries"]:
        row = dict(e)
        row["payment"] = e["credit"] if e["credit"] > 0 else 0
        row["deposit"] = e["debit"] if e["debit"] > 0 else 0
        row["balance"] = e["running_balance"]
        row["voidable"] = (
            e["source_type"] in ("bank_entry", "transfer", "cc_charge")
            and not e["voided"]
            and e["reconciliation_id"] is None
        )
        # a register entry, a hand-entered journal entry and a deposit open
        # for editing (#388), a reconciled one too (its amount and date stay
        # as they are); a void one does not. The row's editor follows its
        # source: the + Entry form, the journal form, the deposit form. The
        # accountant's sign-in does not edit journal entries (#340, #388),
        # so a manual row is not editable it; a bank entry and a deposit keep
        # #408's behaviour (editable as before, the write-action gating hides
        # the button client-side).
        row["editable"] = (
            e["source_type"] in ("bank_entry", "manual", "deposit")
            and not e["voided"]
            and not (is_accountant and e["source_type"] == "manual")
        )
        # a check written here or as an expense prints (discussion #354):
        # money out of a bank account, not a card, not void
        row["printable"] = (
            e["source_type"] in ("bank_entry", "expense")
            and account.bank_kind == "bank"
            and e["credit"] > 0
            and not e["voided"]
        )
        entries.append(row)
    return {
        "account_id": account.id,
        "account_name": account.name,
        "account_number": account.account_number,
        "bank_kind": account.bank_kind,
        "natural_balance": reg["account"]["natural_balance"],
        "opening_balance": reg["opening_balance"],
        "balance": float(gl_balance(db, account.id)),
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "class_id": cls.id if cls else None,
        "class_name": cls.name if cls else None,
        "entries": entries,
    }
