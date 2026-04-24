# ============================================================================
# Manual Journal Entries — CRUD for hand-entered journal entries
# Feature: Allow users to create/view/void manual journal entries
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.transactions import Transaction, TransactionLine
from app.models.accounts import Account
from app.schemas.journal import JournalEntryCreate, JournalEntryResponse
from app.services.accounting import create_journal_entry
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/journal", tags=["journal"])


@router.get("", response_model=list[JournalEntryResponse])
def list_journal_entries(source_type: str = None, db: Session = Depends(get_db)):
    q = db.query(Transaction)
    if source_type:
        q = q.filter(Transaction.source_type == source_type)
    else:
        q = q.filter(Transaction.source_type == "manual")
    entries = q.order_by(Transaction.date.desc()).all()
    accounts = {a.id: a for a in db.query(Account).all()}
    results = []
    for txn in entries:
        lines_data = []
        for line in txn.lines:
            acct = accounts.get(line.account_id)
            lines_data.append({
                "id": line.id,
                "account_id": line.account_id,
                "account_name": acct.name if acct else "",
                "account_number": acct.account_number if acct else "",
                "debit": float(line.debit),
                "credit": float(line.credit),
                "description": line.description or "",
            })
        results.append(JournalEntryResponse(
            id=txn.id,
            date=txn.date,
            description=txn.description or "",
            reference=txn.reference or "",
            source_type=txn.source_type or "",
            lines=lines_data,
            total_debit=sum(l["debit"] for l in lines_data),
            total_credit=sum(l["credit"] for l in lines_data),
        ))
    return results


@router.get("/{entry_id}", response_model=JournalEntryResponse)
def get_journal_entry(entry_id: int, db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    accounts = {a.id: a for a in db.query(Account).all()}
    lines_data = []
    for line in txn.lines:
        acct = accounts.get(line.account_id)
        lines_data.append({
            "id": line.id,
            "account_id": line.account_id,
            "account_name": acct.name if acct else "",
            "account_number": acct.account_number if acct else "",
            "debit": float(line.debit),
            "credit": float(line.credit),
            "description": line.description or "",
        })
    return JournalEntryResponse(
        id=txn.id,
        date=txn.date,
        description=txn.description or "",
        reference=txn.reference or "",
        source_type=txn.source_type or "",
        lines=lines_data,
        total_debit=sum(l["debit"] for l in lines_data),
        total_credit=sum(l["credit"] for l in lines_data),
    )


@router.post("", response_model=JournalEntryResponse, status_code=201)
def create_manual_journal_entry(data: JournalEntryCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    lines = []
    for line in data.lines:
        if line.debit == 0 and line.credit == 0:
            continue
        acct = db.query(Account).filter(Account.id == line.account_id).first()
        if not acct:
            raise HTTPException(status_code=404, detail=f"Account {line.account_id} not found")
        lines.append({
            "account_id": line.account_id,
            "debit": Decimal(str(line.debit)),
            "credit": Decimal(str(line.credit)),
            "description": line.description or "",
        })

    if not lines:
        raise HTTPException(status_code=400, detail="No valid lines")

    try:
        txn = create_journal_entry(
            db, data.date, data.description,
            lines, source_type="manual",
            reference=data.reference or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(txn)
    return get_journal_entry(txn.id, db)


@router.post("/{entry_id}/void", response_model=JournalEntryResponse)
def void_journal_entry(entry_id: int, db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.id == entry_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if txn.source_type and txn.source_type.endswith("_void"):
        raise HTTPException(status_code=400, detail="Cannot void a reversal entry")

    check_closing_date(db, txn.date)

    reverse_lines = [
        {"account_id": ol.account_id, "debit": ol.credit, "credit": ol.debit,
         "description": f"VOID: {ol.description or ''}"}
        for ol in txn.lines
    ]
    if reverse_lines:
        void_txn = create_journal_entry(
            db, txn.date, f"VOID: {txn.description or ''}",
            reverse_lines, source_type="manual_void", source_id=txn.id,
            reference=txn.reference,
        )
        db.commit()
        db.refresh(void_txn)
        return get_journal_entry(void_txn.id, db)

    raise HTTPException(status_code=400, detail="No lines to reverse")
