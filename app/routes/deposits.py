# ============================================================================
# Deposit Recording — Move funds from Undeposited Funds to bank account
# Classic QB "Make Deposits" workflow
#
# A deposit made from the list names the payments it took (each one's
# Undeposited Funds line carries the deposit), so a deposited payment can't
# be voided out from under it and a deposit can be voided to put its
# payments back on the list. See services/undeposited_funds.py.
# ============================================================================

from app.routes._print import print_html
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
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
    DepositUpdate,
    PendingDepositResponse,
)
from app.services.accounting import _q, create_journal_entry, get_undeposited_funds_id
from app.services.bank_posting import (
    reconciled_on,
    replace_journal_lines,
    uncleared_note,
    uncleared_rows,
    void_document,
)
from app.services.bank_register import date_text
from app.services.closing_date import check_closing_date
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_from_db
from app.services.undeposited_funds import (
    dead_transaction_ids,
    reconciled,
    refuse_inactive_deposit_account,
    waiting_balance,
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


def _lock_taken(db: Session, chosen, allow_deposit_id: int = None) -> None:
    """Row-lock the chosen Undeposited Funds lines (no-op on SQLite) and
    refuse (409) if any was taken by another deposit since the page loaded
    — so two deposits at once can't both take the same payment. A line this
    deposit already holds (`allow_deposit_id`) is fine when editing."""
    if not chosen:
        return
    # populate_existing: the lines are already in this session from
    # waiting_items(), and without it the query hands back those copies as
    # read before the lock, so a deposit that committed while this one
    # waited went unseen and the payment was deposited twice (#501).
    locked = (
        db.query(TransactionLine)
        .filter(TransactionLine.id.in_([ln.id for ln in chosen]))
        .with_for_update()
        .populate_existing()
        .all()
    )
    if any(
        ln.deposit_transaction_id and ln.deposit_transaction_id != allow_deposit_id
        for ln in locked
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "One of the payments you ticked was deposited a moment ago. "
                "Reload Make Deposits and tick them again."
            ),
        )


def _over_waiting(total: Decimal, waiting_total: Decimal) -> str:
    """The refusal when the payments ticked come to more than is left in
    Undeposited Funds (an amount typed on an earlier deposit took part)."""
    return (
        f"The payments ticked add up to ${total:,.2f}, but only "
        f"${waiting_total:,.2f} is still waiting in Undeposited Funds: an "
        "earlier deposit typed as an amount took part of them. Deposit "
        f"${waiting_total:,.2f} or less (tick fewer payments, or tick none "
        "and type the amount)."
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


@router.get("/test-slip")
def test_print_deposit_slip(
    deposit_slip_layout: str = Query(default=None),
    deposit_slip_nudge_x_in: str = Query(default=None),
    deposit_slip_nudge_y_in: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """A sample deposit laid onto pre-printed slip stock (#798): made-up
    figures (two checks, some cash, today's date, "Sample Checking") at the
    places the layout gives, through the same renderer as a real deposit,
    so the layout can be lined up by holding the sheet against a slip.

    The Settings page passes the form's current (unsaved) layout and
    nudges, each checked the way saving it would be; any left out reads the
    saved setting. It is always the pre-printed slip, whatever the stock
    setting says, since that is what it lines up. Nothing is stored."""
    from datetime import date

    from fastapi.responses import Response

    from app.services.pdf_service import SAMPLE_DEPOSIT, generate_deposit_pdf
    from app.services.request_utils import content_disposition
    from app.services.settings_service import (
        SettingValueError,
        clean_setting_value,
        get_all_settings,
    )

    company = dict(get_all_settings(db))
    for key, value in (
        ("deposit_slip_layout", deposit_slip_layout),
        ("deposit_slip_nudge_x_in", deposit_slip_nudge_x_in),
        ("deposit_slip_nudge_y_in", deposit_slip_nudge_y_in),
    ):
        if value is not None:
            try:
                company[key] = clean_setting_value(key, value)
            except SettingValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
    company["deposit_slip_stock"] = "preprinted"
    sample = {**SAMPLE_DEPOSIT, "date": date.today()}
    pdf = generate_deposit_pdf(sample, company)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition("Deposit_slip_test_print.pdf")
        },
    )


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


@router.get("/{deposit_id}/pdf")
def deposit_pdf(deposit_id: int, db: Session = Depends(get_db)):
    """The deposit slip as a PDF (#260): the account, the date, each
    payment taken and the total; a void deposit's says VOID. Tagged like
    every other document."""
    from fastapi.responses import Response

    from app.services.pdf_service import generate_deposit_pdf
    from app.services.request_utils import content_disposition
    from app.services.settings_service import get_all_settings

    detail = get_deposit(deposit_id, db)
    pdf = generate_deposit_pdf(detail.model_dump(), get_all_settings(db))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Deposit_{detail.date.isoformat()}.pdf"
            )
        },
    )


