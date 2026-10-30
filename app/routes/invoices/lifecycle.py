from datetime import date, timedelta
from decimal import Decimal

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.schemas.common import StrictModel
from typing import Optional
from sqlalchemy.exc import IntegrityError
from app.schemas.credit_memos import CreditMemoResponse
from datetime import date as dt_date
from app.database import get_db
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.schemas.invoices import InvoiceResponse, ZeroTotalConfirmation
from app.services.accounting import (
    create_journal_entry,
    get_ar_account_id,
    _q,
    void_line_memo,
)
from app.services.jobs_service import own_job
from app.services.numbering import next_invoice_number
from app.services.settings_service import get_all_settings as get_settings
from app.services.closing_date import check_closing_date

from app.routes.invoices._router import router
from app.routes.invoices.helpers import (
    invoice_customer_name,
    _due_date_from_terms,
    _post_invoice_journal,
    confirm_credit_limit,
    confirm_zero_total,
    opening_status,
)
from app.services.donor_documents import document_label
from app.services.sub_customers import invoice_addresses
from app.services.terminology import document_reference, terms_from_db


@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
def void_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Void — creates a reversing journal entry. A pay application's invoice
    is voided with the application."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.pay_application_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoice_belongs_to_pay_application",
                "message": (
                    "This invoice was created by a pay application; void the "
                    "pay application to void it."
                ),
            },
        )
    return void_invoice_unchecked(invoice_id, db)


def void_invoice_unchecked(invoice_id: int, db: Session, commit: bool = True):
    """The void itself, without the pay-application guard (the application's
    own void calls this). With `commit=False` the work is flushed and left
    for the caller to commit, so it can be undone with the caller's."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Invoice already voided")
    # Voiding an invoice with payments applied would reverse the full A/R
    # while the payment's cash-receipt JE + allocations stay on the books —
    # double-counting cash and reversing A/R twice. Require the payment(s)
    # to be voided first so the ledger stays consistent.
    if (invoice.amount_paid or Decimal("0")) > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot void an invoice with payments applied. Void the "
                "payment(s) first, then void the invoice."
            ),
        )
    check_closing_date(db, invoice.date)

    # An invoice or sales receipt the QuickBooks Online import created has
    # no posting of its own: the ledger import's posting for it is reversed
    # instead (none if that import never ran, as in 2.17). When that posting
    # carried the cost of its stock, the stock goes back without a cost
    # entry of ours.
    from app.services import qbo_documents

    cost_in_import = qbo_documents.void_invoice_import_posting(
        db, invoice, document_label(invoice, terms_from_db(db)).lower()
    )

    # Create reversing journal entry if original had one
    if invoice.transaction_id:
        from app.models.transactions import TransactionLine

        original_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == invoice.transaction_id)
            .all()
        )
        reverse_lines = []
        for ol in original_lines:
            reverse_lines.append(
                {
                    "account_id": ol.account_id,
                    "debit": ol.credit,  # swap debit/credit
                    "credit": ol.debit,
                    "description": void_line_memo(ol.description),
                    "basis": ol.basis,
                }
            )
        if reverse_lines:
            create_journal_entry(
                db,
                invoice.date,
                f"VOID {document_label(invoice, terms_from_db(db))} #{invoice.invoice_number}",
                reverse_lines,
                source_type="invoice_void",
                source_id=invoice.id,
                class_id=invoice.class_id,
                job_id=invoice.job_id,
                reference=invoice.invoice_number,
            )

    # A late fee posts its own A/R debit (source_type="late_fee") on top of
    # the invoice, so the void above leaves it open with nothing to apply it
    # to. Reverse every late-fee posting for this invoice in the same
    # transaction, with the invoice-void audit shape, so A/R (and the job's
    # A/R) tie back to zero.
    from app.models.transactions import Transaction, TransactionLine

    fee_txns = (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "late_fee",
            Transaction.source_id == invoice.id,
        )
        .all()
    )
    from app.services.accounting import reversing_lines
    from app.services.qbo_documents import reversed_already

    for fee_txn in fee_txns:
        # A fee already voided in the journal is off A/R; reversing it again
        # would leave A/R at minus the fee.
        if reversed_already(db, fee_txn):
            continue
        fee_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == fee_txn.id)
            .all()
        )
        # Each line's own job and class (and other dimensions) carried over,
        # as a journal void does: a fee posted before fees carried the job,
        # or before the invoice's job changed, comes off where it went on
        # and never credits another job's A/R.
        reverse_fee_lines = reversing_lines(fee_lines)
        if reverse_fee_lines:
            # On the fee's own date, as the invoice's reversal sits on the
            # invoice's: the fee came off the day it went on.
            create_journal_entry(
                db,
                fee_txn.date,
                f"VOID late fee - {document_label(invoice, terms_from_db(db))} #{invoice.invoice_number}",
                reverse_fee_lines,
                source_type="invoice_void",
                source_id=invoice.id,
                class_id=fee_txn.class_id,
                job_id=fee_txn.job_id,
                reference=invoice.invoice_number,
            )

    # ---- Phase 11: reverse inventory movements ----
    # Pass the ORIGINAL (invoice, id) so reverse_sale looks up the sale's
    # historical unit_cost — this keeps the reversal balanced even if
    # avg_cost moved between the sale and the void.
    from app.services.inventory_service import reverse_sale

    for line in invoice.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory:
            reverse_sale(
                db,
                item,
                quantity=Decimal(str(line.quantity)),
                source_type="invoice_void",
                source_id=invoice.id,
                original_source_type="invoice",
                original_source_id=invoice.id,
                txn_date=invoice.date,
                post_journal=not cost_in_import,
            )

    invoice.status = InvoiceStatus.VOID
    invoice.balance_due = Decimal("0")
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice_customer_name(invoice)
    return resp


@router.post("/{invoice_id}/send", response_model=InvoiceResponse)
def mark_invoice_sent(invoice_id: int, db: Session = Depends(get_db)):
    """Mark the invoice as sent."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=400, detail="Only draft invoices can be marked as sent"
        )
    invoice.status = InvoiceStatus.SENT
    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    if invoice.customer:
        resp.customer_name = invoice_customer_name(invoice)
    return resp


