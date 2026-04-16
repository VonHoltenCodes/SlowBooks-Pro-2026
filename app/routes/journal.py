from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.transactions import Transaction
from app.schemas.journal import JournalEntryCreate, JournalEntryResponse, JournalLineResponse
from app.services.accounting import create_journal_entry, reverse_journal_entry
from app.services.auth import require_permissions
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _is_voided(db: Session, txn_id: int) -> bool:
    return db.query(Transaction).filter(Transaction.source_type == "manual_journal_void", Transaction.source_id == txn_id).first() is not None


def _journal_response(db: Session, txn: Transaction) -> JournalEntryResponse:
    lines = []
    for line in txn.lines:
        account = db.query(Account).filter(Account.id == line.account_id).first()
        lines.append(JournalLineResponse(
            account_id=line.account_id,
            account_name=account.name if account else None,
            account_number=account.account_number if account else None,
            debit=line.debit,
            credit=line.credit,
            description=line.description,
        ))
    return JournalEntryResponse(
        id=txn.id,
        date=txn.date,
        description=txn.description,
        reference=txn.reference,
        source_type=txn.source_type,
        is_voided=_is_voided(db, txn.id),
        lines=lines,
        created_at=txn.created_at,
    )


@router.get("", response_model=list[JournalEntryResponse])
def list_journal_entries(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("accounts.manage")),
):
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == "manual_journal")
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    return [_journal_response(db, txn) for txn in txns]


@router.get("/{journal_id}", response_model=JournalEntryResponse)
def get_journal_entry(
    journal_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("accounts.manage")),
):
    txn = db.query(Transaction).filter(Transaction.id == journal_id, Transaction.source_type == "manual_journal").first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return _journal_response(db, txn)


@router.post("", response_model=JournalEntryResponse, status_code=201)
def create_manual_journal(
    data: JournalEntryCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("accounts.manage")),
):
    check_closing_date(db, data.date)
    if len(data.lines) < 2:
        raise HTTPException(status_code=400, detail="At least two lines are required")

    lines = []
    for line in data.lines:
        debit = Decimal(str(line.debit or 0))
        credit = Decimal(str(line.credit or 0))
        if debit < 0 or credit < 0:
            raise HTTPException(status_code=400, detail="Journal amounts must be positive")
        if (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
            raise HTTPException(status_code=400, detail="Each line must contain either a debit or a credit")
        account = db.query(Account).filter(Account.id == line.account_id, Account.is_active == True).first()
        if not account:
            raise HTTPException(status_code=404, detail=f"Account {line.account_id} not found")
        lines.append({
            "account_id": line.account_id,
            "debit": debit,
            "credit": credit,
            "description": line.description or "",
        })

    try:
        txn = create_journal_entry(
            db,
            data.date,
            data.description,
            lines,
            source_type="manual_journal",
            reference=data.reference or "",
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    db.commit()
    db.refresh(txn)
    return _journal_response(db, txn)


@router.post("/{journal_id}/void", response_model=JournalEntryResponse)
def void_manual_journal(
    journal_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("accounts.manage")),
):
    txn = db.query(Transaction).filter(Transaction.id == journal_id, Transaction.source_type == "manual_journal").first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if _is_voided(db, txn.id):
        raise HTTPException(status_code=400, detail="Journal entry already voided")

    check_closing_date(db, txn.date)
    reverse_journal_entry(
        db,
        txn.id,
        txn.date,
        f"VOID Journal Entry #{txn.id}",
        source_type="manual_journal_void",
        source_id=txn.id,
        reference=txn.reference or str(txn.id),
    )
    db.commit()
    db.refresh(txn)
    return _journal_response(db, txn)
