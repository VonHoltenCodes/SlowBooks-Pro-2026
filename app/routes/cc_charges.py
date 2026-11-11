# ============================================================================
# Credit Card Charges — Enter credit card expenses
# DR Expense Account, CR Credit Card Payable (2100)
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.schemas.cc_charges import CCChargeCreate
from app.services.accounting import create_journal_entry, get_cc_account_id
from app.services.bank_posting import void_document
from app.services.bank_register import voided_transaction_ids
from app.services.closing_date import check_closing_date
from app.services.purchase_posting import refuse_control_account
from app.models.transactions import Transaction

router = APIRouter(prefix="/api/cc-charges", tags=["cc-charges"])


@router.get("")
def list_cc_charges(db: Session = Depends(get_db)):
    """List credit card charge transactions."""
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == "cc_charge")
        .order_by(Transaction.date.desc())
        .all()
    )
    voided = voided_transaction_ids(db, [t.id for t in txns])
    ids = {ln.account_id for t in txns for ln in t.lines}
    names = (
        {a.id: a.name for a in db.query(Account).filter(Account.id.in_(ids)).all()}
        if ids
        else {}
    )
    results = []
    for txn in txns:
        expense_line = next((ln for ln in txn.lines if ln.debit > 0), None)
        card_line = next((ln for ln in txn.lines if ln.credit > 0), None)
        results.append(
            {
                "id": txn.id,
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "description": txn.description or "",
                "reference": txn.reference or "",
                "amount": float(expense_line.debit) if expense_line else 0,
                "account_name": (
                    names.get(expense_line.account_id, "") if expense_line else ""
                ),
                "card_account_id": card_line.account_id if card_line else None,
                "card_account_name": (
                    names.get(card_line.account_id, "") if card_line else ""
                ),
                "status": "void" if txn.id in voided else "recorded",
                "replaces_id": txn.replaces_id,
                "replaced_by_id": txn.replaced_by_id,
            }
        )
    return results


@router.post("", status_code=201)
def create_cc_charge(data: CCChargeCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    cc_account_id = data.card_account_id or get_cc_account_id(db)
    if not cc_account_id:
        raise HTTPException(
            status_code=400, detail="Credit Card account (2100) not found"
        )
    card = db.query(Account).filter(Account.id == cc_account_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card account not found")
    if card.account_type.value != "liability":
        raise HTTPException(
            status_code=400,
            detail=f"{card.name} is not a credit-card (liability) account",
        )

    expense_account = db.query(Account).filter(Account.id == data.account_id).first()
    if not expense_account:
        raise HTTPException(status_code=404, detail="Expense account not found")
    # never A/R, A/P, Undeposited Funds or Inventory (#785)
    refuse_control_account(expense_account, "The card charge")

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    journal_lines = [
        {
            "account_id": data.account_id,
            "debit": amount,
            "credit": Decimal("0"),
            "description": data.memo or data.payee or "",
            **(
                {"function": data.function}
                if "function" in data.model_fields_set
                else {}
            ),
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
        db,
        data.date,
        desc,
        journal_lines,
        source_type="cc_charge",
        reference=data.reference or "",
        class_id=data.class_id,
        job_id=data.job_id,
    )

    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(amount)}


@router.post("/{charge_id}/void")
def void_cc_charge(charge_id: int, db: Session = Depends(get_db)):
    """Reverse a charge: the original stays, a mirror image cancels it."""
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == charge_id, Transaction.source_type == "cc_charge")
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Charge not found")
    if txn.id in voided_transaction_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Charge is already void")
    reversal = void_document(db, txn, "cc_charge_void")
    db.commit()
    return {
        "status": "void",
        "transaction_id": txn.id,
        "void_transaction_id": reversal.id,
    }


@router.post("/{charge_id}/replace")
def replace_cc_charge(
    charge_id: int, data: CCChargeCreate, db: Session = Depends(get_db)
):
    """Change a card charge (#689): void it and post `data` in its place,
    the two linked, under the void's refusals."""
    from app.services.replace_transaction import replace

    return replace(db, "cc_charge", charge_id, data)