@router.post("/apply-late-fees")
def apply_late_fees(db: Session = Depends(get_db)):
    """Apply late fees to overdue invoices past the grace period."""
    words = terms_from_db(db)
    from app.models.transactions import Transaction

    settings_dict = get_settings(db)

    if settings_dict.get("late_fee_enabled") != "true":
        raise HTTPException(
            status_code=400, detail="Late fees are not enabled in settings"
        )

    rate = Decimal(settings_dict.get("late_fee_rate", "1.5")) / 100
    grace_days = int(settings_dict.get("late_fee_grace_days", "15"))
    today = date.today()

    overdue = (
        db.query(Invoice)
        .filter(
            # DRAFT invoices are unsent — never charge late fees on an
            # invoice the customer hasn't received.
            Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL])
        )
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date <= today - timedelta(days=grace_days))
        .all()
    )

    # Ensure Late Fee Income account exists (4800)
    late_fee_account = (
        db.query(Account).filter(Account.account_number == "4800").first()
    )
    if not late_fee_account:
        from app.models.accounts import AccountType as AT

        late_fee_account = Account(
            name="Late Fee Income",
            account_number="4800",
            account_type=AT.INCOME,
            is_system=False,
            balance=Decimal("0"),
        )
        db.add(late_fee_account)
        db.flush()

    ar_id = get_ar_account_id(db)
    if not ar_id:
        raise HTTPException(
            status_code=400, detail="Accounts Receivable (1100) not found"
        )

    applied = 0
    for inv in overdue:
        # Check if late fee already applied (look for journal entry with source_type=late_fee, source_id=inv.id)
        existing = (
            db.query(Transaction)
            .filter(
                Transaction.source_type == "late_fee",
                Transaction.source_id == inv.id,
            )
            .first()
        )
        if existing:
            continue

        fee_amount = _q(inv.balance_due * rate)
        if fee_amount <= 0:
            continue

        # Create journal entry: DR A/R, CR Late Fee Income. A fee on a
        # job-costed invoice carries that job on both lines (and the header),
        # so the job's A/R ties out and the void can reverse it cleanly —
        # the same way the invoice's own posting and void reversal do.
        journal_lines = [
            {
                "account_id": ar_id,
                "debit": fee_amount,
                "credit": Decimal("0"),
                "description": f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
                "job_id": inv.job_id,
                "class_id": inv.class_id,
            },
            {
                "account_id": late_fee_account.id,
                "debit": Decimal("0"),
                "credit": fee_amount,
                "description": f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
                "job_id": inv.job_id,
                "class_id": inv.class_id,
            },
        ]
        create_journal_entry(
            db,
            today,
            f"Late fee - {document_label(inv, words)} #{inv.invoice_number}",
            journal_lines,
            source_type="late_fee",
            source_id=inv.id,
            class_id=inv.class_id,
            job_id=inv.job_id,
        )

        # Update invoice totals (add to subtotal too so total == subtotal + tax_amount)
        inv.subtotal += fee_amount
        inv.total += fee_amount
        inv.balance_due += fee_amount
        applied += 1

    db.commit()
    return {"applied": applied, "total_overdue": len(overdue)}


