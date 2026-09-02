# ============================================================================
# Expenses — record a receipt that's already been paid (card, cash, check)
# DR Expense Account, CR the bank / credit-card account it was paid from.
# One step, no bill to pay later; the Scan Receipt flow lands here.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.schemas.expenses import ExpenseCreate, ExpenseResponse
from app.services.accounting import create_journal_entry
from app.services.closing_date import check_closing_date

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

SOURCE_TYPE = "expense"
VOID_SOURCE_TYPE = "expense_void"

# Accounts an expense can be paid from: cash on hand or credit extended.
PAID_FROM_TYPES = ("asset", "liability")


def _void_ids(db: Session, txn_ids: list[int]) -> set[int]:
    """Ids of expenses that already have a reversing entry posted."""
    if not txn_ids:
        return set()
    rows = (
        db.query(Transaction.source_id)
        .filter(
            Transaction.source_type == VOID_SOURCE_TYPE,
            Transaction.source_id.in_(txn_ids),
        )
        .all()
    )
    return {r[0] for r in rows}


def _serialize(
    txn: Transaction, db: Session, voided: bool | None = None
) -> ExpenseResponse:
    if voided is None:
        voided = txn.id in _void_ids(db, [txn.id])
    debit_line = next((ln for ln in txn.lines if ln.debit > 0), None)
    credit_line = next((ln for ln in txn.lines if ln.credit > 0), None)
    vendor = (
        db.query(Vendor).filter(Vendor.id == txn.source_id).first()
        if txn.source_id
        else None
    )
    desc = txn.description or ""
    payee = vendor.name if vendor else desc.removeprefix("Expense: ").strip()
    return ExpenseResponse(
        id=txn.id,
        date=txn.date,
        payee=payee,
        vendor_id=vendor.id if vendor else None,
        expense_account_name=(
            debit_line.account.name if debit_line and debit_line.account else ""
        ),
        paid_from_account_name=(
            credit_line.account.name if credit_line and credit_line.account else ""
        ),
        amount=float(debit_line.debit) if debit_line else 0.0,
        reference=txn.reference or "",
        memo=(debit_line.description or "") if debit_line else "",
        status="void" if voided else "recorded",
    )


@router.get("", response_model=list[ExpenseResponse])
def list_expenses(db: Session = Depends(get_db)):
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    voided = _void_ids(db, [t.id for t in txns])
    return [_serialize(t, db, t.id in voided) for t in txns]


@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(expense_id: int, db: Session = Depends(get_db)):
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == expense_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return _serialize(txn, db)


@router.post("/{expense_id}/void", response_model=ExpenseResponse)
def void_expense(expense_id: int, db: Session = Depends(get_db)):
    """Reverse a recorded expense — same convention as bills and manual
    entries: the original stays in the ledger, a mirror-image entry
    cancels it, and the expense shows as void. Booked it to the wrong
    account? Void it and enter it again."""
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == expense_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if txn.id in _void_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Expense is already void")

    check_closing_date(db, txn.date)

    reverse_lines = [
        {
            "account_id": ln.account_id,
            "debit": ln.credit,
            "credit": ln.debit,
            "description": f"VOID: {ln.description or ''}",
        }
        for ln in txn.lines
    ]
    create_journal_entry(
        db,
        txn.date,
        f"VOID {txn.description or 'Expense'}",
        reverse_lines,
        source_type=VOID_SOURCE_TYPE,
        source_id=txn.id,
        reference=txn.reference or "",
        class_id=txn.class_id,
    )
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db, True)


@router.post("", response_model=ExpenseResponse, status_code=201)
def create_expense(data: ExpenseCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    expense_account = (
        db.query(Account).filter(Account.id == data.expense_account_id).first()
    )
    if expense_account is None:
        raise HTTPException(status_code=404, detail="Expense account not found")

    paid_from = (
        db.query(Account).filter(Account.id == data.paid_from_account_id).first()
    )
    if paid_from is None:
        raise HTTPException(status_code=404, detail="Paid-from account not found")
    if paid_from.account_type not in PAID_FROM_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Paid-from must be a bank, cash, or credit card account",
        )
    if paid_from.id == expense_account.id:
        raise HTTPException(
            status_code=400, detail="Expense and paid-from accounts must differ"
        )

    vendor = None
    if data.vendor_id is not None:
        vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    payee = (data.payee or "").strip() or (vendor.name if vendor else "")
    memo = (data.memo or "").strip()
    line_desc = memo or payee
    journal_lines = [
        {
            "account_id": expense_account.id,
            "debit": amount,
            "credit": Decimal("0"),
            "description": line_desc,
        },
        {
            "account_id": paid_from.id,
            "debit": Decimal("0"),
            "credit": amount,
            "description": line_desc,
        },
    ]

    txn = create_journal_entry(
        db,
        data.date,
        f"Expense: {payee}" if payee else "Expense",
        journal_lines,
        source_type=SOURCE_TYPE,
        # The vendor is the only linked record an expense has; keep the
        # id where every other posting keeps its origin.
        source_id=vendor.id if vendor else None,
        reference=data.reference or "",
        class_id=data.class_id,
    )
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db)
