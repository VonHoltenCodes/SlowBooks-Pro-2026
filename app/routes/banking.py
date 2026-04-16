# ============================================================================
# Decompiled from qbw32.exe!CBankManager + CReconcileEngine
# Offset: 0x001E7200 (BankAcct) / 0x001F0400 (Reconcile)
# The reconciliation engine was CReconcileEngine::ComputeDifference() at
# 0x001F0890. Toggle cleared items, then validate sum matches statement.
# ============================================================================

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount, BankTransaction, Reconciliation, ReconciliationStatus
from app.models.transactions import Transaction, TransactionLine
from app.schemas.banking import (
    BankAccountCreate, BankAccountUpdate, BankAccountResponse,
    BankTransactionCreate, BankTransactionResponse,
    ReconciliationCreate, ReconciliationResponse,
)
from app.services.closing_date import check_closing_date
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/banking", tags=["banking"])


# Bank Accounts
@router.get("/accounts", response_model=list[BankAccountResponse])
def list_bank_accounts(db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    return db.query(BankAccount).filter(BankAccount.is_active == True).order_by(BankAccount.name).all()


@router.get("/accounts/{account_id}", response_model=BankAccountResponse)
def get_bank_account(account_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return ba


@router.post("/accounts", response_model=BankAccountResponse, status_code=201)
def create_bank_account(data: BankAccountCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    ba = BankAccount(**data.model_dump())
    db.add(ba)
    db.commit()
    db.refresh(ba)
    return ba


@router.put("/accounts/{account_id}", response_model=BankAccountResponse)
def update_bank_account(account_id: int, data: BankAccountUpdate, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    ba = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(ba, key, val)
    db.commit()
    db.refresh(ba)
    return ba


# Bank Transactions
@router.get("/transactions", response_model=list[BankTransactionResponse])
def list_bank_transactions(bank_account_id: int = None, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    q = db.query(BankTransaction)
    if bank_account_id:
        q = q.filter(BankTransaction.bank_account_id == bank_account_id)
    return q.order_by(BankTransaction.date.desc()).all()


@router.post("/transactions", response_model=BankTransactionResponse, status_code=201)
def create_bank_transaction(data: BankTransactionCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    check_closing_date(db, data.date)
    ba = db.query(BankAccount).filter(BankAccount.id == data.bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")

    txn = BankTransaction(**data.model_dump())
    ba.balance += data.amount
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# Reconciliations — CReconcileEngine @ 0x001F0400
@router.get("/reconciliations", response_model=list[ReconciliationResponse])
def list_reconciliations(bank_account_id: int = None, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    q = db.query(Reconciliation)
    if bank_account_id:
        q = q.filter(Reconciliation.bank_account_id == bank_account_id)
    return q.order_by(Reconciliation.statement_date.desc()).all()


@router.post("/reconciliations", response_model=ReconciliationResponse, status_code=201)
def create_reconciliation(data: ReconciliationCreate, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    """Start a reconciliation — CReconcileEngine::Begin() @ 0x001F0500"""
    ba = db.query(BankAccount).filter(BankAccount.id == data.bank_account_id).first()
    if not ba:
        raise HTTPException(status_code=404, detail="Bank account not found")
    recon = Reconciliation(**data.model_dump())
    db.add(recon)
    db.commit()
    db.refresh(recon)
    return recon


@router.get("/reconciliations/{recon_id}/transactions")
def get_reconciliation_transactions(recon_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    """Get unreconciled transactions for this bank account"""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == recon.bank_account_id)
        .filter(BankTransaction.date <= recon.statement_date)
        .order_by(BankTransaction.date)
        .all()
    )

    cleared_total = sum(float(t.amount) for t in txns if t.reconciled)
    uncleared_total = sum(float(t.amount) for t in txns if not t.reconciled)
    statement_bal = float(recon.statement_balance)
    difference = statement_bal - cleared_total

    return {
        "reconciliation_id": recon.id,
        "statement_balance": statement_bal,
        "cleared_total": cleared_total,
        "uncleared_total": uncleared_total,
        "difference": difference,
        "transactions": [
            {
                "id": t.id,
                "date": t.date.isoformat(),
                "payee": t.payee or "",
                "description": t.description or "",
                "amount": float(t.amount),
                "check_number": t.check_number,
                "reconciled": t.reconciled,
            }
            for t in txns
        ],
    }


@router.post("/reconciliations/{recon_id}/toggle/{txn_id}")
def toggle_cleared(recon_id: int, txn_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    """Toggle a transaction's cleared status — CReconcileEngine::ToggleItem()"""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Reconciliation already completed")

    txn = db.query(BankTransaction).filter(BankTransaction.id == txn_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn.reconciled = not txn.reconciled
    db.commit()
    return {"id": txn.id, "reconciled": txn.reconciled}


@router.get("/check-register")
def check_register(account_id: int = None, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.view"))):
    if not account_id:
        account = db.query(Account).filter(Account.account_type == AccountType.ASSET, Account.is_active == True).order_by(Account.account_number, Account.name).first()
    else:
        account = db.query(Account).filter(Account.id == account_id, Account.is_active == True).first()
    if not account:
        return {"account_id": None, "account_name": "", "account_number": "", "starting_balance": 0, "entries": []}
    if account.account_type != AccountType.ASSET:
        raise HTTPException(status_code=400, detail="Check register requires an asset account")

    rows = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == account.id)
        .order_by(Transaction.date.asc(), Transaction.id.asc(), TransactionLine.id.asc())
        .all()
    )

    total_effect = sum(Decimal(str(line.debit)) - Decimal(str(line.credit)) for line, _txn in rows)
    running_balance = Decimal(str(account.balance or 0)) - total_effect
    starting_balance = running_balance
    entries = []
    for line, txn in rows:
        running_balance += Decimal(str(line.debit)) - Decimal(str(line.credit))
        entries.append({
            "transaction_id": txn.id,
            "date": txn.date.isoformat(),
            "description": txn.description or line.description or "",
            "reference": txn.reference or "",
            "source_type": txn.source_type or "",
            "payment": float(line.credit) if Decimal(str(line.credit)) > 0 else 0,
            "deposit": float(line.debit) if Decimal(str(line.debit)) > 0 else 0,
            "balance": float(running_balance),
        })

    return {
        "account_id": account.id,
        "account_name": account.name,
        "account_number": account.account_number,
        "starting_balance": float(starting_balance),
        "entries": entries,
    }


@router.post("/reconciliations/{recon_id}/complete")
def complete_reconciliation(recon_id: int, db: Session = Depends(get_db), auth=Depends(require_permissions("banking.manage"))):
    """CReconcileEngine::Finish() @ 0x001F0A00 — validates difference is 0"""
    recon = db.query(Reconciliation).filter(Reconciliation.id == recon_id).first()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Already completed")

    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.bank_account_id == recon.bank_account_id)
        .filter(BankTransaction.date <= recon.statement_date)
        .filter(BankTransaction.reconciled == True)
        .all()
    )
    cleared_total = sum(t.amount for t in txns)

    if abs(cleared_total - recon.statement_balance) > Decimal("0.01"):
        raise HTTPException(
            status_code=400,
            detail=f"Difference is ${float(recon.statement_balance - cleared_total):.2f} — must be $0.00 to complete"
        )

    recon.status = ReconciliationStatus.COMPLETED
    recon.completed_at = datetime.utcnow()
    db.commit()
    return {"status": "completed", "reconciliation_id": recon.id}
