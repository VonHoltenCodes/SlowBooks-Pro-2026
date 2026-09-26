# ============================================================================
# Deposit Recording — Move funds from Undeposited Funds to bank account
# Classic QB "Make Deposits" workflow
#
# A deposit made from the list names the payments it took (each one's
# Undeposited Funds line carries the deposit), so a deposited payment can't
# be voided out from under it and a deposit can be voided to put its
# payments back on the list. See services/undeposited_funds.py.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.models.accounts import Account
from app.models.payments import Payment, PaymentAllocation
from app.models.transactions import Transaction, TransactionLine
from app.routes._helpers import clamp_pagination
from app.schemas.deposits import (
    DepositCreate,
    DepositDetailResponse,
    DepositResponse,
    PendingDepositResponse,
)
from app.services.accounting import _q, create_journal_entry, get_undeposited_funds_id
from app.services.bank_posting import void_document
from app.services.closing_date import check_closing_date
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db
from app.services.undeposited_funds import (
    dead_transaction_ids,
    reconciled,
    waiting_items,
)

router = APIRouter(prefix="/api/deposits", tags=["deposits"])


def _payment_details(db: Session, txns) -> dict[int, dict]:
    """Per payment transaction: who paid, the check number and reference,
    the method, and what it paid — a sales receipt by its number — so two
    "Payment from Acme" lines can be told apart. One pass for the page."""
    pay_ids = {t.source_id for t in txns if t.source_type == "payment" and t.source_id}
    if not pay_ids:
        return {}
    payments = (
        db.query(Payment)
        .options(
            joinedload(Payment.customer),
            selectinload(Payment.allocations).joinedload(PaymentAllocation.invoice),
        )
        .filter(Payment.id.in_(pay_ids))
        .all()
    )
    words = terms_from_db(db)
    out = {}
    for p in payments:
        name = p.customer.name if p.customer else ""
        invoices = [a.invoice for a in p.allocations if a.invoice is not None]
        receipt = next((i for i in invoices if i.is_sales_receipt), None)
        refs = [
            document_reference(document_label(i, words), i.invoice_number)
            for i in invoices
        ]
        out[p.transaction_id] = {
            "payment_id": p.id,
            "received_from": name,
            "check_number": p.check_number or "",
            "payment_reference": p.reference or "",
            "method": p.method or "",
            "document": ", ".join(refs),
            "description": (
                document_reference(
                    document_label(receipt, words), receipt.invoice_number, name
                )
                if receipt
                else None
            ),
        }
    return out


def _payment_line(tl: TransactionLine, txn: Transaction, d: dict):
    """A payment's Undeposited Funds line as the page lists it."""
    return PendingDepositResponse(
        transaction_line_id=tl.id,
        transaction_id=txn.id,
        date=txn.date,
        description=d.get("description") or txn.description or "",
        reference=txn.reference or "",
        source_type=txn.source_type or "",
        amount=float(tl.debit),
        payment_id=d.get("payment_id"),
        received_from=d.get("received_from", ""),
        check_number=d.get("check_number", ""),
        payment_reference=d.get("payment_reference", ""),
        method=d.get("method", ""),
        document=d.get("document", ""),
    )


@router.get("/pending", response_model=list[PendingDepositResponse])
def list_pending_deposits(db: Session = Depends(get_db)):
    """Payments sitting in Undeposited Funds (1200), newest first."""
    items = waiting_items(db)
    details = _payment_details(db, [t for _, t in items])
    return [
        _payment_line(tl, txn, details.get(txn.id, {})) for tl, txn in reversed(items)
    ]


