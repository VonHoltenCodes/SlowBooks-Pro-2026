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

from contextlib import contextmanager
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session, joinedload, selectinload

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
    prior_tax_exact=0,
    prior_tax=0,
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
    application's tax and matches its invoice.

    An application that brings a taxable line to 100% complete trues the tax
    up: it charges the unrounded tax of every application so far
    (`prior_tax_exact`, plus its own) rounded once, less the tax already
    billed (`prior_tax`). Rounding each application's tax on its own drifts a
    cent from the bid's over several applications; billed to 100%, the total
    tax is the bid's to the cent. A partial application rounds its own."""
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
    rate = Decimal(str(tax_rate or 0))
    tax_exact = taxable_billed * rate
    completes_taxable_line = any(
        getattr(ln, "is_taxable", True) is not False
        and r["scheduled_value"] != _ZERO
        and r["completed_to_date"] == r["scheduled_value"]
        and _q(r["previous"] + _q(getattr(ln, "prior_stored", 0)))
        != r["scheduled_value"]
        for ln, r in zip(lines, rows)
    )
    if rate and completes_taxable_line:
        tax_amount = _q(_q(Decimal(str(prior_tax_exact)) + tax_exact) - _q(prior_tax))
    else:
        tax_amount = _q(tax_exact)
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
        # unrounded, for the next application's true-up
        "tax_exact": tax_exact,
        "current_due": current_due,
        "balance_to_finish_total": balance_total,
        "release_retainage": release,
        "deposit_applied": deposit,
        "net_due": net_due,
    }


# ── History (earlier APPROVED applications, read live) ──────────────────────


def _approved(db: Session, job_id: int, before_no=None, as_of=None, lines=False):
    """This job's approved applications, oldest first; optionally only those
    numbered below `before_no` (an application's own history excludes it) or
    dated on or before `as_of` (a report reading the books as of a date).
    `lines` loads each one's lines and invoice with it."""
    from app.models.pay_applications import PayApplication

    q = db.query(PayApplication).filter(
        PayApplication.job_id == job_id,
        PayApplication.status == "approved",
    )
    if lines:
        # The history walks every application's lines and invoice: lazy, that
        # was a query per earlier application each time an application's
        # figures were read (#731).
        q = q.options(
            selectinload(PayApplication.lines),
            joinedload(PayApplication.invoice),
        )
    if before_no is not None:
        q = q.filter(PayApplication.application_no < before_no)
    if as_of is not None:
        q = q.filter(PayApplication.date <= as_of)
    return q.order_by(PayApplication.application_no).all()


# ── One history pass per read ───────────────────────────────────────────────
#
# Reading one application's figures asks for the same history a dozen times
# (its previous, its stored, the carried lines, the totals, the retainage
# held, the warnings, the contract sums), and each pass walks every earlier
# approved application: the 16th application of a job took three times as
# long to edit as on 2.31.10 (#731). Inside `history_cache(db)` a pass is
# kept by (job, before_no, as_of) and reused, and any write drops it: a
# flush, a commit or rollback, a bulk UPDATE/DELETE or raw SQL, and the
# session holding unflushed changes all mean the next read walks again.

_HISTORY_CACHE = "pay_application_history"
_HISTORY_DEPTH = "pay_application_history_depth"


@contextmanager
def history_cache(db: Session):
    """Reuse history passes for the duration of a read (nests)."""
    depth = db.info.get(_HISTORY_DEPTH, 0)
    db.info[_HISTORY_DEPTH] = depth + 1
    if depth == 0:
        db.info[_HISTORY_CACHE] = {}
    try:
        yield
    finally:
        db.info[_HISTORY_DEPTH] = depth
        if depth == 0:
            db.info.pop(_HISTORY_CACHE, None)


def _drop_history_cache(session, *_args) -> None:
    cache = session.info.get(_HISTORY_CACHE)
    if cache:
        cache.clear()


for _event in ("after_flush", "after_commit", "after_rollback", "after_soft_rollback"):
    event.listen(Session, _event, _drop_history_cache)


@event.listens_for(Session, "do_orm_execute")
def _drop_history_cache_on_write(state) -> None:
    if not state.is_select:
        _drop_history_cache(state.session)