class WriteOffRequest(StrictModel):
    date: dt_date
    amount: Optional[Decimal] = None  # default: the whole open balance
    memo: Optional[str] = None


def _post_write_off(db: Session, inv: Invoice, when: dt_date, amount: Decimal, memo):
    """One write-off credit memo for `inv`, flushed and not committed: DR
    Bad Debt Expense / CR A/R, applied to the invoice at once, so the A/R
    subledger, the statement and the aging all agree. The caller commits,
    so a batch posts all of its memos or none. Undone by voiding the memo."""
    from app.services.accounting import get_bad_debt_account_id

    memo = (
        memo
        or f"Write-off: {document_label(inv, terms_from_db(db))} #{inv.invoice_number}"
    )
    return post_applied_credit(
        db, inv, when, amount, memo, get_bad_debt_account_id(db), "write-off of"
    )


def post_applied_credit(
    db: Session,
    inv: Invoice,
    when: dt_date,
    amount: Decimal,
    memo: str,
    account_id: int,
    what: str,
    discount_payment_id: int | None = None,
):
    """A credit memo for `amount` applied to `inv` at once, flushed and not
    committed: DR `account_id` / CR A/R. A write-off (Bad Debt Expense) and
    an early-payment discount (#664: the account the payment named, and
    the payment it was taken with) are both this. `what` names it in the
    entry's description ("write-off of")."""
    from app.models.credit_memos import (
        CreditApplication,
        CreditMemo,
        CreditMemoLine,
        CreditMemoStatus,
    )
    from app.services.numbering import next_credit_memo_number

    balance = Decimal(str(inv.balance_due or 0))
    ar_id = get_ar_account_id(db)
    cm = None
    for _ in range(10):
        cm = CreditMemo(
            memo_number=next_credit_memo_number(db),
            customer_id=inv.customer_id,
            date=when,
            original_invoice_id=inv.id,
            subtotal=amount,
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=amount,
            amount_applied=amount,
            balance_remaining=Decimal("0"),
            notes=memo,
            class_id=inv.class_id,
            job_id=inv.job_id,
            status=CreditMemoStatus.APPLIED,
            is_write_off=discount_payment_id is None,
            discount_payment_id=discount_payment_id,
        )
        try:
            # a savepoint: a taken memo number rolls back this memo only,
            # never a batch's earlier memos
            with db.begin_nested():
                db.add(cm)
                db.flush()
            break
        except IntegrityError as e:
            if "memo_number" not in str(e.orig).lower():
                raise
            cm = None
    if cm is None:
        raise HTTPException(
            status_code=503, detail="Could not assign a credit memo number"
        )
    db.add(
        CreditMemoLine(
            credit_memo_id=cm.id,
            description=memo,
            quantity=1,
            rate=amount,
            amount=amount,
            line_order=0,
        )
    )
    txn = create_journal_entry(
        db,
        when,
        f"Credit Memo {cm.memo_number} - {what} "
        + document_reference(
            document_label(inv, terms_from_db(db)), inv.invoice_number
        ),
        [
            {
                "account_id": account_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": memo,
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": memo,
            },
        ],
        source_type="credit_memo",
        source_id=cm.id,
        class_id=inv.class_id,
        job_id=inv.job_id,
    )
    cm.transaction_id = txn.id
    db.add(CreditApplication(credit_memo_id=cm.id, invoice_id=inv.id, amount=amount))
    inv.amount_paid = Decimal(str(inv.amount_paid or 0)) + amount
    inv.balance_due = balance - amount
    inv.status = InvoiceStatus.PAID if inv.balance_due == 0 else InvoiceStatus.PARTIAL
    return cm