@router.get("", response_model=list[DepositResponse])
def list_deposits(skip: int = 0, limit: int = 25, db: Session = Depends(get_db)):
    """Deposits made, newest first — with the payments each took, and
    whether it can still be voided (not void already, not reconciled)."""
    skip, limit = clamp_pagination(skip, limit, max_limit=200)
    txns = (
        db.query(Transaction)
        .options(selectinload(Transaction.lines))
        .filter(Transaction.source_type == "deposit")
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    if not txns:
        return []
    ids = [t.id for t in txns]
    counts = dict(
        db.query(TransactionLine.deposit_transaction_id, func.count(TransactionLine.id))
        .filter(TransactionLine.deposit_transaction_id.in_(ids))
        .group_by(TransactionLine.deposit_transaction_id)
        .all()
    )
    dead = dead_transaction_ids(db, txns)
    bank_ids = {ln.account_id for t in txns for ln in t.lines if ln.debit > 0}
    names = {
        a.id: a.name for a in db.query(Account).filter(Account.id.in_(bank_ids)).all()
    }
    out = []
    for t in txns:
        bank = next((ln for ln in t.lines if ln.debit > 0), None)
        out.append(
            DepositResponse(
                id=t.id,
                date=t.date,
                reference=t.reference or "",
                account_id=bank.account_id if bank else None,
                account_name=names.get(bank.account_id, "") if bank else "",
                amount=Decimal(str(bank.debit)) if bank else Decimal("0"),
                items=counts.get(t.id),
                voided=t.id in dead,
                reconciled=reconciled(t),
            )
        )
    return out


@router.get("/{deposit_id}", response_model=DepositDetailResponse)
def get_deposit(deposit_id: int, db: Session = Depends(get_db)):
    """One deposit and the payments it took. The bank register links a
    deposit here (#/deposits/{id}); the link said "Page not found"."""
    txn = (
        db.query(Transaction)
        .options(selectinload(Transaction.lines))
        .filter(Transaction.id == deposit_id, Transaction.source_type == "deposit")
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Deposit not found")
    taken = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(TransactionLine.deposit_transaction_id == txn.id)
        .order_by(Transaction.date, Transaction.id, TransactionLine.id)
        .all()
    )
    details = _payment_details(db, [t for _, t in taken])
    bank = next((ln for ln in txn.lines if ln.debit > 0), None)
    account = db.get(Account, bank.account_id) if bank else None
    return DepositDetailResponse(
        id=txn.id,
        date=txn.date,
        reference=txn.reference or "",
        account_id=account.id if account else None,
        account_name=account.name if account else "",
        amount=Decimal(str(bank.debit)) if bank else Decimal("0"),
        items=len(taken) or None,
        voided=txn.id in dead_transaction_ids(db, [txn]),
        reconciled=reconciled(txn),
        payments=[_payment_line(tl, t, details.get(t.id, {})) for tl, t in taken],
    )


@router.post("")
def create_deposit(data: DepositCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    bank_account = (
        db.query(Account).filter(Account.id == data.deposit_to_account_id).first()
    )
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    uf_id = get_undeposited_funds_id(db)
    if not uf_id:
        raise HTTPException(
            status_code=400, detail="Undeposited Funds account not found"
        )

    chosen: list[TransactionLine] = []
    if data.line_ids:
        # The deposit is the payments ticked, each still waiting — not a
        # number the page added up, which a second tab or a payment voided
        # since the page loaded can make wrong.
        waiting = {ln.id: ln for ln, _ in waiting_items(db, uf_id)}
        for line_id in dict.fromkeys(data.line_ids):
            line = waiting.get(line_id)
            if line is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "One of the payments you ticked has already been "
                        "deposited or voided. Reload Make Deposits and tick "
                        "them again."
                    ),
                )
            chosen.append(line)
        # Row-lock the chosen lines so two deposits at once can't both take
        # the same payment (no-op on SQLite).
        locked = (
            db.query(TransactionLine)
            .filter(TransactionLine.id.in_([ln.id for ln in chosen]))
            .with_for_update()
            .all()
        )
        if any(ln.deposit_transaction_id for ln in locked):
            raise HTTPException(
                status_code=409,
                detail=(
                    "One of the payments you ticked was deposited a moment ago. "
                    "Reload Make Deposits and tick them again."
                ),
            )
        total = _q(sum((Decimal(str(ln.debit)) for ln in chosen), Decimal("0")))
        if data.total is not None and _q(data.total) != total:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The payments ticked add up to ${total:,.2f}, not "
                    f"${_q(data.total):,.2f}. Reload Make Deposits and try again."
                ),
            )
    else:
        total = _q(data.total or 0)

    if total <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be positive")

    journal_lines = [
        {
            "account_id": data.deposit_to_account_id,
            "debit": total,
            "credit": Decimal("0"),
            "description": f"Deposit to {bank_account.name}",
        },
        {
            "account_id": uf_id,
            "debit": Decimal("0"),
            "credit": total,
            "description": f"Deposit to {bank_account.name}",
        },
    ]

    txn = create_journal_entry(
        db,
        data.date,
        f"Deposit to {bank_account.name}",
        journal_lines,
        source_type="deposit",
        reference=data.reference or "",
        class_id=data.class_id,
        job_id=data.job_id,
    )
    for line in chosen:
        line.deposit_transaction_id = txn.id

    db.commit()
    return {
        "status": "ok",
        "transaction_id": txn.id,
        "amount": float(total),
        "items": len(chosen),
    }


@router.post("/{deposit_id}/void", response_model=DepositResponse)
def void_deposit(deposit_id: int, db: Session = Depends(get_db)):
    """Take a deposit back: post its mirror image and put every payment it
    took back on the Make Deposits list. This is how one payment comes out
    of a deposit — void it, then deposit the others again."""
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == deposit_id, Transaction.source_type == "deposit")
        .with_for_update()
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if txn.id in dead_transaction_ids(db, [txn]):
        raise HTTPException(status_code=400, detail="This deposit is already void")
    if reconciled(txn):
        raise HTTPException(
            status_code=400,
            detail=(
                "This deposit has been reconciled with a bank statement, so it "
                "can't be voided."
            ),
        )
    void_document(db, txn, "deposit_void")
    items = (
        db.query(TransactionLine)
        .filter(TransactionLine.deposit_transaction_id == txn.id)
        .all()
    )
    for line in items:
        line.deposit_transaction_id = None
    db.commit()
    bank = next((ln for ln in txn.lines if ln.debit > 0), None)
    account = db.get(Account, bank.account_id) if bank else None
    return DepositResponse(
        id=txn.id,
        date=txn.date,
        reference=txn.reference or "",
        account_id=account.id if account else None,
        account_name=account.name if account else "",
        amount=Decimal(str(bank.debit)) if bank else Decimal("0"),
        items=len(items) or None,
        voided=True,
        reconciled=False,
    )
