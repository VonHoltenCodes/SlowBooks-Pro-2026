# ============================================================================
# Pay applications (#555) — the owner's progress billing from the schedule of
# values. The document and its lifecycle: a draft pre-filled from the SOV,
# edited while draft/submitted, approved into an invoice (and a deposit
# payment when one applies), voided back out.
#
# The math is pure (app/services/pay_applications.py); the effects — the
# invoice, its retainage journal (A/R, 1150, income, tax) and the deposit
# payment — land on approve and reverse on void.
# ============================================================================

from datetime import date as dt_date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.job_deposits import JobDeposit  # noqa: F401 (register the table)
from app.models.jobs import Job, SovLine
from app.models.pay_applications import PayApplication, PayApplicationLine
from app.schemas.pay_applications import (
    PayApplicationCreate,
    PayApplicationLineOut,
    PayApplicationOut,
    PayApplicationUpdate,
)
from app.services.accounting import (
    _q,
    create_journal_entry,
    ensure_construction_accounts,
    get_ar_account_id,
    get_default_income_account_id,
    get_sales_tax_account_id,
)
from app.services.closing_date import check_closing_date
from app.services.numbering import next_invoice_number
from app.services.payment_terms import term_id_for
from app.services.pay_applications import (
    application_figures,
    deposits_figures,
    effective_tax_rate,
    line_computed,
    previous_by_sov_line,
    retainage_outstanding,
)
from app.services.request_utils import content_disposition
from app.services.sub_customers import invoice_addresses

router = APIRouter(prefix="/api/pay-applications", tags=["pay-applications"])

_ZERO = Decimal("0")
_OPEN = ("draft", "submitted")


class _LineInput:
    """A line for the pure math: a stored line with its history `previous`."""

    __slots__ = ("scheduled_value", "previous", "this_period", "stored")

    def __init__(self, previous, ln):
        self.scheduled_value = ln.scheduled_value
        self.previous = previous
        self.this_period = ln.this_period
        self.stored = ln.stored


