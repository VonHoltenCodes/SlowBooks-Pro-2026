# ============================================================================
# Bank accounts + reconciliation — toggle cleared items, then validate
# their sum matches the statement balance.
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.models.accounts import Account
from app.models.banking import (
    BankAccount,
    BankTransaction,
    Reconciliation,
    ReconciliationStatus,
)
from app.schemas.banking import (
    BankAccountCreate,
    BankAccountUpdate,
    BankAccountResponse,
    BankEntryResponse,
    BankTransactionCreate,
    BankTransactionResponse,
    LegacyBalancePost,
    ReconciliationCreate,
    StatementAdd,
    StatementCategory,
    StatementMatch,
    ReconciliationResponse,
)
from app.models.transactions import Transaction
from app.services.bank_posting import (
    post_bank_entry,
    post_opening_balance,
    post_statement_balance,
    require_bank_account,
    void_document,
)
from app.services.bank_register import (
    account_register,
    balance_as_of,
    gl_balance,
    gl_balances,
    voided_transaction_ids,
)
from app.services.closing_date import check_closing_date

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
    fields = data.model_dump(exclude_unset=True)
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


@router.post("/transactions", response_model=BankEntryResponse, status_code=201)
def create_bank_transaction(data: BankTransactionCreate, db: Session = Depends(get_db)):
    """A register entry posts to the ledger (issue #114): DR category /
    CR account for money out, the reverse for money in; a bank or card
    category makes it a transfer."""
    check_closing_date(db, data.date)
    account = _resolve_account(db, data.account_id, data.bank_account_id)
    category = db.query(Account).filter(Account.id == data.category_account_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category account not found")
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
    )
    db.commit()
    db.refresh(txn)
    return _entry_out(txn, account.id, db)


@router.post("/entries/{txn_id}/void", response_model=BankEntryResponse)
def void_bank_entry(txn_id: int, db: Session = Depends(get_db)):
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == txn_id, Transaction.source_type == "bank_entry")
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Bank entry not found")
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Bank entry is already void")
    bank_line = next(
        (ln for ln in txn.lines if ln.account and ln.account.bank_kind), txn.lines[0]
    )
    void_document(db, txn, "bank_entry_void")
    db.commit()
    db.refresh(txn)
    return _entry_out(txn, bank_line.account_id, db, status="void")


# ---------------------------------------------------------------------------
# Reconciliations — over the ledger account's lines (issue #114).
# ---------------------------------------------------------------------------


@router.get("/reconciliations", response_model=list[ReconciliationResponse])
def list_reconciliations(
    account_id: int = None, bank_account_id: int = None, db: Session = Depends(get_db)
):
    q = db.query(Reconciliation)
    if account_id:
        q = q.filter(Reconciliation.account_id == account_id)
    elif bank_account_id:
        q = q.filter(Reconciliation.bank_account_id == bank_account_id)
    return q.order_by(
        Reconciliation.statement_date.desc(), Reconciliation.id.desc()
    ).all()


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


@router.post("/reconciliations/{recon_id}/complete")
def complete_reconciliation(recon_id: int, db: Session = Depends(get_db)):
    """Finish: the difference must be zero; the cleared lines are stamped."""
    from app.services import reconciliation as rc

    out = rc.complete(db, _recon(db, recon_id))
    db.commit()
    return out


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
    db: Session = Depends(get_db),
):
    """The register: every ledger line on a bank or card account, natural-
    balance running balance (a card shows the amount owed positive), with
    the posting each row came from and whether it has cleared."""
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

    reg = account_register(db, account, start_date, end_date)
    entries = []
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
        entries.append(row)
    return {
        "account_id": account.id,
        "account_name": account.name,
        "account_number": account.account_number,
        "bank_kind": account.bank_kind,
        "natural_balance": reg["account"]["natural_balance"],
        "opening_balance": reg["opening_balance"],
        "balance": float(gl_balance(db, account.id)),
        "entries": entries,
    }