@router.get("/{deposit_id}/print-preview")
def deposit_print_preview(deposit_id: int, db: Session = Depends(get_db)):
    """The slip as a page for the browser's print dialog."""
    from fastapi.responses import HTMLResponse

    from app.services.pdf_service import render_deposit_html
    from app.services.settings_service import get_all_settings

    detail = get_deposit(deposit_id, db)
    html_str = render_deposit_html(detail.model_dump(), get_all_settings(db))
    html_str = print_html(html_str)
    return HTMLResponse(content=html_str)


@router.post("")
def create_deposit(data: DepositCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

    bank_account = (
        db.query(Account).filter(Account.id == data.deposit_to_account_id).first()
    )
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    # an account deactivated in the chart takes no new deposit (#741); an
    # existing deposit keeps its account, and editing it never changes it
    refuse_inactive_deposit_account(db, bank_account.id)

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
        _lock_taken(db, chosen)
        total = _q(sum((Decimal(str(ln.debit)) for ln in chosen), Decimal("0")))
        if data.total is not None and _q(data.total) != total:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The payments ticked add up to ${total:,.2f}, not "
                    f"${_q(data.total):,.2f}. Reload Make Deposits and try again."
                ),
            )
        # A typed (lump) deposit nets only against whole items, so after one
        # took part of a payment that payment still lists in full. The ticked
        # total is capped by the live balance too, or ticking it would take
        # the part already deposited a second time.
        waiting_total = _q(waiting_balance(db, uf_id))
        if total > waiting_total:
            raise HTTPException(
                status_code=400, detail=_over_waiting(total, waiting_total)
            )
    else:
        # No payments ticked: a typed lump sum. Cap it at what is actually
        # waiting in Undeposited Funds — the waiting items less what an
        # earlier partial lump deposit already took from them — otherwise a
        # number the page added up can post more than ever came in.
        total = _q(data.total or 0)
        waiting_total = _q(waiting_balance(db, uf_id))
        if total > waiting_total:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Only ${waiting_total:,.2f} is waiting in Undeposited "
                    f"Funds; you asked to deposit ${total:,.2f}. Reload Make "
                    f"Deposits and try again."
                ),
            )

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