def _locked_invoice(db: Session, invoice_id: int):
    """The invoice, row-locked for the read-check-write a write-off is, as
    the payment path locks it (#501): a payment landing at the same moment
    waits for the write-off, or the write-off for it, and reads the balance
    the other left. Without the lock both read the same balance and the
    later write lost the other's (A/R off by the payment). populate_existing
    refreshes a copy already in the session. No-op lock on SQLite."""
    return (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


@router.post(
    "/{invoice_id}/write-off", response_model=CreditMemoResponse, status_code=201
)
def write_off_invoice(
    invoice_id: int, data: WriteOffRequest, db: Session = Depends(get_db)
):
    """Forgive an open balance (a pledge that will never be paid, a small
    balance not worth collecting): a credit memo flagged as a write-off,
    posting DR Bad Debt Expense / CR A/R and applied to the invoice at
    once. Any company, since 2.22.2 (#255); undo it by voiding the memo."""
    check_closing_date(db, data.date)
    inv = _locked_invoice(db, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status == InvoiceStatus.VOID:
        raise HTTPException(status_code=400, detail="Invoice is void")
    balance = Decimal(str(inv.balance_due or 0))
    if balance <= 0:
        raise HTTPException(
            status_code=400, detail="Nothing to write off — no open balance"
        )
    amount = _q(Decimal(str(data.amount))) if data.amount is not None else balance
    if amount <= 0 or amount > balance:
        raise HTTPException(
            status_code=400, detail="Write-off must be between 0 and the open balance"
        )
    cm = _post_write_off(db, inv, data.date, amount, data.memo)
    db.commit()
    db.refresh(cm)
    resp = CreditMemoResponse.model_validate(cm)
    resp.customer_name = invoice_customer_name(inv)
    return resp


# ── small balances, from A/R Aging (#255) ────────────────────────────────────

_OPEN = (InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PARTIAL)


def _small_balances(db: Session, under: Decimal, as_of: dt_date) -> list:
    """The open invoices whose balance, at the rate it was booked at, is
    under the figure: what A/R Aging's "Write off balances under…" lists
    before anything posts."""
    from app.services.contact_balances import home_amount

    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.status.in_(_OPEN), Invoice.balance_due > 0, Invoice.date <= as_of
        )
        .all()
    )
    rows = []
    for inv in invoices:
        bal = home_amount(inv.balance_due, inv.exchange_rate)
        if bal >= under:
            continue
        rows.append(
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "customer_id": inv.customer_id,
                "customer_name": invoice_customer_name(inv) or "",
                "date": inv.date.isoformat(),
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "days_overdue": (
                    max((as_of - inv.due_date).days, 0) if inv.due_date else 0
                ),
                "balance_due": str(_q(bal)),
            }
        )
    rows.sort(key=lambda r: ((r["customer_name"] or "").lower(), r["date"], r["id"]))
    return rows