def _signed(account_id, amount, description, natural, **extra):
    """A journal leg that puts `amount` on its account's natural side (debit
    or credit) and flips to the other side when it is negative. None when the
    amount rounds to zero, so the caller can drop it."""
    amount = _q(amount)
    if amount == 0:
        return None
    if (natural == "debit") == (amount > 0):
        debit, credit = abs(amount), _ZERO
    else:
        debit, credit = _ZERO, abs(amount)
    return {
        "account_id": account_id,
        "debit": debit,
        "credit": credit,
        "description": description,
        **extra,
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _get(db: Session, app_id: int) -> PayApplication:
    app = db.get(PayApplication, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Pay application not found")
    return app


def _sub_waivers(db: Session, job_id: int, period_to) -> dict:
    """The job's bills dated on/before the period whose lien-waiver status is
    missing or pending — what the GC should collect before submitting. Warn
    only."""
    from app.models.bills import Bill, BillStatus

    q = db.query(Bill).filter(Bill.job_id == job_id, Bill.status != BillStatus.VOID)
    if period_to is not None:
        q = q.filter(Bill.date <= period_to)
    bills = []
    for b in q.order_by(Bill.date).all():
        if b.lien_waiver_status in (None, "pending"):
            bills.append(
                {
                    "bill_id": b.id,
                    "bill_number": b.bill_number,
                    "vendor_name": b.vendor.name if b.vendor else "",
                    "date": b.date.isoformat() if b.date else None,
                    "lien_waiver_status": b.lien_waiver_status,
                }
            )
    return {"bills": bills, "missing": len(bills)}


def _line_obj(prev, ln):
    return _LineInput(prev.get(ln.sov_line_id, _ZERO), ln)


def _out(db: Session, app: PayApplication) -> PayApplicationOut:
    prev = previous_by_sov_line(db, app.job_id, app.application_no)
    lines = []
    for ln in app.lines:
        row = line_computed(_line_obj(prev, ln), app.retainage_pct)
        lines.append(
            PayApplicationLineOut(
                id=ln.id,
                sov_line_id=ln.sov_line_id,
                line_no=ln.line_no,
                item_no=ln.item_no,
                description=ln.description,
                **row,
            )
        )
    totals = {
        k: (float(v) if isinstance(v, Decimal) else v)
        for k, v in application_figures(db, app).items()
    }
    out = PayApplicationOut.model_validate(app)
    out.lines = lines
    out.totals = totals
    out.job_number = app.job.job_number if app.job else None
    out.tax_exempt = bool(
        app.job and app.job.customer and app.job.customer.is_taxable is False
    )
    out.invoice_number = app.invoice.invoice_number if app.invoice else None
    out.sub_waivers = _sub_waivers(db, app.job_id, app.period_to)
    out.unapplied_deposits = float(deposits_figures(db, app.job_id)["unapplied"])
    out.retainage_outstanding = float(retainage_outstanding(db, app.job_id))
    return out


def _validate(db: Session, app: PayApplication) -> None:
    """The per-line and header guards (422): a line billed past its scheduled
    value (in its own direction — a deduct line bills down), a release past
    the held-to-date, a deposit past what is unapplied or past the current due."""
    prev = previous_by_sov_line(db, app.job_id, app.application_no)
    for ln in app.lines:
        scheduled = _q(ln.scheduled_value)
        completed = _q(
            prev.get(ln.sov_line_id, _ZERO) + _q(ln.this_period) + _q(ln.stored)
        )
        # Completed and stored stay on the line's own side of zero (>= 0, or
        # <= 0 on a deduct line): a negative this-period or stored cannot pull
        # work already billed below nothing (#576).
        label = f"{ln.item_no or ''} {ln.description or ''}".strip()
        wrong_side = (
            (completed < _ZERO or _q(ln.stored) < _ZERO)
            if scheduled >= _ZERO
            else (completed > _ZERO or _q(ln.stored) > _ZERO)
        )
        if wrong_side:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{label}: completed to date ({completed:.2f}) and stored "
                    f"({_q(ln.stored):.2f}) cannot go below zero."
                ).lstrip(": "),
            )
        # Direction-aware: a positive line can't bill past its scheduled value;
        # a negative (deduct) line can't be credited past it the other way.
        over = completed > scheduled if scheduled >= _ZERO else completed < scheduled
        if over:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{ln.item_no or ''} {ln.description or ''}".strip()
                    + f": completed to date {completed:.2f} is more than the "
                    f"scheduled value {scheduled:.2f}."
                ),
            )
    outstanding = retainage_outstanding(db, app.job_id)
    if _q(app.release_retainage) > outstanding:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Release {_q(app.release_retainage):.2f} is more than the "
                f"{outstanding:.2f} of retainage held to date."
            ),
        )
    # Retainage to date (gross at this application's percent, less the prior
    # and this release) cannot go below zero: dropping the percent and
    # releasing on the same application would otherwise over-bill the owner.
    held = application_figures(db, app)["retainage_to_date"]
    if held < _ZERO:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Retainage to date would be {held:.2f}: the release of "
                f"{_q(app.release_retainage):.2f} is more than the retainage "
                f"held at {app.retainage_pct}% on the work completed. Release "
                "less, or keep the retainage percent until the release is billed."
            ),
        )
    unapplied = deposits_figures(db, app.job_id)["unapplied"]
    deposit = _q(app.deposit_applied)
    if deposit > unapplied:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Deposit applied {deposit:.2f} is more than the "
                f"{unapplied:.2f} of unapplied deposits."
            ),
        )
    current_due = application_figures(db, app)["current_due"]
    if deposit > 0 and deposit > current_due:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Deposit applied {deposit:.2f} is more than the current payment "
                f"due {current_due:.2f} on this pay application."
            ),
        )


# ── CRUD ─────────────────────────────────────────────────────────────────


def _estimate_tax_rate(db: Session, job: Job) -> Decimal:
    """The job's accepted estimate's tax rate (the contract estimate, else an
    accepted estimate on the job), else 0 — what a pay application taxes with."""
    from app.models.estimates import Estimate, EstimateStatus

    est = (
        db.get(Estimate, job.contract_estimate_id) if job.contract_estimate_id else None
    )
    if est is None:
        est = (
            db.query(Estimate)
            .filter(
                Estimate.job_id == job.id, Estimate.status == EstimateStatus.ACCEPTED
            )
            .order_by(Estimate.id.desc())
            .first()
        )
    return _q6(est.tax_rate) if est and est.tax_rate else _ZERO


def _q6(value) -> Decimal:
    """Tax rates keep six places, as estimates store them (8.875% = 0.08875)."""
    from app.services.accounting import quantize_to

    return quantize_to(value, Decimal("0.000001"))


