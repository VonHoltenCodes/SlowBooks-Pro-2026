# ============================================================================
# Pay applications (#555) — the owner's progress billing math, in one place.
#
# The math is pure (line_computed, application_totals): Decimal, cents
# half-up, the same arithmetic the route, the PDF and the tests read. The
# history helpers (previous work, retainage held/released to date, deposits)
# read earlier APPROVED applications live, so a voided application leaves
# nothing stale behind.
#
# An application bills a period against the schedule of values:
#   completed_to_date = previous + this_period + stored
# where `previous` is the sum of this_period on earlier approved applications
# for the same SOV line, so stored materials carry in `stored` until they are
# installed (then stored drops and this_period takes them up). Cumulative work
# billed to income equals completed_to_date.
# ============================================================================

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.accounting import _q

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def line_computed(line, retainage_pct) -> dict:
    """Per-line figures for one application line. `line` carries
    scheduled_value, previous, this_period and stored (Decimals or coercible).
    `retainage_pct` is the application's percent (e.g. 10 for 10%)."""
    scheduled = _q(getattr(line, "scheduled_value", 0))
    previous = _q(getattr(line, "previous", 0))
    this_period = _q(getattr(line, "this_period", 0))
    stored = _q(getattr(line, "stored", 0))
    pct = Decimal(str(retainage_pct or 0))
    completed = _q(previous + this_period + stored)
    pct_complete = float(_q(completed / scheduled * _HUNDRED)) if scheduled else 0.0
    return {
        "scheduled_value": scheduled,
        "previous": previous,
        "this_period": this_period,
        "stored": stored,
        "completed_to_date": completed,
        "pct_complete": pct_complete,
        "balance_to_finish": _q(scheduled - completed),
        "retainage": _q(completed * pct / _HUNDRED),
    }


def application_totals(
    lines,
    *,
    retainage_pct,
    previous_retainage,
    previous_applications,
    previous_released=0,
    previous_billed=0,
    tax_rate=0,
    release_retainage=0,
    deposit_applied=0,
) -> dict:
    """The application's totals. `lines` is any iterable of objects carrying
    scheduled_value / previous / this_period / stored. The scalars come from
    history: `previous_retainage` is the prior approved application's
    retainage_to_date, `previous_released` the retainage released on prior
    approved applications, `previous_applications` the sum of prior approved
    applications' current_due.

    Retainage to date is NET of releases: what is still held on the work
    completed. A release is just retainage going down, so the cumulative
    current-due figures line up with the invoices and account 1150 (#576).

    Sales tax rides on top: `tax_rate` (0 when tax is off for the application
    or the customer is exempt) taxes what this application bills on its
    taxable lines. What it bills is completed to date less `previous_billed`
    (the prior approved application's completed to date); a line whose
    `is_taxable` is False takes its own billing (this period plus the change
    in stored since `prior_stored`) out of the taxed figure. The
    earned/previous figures stay before tax, so the cumulative arithmetic
    never counts an earlier application's tax again; `current_due` adds this
    application's tax and matches its invoice."""
    lines = list(lines)
    rows = [line_computed(ln, retainage_pct) for ln in lines]
    scheduled_total = _q(sum((r["scheduled_value"] for r in rows), _ZERO))
    previous_total = _q(sum((r["previous"] for r in rows), _ZERO))
    this_period_total = _q(sum((r["this_period"] for r in rows), _ZERO))
    stored_total = _q(sum((r["stored"] for r in rows), _ZERO))
    completed = _q(sum((r["completed_to_date"] for r in rows), _ZERO))
    retainage_gross = _q(sum((r["retainage"] for r in rows), _ZERO))
    balance_total = _q(sum((r["balance_to_finish"] for r in rows), _ZERO))

    prev_ret = _q(previous_retainage)
    prev_cert = _q(previous_applications)
    prev_released = _q(previous_released)
    release = _q(release_retainage)
    deposit = _q(deposit_applied)

    retainage_to_date = _q(retainage_gross - prev_released - release)
    # Retainage newly withheld this period (gross, before this release): the
    # 1150 debit on the invoice. The release is the 1150 credit.
    retainage_this_period = _q(retainage_to_date - prev_ret + release)
    total_earned_less_retainage = _q(completed - retainage_to_date)
    due_before_tax = _q(total_earned_less_retainage - prev_cert)
    billed_this_period = _q(completed - _q(previous_billed))
    # A line not taxable (its SOV line's flag) bills this period plus the
    # change in its stored materials, none of it taxed.
    exempt_billed = _q(
        sum(
            (
                _q(r["this_period"] + r["stored"] - _q(getattr(ln, "prior_stored", 0)))
                for ln, r in zip(lines, rows)
                if getattr(ln, "is_taxable", True) is False
            ),
            _ZERO,
        )
    )
    taxable_billed = _q(billed_this_period - exempt_billed)
    tax_amount = _q(taxable_billed * Decimal(str(tax_rate or 0)))
    current_due = _q(due_before_tax + tax_amount)
    net_due = _q(current_due - deposit)
    return {
        "scheduled_total": scheduled_total,
        "previous_total": previous_total,
        "this_period_total": this_period_total,
        "stored_total": stored_total,
        "completed_to_date": completed,
        "retainage_to_date": retainage_to_date,
        "previous_retainage": prev_ret,
        "previous_released": prev_released,
        "retainage_this_period": retainage_this_period,
        "total_earned_less_retainage": total_earned_less_retainage,
        "previous_applications": prev_cert,
        "billed_this_period": billed_this_period,
        "taxable_billed": taxable_billed,
        "due_before_tax": due_before_tax,
        "tax_rate": Decimal(str(tax_rate or 0)),
        "tax_amount": tax_amount,
        "current_due": current_due,
        "balance_to_finish_total": balance_total,
        "release_retainage": release,
        "deposit_applied": deposit,
        "net_due": net_due,
    }


