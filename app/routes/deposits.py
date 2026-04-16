from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.payments import Payment
from app.schemas.deposits import DepositCreate, PendingDepositResponse
from app.services.accounting import create_journal_entry, get_undeposited_funds_id
from app.services.auth import require_permissions
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/deposits", tags=["deposits"])


def _pending_payments(db: Session) -> list[Payment]:
    clearing_id = get_undeposited_funds_id(db, allow_create=False)
    if not clearing_id:
        return []
    return (
        db.query(Payment)
        .filter(Payment.is_voided == False)
        .filter(Payment.deposit_transaction_id.is_(None))
        .filter((Payment.deposit_to_account_id.is_(None)) | (Payment.deposit_to_account_id == clearing_id))
        .order_by(Payment.date.asc(), Payment.id.asc())
        .all()
    )


@router.get("/pending", response_model=list[PendingDepositResponse])
def list_pending_deposits(
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.view")),
):
    return [
        PendingDepositResponse(
            payment_id=payment.id,
            transaction_id=payment.transaction_id,
            date=payment.date,
            customer_name=payment.customer.name if payment.customer else "Unknown",
            reference=payment.reference or payment.check_number,
            amount=float(payment.amount),
        )
        for payment in _pending_payments(db)
    ]


@router.post("")
def create_deposit(
    data: DepositCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("banking.manage")),
):
    if not data.payment_ids:
        raise HTTPException(status_code=400, detail="Select at least one payment to deposit")

    check_closing_date(db, data.date)
    bank_account = db.query(Account).filter(Account.id == data.deposit_to_account_id, Account.is_active == True).first()
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if bank_account.account_type != AccountType.ASSET:
        raise HTTPException(status_code=400, detail="Deposit account must be an asset account")

    clearing_id = get_undeposited_funds_id(db, allow_create=False)
    if not clearing_id:
        raise HTTPException(status_code=400, detail="Undeposited Funds / Receipt Clearing account not found")
    if bank_account.id == clearing_id:
        raise HTTPException(status_code=400, detail="Deposit account must differ from Undeposited Funds")

    pending_by_id = {payment.id: payment for payment in _pending_payments(db)}
    selected = []
    for payment_id in data.payment_ids:
        payment = pending_by_id.get(payment_id)
        if not payment:
            raise HTTPException(status_code=400, detail=f"Payment {payment_id} is not available for deposit")
        selected.append(payment)

    total = sum(Decimal(str(payment.amount)) for payment in selected)
    description = data.memo or f"Deposit to {bank_account.name}"
    txn = create_journal_entry(
        db,
        data.date,
        description,
        [
            {
                "account_id": bank_account.id,
                "debit": total,
                "credit": Decimal("0"),
                "description": description,
            },
            {
                "account_id": clearing_id,
                "debit": Decimal("0"),
                "credit": total,
                "description": description,
            },
        ],
        source_type="deposit",
        reference=data.reference or "",
    )
    for payment in selected:
        payment.deposit_transaction_id = txn.id

    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(total)}