@router.get("", response_model=list[PayApplicationOut])
def list_pay_applications(job_id: int = Query(...), db: Session = Depends(get_db)):
    apps = (
        db.query(PayApplication)
        .filter(PayApplication.job_id == job_id)
        .order_by(PayApplication.application_no)
        .all()
    )
    return [_out(db, a) for a in apps]


@router.get("/{app_id}/payments")
def pay_application_payments(app_id: int, db: Session = Depends(get_db)):
    """The payments received on this application's invoice, each with the lien
    waivers already made for it on this job — what the Waivers screen lists.
    A payment split across two jobs' invoices has a waiver per job (#576)."""
    from app.models.lien_waivers import LienWaiver
    from app.models.payments import Payment, PaymentAllocation

    app = _get(db, app_id)
    if not app.invoice_id:
        return []
    payments = (
        db.query(Payment)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .filter(
            PaymentAllocation.invoice_id == app.invoice_id, Payment.is_voided.is_(False)
        )
        .order_by(Payment.date, Payment.id)
        .all()
    )
    out = []
    for pay in payments:
        waivers = (
            db.query(LienWaiver)
            .filter(LienWaiver.payment_id == pay.id, LienWaiver.job_id == app.job_id)
            .order_by(LienWaiver.id)
            .all()
        )
        out.append(
            {
                "id": pay.id,
                "date": pay.date.isoformat() if pay.date else None,
                "amount": float(pay.amount),
                "method": pay.method,
                "waivers": [
                    {"id": w.id, "waiver_type": w.waiver_type} for w in waivers
                ],
            }
        )
    return out


@router.get("/{app_id}", response_model=PayApplicationOut)
def get_pay_application(app_id: int, db: Session = Depends(get_db)):
    return _out(db, _get(db, app_id))


@router.post("", response_model=PayApplicationOut, status_code=201)
def create_pay_application(data: PayApplicationCreate, db: Session = Depends(get_db)):
    job = db.get(Job, data.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Only one draft/submitted application per job at a time.
    open_one = (
        db.query(PayApplication)
        .filter(PayApplication.job_id == job.id, PayApplication.status.in_(_OPEN))
        .first()
    )
    if open_one is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "application_open",
                "message": f"{open_one.number} is still open; submit or approve it first.",
            },
        )
    last = (
        db.query(PayApplication)
        .filter(PayApplication.job_id == job.id)
        .order_by(PayApplication.application_no.desc())
        .first()
    )
    no = (last.application_no if last else 0) + 1
    # The number keys on the job id (unique), not the job_number (which two
    # jobs can share); the job_number shows beside it (#555 review).
    number = f"PA-{job.id}-{no:02d}"
    app = PayApplication(
        job_id=job.id,
        application_no=no,
        number=number,
        date=data.date or dt_date.today(),
        period_to=data.period_to,
        status="draft",
        retainage_pct=(
            data.retainage_pct if data.retainage_pct is not None else job.retainage_pct
        ),
        tax_rate=_estimate_tax_rate(db, job),
        notes=data.notes,
    )
    db.add(app)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Could not assign a pay application number; retry."
        )
    # Pre-fill a line from every SOV line, in order — change_order, allowance
    # and contingency lines bill like work.
    for n, sov in enumerate(job.sov_lines, start=1):
        db.add(
            PayApplicationLine(
                pay_application_id=app.id,
                sov_line_id=sov.id,
                line_no=n,
                item_no=sov.item_no,
                description=sov.description,
                scheduled_value=sov.scheduled_value,
                this_period=_ZERO,
                stored=_ZERO,
            )
        )
    db.flush()
    db.refresh(app)
    db.commit()
    db.refresh(app)
    return _out(db, app)


