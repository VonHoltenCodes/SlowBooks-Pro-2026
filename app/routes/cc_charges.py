# ============================================================================
# Credit Card Charges — Enter credit card expenses
# DR Expense Account, CR Credit Card Payable (2100)
# ============================================================================

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.schemas.cc_charges import CCChargeCreate, CCChargeResponse
from app.services.accounting import create_journal_entry
from app.services.closing_date import check_closing_date
from app.models.transactions import Transaction, TransactionLine

router = APIRouter(prefix="/api/cc-charges", tags=["cc-charges"])


def _get_cc_account_id(db):
    acct = db.query(Account).filter(Account.account_number == "2100").first()
    return acct.id if acct else None


@router.get("")
def list_cc_charges(db: Session = Depends(get_db)):
    """List credit card charge transactions."""
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == "cc_charge")
        .order_by(Transaction.date.desc())
        .all()
    )
    results = []
    for txn in txns:
        expense_line = None
        cc_line = None
        for line in txn.lines:
            if line.debit > 0:
                expense_line = line
            elif line.credit > 0:
                cc_line = line
        acct = db.query(Account).filter(Account.id == expense_line.account_id).first() if expense_line else None
        results.append({
            "id": txn.id,
            "date": txn.date.isoformat(),
            "description": txn.description or "",
            "reference": txn.reference or "",
            "amount": float(expense_line.debit) if expense_line else 0,
            "account_name": acct.name if acct else "",
        })
    return results


@router.post("", status_code=201)
def create_cc_charge(data: CCChargeCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    cc_account_id = _get_cc_account_id(db)
    if not cc_account_id:
        raise HTTPException(status_code=400, detail="Credit Card account (2100) not found")

    expense_account = db.query(Account).filter(Account.id == data.account_id).first()
    if not expense_account:
        raise HTTPException(status_code=404, detail="Expense account not found")

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    journal_lines = [
        {
            "account_id": data.account_id,
            "debit": amount,
            "credit": Decimal("0"),
            "description": data.memo or data.payee or "",
        },
        {
            "account_id": cc_account_id,
            "debit": Decimal("0"),
            "credit": amount,
            "description": data.memo or data.payee or "",
        },
    ]

    desc = f"CC Charge: {data.payee}" if data.payee else "Credit Card Charge"
    txn = create_journal_entry(
        db, data.date, desc,
        journal_lines, source_type="cc_charge",
        reference=data.reference or "",
    )

    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(amount)}
