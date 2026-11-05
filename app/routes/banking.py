# ============================================================================
# Bank accounts + reconciliation — toggle cleared items, then validate
# their sum matches the statement balance.
# ============================================================================

import re
from datetime import date
from types import SimpleNamespace
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.accounting import _q
from app.services.check_numbers import refuse_duplicate
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
    FeedAddAll,
    FeedExcludeAll,
    StatementPayout,
    StatementVendors,
    StatementCategory,
    StatementMatch,
    ReconciliationResponse,
)
from app.models.contacts import Customer, Vendor
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
from app.services.dimensions import require_dimensions
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
    from app.services.bank_matching import entry_vendors
    from app.services.bank_rules_engine import payout_suggestion
    from app.services.settings_service import get_all_settings

    ids = {r.category_account_id for r in rows if r.category_account_id}
    names = (
        {a.id: a.name for a in db.query(Account).filter(Account.id.in_(ids)).all()}
        if ids
        else {}
    )
    vendors = entry_vendors(db, rows)
    # A payout suggestion (#584) is offered only on an unmatched deposit, so
    # the settings read and the per-provider lookup stay off the common path.
    settings = get_all_settings(db)

    def _payout(r):
        if r.match_status != "unmatched" or not r.amount or r.amount <= 0:
            return None
        return payout_suggestion(db, settings, r.payee, r)

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
            "vendor_id": vendors[r.id].id if r.id in vendors else None,
            "vendor_name": vendors[r.id].name if r.id in vendors else None,
            "suggested_payout": _payout(r),
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
    filters on match_status: unmatched | auto | manual | added | excluded
    (excluded takes in the lines an import set aside as in the books too;
    in_books asks for those alone)."""
    from app.services.bank_dedupe import EXCLUDED

    skip, limit = clamp_pagination(skip, limit)
    q = db.query(BankTransaction)
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    if status == "excluded":
        q = q.filter(BankTransaction.match_status.in_(EXCLUDED))
    elif status:
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
    """Post this statement line as a register entry and link it. With
    `splits` (a provider payout, #584) the other side is several accounts
    instead of one category, validated like a register split."""
    from app.services import bank_matching as m

    data = data or StatementAdd()
    bt = _statement_line(db, txn_id)
    splits = None
    if data.splits:
        # The same conversion and total check the register uses, against
        # the statement amount; mutual exclusivity with the single category
        # is enforced in bank_matching.add.
        acct = m._feed_account(db, bt)
        _, splits = _category_and_splits(
            db,
            acct,
            SimpleNamespace(
                splits=data.splits,
                amount=bt.amount,
                category_account_id=data.category_account_id,
                customer_id=data.customer_id,
            ),
        )
    require_dimensions(db, data.class_id, data.job_id, data.customer_id)
    bt = m.add(
        db,
        bt,
        category_account_id=data.category_account_id,
        payee=data.payee,
        memo=data.memo,
        class_id=data.class_id,
        job_id=data.job_id,
        vendor_id=data.vendor_id,
        splits=splits,
        customer_id=_entry_customer(db, data.customer_id),
    )
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/{txn_id}/payout", response_model=BankTransactionResponse)
def statement_payout(txn_id: int, data: StatementPayout, db: Session = Depends(get_db)):
    """Record an unmatched deposit as a provider payout (#584). The server
    builds the split from the gross — CR the provider's clearing account for
    the gross, DR the fee account for gross − net, DR the bank for the net —
    so the account ids stay off the client; then it adds it the normal way,
    so the line links and voids/reconciliation behave like any bank entry.
    The clearing and fee accounts are created on demand here, at post time."""
    from app.services import bank_matching as m
    from app.services.payments.clearing import (
        DISPLAY_NAMES,
        PROVIDER_NAMES,
        clearing_account_id,
        clearing_balance,
        fee_account_id,
        payouts_allowed,
    )

    if data.provider not in PROVIDER_NAMES:
        raise HTTPException(
            status_code=400, detail=f"Unknown payment provider: {data.provider}"
        )
    # Allowed while clearing is on, and after it is turned off until the
    # clearing balance is paid out.
    if not payouts_allowed(db, data.provider):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{DISPLAY_NAMES[data.provider]} clearing is off and holds "
                "nothing to pay out; turn it on first"
            ),
        )
    bt = _statement_line(db, txn_id)
    net = _q(bt.amount)
    if net <= 0:
        raise HTTPException(
            status_code=400, detail="A payout is a deposit; this line is not money in"
        )
    gross = _q(data.gross)
    if gross < net:
        raise HTTPException(
            status_code=400,
            detail="The gross cleared cannot be less than the net that reached the bank",
        )
    display = DISPLAY_NAMES[data.provider]
    # A payout recorded by hand (Record payout) already sits in the ledger
    # as a match for this deposit, however late the feed line arrives.
    from app.services import bank_matching as _m

    if _m.candidate_lines(db, _m._feed_account(db, bt).id, bt.amount, bt.date):
        raise HTTPException(
            status_code=400,
            detail=(
                "This deposit matches an entry already in your books, perhaps a "
                "payout you recorded by hand. Use Match instead of posting it twice."
            ),
        )
    # Clearing holds the net of everything not yet paid out; a bigger gross
    # would book the difference as fees.
    held = clearing_balance(db, data.provider)
    if gross > held:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The gross ({gross:,.2f}) is more than {display} Clearing holds "
                f"({held:,.2f}). Enter the net that reached the bank, or the "
                "gross the provider actually cleared."
            ),
        )
    clearing = (
        db.query(Account)
        .filter(Account.id == clearing_account_id(db, data.provider))
        .first()
    )
    splits = [(clearing, gross, f"{display} payout")]
    fee = gross - net
    if fee > 0:
        fee_acct = (
            db.query(Account)
            .filter(Account.id == fee_account_id(db, data.provider))
            .first()
        )
        splits.append((fee_acct, -fee, f"{display} payout fee"))
    bt = m.add(db, bt, payee=f"{display} payout", splits=splits)
    db.commit()
    db.refresh(bt)
    return _statement_out([bt], db)[0]


@router.post("/transactions/vendors")
def statement_vendors(data: StatementVendors, db: Session = Depends(get_db)):
    """Make the payees of these added feed lines vendors, or link them to
    the vendor of that name, and name it on their entries (#491)."""
    from app.services import bank_matching as m

    out = m.attach_vendors(db, data.line_ids)
    db.commit()
    return out


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
def feed_add_all(
    account_id: int, data: FeedAddAll = None, db: Session = Depends(get_db)
):
    """Add every unmatched line that already carries a category, each with
    the class, job and vendor or customer its review row chose (#671)."""
    from app.services import bank_matching as m

    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    picks = {}
    for p in (data.lines if data else []):
        # a class or job a row picked must exist and be live, as one Add
        # checks; refused before anything posts (2.31.4 gate, W-95)
        require_dimensions(db, p.class_id, p.job_id, p.customer_id)
        picks[p.id] = {
            "class_id": p.class_id,
            "job_id": p.job_id,
            "vendor_id": p.vendor_id,
            "customer_id": _entry_customer(db, p.customer_id),
            "payee": p.payee or None,
        }
    out = m.add_all(db, ba, picks)
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
    # a line whose posting another statement line already holds (the same
    # month imported again in another format) is in the books (#705)
    from app.services.bank_dedupe import set_aside_already_in_books

    in_books = set_aside_already_in_books(db, ba, rows)
    db.commit()
    return {"matched": n, "already_in_books": in_books}


@router.post("/accounts/{account_id}/feed/exclude-all")
def feed_exclude_all(
    account_id: int, data: FeedExcludeAll, db: Session = Depends(get_db)
):
    """Exclude every statement line the review list shows (#705): the ids
    it sends, each one this feed's and still waiting for review. A line
    matched or added meanwhile is left alone. Returns how many."""
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    rows = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == ba.id,
            BankTransaction.id.in_(data.line_ids or [-1]),
            BankTransaction.match_status == "unmatched",
            BankTransaction.transaction_line_id.is_(None),
        )
        .all()
    )
    for bt in rows:
        bt.match_status = "excluded"
    db.commit()
    return {"excluded": len(rows)}


EXCLUDED_WHY = {
    "excluded": "excluded by you",
    "in_books": "already in the books: same date and amount as a matched line",
}


@router.get("/accounts/{account_id}/feed/excluded")
def feed_excluded(account_id: int, db: Session = Depends(get_db)):
    """The feed's excluded statement lines, for the review list's Show
    excluded (W-116): each with why it is out of To review, the user's
    Exclude (one, or Exclude all shown) or an import's "already in the
    books". Restore (POST /transactions/{id}/restore) brings one back.
    Newest first; `count` is how many."""
    from app.services.bank_dedupe import EXCLUDED

    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    rows = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.bank_account_id == ba.id,
            BankTransaction.match_status.in_(EXCLUDED),
        )
        .order_by(BankTransaction.date.desc(), BankTransaction.id.desc())
        .all()
    )
    lines = [
        {
            "id": r.id,
            "date": r.date.isoformat(),
            "payee": r.payee,
            "description": r.description,
            "amount": str(r.amount),
            "reason": r.match_status,
            "reason_text": EXCLUDED_WHY[r.match_status],
        }
        for r in rows
    ]
    return {"count": len(lines), "lines": lines}