@router.put("/{app_id}", response_model=PayApplicationOut)
def update_pay_application(
    app_id: int, data: PayApplicationUpdate, db: Session = Depends(get_db)
):
    app = _get(db, app_id)
    if app.status not in _OPEN:
        raise HTTPException(
            status_code=409,
            detail="Only a draft or submitted pay application can be edited.",
        )
    for key in (
        "date",
        "period_to",
        "retainage_pct",
        "tax_rate",
        "charge_tax",
        "release_retainage",
        "deposit_applied",
        "lien_waiver_status",
        "notes",
    ):
        val = getattr(data, key)
        if val is not None:
            setattr(app, key, val)
    if data.lines is not None:
        by_sov = {ln.sov_line_id: ln for ln in app.lines}
        for row in data.lines:
            ln = by_sov.get(row.sov_line_id)
            if ln is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"SOV line {row.sov_line_id} is not on this pay application.",
                )
            ln.this_period = _q(row.this_period)
            ln.stored = _q(row.stored)
    db.flush()
    _validate(db, app)
    db.commit()
    db.refresh(app)
    return _out(db, app)


@router.post("/{app_id}/submit", response_model=PayApplicationOut)
def submit_pay_application(app_id: int, db: Session = Depends(get_db)):
    app = _get(db, app_id)
    if app.status != "draft":
        raise HTTPException(
            status_code=409, detail="Only a draft pay application can be submitted."
        )
    _validate(db, app)
    app.status = "submitted"
    db.commit()
    db.refresh(app)
    return _out(db, app)


# ── Approve → invoice (+ deposit payment) ──────────────────────────────────


@router.post("/{app_id}/approve", response_model=PayApplicationOut)
def approve_pay_application(app_id: int, db: Session = Depends(get_db)):
    app = _get(db, app_id)
    if app.status not in _OPEN:
        raise HTTPException(
            status_code=409,
            detail="Only a draft or submitted pay application can be approved.",
        )
    # jobs.customer_id is NOT NULL, so the customer always resolves.
    job = db.get(Job, app.job_id)
    customer = db.get(Customer, job.customer_id)
    when = app.date or dt_date.today()
    check_closing_date(db, when)
    _validate(db, app)

    figures = application_figures(db, app)
    retainage_held = figures["retainage_this_period"]
    retainage_released = figures["release_retainage"]

    # This period's billable amount per line: this_period + the stored delta
    # (this application's stored − the prior approved application's stored),
    # so stored materials are billed when stored and reversed when installed.
    # A deduct line may be negative.
    prior_stored = _prior_stored(db, app)
    invoice_lines = []  # (description, amount, cost_code_id)
    for ln in app.lines:
        delta = _q(ln.stored) - prior_stored.get(ln.sov_line_id, _ZERO)
        amount = _q(_q(ln.this_period) + delta)
        if amount == 0:
            continue
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        desc = (
            f"{ln.item_no or ''} {ln.description or ''}".strip() or "Progress billing"
        )
        invoice_lines.append((desc, amount, sov.cost_code_id if sov else None))

    deposit = _q(app.deposit_applied)
    moved = any(
        _q(ln.this_period) != 0
        or _q(ln.stored) != prior_stored.get(ln.sov_line_id, _ZERO)
        for ln in app.lines
    )
    # Nothing at all to bill, hold, release, apply or move — refuse rather
    # than approve an empty application (#555 review).
    if (
        not invoice_lines
        and retainage_held == 0
        and retainage_released == 0
        and deposit == 0
        and not moved
    ):
        raise HTTPException(
            status_code=422,
            detail="This payment application has nothing to bill, hold, release or apply.",
        )

    app.contract_sum_original = job.original_contract_amount
    app.contract_sum_to_date = job.contract_amount
    invoice = None
    if invoice_lines or retainage_held != 0 or retainage_released != 0 or deposit:
        # Tax over the billed work at the application's rate when tax is on and
        # the owner is taxable — the same figure the application shows.
        tax_rate = _q6(effective_tax_rate(app))
        subtotal = _q(sum((a for _d, a, _c in invoice_lines), _ZERO))
        tax_amount = _q(subtotal * tax_rate) if tax_rate else _ZERO
        total = _q(subtotal + tax_amount)
        invoice = _create_invoice(
            db,
            customer=customer,
            job=job,
            when=when,
            lines=invoice_lines,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            total=total,
            retainage_held=retainage_held,
            retainage_released=retainage_released,
            taxable=bool(tax_rate),
            pay_application=app,
        )
    # else: the application only moved stored material into the work — zero
    # current due, so there is no invoice to post (#576).

    app.status = "approved"
    app.approved_at = datetime.now(timezone.utc)
    app.invoice_id = invoice.id if invoice is not None else None
    db.flush()

    if deposit > 0:
        _apply_deposit(db, app, invoice, customer, deposit, when)
    else:
        db.commit()
    db.refresh(app)
    return _out(db, app)