@router.put("/{deposit_id}", response_model=DepositDetailResponse)
def update_deposit(deposit_id: int, data: DepositUpdate, db: Session = Depends(get_db)):
    """Edit a deposit in place from the register (#388), the way the
    bank-entry edit rewrites a register entry: the same journal entry, its
    date, slip reference, class and job, and which undeposited payments it
    takes. The two ledger lines (DR bank / CR Undeposited Funds) are
    reposted through `replace_journal_lines` — not mutated by hand — so the
    deposit's statement match and audit trail stay with it; the payments it
    no longer takes go back on the Make Deposits list and the new ones are
    stamped with it. A deposit on a reconciled bank statement keeps its
    amount and date (a change is refused with 400, as its void is)."""
    txn = (
        db.query(Transaction)
        .options(selectinload(Transaction.lines))
        .filter(Transaction.id == deposit_id, Transaction.source_type == "deposit")
        .with_for_update()
        .first()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if txn.id in dead_transaction_ids(db, [txn]):
        raise HTTPException(
            status_code=400,
            detail="This deposit is void; a void deposit is not edited.",
        )
    bank_line = next((ln for ln in txn.lines if ln.debit > 0), None)
    if bank_line is None:
        raise HTTPException(status_code=400, detail="This deposit has no bank line")
    bank_account = db.get(Account, bank_line.account_id)
    old_total = _q(bank_line.debit)

    uf_id = get_undeposited_funds_id(db)
    held = (
        db.query(TransactionLine)
        .filter(TransactionLine.deposit_transaction_id == txn.id)
        .all()
    )
    # A deposit Make Deposits posted is exactly DR bank / CR Undeposited
    # Funds, and only that shape is reposted from the payments it takes. An
    # IIF or QuickBooks-report import posts deposits with their own splits —
    # straight to income or other accounts, with classes and memos — and
    # rewriting one as bank / Undeposited Funds would take its income off
    # the books (#388 review: DR Checking 500 / CR Sales 500 came back as
    # Sales 0, Undeposited Funds -500 after a reference fix). Such a deposit
    # keeps every line; only its date, reference, class and job change.
    others = [ln for ln in txn.lines if ln is not bank_line]
    standard = (
        len(others) == 1
        and others[0].account_id == uf_id
        and _q(others[0].credit) == old_total
        and _q(others[0].debit) == 0
    )
    if not standard and data.line_ids and set(data.line_ids) != {ln.id for ln in held}:
        accts = [db.get(Account, ln.account_id) for ln in others]
        names = ", ".join(
            dict.fromkeys(a.name for a in accts if a is not None and a.id != uf_id)
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"This deposit posts to {names or 'its own accounts'}, not only "
                "Undeposited Funds (it was imported that way), so the payments "
                "it takes and its amount cannot be changed here. Its date and "
                "reference can; to change the amount, void it and deposit again."
            ),
        )
    # An empty or omitted `line_ids` leaves the payments alone — a legacy
    # deposit that named none (imported, or an amount posted by hand) can
    # still have its date or reference fixed, and the form omits `line_ids`
    # when nothing was re-ticked (#388 review). A non-empty list sets which
    # payments it takes: the ones it holds now plus any still waiting.
    if data.line_ids and standard:
        pool = {ln.id: ln for ln in held}
        for ln, _ in waiting_items(db, uf_id):
            pool.setdefault(ln.id, ln)
        chosen = []
        for line_id in dict.fromkeys(data.line_ids):
            line = pool.get(line_id)
            if line is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "One of the payments you ticked is not on this deposit "
                        "and is no longer waiting. Reload and tick them again."
                    ),
                )
            chosen.append(line)
        _lock_taken(db, chosen, allow_deposit_id=txn.id)
        new_total = _q(sum((Decimal(str(ln.debit)) for ln in chosen), Decimal("0")))
        if new_total <= 0:
            raise HTTPException(
                status_code=400, detail="A deposit takes at least one payment"
            )
        # What this deposit may take: what is waiting now plus what it holds
        # (released, those wait again) — never more than came in.
        available = _q(waiting_balance(db, uf_id)) + old_total
        if new_total > available:
            raise HTTPException(
                status_code=400, detail=_over_waiting(new_total, available)
            )
    else:
        chosen = held
        new_total = old_total

    new_date = data.date if data.date is not None else txn.date
    new_ref = data.reference if "reference" in data.model_fields_set else txn.reference
    new_class = data.class_id if "class_id" in data.model_fields_set else txn.class_id
    new_job = data.job_id if "job_id" in data.model_fields_set else txn.job_id

    recon = reconciled_on(db, txn)
    if recon is not None and (new_date != txn.date or new_total != old_total):
        raise HTTPException(
            status_code=400,
            detail=(
                f"This deposit was reconciled on the "
                f"{date_text(recon.statement_date)} statement, so its amount "
                "and date cannot be changed, just as it cannot be voided. "
                "Correct the difference with a new deposit."
            ),
        )
    check_closing_date(db, txn.date)
    check_closing_date(db, new_date)

    if standard:
        label = (
            f"Deposit to {bank_account.name}"
            if bank_account
            else (txn.description or "")
        )
        lines = [
            {
                "account_id": bank_line.account_id,
                "debit": new_total,
                "credit": Decimal("0"),
                "description": label,
            },
            {
                "account_id": uf_id,
                "debit": Decimal("0"),
                "credit": new_total,
                "description": label,
            },
        ]
    else:
        # every line as it stands, each dimension named so nothing is
        # re-derived: the amounts, memos, classes, jobs and cost codes stay
        label = txn.description or ""
        lines = [
            {
                "account_id": ln.account_id,
                "debit": ln.debit,
                "credit": ln.credit,
                "description": ln.description or "",
                "class_id": ln.class_id,
                "job_id": ln.job_id,
                "cost_code_id": ln.cost_code_id,
                "is_billable": bool(ln.is_billable),
                "function": ln.function,
            }
            for ln in txn.lines
        ]
    uncleared = replace_journal_lines(
        db,
        txn,
        new_date,
        label,
        lines,
        source_type="deposit",
        reference=new_ref or "",
        class_id=new_class,
        job_id=new_job,
    )
    chosen_ids = {ln.id for ln in chosen}
    for ln in held:
        if ln.id not in chosen_ids:
            ln.deposit_transaction_id = None
    for ln in chosen:
        ln.deposit_transaction_id = txn.id
    db.commit()
    out = get_deposit(txn.id, db)
    # a new total takes the cleared mark off the bank line: say so, as the
    # journal entry's edit does
    out.uncleared = uncleared_rows(uncleared)
    out.note = uncleared_note(uncleared)
    return out


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
