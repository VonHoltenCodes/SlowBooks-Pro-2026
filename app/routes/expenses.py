# ============================================================================
# Expenses — record a receipt that's already been paid (card, cash, check)
# DR Expense Account, CR the bank / credit-card account it was paid from.
# One step, no bill to pay later; the Scan Receipt flow lands here.
# ============================================================================

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.schemas.expenses import ExpenseCreate, ExpenseLineOut, ExpenseResponse
from app.services.accounting import create_journal_entry, reversing_lines
from app.services.job_costing import line_cost_type
from app.services.bank_posting import assert_not_reconciled, release_statement_links
from app.services.check_numbers import refuse_duplicate
from app.services import pending_attachments
from app.services.closing_date import check_closing_date
from app.services.purchase_posting import _line_label, refuse_control_account

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


def _memo_of(txn: Transaction, credit_line) -> str:
    """The memo typed on the expense: its own column (#648), "" when none
    was typed. An expense saved before the column existed has none stored:
    its bank line holds the memo or, with none typed, the payee the entry's
    description carries ("Expense: {payee}", the vendor's name only when no
    payee was typed), which is not a memo."""
    if txn.memo is not None:
        return txn.memo
    line_desc = (credit_line.description or "") if credit_line else ""
    on_entry = (txn.description or "").removeprefix("Expense: ").strip()
    return "" if line_desc == on_entry else line_desc


def _serialize(
    txn: Transaction, db: Session, voided: bool | None = None
) -> ExpenseResponse:
    if voided is None:
        voided = txn.id in _void_ids(db, [txn.id])
    # the expense's lines are the ledger's debit lines, in the order posted
    debit_lines = sorted((ln for ln in txn.lines if ln.debit > 0), key=lambda x: x.id)
    debit_line = debit_lines[0] if debit_lines else None
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
        expense_account_id=debit_line.account_id if debit_line else None,
        lines=[
            ExpenseLineOut(
                account_id=ln.account_id,
                account_name=ln.account.name if ln.account else "",
                amount=float(ln.debit),
                description=ln.description or "",
                class_id=ln.class_id,
                job_id=ln.job_id,
                cost_code_id=ln.cost_code_id,
                cost_type=ln.cost_type,
                function=ln.function,
                is_billable=bool(ln.is_billable),
                line_order=i,
            )
            for i, ln in enumerate(debit_lines)
        ],
        expense_account_name=(
            debit_line.account.name if debit_line and debit_line.account else ""
        ),
        paid_from_account_name=(
            credit_line.account.name if credit_line and credit_line.account else ""
        ),
        amount=float(sum((ln.debit for ln in debit_lines), Decimal("0"))),
        reference=txn.reference or "",
        # the memo typed on the expense, kept on its own (#648); a split's
        # debit lines carry their own (#599)
        memo=_memo_of(txn, credit_line),
        status="void" if voided else "recorded",
        printable=(
            not voided
            and credit_line is not None
            and credit_line.account is not None
            and credit_line.account.bank_kind == "bank"
        ),
        replaces_id=txn.replaces_id,
        replaced_by_id=txn.replaced_by_id,
        paid_from_account_id=credit_line.account_id if credit_line else None,
        class_id=txn.class_id,
        job_id=txn.job_id,
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

    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)

    create_journal_entry(
        db,
        txn.date,
        f"VOID {txn.description or 'Expense'}",
        reversing_lines(txn.lines),
        source_type=VOID_SOURCE_TYPE,
        source_id=txn.id,
        reference=txn.reference or "",
        class_id=txn.class_id,
        job_id=txn.job_id,
    )
    release_statement_links(db, txn)
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db, True)