@router.get("/write-offs/candidates")
def write_off_candidates(
    under: Decimal = Query(..., gt=0),
    as_of_date: dt_date = Query(default=None),
    db: Session = Depends(get_db),
):
    """The open balances under a figure, listed before any is written off
    (#255): `[{id, invoice_number, customer_id, customer_name, date,
    due_date, days_overdue, balance_due}]`."""
    return _small_balances(db, _q(under), as_of_date or dt_date.today())


class WriteOffBatchRequest(StrictModel):
    date: dt_date
    under: Decimal
    invoice_ids: list[int]
    memo: Optional[str] = None


@router.post("/write-offs", status_code=201)
def write_off_batch(data: WriteOffBatchRequest, db: Session = Depends(get_db)):
    """Write off the listed invoices' balances, one credit memo each to Bad
    Debt Expense, all posted together or none (#255). An invoice whose
    balance is no longer under the figure (a payment landed since it was
    listed), is paid, or is void is skipped and named, not written off.
    Returns `{written_off: [...], skipped: [{invoice_id, reason}]}`."""
    check_closing_date(db, data.date)
    under = _q(data.under)
    if under <= 0:
        raise HTTPException(status_code=400, detail="under must be more than zero")
    from app.services.contact_balances import home_amount

    written, skipped = [], []
    for invoice_id in dict.fromkeys(data.invoice_ids):
        inv = _locked_invoice(db, invoice_id)
        if not inv:
            skipped.append({"invoice_id": invoice_id, "reason": "not found"})
            continue
        row = {"invoice_id": inv.id, "invoice_number": inv.invoice_number}
        balance = Decimal(str(inv.balance_due or 0))
        if inv.status == InvoiceStatus.VOID:
            skipped.append({**row, "reason": "void"})
            continue
        if balance <= 0 or inv.status not in _OPEN:
            skipped.append({**row, "reason": "already paid"})
            continue
        if home_amount(balance, inv.exchange_rate) >= under:
            skipped.append({**row, "reason": "the balance changed since it was listed"})
            continue
        cm = _post_write_off(db, inv, data.date, _q(balance), data.memo)
        written.append(
            {
                **row,
                "customer_name": invoice_customer_name(inv) or "",
                "amount": str(_q(balance)),
                "credit_memo_id": cm.id,
                "memo_number": cm.memo_number,
            }
        )
    db.commit()
    return {"written_off": written, "skipped": skipped}


