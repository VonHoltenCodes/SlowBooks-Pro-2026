from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction
from app.schemas.cc_charges import CCChargeCreate, CCChargeResponse
from app.services.accounting import create_journal_entry
from app.services.auth import require_permissions
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/cc-charges", tags=["cc-charges"])


@router.get("", response_model=list[CCChargeResponse])
def list_cc_charges(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.view")),
):
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == "cc_charge")
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    results = []
    for txn in txns:
        expense_line = next((line for line in txn.lines if Decimal(str(line.debit)) > 0), None)
        card_line = next((line for line in txn.lines if Decimal(str(line.credit)) > 0), None)
        expense_account = db.query(Account).filter(Account.id == expense_line.account_id).first() if expense_line else None
        card_account = db.query(Account).filter(Account.id == card_line.account_id).first() if card_line else None
        results.append(CCChargeResponse(
            id=txn.id,
            date=txn.date,
            payee=txn.description.removeprefix("CC Charge: ") if txn.description else None,
            account_id=expense_line.account_id if expense_line else 0,
            account_name=expense_account.name if expense_account else None,
            credit_card_account_id=card_line.account_id if card_line else 0,
            credit_card_account_name=card_account.name if card_account else None,
            amount=expense_line.debit if expense_line else Decimal("0"),
            reference=txn.reference,
            memo=expense_line.description if expense_line else None,
        ))
    return results


@router.post("", response_model=CCChargeResponse, status_code=201)
def create_cc_charge(
    data: CCChargeCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.manage")),
):
    check_closing_date(db, data.date)
    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    expense_account = db.query(Account).filter(Account.id == data.account_id, Account.is_active == True).first()
    if not expense_account:
        raise HTTPException(status_code=404, detail="Expense account not found")
    if expense_account.account_type not in (AccountType.EXPENSE, AccountType.COGS):
        raise HTTPException(status_code=400, detail="Charge account must be an expense or cost-of-sales account")

    card_account = db.query(Account).filter(Account.id == data.credit_card_account_id, Account.is_active == True).first()
    if not card_account:
        raise HTTPException(status_code=404, detail="Credit card liability account not found")
    if card_account.account_type != AccountType.LIABILITY:
        raise HTTPException(status_code=400, detail="Credit card account must be a liability account")

    description = f"CC Charge: {data.payee}" if data.payee else "Credit Card Charge"
    txn = create_journal_entry(
        db,
        data.date,
        description,
        [
            {
                "account_id": expense_account.id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": data.memo or data.payee or "",
            },
            {
                "account_id": card_account.id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": data.memo or data.payee or "",
            },
        ],
        source_type="cc_charge",
        reference=data.reference or "",
    )
    db.commit()
    db.refresh(txn)
    return CCChargeResponse(
        id=txn.id,
        date=txn.date,
        payee=data.payee,
        account_id=expense_account.id,
        account_name=expense_account.name,
        credit_card_account_id=card_account.id,
        credit_card_account_name=card_account.name,
        amount=amount,
        reference=txn.reference,
        memo=data.memo,
    )