def _pending_changes(db: Session) -> bool:
    """Unflushed changes to anything a history pass reads (the applications,
    their lines and invoices, the SOV lines' tax flags, the job and its
    customer). Others, such as the audit rows a flush leaves to the next one,
    do not change a pass."""
    from app.models.contacts import Customer
    from app.models.invoices import Invoice
    from app.models.jobs import Job, SovLine
    from app.models.pay_applications import PayApplication, PayApplicationLine

    read = (PayApplication, PayApplicationLine, Invoice, SovLine, Job, Customer)
    return any(
        isinstance(obj, read) for objs in (db.new, db.deleted, db.dirty) for obj in objs
    )


def _copy_history(h: dict) -> dict:
    """A caller's own copy: the line maps are dicts a caller may change."""
    return {k: dict(v) if isinstance(v, dict) else v for k, v in h.items()}


def _history(db: Session, job_id: int, before_no=None, as_of=None) -> dict:
    """`_history_pass`, reused inside `history_cache` while nothing has been
    written since (see above)."""
    cache = db.info.get(_HISTORY_CACHE)
    if cache is None:
        return _history_pass(db, job_id, before_no, as_of)
    key = (job_id, before_no, as_of)
    if key in cache and not _pending_changes(db):
        return _copy_history(cache[key])
    h = _history_pass(db, job_id, before_no, as_of)
    # the pass's query flushed whatever was pending; a session that still
    # holds changes (autoflush off) keeps nothing
    if not _pending_changes(db):
        cache[key] = h
    return _copy_history(h)


def _history_pass(db: Session, job_id: int, before_no=None, as_of=None) -> dict:
    return _history_walk(db, job_id, before_no, as_of)[0]


def prime_history(db: Session, job_id: int, before_nos) -> None:
    """Inside `history_cache`, fill the cache for every application numbered
    in `before_nos` (and the whole job) from ONE walk: a list of the job's
    applications reads each one's history, and each is a prefix of the
    next's, so walking them one by one was quadratic (#731)."""
    cache = db.info.get(_HISTORY_CACHE)
    if cache is None or _pending_changes(db):
        return
    final, snapshots = _history_walk(db, job_id, stops=before_nos)
    if _pending_changes(db):
        return
    cache[(job_id, None, None)] = final
    for no, h in snapshots.items():
        cache[(job_id, no, None)] = h