@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse, status_code=201)
def duplicate_invoice(
    invoice_id: int,
    data: Optional[ZeroTotalConfirmation] = None,
    db: Session = Depends(get_db),
):
    """Duplicate — the same sale under a new number, dated today and not
    sent yet. The copy carries what the original says about the sale: its
    currency and the rate it was booked at, the job, class and PO number,
    and each line's item, job, class and cost code. Each line's tax is the
    customer's as they stand today, and the copy is billed as of today
    too: the Bill To is the payer's now and the ship-to the customer's now
    (2.31.1 gate, W-73). It posts the way a new invoice does.

    The copy used to drop the currency and rate, so a EUR 850.00 invoice's
    duplicate was a $850.00 invoice (and posted $850 where the original
    booked $935), and it dropped the job and every line's job, class and
    cost code, so the copy was missing from job costing (found integrating
    the 2.17.3 exploratory fixes)."""
    from app.services.accounting import compute_line_totals, taxed_copy_lines
    from app.services.inventory_hooks import post_sale_for_invoice

    words = terms_from_db(db)
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not original:
        raise HTTPException(status_code=404, detail="Invoice not found")

    today = date.today()
    copied = taxed_copy_lines(original.lines, original.customer)
    subtotal, tax_amount, total = compute_line_totals(copied, original.tax_rate)
    noun = document_label(original, words).lower()
    confirm_zero_total(
        total,
        bool(data and data.allow_zero_total),
        noun,
        "duplicating it",
        question=f"This {noun} adds up to $0.00. Duplicate it anyway?",
    )
    # Past the payer's credit limit? Asked as a new invoice is (2.31.1
    # gate, W-79): the same 409 "credit_limit", and the copy is made with
    # allow_over_credit_limit, as create takes it. The copy owes its whole
    # total, at the rate it carries.
    from app.services.contact_balances import home_amount

    confirm_credit_limit(
        db,
        original.customer,
        home_amount(total, original.exchange_rate),
        bool(data and data.allow_over_credit_limit),
        noun,
        ask="Duplicate it anyway?",
    )

    left_off: list[str] = []
    new_invoice = Invoice(
        invoice_number=next_invoice_number(db),
        customer_id=original.customer_id,
        status=opening_status(total),
        date=today,
        due_date=_due_date_from_terms(db, today, original.terms),
        terms=original.terms,
        po_number=original.po_number,
        subtotal=subtotal,
        tax_rate=original.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        currency=original.currency,
        exchange_rate=original.exchange_rate,
        is_pledge=original.is_pledge,
        fair_value_amount=original.fair_value_amount,
        fair_value_description=original.fair_value_description,
        notes=original.notes,
        class_id=original.class_id,
        # The copy carries the customer's jobs only; another customer's job
        # stored on the original (books from before the rule) is left off,
        # and the response says which (NEW-36).
        job_id=own_job(db, original.customer_id, original.job_id, left_off),
        # A new invoice, billed as of now (2.31.1 gate, W-73): the Bill To
        # whole is the payer's today, the street with the name and country,
        # and the ship-to the customer's today. The original's frozen block
        # is what was sent then: a tenant moved since would get a copy on
        # the new property's account printing the old one's street.
        **(invoice_addresses(db, original.customer) if original.customer else {}),
    )
    db.add(new_invoice)
    db.flush()

    new_lines = []
    for oline, cline in zip(original.lines, copied):
        line = InvoiceLine(
            invoice_id=new_invoice.id,
            item_id=oline.item_id,
            description=oline.description,
            quantity=oline.quantity,
            rate=oline.rate,
            amount=_q(Decimal(str(oline.quantity)) * Decimal(str(oline.rate))),
            class_name=oline.class_name,
            job_id=own_job(db, original.customer_id, oline.job_id, left_off),
            class_id=oline.class_id,
            cost_code_id=oline.cost_code_id,
            is_taxable=cline.is_taxable,
            line_order=oline.line_order,
        )
        db.add(line)
        new_lines.append(line)

    # The posting a new invoice gets: DR A/R, CR income per line with its
    # job / class / cost code, CR sales tax — converted to home currency at
    # the copy's rate.
    txn = _post_invoice_journal(
        db, new_invoice, new_lines, invoice_customer_name(original) or ""
    )
    # A duplicated $0.00 invoice posts no journal, so it keeps no transaction.
    new_invoice.transaction_id = txn.id if txn is not None else None

    # Phase 11 (audit fix): a duplicated invoice is a FRESH sale, so it
    # must hit the inventory ledger just like create_invoice does.
    db.flush()
    db.refresh(new_invoice)
    post_sale_for_invoice(db, new_invoice, txn_date=today)

    db.commit()
    db.refresh(new_invoice)
    resp = InvoiceResponse.model_validate(new_invoice)
    if new_invoice.customer:
        resp.customer_name = invoice_customer_name(new_invoice)
    resp.jobs_left_off = left_off
    return resp
