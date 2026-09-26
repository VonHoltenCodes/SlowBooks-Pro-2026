"""Reconciliation over the ledger's lines (issue #114): the statement's
ending balance against the bank account's cleared lines, with the prior
completed statement as the beginning balance. A matched statement line
arrives already cleared; a completed reconciliation stamps its lines so a
void cannot undo a closed month.
"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.banking import BankTransaction, Reconciliation, ReconciliationStatus
from app.models.transactions import Transaction, TransactionLine
from app.services.bank_posting import require_bank_account
from app.services.bank_register import (
    is_debit_normal,
    payees_for,
    references_for,
    source_link,
)

TOLERANCE = Decimal("0.005")


def start(
    db: Session, account_id: int, statement_date: date, statement_balance: Decimal
) -> Reconciliation:
    acct = require_bank_account(db, account_id)
    open_one = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.account_id == acct.id,
            Reconciliation.status == ReconciliationStatus.IN_PROGRESS,
        )
        .first()
    )
    if open_one:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A reconciliation is already in progress for this account",
                "existing_id": open_one.id,
            },
        )
    last = (
        db.query(Reconciliation)
        .filter(
            Reconciliation.account_id == acct.id,
            Reconciliation.status == ReconciliationStatus.COMPLETED,
        )
        .order_by(Reconciliation.statement_date.desc(), Reconciliation.id.desc())
        .first()
    )
    recon = Reconciliation(
        account_id=acct.id,
        statement_date=statement_date,
        statement_balance=Decimal(str(statement_balance)),
        beginning_balance=(
            Decimal(str(last.statement_balance)) if last else Decimal("0")
        ),
        status=ReconciliationStatus.IN_PROGRESS,
    )
    db.add(recon)
    db.flush()
    return recon


def _candidates(db: Session, recon: Reconciliation):
    return (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.account_id == recon.account_id)
        .filter(Transaction.date <= recon.statement_date)
        .filter(
            (TransactionLine.reconciliation_id.is_(None))
            | (TransactionLine.reconciliation_id == recon.id)
        )
        .order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .all()
    )


def session(db: Session, recon: Reconciliation) -> dict:
    acct = db.query(Account).filter(Account.id == recon.account_id).first()
    if not acct:
        raise HTTPException(
            status_code=400,
            detail="This reconciliation predates 2.10 and has no ledger account",
        )
    debit_normal = is_debit_normal(acct)
    rows = _candidates(db, recon)
    matched_lines = {
        r[0]
        for r in db.query(BankTransaction.transaction_line_id)
        .filter(
            BankTransaction.transaction_line_id.in_([tl.id for tl, _ in rows] or [0])
        )
        .all()
    }
    payees = payees_for(db, [txn for _, txn in rows])
    refs = references_for(db, list({txn.id: txn for _, txn in rows}.values()))
    cleared_total = Decimal("0")
    uncleared_total = Decimal("0")
    out_rows = []
    for tl, txn in rows:
        dr = Decimal(str(tl.debit or 0))
        cr = Decimal(str(tl.credit or 0))
        amount = (dr - cr) if debit_normal else (cr - dr)
        if tl.cleared:
            cleared_total += amount
        else:
            uncleared_total += amount
        out_rows.append(
            {
                "id": tl.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "payee": payees.get(txn.id, ""),
                "description": txn.description or tl.description or "",
                "reference": refs.get(txn.id, ""),
                "check_number": refs.get(txn.id) or None,
                "amount": float(amount),
                "reconciled": bool(tl.cleared),
                "matched": tl.id in matched_lines,
                "source_type": txn.source_type,
                "source_link": source_link(txn),
            }
        )
    statement = Decimal(str(recon.statement_balance or 0))
    beginning = Decimal(str(recon.beginning_balance or 0))
    difference = statement - (beginning + cleared_total)
    return {
        "reconciliation_id": recon.id,
        "account_id": recon.account_id,
        "statement_date": recon.statement_date.isoformat(),
        "statement_balance": float(statement),
        "beginning_balance": float(beginning),
        "cleared_total": float(cleared_total),
        "uncleared_total": float(uncleared_total),
        "difference": float(difference),
        "status": recon.status.value,
        "transactions": out_rows,
    }


def _open(recon: Reconciliation) -> None:
    if recon.status == ReconciliationStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Reconciliation already completed")


def toggle(db: Session, recon: Reconciliation, line_id: int) -> TransactionLine:
    _open(recon)
    line = db.query(TransactionLine).filter(TransactionLine.id == line_id).first()
    if not line:
        raise HTTPException(status_code=404, detail="Ledger line not found")
    if line.account_id != recon.account_id:
        raise HTTPException(status_code=400, detail="That line is not on this account")
    if line.reconciliation_id and line.reconciliation_id != recon.id:
        raise HTTPException(
            status_code=400, detail="That line is in a completed reconciliation"
        )
    txn = db.query(Transaction).filter(Transaction.id == line.transaction_id).first()
    if txn.date > recon.statement_date:
        raise HTTPException(
            status_code=400, detail="That line is after the statement date"
        )
    line.cleared = not line.cleared
    return line


def complete(db: Session, recon: Reconciliation) -> dict:
    _open(recon)
    data = session(db, recon)
    difference = Decimal(str(data["difference"]))
    if abs(difference) > TOLERANCE:
        raise HTTPException(
            status_code=400,
            detail=f"Difference is ${float(difference):.2f} — must be $0.00 to complete",
        )
    n = 0
    for tl, _ in _candidates(db, recon):
        if tl.cleared:
            tl.reconciliation_id = recon.id
            n += 1
    recon.cleared_total = Decimal(str(data["cleared_total"]))
    recon.status = ReconciliationStatus.COMPLETED
    recon.completed_at = datetime.utcnow()
    return {"status": "completed", "reconciliation_id": recon.id, "cleared_count": n}


def abandon(db: Session, recon: Reconciliation) -> None:
    """Drop an in-progress reconciliation; cleared ticks stay (they are
    facts about the lines, as in QuickBooks)."""
    _open(recon)
    db.delete(recon)