def _entry_vendor(db: Session, vendor_id, payee):
    """The vendor a register entry is paid to (#590): the one asked for
    (404 when there is none), else the vendor whose name is exactly the
    payee typed, else None, and the payee stays text."""
    from app.services.bank_matching import vendor_named

    if vendor_id is not None:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        return vendor
    return vendor_named(db, payee)


def _link_vendor(txn: Transaction, vendor) -> None:
    """Name the vendor on the entry. A transfer between two bank or card
    accounts is not paid to anyone, so it takes none."""
    wanted = (
        vendor.id if vendor is not None and txn.source_type == "bank_entry" else None
    )
    if txn.vendor_id != wanted:  # a save that changes nothing writes nothing
        txn.vendor_id = wanted


def _vendor_name(db: Session, vendor_id):
    vendor = db.query(Vendor).get(vendor_id) if vendor_id else None
    return vendor.name if vendor else None


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
        "vendor_id": txn.vendor_id,
        "vendor_name": _vendor_name(db, txn.vendor_id),
        **_one_customer(txn, account_id, db),
    }


def _one_customer(txn, account_id: int, db: Session) -> dict:
    """The name on the entry's single income line (#606); an entry of
    several lines says each line's own in its splits."""
    others = [ln for ln in txn.lines if ln.account_id != account_id]
    cid = others[0].customer_id if len(others) == 1 else None
    return {"customer_id": cid, "customer_name": _customer_name(db, cid)}