def _prior_stored(db: Session, app: PayApplication) -> dict:
    from app.services.pay_applications import stored_by_sov_line

    return stored_by_sov_line(db, app.job_id, app.application_no)


def _create_invoice(
    db,
    *,
    customer,
    job,
    when,
    lines,
    subtotal,
    tax_rate,
    tax_amount,
    total,
    retainage_held,
    retainage_released,
    taxable,
    pay_application,
):
    """Build the pay application's invoice and post its retainage journal:
    DR A/R (net of retainage), DR 1150 for retainage held, CR 1150 for
    retainage released, CR income per line, CR sales tax."""
    ar_id = get_ar_account_id(db)
    income_id = get_default_income_account_id(db)
    retainage_recv = ensure_construction_accounts(db)["1150"].id
    balance_due = _q(total - retainage_held + retainage_released)
    # The payer's whole Bill To as of now and the customer's own Ship To,
    # through the helper every other new invoice uses (a pay application's
    # invoice used to carry no street, so it printed a name alone).
    addresses = invoice_addresses(db, customer)

    invoice = None
    last_err = None
    for _ in range(10):
        number = next_invoice_number(db)
        invoice = Invoice(
            invoice_number=number,
            customer_id=customer.id,
            **addresses,
            status=(InvoiceStatus.PAID if balance_due == 0 else InvoiceStatus.SENT),
            date=when,
            due_date=when,
            terms="Net 30",
            payment_term_id=term_id_for(db, "Net 30"),
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            total=total,
            amount_paid=_ZERO,
            balance_due=balance_due,
            retainage_held=retainage_held,
            retainage_released=retainage_released,
            pay_application_id=pay_application.id,
            job_id=job.id,
            notes=f"{pay_application.number}",
        )
        db.add(invoice)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "invoice_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            invoice = None
    if invoice is None:
        raise HTTPException(
            status_code=503, detail="Could not assign an invoice number; retry."
        ) from last_err

    for i, (desc, amount, cost_code_id) in enumerate(lines):
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                description=desc,
                quantity=Decimal("1"),
                rate=amount,
                amount=amount,
                job_id=job.id,
                cost_code_id=cost_code_id,
                is_taxable=taxable,
                line_order=i,
            )
        )

    # Signed legs: a figure lands on its account's natural side, and flips to
    # the other side when it is negative — so a dropped retainage percent
    # credits 1150 and a deduct line debits income, and the entry still
    # balances (#555 review).
    journal_lines = [
        _signed(
            ar_id,
            total - retainage_held + retainage_released,
            f"{pay_application.number} - {customer.name}",
            "debit",
            job_id=job.id,
        ),
        _signed(
            retainage_recv,
            retainage_held,
            f"Retainage withheld - {pay_application.number}",
            "debit",
            job_id=job.id,
        ),
        _signed(
            retainage_recv,
            -retainage_released,
            f"Retainage released - {pay_application.number}",
            "debit",
            job_id=job.id,
        ),
    ]
    for desc, amount, cost_code_id in lines:
        journal_lines.append(
            _signed(
                income_id,
                amount,
                desc,
                "credit",
                job_id=job.id,
                cost_code_id=cost_code_id,
            )
        )
    if tax_amount != 0:
        journal_lines.append(
            _signed(get_sales_tax_account_id(db), tax_amount, "Sales tax", "credit")
        )
    journal_lines = [line for line in journal_lines if line is not None]
    txn = create_journal_entry(
        db,
        when,
        f"{pay_application.number} - {customer.name}",
        journal_lines,
        source_type="invoice",
        source_id=invoice.id,
        reference=invoice.invoice_number,
        job_id=job.id,
    )
    invoice.transaction_id = txn.id
    db.flush()
    return invoice


def _apply_deposit(db, app, invoice, customer, amount, when):
    """Apply an owner deposit to the pay application's invoice: a Payment
    whose deposit_to account is Customer Deposits (2210), so its journal is
    DR 2210 / CR A/R."""
    from app.routes.payments import create_payment
    from app.schemas.payments import PaymentAllocationCreate, PaymentCreate

    deposits_acct = ensure_construction_accounts(db)["2210"].id
    data = PaymentCreate(
        customer_id=customer.id,
        date=when,
        amount=amount,
        method="deposit",
        deposit_to_account_id=deposits_acct,
        notes=f"Deposit applied — {app.number}",
        allocations=[PaymentAllocationCreate(invoice_id=invoice.id, amount=amount)],
    )
    resp = create_payment(data, db)  # commits
    app.deposit_payment_id = resp.id
    db.commit()