# ── History (earlier APPROVED applications, read live) ──────────────────────


def _approved(db: Session, job_id: int, before_no=None, as_of=None):
    """This job's approved applications, oldest first; optionally only those
    numbered below `before_no` (an application's own history excludes it) or
    dated on or before `as_of` (a report reading the books as of a date)."""
    from app.models.pay_applications import PayApplication

    q = db.query(PayApplication).filter(
        PayApplication.job_id == job_id,
        PayApplication.status == "approved",
    )
    if before_no is not None:
        q = q.filter(PayApplication.application_no < before_no)
    if as_of is not None:
        q = q.filter(PayApplication.date <= as_of)
    return q.order_by(PayApplication.application_no).all()


def _history(db: Session, job_id: int, before_no=None, as_of=None) -> dict:
    """One oldest-first pass over this job's approved applications numbered
    below `before_no` (all of them when None). Accumulates, with the pure
    `application_totals` and no recursion, the state an application reads from
    its history:

    - `previous`  — SOV line id → Σ this_period billed so far;
    - `stored`    — SOV line id → the latest approved application's stored;
    - `prev_retainage` — the latest approved application's retainage_to_date
      (net of releases); `prev_released` — Σ releases so far;
    - `prev_applications` — Σ of the approved applications' due before tax;
    - `billed`    — the latest approved application's completed_to_date
      (each application's figure is already cumulative; summing them would
      count every earlier period again).

    One pass keeps the list and the next approve linear, not exponential — the
    old code recursed into application_figures per prior (#555 review)."""
    previous: dict = {}
    stored: dict = {}
    prev_ret = _ZERO
    prev_released = _ZERO
    prev_cert = _ZERO
    billed = _ZERO
    for app in _approved(db, job_id, before_no, as_of=as_of):
        rows = [
            _Row(
                scheduled_value=ln.scheduled_value,
                previous=previous.get(ln.sov_line_id, _ZERO),
                this_period=ln.this_period,
                stored=ln.stored,
            )
            for ln in app.lines
        ]
        totals = application_totals(
            rows,
            retainage_pct=app.retainage_pct,
            previous_retainage=prev_ret,
            previous_applications=prev_cert,
            previous_released=prev_released,
            previous_billed=billed,
            release_retainage=app.release_retainage,
            deposit_applied=app.deposit_applied,
        )
        prev_released = _q(prev_released + _q(app.release_retainage))
        for ln in app.lines:
            if ln.sov_line_id is None:
                continue
            previous[ln.sov_line_id] = _q(
                previous.get(ln.sov_line_id, _ZERO) + _q(ln.this_period)
            )
            stored[ln.sov_line_id] = _q(ln.stored)
        prev_ret = totals["retainage_to_date"]
        prev_cert = _q(prev_cert + totals["due_before_tax"])
        billed = _q(totals["completed_to_date"])  # cumulative already; the latest wins
    return {
        "previous": previous,
        "stored": stored,
        "prev_retainage": prev_ret,
        "prev_released": prev_released,
        "prev_applications": prev_cert,
        "billed": billed,
    }


def previous_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → Σ this_period on earlier approved applications."""
    return _history(db, job_id, before_no)["previous"]


def stored_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → the latest earlier approved application's stored value."""
    return _history(db, job_id, before_no)["stored"]


def effective_tax_rate(app) -> Decimal:
    """The rate this application taxes with: its own rate when tax is on and
    the owner is taxable, else 0 (the invoice routes tax the same way). A job
    whose contractor pays the tax on materials bills no sales tax at all,
    stored materials included, whatever the SOV's Tax boxes say."""
    from app.services.contracts import contractor_pays_tax

    customer = app.job.customer if app.job is not None else None
    if not app.charge_tax or (customer is not None and customer.is_taxable is False):
        return _ZERO
    if contractor_pays_tax(app.job):
        return _ZERO
    return Decimal(str(app.tax_rate or 0))