def _customer_name(db: Session, customer_id):
    customer = db.get(Customer, customer_id) if customer_id else None
    return customer.name if customer else None


def _entry_customer(db: Session, customer_id, own=()):
    """A customer (donor) named on an entry or one of its lines (#606): it
    must exist and be active, unless the entry already carries it (so a
    memo fix on an old entry never trips over a donor made inactive since)."""
    if customer_id is None:
        return None
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.is_active and customer_id not in own:
        raise HTTPException(
            status_code=422, detail=f"{customer.name} is inactive; pick another name"
        )
    return customer_id


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


def _category_and_splits(
    db: Session, account: Account, data, own_customers=()
) -> tuple:
    """The other side of a register entry as the request names it: the
    category account, and the split lines ([(account, amount, memo, dims), …])
    when there are any — the same checks for a new entry and an edit. A
    line's name is its own, else the entry's (#606)."""
    splits = None
    default_customer = _entry_customer(
        db, getattr(data, "customer_id", None), own_customers
    )
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
                    {
                        "class_id": part.class_id,
                        "job_id": part.job_id,
                        "customer_id": _entry_customer(
                            db, part.customer_id, own_customers
                        )
                        or default_customer,
                    },
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
def create_bank_transaction(
    data: BankTransactionCreate,
    db: Session = Depends(get_db),
    allow_duplicate_check_number: bool = Query(False),
):
    """A register entry posts to the ledger (issue #114): DR category /
    CR account for money out, the reverse for money in; a bank or card
    category makes it a transfer. A payment whose check number the account
    already has is a 409 the person answers (#481): keep the number (send
    it again with allow_duplicate_check_number=true) or edit it."""
    check_closing_date(db, data.date)
    account = _resolve_account(db, data.account_id, data.bank_account_id)
    category, splits = _category_and_splits(db, account, data)
    vendor = _entry_vendor(db, data.vendor_id, data.payee)
    customer_id = _entry_customer(db, data.customer_id)
    require_dimensions(db, data.class_id, data.job_id, customer_id)
    if data.amount < 0:
        refuse_duplicate(
            db, account.id, data.check_number, allow=allow_duplicate_check_number
        )
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
        customer_id=None if splits else customer_id,
    )
    _link_vendor(txn, vendor)
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