def _line_specs(data: ExpenseCreate) -> list[dict]:
    """The expense's lines as plain dicts, checked: the older single
    expense_account_id + amount is one line; `lines` must add up to the
    amount paid, and no line may be zero or negative."""
    if data.lines is not None and data.expense_account_id is not None:
        raise HTTPException(
            status_code=422,
            detail="Send either lines or expense_account_id, not both",
        )
    if data.lines is None:
        if data.expense_account_id is None:
            raise HTTPException(
                status_code=422, detail="An expense needs an account or lines"
            )
        if data.amount is None or Decimal(str(data.amount)) <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        # the header-level fields are the one line's own
        return [
            {
                "account_id": data.expense_account_id,
                "amount": Decimal(str(data.amount)),
                "description": None,
                "class_id": None,
                "job_id": None,
                "cost_code_id": data.cost_code_id,
                "cost_type": data.cost_type,
                "is_billable": data.is_billable,
                "function": data.function,
                "set_function": "function" in data.model_fields_set,
            }
        ]
    if not data.lines:
        raise HTTPException(
            status_code=422, detail="An expense needs at least one line"
        )
    specs = []
    for n, ln in enumerate(data.lines, 1):
        part = Decimal(str(ln.amount)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if part == 0:
            raise HTTPException(
                status_code=422,
                detail=f"Line {n} is 0.00 and posts nothing; enter its share or remove the line",
            )
        if part < 0:
            raise HTTPException(
                status_code=422, detail=f"Line {n}: the amount must be positive"
            )
        specs.append(
            {
                "account_id": ln.account_id,
                "amount": part,
                "description": (ln.description or "").strip() or None,
                "class_id": ln.class_id,
                "job_id": ln.job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
                "is_billable": ln.is_billable,
                "function": ln.function,
                "set_function": "function" in ln.model_fields_set,
            }
        )
    total = sum((sp["amount"] for sp in specs), Decimal("0"))
    if data.amount is not None and Decimal(str(data.amount)) != total:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The lines add up to ${total:,.2f} but the amount paid is "
                f"${Decimal(str(data.amount)):,.2f}; they must match"
            ),
        )
    return specs


@router.post("", response_model=ExpenseResponse, status_code=201)
def create_expense(
    data: ExpenseCreate,
    db: Session = Depends(get_db),
    allow_duplicate_check_number: bool = Query(False),
):
    check_closing_date(db, data.date)
    # files attached on the form before this save (#796): checked before
    # anything is written, attached with the expense below
    pending = pending_attachments.take(db, data.pending_attachments)

    specs = _line_specs(data)
    amount = sum((sp["amount"] for sp in specs), Decimal("0"))
    expense_accounts = {}
    for n, sp in enumerate(specs, 1):
        acct = db.query(Account).filter(Account.id == sp["account_id"]).first()
        if acct is None:
            raise HTTPException(status_code=404, detail="Expense account not found")
        # never A/R, A/P, Undeposited Funds or Inventory (#785)
        refuse_control_account(
            acct,
            (
                "The expense"
                if data.lines is None
                else _line_label(n, sp["description"])
            ),
        )
        expense_accounts[acct.id] = acct

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
    if paid_from.id in expense_accounts:
        raise HTTPException(
            status_code=400, detail="Expense and paid-from accounts must differ"
        )

    # a check whose number the bank account already has (#481): the 409
    # the person answers, keep the number or edit it
    refuse_duplicate(
        db, paid_from.id, data.reference, allow=allow_duplicate_check_number
    )

    vendor = None
    if data.vendor_id is not None:
        vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    payee = (data.payee or "").strip() or (vendor.name if vendor else "")
    memo = (data.memo or "").strip()
    # the bank line shows in the register: the memo, else the payee
    line_desc = memo or payee
    journal_lines = []
    for sp in specs:
        line = {
            "account_id": sp["account_id"],
            "debit": sp["amount"],
            "credit": Decimal("0"),
            "description": sp["description"] or line_desc,
            "cost_code_id": sp["cost_code_id"],
            "cost_type": line_cost_type(db, sp["cost_code_id"], sp["cost_type"]),
            "is_billable": sp["is_billable"],
        }
        # a line's own class and job; left out, the expense's (the ledger
        # fills them in)
        for key in ("class_id", "job_id"):
            if sp[key] is not None:
                line[key] = sp[key]
        if sp["set_function"]:
            line["function"] = sp["function"]
        journal_lines.append(line)
    journal_lines.append(
        {
            "account_id": paid_from.id,
            "debit": Decimal("0"),
            "credit": amount,
            "description": line_desc,
        }
    )

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
        job_id=data.job_id,
    )
    # the memo on its own (#648): "" when none was typed, which tells it
    # from an expense saved before the column existed
    txn.memo = memo
    db.flush()
    pending_attachments.bind(db, pending, "expense", txn.id)
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db)


@router.post("/{expense_id}/replace", response_model=ExpenseResponse)
def replace_expense(
    expense_id: int,
    data: ExpenseCreate,
    db: Session = Depends(get_db),
    allow_duplicate_check_number: bool = Query(False),
):
    """Change a recorded expense (#689): void it and post `data` in its
    place, the two linked. The void's refusals stand (a reconciled line, a
    closed period), in its words."""
    from app.services.replace_transaction import replace

    return replace(db, "expense", expense_id, data, allow_duplicate_check_number)
