# ============================================================================
# Transfers between bank and credit-card accounts: DR to / CR from.
# The way a card gets paid (issue #114).
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.transactions import Transaction
from app.schemas.transfers import TransferCreate, TransferResponse
from app.services.bank_posting import post_transfer, require_bank_account, void_document
from app.services.bank_register import voided_transaction_ids
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

SOURCE_TYPE = "transfer"
VOID_SOURCE_TYPE = "transfer_void"


def _out(txn: Transaction, voided: bool, names: dict) -> dict:
    debit = next(ln for ln in txn.lines if ln.debit > 0)
    credit = next(ln for ln in txn.lines if ln.credit > 0)
    desc = txn.description or ""
    memo = desc.split(" — ", 1)[1] if " — " in desc else ""
    return {
        "id": txn.id,
        "date": txn.date,
        "from_account_id": credit.account_id,
        "from_account_name": names.get(credit.account_id, ""),
        "to_account_id": debit.account_id,
        "to_account_name": names.get(debit.account_id, ""),
        "amount": debit.debit,
        "memo": memo,
        "reference": txn.reference or "",
        "status": "void" if voided else "recorded",
    }


def _names(db: Session, txns) -> dict:
    ids = {ln.account_id for t in txns for ln in t.lines}
    if not ids:
        return {}
    return {a.id: a.name for a in db.query(Account).filter(Account.id.in_(ids)).all()}


@router.get("", response_model=list[TransferResponse])
def list_transfers(db: Session = Depends(get_db)):
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    voided = voided_transaction_ids(db, [t.id for t in txns])
    names = _names(db, txns)
    return [_out(t, t.id in voided, names) for t in txns]


@router.post("", response_model=TransferResponse, status_code=201)
def create_transfer(data: TransferCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    src = require_bank_account(db, data.from_account_id)
    dst = require_bank_account(db, data.to_account_id)
    txn = post_transfer(
        db,
        data.date,
        src,
        dst,
        data.amount,
        data.memo,
        data.reference,
        class_id=data.class_id,
    )
    db.commit()
    db.refresh(txn)
    return _out(txn, False, _names(db, [txn]))


@router.post("/{transfer_id}/void", response_model=TransferResponse)
def void_transfer(transfer_id: int, db: Session = Depends(get_db)):
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == transfer_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Transfer is already void")
    void_document(db, txn, VOID_SOURCE_TYPE)
    db.commit()
    db.refresh(txn)
    return _out(txn, True, _names(db, [txn]))