def _gift_letters(db: Session, txn, status: str) -> list[dict]:
    """The entry's income lines that name a donor, for the acknowledgment
    letter buttons (#606); none on a void entry."""
    from app.services.donor_documents import NotAGift, gift_from_bank_line

    out = []
    for ln in txn.lines:
        if status == "void" or not ln.customer_id:
            continue
        try:
            gift = gift_from_bank_line(db, ln, txn)
        except NotAGift:
            continue
        out.append(
            {
                "line_id": ln.id,
                "customer_name": _customer_name(db, ln.customer_id),
                "amount": str(gift["amount"]),
            }
        )
    return out


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
                "customer_id": ln.customer_id,
            }
            for ln in others
        ]
        if len(others) > 1
        else None
    )
    out["gift_letters"] = _gift_letters(db, txn, status)
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
    txn_id: int,
    data: BankEntryUpdate,
    db: Session = Depends(get_db),
    allow_duplicate_check_number: bool = Query(False),
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
    # the names the entry already carries (#606): kept without a new check
    own_customers = {ln.customer_id for ln in txn.lines if ln.customer_id}
    if "splits" in given or "category_account_id" in given:
        category, splits = _category_and_splits(
            db,
            account,
            SimpleNamespace(
                splits=data.splits,
                category_account_id=data.category_account_id,
                amount=new_amount,
                customer_id=data.customer_id,
            ),
            own_customers,
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
            own_customers,
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

    # a new check number on a payment is checked as a new entry's is (#481);
    # an entry keeping its own number is not asked about it again
    new_ref = keep("check_number", txn.reference or None)
    if _q(new_amount) < 0 and (new_ref or "").strip() != (txn.reference or "").strip():
        refuse_duplicate(
            db,
            account.id,
            new_ref,
            allow=allow_duplicate_check_number,
            exclude_txn_id=txn.id,
        )

    # the vendor (#590): the one named, or none when null; else, when the
    # payee text changed, the vendor it now exactly names; else unchanged
    new_payee = keep("payee", now["payee"] or None)
    relink = "vendor_id" in given or (new_payee or "") != (now["payee"] or "")
    vendor = (
        _entry_vendor(db, data.vendor_id, None if "vendor_id" in given else new_payee)
        if relink
        else None
    )

    # the name on a plain entry's income line (#606): the one sent (null
    # clears it), else the one it has
    new_customer = _entry_customer(
        db,
        data.customer_id if "customer_id" in given else now["customer_id"],
        own_customers,
    )
    uncleared = replace_bank_entry(
        db,
        txn,
        account,
        new_date,
        new_amount,
        category,
        payee=new_payee,
        memo=keep("description", now["description"] or None),
        reference=keep("check_number", txn.reference or None),
        class_id=keep("class_id", txn.class_id),
        job_id=keep("job_id", txn.job_id),
        splits=splits,
        customer_id=None if splits else new_customer,
    )
    if relink:
        _link_vendor(txn, vendor)
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
    from app.services.settings_service import get_all_settings, use_account_numbers

    data = rc.report(db, _recon(db, recon_id))
    pdf = generate_report_pdf(
        rc.report_sections(data, use_account_numbers(db)), get_all_settings(db)
    )
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


# The reconciliation report as a page that opens the print dialog (#683):
# the PDF route above, answering the HTML its PDF is rendered from.
@router.get("/reconciliations/{recon_id}/print", response_class=HTMLResponse)
def reconciliation_report_print(recon_id: int, db: Session = Depends(get_db)):
    from app.routes._print import print_page

    return print_page(reconciliation_report_pdf)(recon_id=recon_id, db=db)


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