# ── Void ────────────────────────────────────────────────────────────────────


@router.post("/{app_id}/void", response_model=PayApplicationOut)
def void_pay_application(app_id: int, db: Session = Depends(get_db)):
    app = _get(db, app_id)
    if app.status != "approved":
        raise HTTPException(
            status_code=409, detail="Only an approved pay application can be voided."
        )
    # Later applications are figured from this one (previous work, retainage,
    # payment applications), and their invoices are already posted; so only the latest
    # approved application can be voided (#576).
    later = (
        db.query(PayApplication)
        .filter(
            PayApplication.job_id == app.job_id,
            PayApplication.status == "approved",
            PayApplication.application_no > app.application_no,
        )
        .order_by(PayApplication.application_no)
        .first()
    )
    if later is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "later_application",
                "message": f"{later.number} is approved after this one; void it first.",
            },
        )
    invoice = db.get(Invoice, app.invoice_id) if app.invoice_id else None
    # The deposit payment, the invoice and the application are voided in one
    # transaction: a refusal anywhere (a payment still applied, the closing
    # date) rolls the lot back, so the deposit is never left voided under an
    # application and invoice that still stand (#576).
    try:
        if invoice is not None:
            _refuse_if_truly_paid(db, app, invoice)
            dep = _deposit_payment(db, app, invoice)
            if dep is not None:
                from app.routes.payments import void_payment_unchecked

                # restores the balance; the invoice void needs it clear
                void_payment_unchecked(dep.id, db, commit=False)
                db.refresh(invoice)
            from app.routes.invoices.lifecycle import void_invoice_unchecked

            void_invoice_unchecked(invoice.id, db, commit=False)  # reverses 1150
        app.status = "void"
        app.voided_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(app)
    return _out(db, app)


def _deposit_payment(db, app, invoice):
    """The deposit payment this application's approval created: the one it
    recorded on `deposit_payment_id`, else (older rows) a deposit-method
    payment to Customer Deposits (2210) allocated to the invoice."""
    from app.models.payments import Payment, PaymentAllocation

    if app.deposit_payment_id:
        dep = db.get(Payment, app.deposit_payment_id)
        if dep is not None and not dep.is_voided:
            return dep
    # No deposit was applied → there is no own deposit payment, so any payment
    # on the invoice (even a deposit-method one) is somebody else's.
    if _q(app.deposit_applied) <= 0:
        return None
    deposits_acct = ensure_construction_accounts(db)["2210"].id
    return (
        db.query(Payment)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .filter(
            PaymentAllocation.invoice_id == invoice.id,
            Payment.method == "deposit",
            Payment.deposit_to_account_id == deposits_acct,
            Payment.is_voided.is_(False),
        )
        .first()
    )


def _refuse_if_truly_paid(db, app, invoice):
    """409 invoice_paid when the invoice carries a payment that is not this
    application's own deposit payment — a real receipt has to be voided first.
    An unrelated deposit-method payment counts, so matching is by id."""
    from app.models.payments import Payment, PaymentAllocation

    own = _deposit_payment(db, app, invoice)
    own_id = own.id if own is not None else None
    others = (
        db.query(Payment)
        .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
        .filter(
            PaymentAllocation.invoice_id == invoice.id,
            Payment.is_voided.is_(False),
            Payment.id != own_id,
        )
        .first()
    )
    if others is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoice_paid",
                "message": "The invoice has a payment on it; void the payment first.",
            },
        )


# ── PDF ──────────────────────────────────────────────────────────────────


@router.get("/{app_id}/pdf")
def pay_application_pdf(app_id: int, db: Session = Depends(get_db)):
    from app.services.pdf_service import generate_pay_application_pdf
    from app.services.settings_service import get_all_settings

    app = _get(db, app_id)
    out = _out(db, app)
    company = get_all_settings(db)
    job = db.get(Job, app.job_id)
    pdf_bytes = generate_pay_application_pdf(app, out, job, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"PayApplication_{app.number}.pdf"
            )
        },
    )