def _history_walk(db: Session, job_id: int, before_no=None, as_of=None, stops=()):
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
      count every earlier period again);
    - `tax_exact` — Σ the applications' unrounded tax, and `tax_billed` Σ
      the tax their invoices charged, for a completing application's true-up.

    One pass keeps the list and the next approve linear, not exponential — the
    old code recursed into application_figures per prior (#555 review).

    Returns that state and, for each application number in `stops`, the
    state its own history reads (the applications numbered below it)."""
    previous: dict = {}
    stored: dict = {}
    prev_ret = _ZERO
    prev_released = _ZERO
    prev_cert = _ZERO
    billed = _ZERO
    tax_exact = _ZERO
    tax_billed = _ZERO
    apps = _approved(db, job_id, before_no, as_of=as_of, lines=True)
    taxable = sov_lines_taxable(
        db, {ln.sov_line_id for app in apps for ln in app.lines}
    )
    pending_stops = sorted(set(stops))
    snapshots = {}

    def state():
        return {
            "previous": dict(previous),
            "stored": dict(stored),
            "prev_retainage": prev_ret,
            "prev_released": prev_released,
            "prev_applications": prev_cert,
            "billed": billed,
            "tax_exact": tax_exact,
            "tax_billed": tax_billed,
        }

    for app in apps:
        while pending_stops and pending_stops[0] <= app.application_no:
            snapshots[pending_stops.pop(0)] = state()
        rows = [
            _Row(
                scheduled_value=ln.scheduled_value,
                previous=previous.get(ln.sov_line_id, _ZERO),
                this_period=ln.this_period,
                stored=ln.stored,
                is_taxable=taxable.get(ln.sov_line_id, True),
                prior_stored=stored.get(ln.sov_line_id, _ZERO),
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
            tax_rate=effective_tax_rate(app),
            release_retainage=app.release_retainage,
            deposit_applied=app.deposit_applied,
            prior_tax_exact=tax_exact,
            prior_tax=tax_billed,
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
        tax_exact += totals["tax_exact"]
        # the tax its invoice charged (none when it only moved stored work,
        # nor at a rate of 0)
        if totals["tax_rate"] and app.invoice is not None:
            tax_billed = _q(tax_billed + _q(app.invoice.tax_amount))
    for no in pending_stops:
        snapshots[no] = state()
    return state(), snapshots


def previous_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → Σ this_period on earlier approved applications."""
    return _history(db, job_id, before_no)["previous"]


def stored_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → the latest earlier approved application's stored value."""
    return _history(db, job_id, before_no)["stored"]


def carried_by_earlier(db: Session, job_id: int, before_no=None) -> set:
    """SOV line ids an earlier approved application billed work on or left a
    stored balance on: money already on the schedule, so a later application
    carries the line whatever its date."""
    h = _history(db, job_id, before_no)
    return {
        sov_id
        for sov_id in set(h["previous"]) | set(h["stored"])
        if h["previous"].get(sov_id, _ZERO) != _ZERO
        or h["stored"].get(sov_id, _ZERO) != _ZERO
    }


def effective_tax_rate(app) -> Decimal:
    """The rate this application taxes with: its own rate when tax is on and
    the owner is taxable, else 0 (the invoice routes tax the same way). A job
    whose contractor pays the tax on materials bills no sales tax at all,
    stored materials included, whatever the SOV's Tax boxes say."""
    from app.services.contracts import contractor_pays_tax

    if not app.tax_rate:
        return _ZERO
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
    """409 when a line bills something on a change order's schedule line and
    the application is dated before that change order takes effect: the
    application's contract sum does not carry the change yet, so it cannot
    bill it (moving the date past the change order, billing its line and
    moving the date back would bill a change not yet in effect). A line that
    bills nothing new (no work this period, its stored balance as the
    earlier applications left it) is left alone."""
    from app.models.change_orders import ChangeOrder
    from app.models.jobs import SovLine
    from app.services.change_orders import co_effective_date

    prior_stored = stored_by_sov_line(db, app.job_id, app.application_no)
    for ln in app.lines:
        if _q(ln.this_period) == _ZERO and _q(ln.stored) == prior_stored.get(
            ln.sov_line_id, _ZERO
        ):
            continue
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        if sov_line_in_effect(db, sov, app.date):
            continue
        co = db.get(ChangeOrder, sov.change_order_id)
        effective = co_effective_date(co)
        # A change order's line is often described by its number alone,
        # which the sentence already names.
        name = sov.item_no or (
            sov.description if sov.description != co.number else None
        )
        line = f"its line {name}" if name else "the change order's line"
        raise HTTPException(
            status_code=409,
            detail={
                "code": "change_order_not_in_effect",
                "message": f"{co.number} takes effect on {effective.isoformat()}, "
                f"after this application's date ({app.date.isoformat()}), so "
                f"{line} cannot bill on it. Date the application "
                f"{effective.isoformat()} or later, or clear the line's amounts.",
            },
        )


def lines_in_effect(db: Session, app) -> list:
    """The application's lines less a change order's line that takes effect
    after the application's date. One already on the schedule stays: it is
    money, whether this application bills it or an earlier approved one billed
    work or left stored materials on it."""
    from app.models.jobs import SovLine

    carried = carried_by_earlier(db, app.job_id, app.application_no)
    kept = []
    for ln in app.lines:
        billed = (
            _q(ln.this_period) != _ZERO
            or _q(ln.stored) != _ZERO
            or ln.sov_line_id in carried
        )
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        if billed or sov_line_in_effect(db, sov, app.date):
            kept.append(ln)
    return kept


def carried_change_orders(db: Session, app) -> set:
    """Ids of the change orders whose schedule lines the application carries:
    its contract sum to date counts each, so the summary foots against the
    line detail even when the application is dated before one takes effect."""
    from app.models.jobs import SovLine

    ids = set()
    for ln in lines_in_effect(db, app):
        sov = db.get(SovLine, ln.sov_line_id) if ln.sov_line_id else None
        if sov is not None and sov.change_order_id is not None:
            ids.add(sov.change_order_id)
    return ids


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
        prior_tax_exact=h["tax_exact"],
        prior_tax=h["tax_billed"],
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


def sov_lines_taxable(db: Session, sov_line_ids) -> dict:
    """SOV line id → whether it is taxable, for many lines in one query (what
    sov_line_taxable answers one line at a time). An id with no SOV line is
    absent, and reads as taxed through `.get(id, True)`, as there."""
    from app.models.jobs import SovLine

    ids = {i for i in sov_line_ids if i}
    if not ids:
        return {}
    return {
        sov_id: bool(flag)
        for sov_id, flag in db.query(SovLine.id, SovLine.is_taxable).filter(
            SovLine.id.in_(ids)
        )
    }


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