def sov_line_in_effect(db: Session, sov, on) -> bool:
    """False for a change order's schedule line on an application dated before
    the change order takes effect: the application's contract sum does not
    carry that change yet, so its schedule must not either."""
    if sov is None or sov.change_order_id is None or on is None:
        return True
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_effective_date

    co = db.get(ChangeOrder, sov.change_order_id)
    effective = co_effective_date(co) if co is not None else None
    return effective is None or effective <= on


def refuse_lines_not_in_effect(db: Session, app) -> None:
    """409 when a line carrying an amount is a change order's schedule line
    and the application is dated before that change order takes effect: the
    application's contract sum does not carry the change yet, so it cannot
    bill it (M bug 2: moving the date to 9/25, billing CO-003's line, and
    moving it back to 9/01 billed a change effective 9/20). A zero line is
    left alone (it is hidden, as before)."""
    from app.models.change_orders import ChangeOrder
    from app.models.jobs import SovLine
    from app.services.change_orders import co_effective_date

    for ln in app.lines:
        if _q(ln.this_period) == _ZERO and _q(ln.stored) == _ZERO:
            continue
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        if sov_line_in_effect(db, sov, app.date):
            continue
        co = db.get(ChangeOrder, sov.change_order_id)
        effective = co_effective_date(co)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "change_order_not_in_effect",
                "message": f"{co.number} takes effect on {effective.isoformat()}, "
                f"after this application's date ({app.date.isoformat()}), so its "
                f"line {sov.item_no or ''} cannot bill on it. Date the application "
                f"{effective.isoformat()} or later, or clear the line's amounts.",
            },
        )


def lines_in_effect(db: Session, app) -> list:
    """The application's lines less a change order's line that takes effect
    after the application's date (one already billed on stays: it is money)."""
    from app.models.jobs import SovLine

    kept = []
    for ln in app.lines:
        billed = _q(ln.this_period) != _ZERO or _q(ln.stored) != _ZERO
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        if billed or sov_line_in_effect(db, sov, app.date):
            kept.append(ln)
    return kept


def application_figures(db: Session, app) -> dict:
    """Full totals for one application, with its lines' `previous` filled from
    one history pass. Reads live, so it is correct whether draft or approved."""
    h = _history(db, app.job_id, app.application_no)
    rows = [
        _Row(
            scheduled_value=ln.scheduled_value,
            previous=h["previous"].get(ln.sov_line_id, _ZERO),
            this_period=ln.this_period,
            stored=ln.stored,
            is_taxable=sov_line_taxable(db, ln.sov_line_id),
            prior_stored=h["stored"].get(ln.sov_line_id, _ZERO),
        )
        for ln in lines_in_effect(db, app)
    ]
    figures = application_totals(
        rows,
        retainage_pct=app.retainage_pct,
        previous_retainage=h["prev_retainage"],
        previous_applications=h["prev_applications"],
        previous_released=h["prev_released"],
        previous_billed=h["billed"],
        tax_rate=effective_tax_rate(app),
        release_retainage=app.release_retainage,
        deposit_applied=app.deposit_applied,
    )
    # The contractor pays the tax on this job's materials: nothing billed is
    # taxed, so "taxable billed" reads 0 beside the 0 tax.
    from app.services.contracts import contractor_pays_tax

    if contractor_pays_tax(app.job):
        figures["taxable_billed"] = _ZERO
    return figures


def sov_balance_warnings(job) -> list[str]:
    """A sentence when the schedule of values does not add up to the contract
    sum to date: a pay application prints the sum above line detail that
    totals something else. Not a refusal (a job may be mid-setup). Nothing for
    a job with no sum or no lines yet."""
    if job.contract_amount is None or not job.sov_lines:
        return []
    sov_total = _q(sum((Decimal(str(r.scheduled_value)) for r in job.sov_lines), _ZERO))
    gap = _q(Decimal(str(job.contract_amount)) - sov_total)
    if gap == 0:
        return []
    way = "short of" if gap > 0 else "over"
    return [
        f"The schedule of values totals ${sov_total:,.2f}, ${abs(gap):,.2f} {way} "
        f"the contract sum to date of ${_q(Decimal(str(job.contract_amount))):,.2f}. "
        "Pay applications print the contract sum above lines that do not add up "
        "to it: add the difference to the schedule or correct the contract sum."
    ]


def application_warnings(db: Session, app) -> list[str]:
    """Sentences for an open or approved pay application that will quietly do
    less than it looks like: a schedule of values that does not foot to the
    contract sum, and taxable lines billed at a tax rate of 0 on a job where
    the owner is charged tax."""
    if app.status == "void":
        return []
    warnings = sov_balance_warnings(app.job) if app.job is not None else []
    customer = app.job.customer if app.job is not None else None
    from app.services.contracts import contractor_pays_tax

    taxed = (
        app.charge_tax
        and not (customer is not None and customer.is_taxable is False)
        and not contractor_pays_tax(app.job)
    )
    if (
        taxed
        and not Decimal(str(app.tax_rate or 0))
        and any(sov_line_taxable(db, ln.sov_line_id) for ln in lines_in_effect(db, app))
    ):
        warnings.append(
            f"{app.number} has taxable lines but a tax rate of 0, so it bills no "
            "sales tax. A job takes its pay application tax rate from its accepted "
            "estimate, and this one has none: set the Tax rate on the application, "
            "or untick Charge tax if none is due."
        )
    return warnings


def retainage_outstanding(db: Session, job_id: int, as_of=None) -> Decimal:
    """Owner retainage still held — the latest approved application's
    retainage_to_date, which is already net of releases (dated on or before
    `as_of` when given)."""
    return _history(db, job_id, as_of=as_of)["prev_retainage"]


def retainage_held_to_date(db: Session, job_id: int, as_of=None) -> Decimal:
    """Owner retainage withheld to date, before releases (outstanding +
    released), so held − released reads as what is still outstanding."""
    return _q(
        retainage_outstanding(db, job_id, as_of=as_of)
        + retainage_released_to_date(db, job_id, as_of=as_of)
    )


def retainage_released_to_date(db: Session, job_id: int, as_of=None) -> Decimal:
    """Σ release_retainage of approved applications (dated ≤ `as_of`)."""
    return _q(
        sum(
            (_q(a.release_retainage) for a in _approved(db, job_id, as_of=as_of)), _ZERO
        )
    )


def deposits_figures(db: Session, job_id: int) -> dict:
    """Owner deposits: received (non-void), applied (Σ approved applications'
    deposit_applied) and unapplied."""
    from app.models.job_deposits import JobDeposit

    received = _q(
        sum(
            (
                _q(d.amount)
                for d in db.query(JobDeposit).filter(
                    JobDeposit.job_id == job_id, JobDeposit.voided_at.is_(None)
                )
            ),
            _ZERO,
        )
    )
    applied = _q(sum((_q(a.deposit_applied) for a in _approved(db, job_id)), _ZERO))
    return {
        "received": received,
        "applied": applied,
        "unapplied": _q(received - applied),
    }


def billed_to_date(db: Session, job_id: int, as_of=None) -> Decimal:
    """Owner billing to date — the latest approved application's cumulative
    completed_to_date (not a sum across applications), counting only
    applications dated on or before `as_of` when given."""
    return _history(db, job_id, as_of=as_of)["billed"]


def billed_completed_by_sov_line(db: Session, job_id: int) -> dict:
    """SOV line id → completed_to_date billed across approved applications
    (Σ this_period of all approved + the latest approved application's
    stored). What a line cannot be dropped or shrunk below (#555)."""
    h = _history(db, job_id)
    prev, stored = h["previous"], h["stored"]
    keys = set(prev) | set(stored)
    return {k: _q(prev.get(k, _ZERO) + stored.get(k, _ZERO)) for k in keys}


def has_approved_application(db: Session, job_id: int, as_of=None) -> bool:
    """Whether the job has an approved application dated on or before `as_of`
    (any approved one when `as_of` is None). The contract summary and the WIP
    schedule read this to decide whether billing comes from the applications
    or from posted revenue."""
    return bool(_approved(db, job_id, as_of=as_of))


def sov_line_taxable(db: Session, sov_line_id) -> bool:
    """Whether an application line's SOV line is taxable; a line with no SOV
    line (or one since removed) is taxed, as every line was before the flag."""
    from app.models.jobs import SovLine

    sov = db.get(SovLine, sov_line_id) if sov_line_id else None
    return True if sov is None else bool(sov.is_taxable)


class _Row:
    """A lightweight line for the pure math, so history-filled `previous`
    (and, for the tax, the line's taxable flag and prior stored) rides
    beside the stored this_period / stored."""

    __slots__ = (
        "scheduled_value",
        "previous",
        "this_period",
        "stored",
        "is_taxable",
        "prior_stored",
    )

    def __init__(
        self,
        scheduled_value,
        previous,
        this_period,
        stored,
        is_taxable=True,
        prior_stored=_ZERO,
    ):
        self.scheduled_value = scheduled_value
        self.previous = previous
        self.this_period = this_period
        self.stored = stored
        self.is_taxable = is_taxable
        self.prior_stored = prior_stored
