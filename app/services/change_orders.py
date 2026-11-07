# ============================================================================
# Change orders (#554) — the math and the effects, in one place.
#
# A change order prices material and labor separately, each line carrying its
# own markup, and every effect on the contract, the SOV, the budget and the
# commitment happens on approve (apply_change_order) and reverses on void
# (reverse_change_order), each in one transaction the route owns.
#
# The math is pure (compute_line, co_totals): Decimal, cents half-up. A draft
# or pending CO recomputes from its inputs; once approved the figures are
# FROZEN — co_totals reads the stored amount/sell_amount and the effective
# markup stamped at approve, so changing a contract term afterward never moves
# an approved CO and void reverses the exact figures it applied.
# ============================================================================

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.job_costing import JobBudget
from app.models.jobs import Job, SovLine
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.services.accounting import _q

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# A commitment a sub CO may append to: issued and still open (not draft, not
# fully-billed/closed, not void). CLOSED means every line is fully billed.
_SUB_OPEN = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)


# ── Pure math ────────────────────────────────────────────────────────────


def line_cost(quantity, unit_cost) -> Decimal:
    """A line's cost: quantity × unit cost, to the cent."""
    return _q(Decimal(str(quantity or 0)) * Decimal(str(unit_cost or 0)))


def effective_markup(
    line_markup, co_markup, job_markup, funding: str, apply_markup: bool = False
) -> Decimal:
    """The percent that applies to a line: the line's own, else the CO's, else
    the job's default, else zero. Allowance- and contingency-funded work
    carries none unless the CO turns markup on (apply_markup)."""
    if funding != "contract" and not apply_markup:
        return _ZERO
    for pct in (line_markup, co_markup, job_markup):
        if pct is not None:
            return Decimal(str(pct))
    return _ZERO


def line_sell(cost: Decimal, markup: Decimal, kind: str) -> Decimal:
    """A line's sell amount. An owner CO or a PCO carries the markup; a sub CO
    moves a commitment by cost, so it carries none."""
    if kind == "sub":
        return _q(cost)
    return _q(cost * (Decimal("1") + markup / _HUNDRED))


def _line_markup(line: ChangeOrderLine, co: ChangeOrder, job: Job) -> Decimal:
    """The markup a line carries. A return of unspent allowance carries none,
    whatever the line, CO or job say: the owner gets the allowance back at
    the price it was held at."""
    if co.allowance_return:
        return _ZERO
    return effective_markup(
        line.markup_pct, co.markup_pct, job.co_markup_pct, co.funding, co.apply_markup
    )


def compute_line(line: ChangeOrderLine, co: ChangeOrder, job: Job) -> None:
    """Stamp a line's server-computed amount (cost) and sell_amount in place,
    from the effective markup (line → CO → job → 0; 0 for allowance-/
    contingency-funded work unless the CO applies markup)."""
    cost = line_cost(line.quantity, line.unit_cost)
    markup = _line_markup(line, co, job)
    line.amount = cost
    line.sell_amount = line_sell(cost, markup, co.kind)


def recompute(co: ChangeOrder, job: Job) -> None:
    """Refresh every line's stored figures from its inputs — but only while the
    CO is draft or pending. Once approved (or void) the figures are frozen:
    they are the numbers the approve applied, and a later change to a contract
    term must not move them."""
    if co.status not in ("draft", "pending"):
        return
    for line in co.lines:
        compute_line(line, co, job)


def _freeze(co: ChangeOrder, job: Job) -> None:
    """Compute and persist each line's cost, sell and the effective markup it
    carried — called once at approve so the CO's figures never move again."""
    for line in co.lines:
        cost = line_cost(line.quantity, line.unit_cost)
        markup = _line_markup(line, co, job)
        line.markup_pct = markup  # stamp the effective markup on the line
        line.amount = cost
        line.sell_amount = line_sell(cost, markup, co.kind)


def co_totals(co: ChangeOrder, job: Job) -> dict:
    """Cost, markup and sell totals for a CO, plus the same per cost type.
    Draft/pending COs recompute first; an approved/void CO reads its frozen
    stored figures."""
    recompute(co, job)
    cost_total = sum((_q(line.amount) for line in co.lines), _ZERO)
    sell_total = sum((_q(line.sell_amount) for line in co.lines), _ZERO)
    by_type: dict[str, dict] = {}
    for line in co.lines:
        bucket = by_type.setdefault(line.cost_type, {"cost": _ZERO, "sell": _ZERO})
        bucket["cost"] += _q(line.amount)
        bucket["sell"] += _q(line.sell_amount)
    return {
        "cost_total": _q(cost_total),
        "sell_total": _q(sell_total),
        "markup_total": _q(sell_total - cost_total),
        "by_cost_type": {
            t: {
                "cost": _q(v["cost"]),
                "sell": _q(v["sell"]),
                "markup": _q(v["sell"] - v["cost"]),
            }
            for t, v in by_type.items()
        },
    }


def _budget_groups(co: ChangeOrder) -> dict:
    """CO lines grouped by (cost_code_id, cost_type) -> (cost, sell), read from
    the stored figures."""
    groups: dict[tuple, list] = {}
    for line in co.lines:
        key = (line.cost_code_id, line.cost_type)
        bucket = groups.setdefault(key, [_ZERO, _ZERO])
        bucket[0] += _q(line.amount)
        bucket[1] += _q(line.sell_amount)
    return groups


# ── The day a change order takes effect ─────────────────────────────────────


def co_effective_date(co: ChangeOrder) -> date | None:
    """The day an approved change order takes effect: the approved_on it was
    approved with, else (a row from before approved_on existed) the local day
    of its approve click. None for a change order that was never approved."""
    if co.approved_on is not None:
        return co.approved_on
    if co.approved_at is None:
        return None
    from app.services.job_reports import _local_date

    return _local_date(co.approved_at)


def resolve_approved_on(co: ChangeOrder, approved_on: date | None) -> date:
    """The date Approve takes effect on: the one given, else the change
    order's own date, else today."""
    return approved_on or co.date or date.today()


# ── Usage roll-ups (read by the contract summary and the exceed guards) ────


def _approved_owner_cos(db: Session, job_id: int, funding: str) -> list[ChangeOrder]:
    return (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job_id,
            ChangeOrder.kind == "owner",
            ChangeOrder.status == "approved",
            ChangeOrder.funding == funding,
        )
        .all()
    )


def contingency_used(db: Session, job: Job) -> Decimal:
    """Σ sell_total of approved contingency-funded owner COs."""
    total = _ZERO
    for co in _approved_owner_cos(db, job.id, "contingency"):
        total += co_totals(co, job)["sell_total"]
    return _q(total)


def allowance_used(db: Session, job: Job, sov_line_id: int) -> Decimal:
    """Σ sell_total of approved allowance-funded owner COs on one allowance
    SOV line — the marked-up price, as contingency draws it."""
    total = _ZERO
    for co in _approved_owner_cos(db, job.id, "allowance"):
        if co.allowance_sov_line_id == sov_line_id:
            total += co_totals(co, job)["sell_total"]
    return _q(total)


def allowance_returned_credit(db: Session, job: Job, sov_line_id: int) -> Decimal:
    """What approved returns of this allowance took off the contract through a
    credit line (the part already billed, so the allowance line itself could
    not shrink). The rest of a return shrinks the allowance line's scheduled
    value directly, so it is already out of `scheduled_value`."""
    total = _ZERO
    returns = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job.id,
            ChangeOrder.allowance_return.is_(True),
            ChangeOrder.status == "approved",
            ChangeOrder.allowance_sov_line_id == sov_line_id,
        )
        .all()
    )
    for co in returns:
        for line in db.query(SovLine).filter(
            SovLine.job_id == job.id, SovLine.change_order_id == co.id
        ):
            total -= _q(line.scheduled_value)
    return _q(total)


def allowance_unspent(db: Session, job: Job, line: SovLine) -> Decimal:
    """What an allowance line still holds: its scheduled value, less the
    change orders that drew on it and the credit lines of any returns."""
    return _q(
        _q(line.scheduled_value)
        - allowance_used(db, job, line.id)
        - allowance_returned_credit(db, job, line.id)
    )


def allowances_summary(db: Session, job: Job) -> list[dict]:
    """Per allowance SOV line: its scheduled value, used and remaining."""
    out = []
    for line in job.sov_lines:
        if line.kind != "allowance":
            continue
        value = _q(line.scheduled_value)
        used = allowance_used(db, job, line.id)
        out.append(
            {
                "sov_line_id": line.id,
                "description": line.description,
                "scheduled_value": float(value),
                "used": float(used),
                "remaining": float(allowance_unspent(db, job, line)),
            }
        )
    return out


def change_orders_summary(db: Session, job: Job) -> dict:
    """Approved/pending counts and the approved contract-funded total."""
    cos = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.job_id == job.id, ChangeOrder.kind == "owner")
        .all()
    )
    approved = [c for c in cos if c.status == "approved"]
    pending = [c for c in cos if c.status == "pending"]
    approved_total = _ZERO
    for co in approved:
        if co.funding == "contract":
            approved_total += co_totals(co, job)["sell_total"]
    return {
        "approved_count": len(approved),
        "pending_count": len(pending),
        "approved_total": float(_q(approved_total)),
    }


# ── Budget rows (source="change", locked) ──────────────────────────────────
#
# Keyed (job, cost_code_id, cost_type, source="change") — the uq_job_budget key
# includes source, so a change row coexists with an estimate-locked row on the
# same code/type (the tree shows one under original, the other under changes).
# Approved COs accumulate into the one change row per key; void subtracts.


def _allowance_cell(db: Session, job_id: int, sov_line: SovLine):
    """The budget cell Accept seeded for an allowance SOV line: the job's
    non-change row on the line's cost code (the largest when a code carries
    several types). None when the job has no such row, as when the schedule
    was typed by hand."""
    q = db.query(JobBudget).filter(
        JobBudget.job_id == job_id, JobBudget.source != "change"
    )
    q = (
        q.filter(JobBudget.cost_code_id == sov_line.cost_code_id)
        if sov_line.cost_code_id is not None
        else q.filter(JobBudget.cost_code_id.is_(None))
    )
    rows = q.all()
    return max(rows, key=lambda r: Decimal(str(r.amount or 0)), default=None)


def _budget_deltas(db: Session, job_id: int, co: ChangeOrder, with_revenue: bool):
    """What this CO adds to the change budget rows: {(code, type): [cost,
    revenue]}. An allowance-funded CO spends money the budget already holds,
    so besides its own cost it takes the same cost back out of the
    allowance's cell, and the job's total budgeted cost stays put."""
    deltas: dict[tuple, list] = {}
    for key, (cost, sell) in _budget_groups(co).items():
        deltas[key] = [cost, sell if with_revenue else _ZERO]
    if co.kind == "owner" and co.funding == "allowance" and not co.allowance_return:
        sov_line = (
            db.get(SovLine, co.allowance_sov_line_id)
            if co.allowance_sov_line_id
            else None
        )
        cell = _allowance_cell(db, job_id, sov_line) if sov_line else None
        if cell is not None:
            drawn = sum((cost for cost, _s in _budget_groups(co).values()), _ZERO)
            key = (cell.cost_code_id, cell.cost_type)
            deltas.setdefault(key, [_ZERO, _ZERO])[0] -= drawn
    return deltas


def _apply_budget_deltas(db: Session, job_id: int, deltas: dict, sign: int) -> None:
    """Add (sign=1) or subtract (sign=-1) the deltas on the change rows,
    creating a row when there is none and deleting one that nets to zero."""
    for (code_id, ctype), (cost, rev) in deltas.items():
        cost, rev = sign * cost, sign * rev
        row = _find_change_budget(db, job_id, code_id, ctype)
        if row is None:
            if sign < 0 or (cost == _ZERO and rev == _ZERO):
                continue
            db.add(
                JobBudget(
                    job_id=job_id,
                    cost_code_id=code_id,
                    cost_type=ctype,
                    amount=cost,
                    revenue_amount=rev,
                    source="change",
                    locked=True,
                )
            )
            continue
        row.amount = _q(Decimal(str(row.amount)) + cost)
        row.revenue_amount = _q(Decimal(str(row.revenue_amount)) + rev)
        if row.amount == _ZERO and row.revenue_amount == _ZERO:
            db.delete(row)
    db.flush()


def _write_change_budgets(
    db: Session, job_id: int, co: ChangeOrder, with_revenue: bool
) -> None:
    """Add this CO's cost into the job's source="change" budget rows, one per
    (cost_code_id, cost_type). A contract-funded owner CO also adds the sell as
    revenue; a contingency-/allowance-funded CO and a sub CO write revenue 0
    (the contract already carries that money). An allowance-funded CO also
    draws its cost out of the allowance's cell (_budget_deltas). Bypasses
    _refuse_locked_budget by design: a change order is the one thing that may
    move a locked budget."""
    _apply_budget_deltas(db, job_id, _budget_deltas(db, job_id, co, with_revenue), 1)


def _remove_change_budgets(
    db: Session, job_id: int, co: ChangeOrder, with_revenue: bool
) -> None:
    """Reverse _write_change_budgets: subtract this CO's cost/revenue from the
    change rows, deleting a row only once it is fully zero (so a deduct CO
    sharing a key with an add leaves the add's figures behind)."""
    _apply_budget_deltas(db, job_id, _budget_deltas(db, job_id, co, with_revenue), -1)


def _find_change_budget(db: Session, job_id: int, code_id, ctype):
    q = db.query(JobBudget).filter(
        JobBudget.job_id == job_id,
        JobBudget.source == "change",
    )
    q = (
        q.filter(JobBudget.cost_code_id == code_id)
        if code_id is not None
        else q.filter(JobBudget.cost_code_id.is_(None))
    )
    q = (
        q.filter(JobBudget.cost_type == ctype)
        if ctype is not None
        else q.filter(JobBudget.cost_type.is_(None))
    )
    return q.first()


def _writes_budget(co: ChangeOrder) -> bool:
    """A sub CO backed by an owner CO (owner_change_order_id set) writes no
    budget of its own — the owner CO already carried the cost, so writing it
    again here would double-count. Only a standalone sub CO writes cost."""
    if co.kind == "sub" and co.owner_change_order_id is not None:
        return False
    return True


# ── Commitment append / remove (sub CO) ─────────────────────────────────────


def _check_commitment(po: PurchaseOrder, co: ChangeOrder) -> None:
    """Guard a sub CO's commitment: it must be on the same job and still open
    (issued, not fully billed, not draft)."""
    if po.job_id != co.job_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "commitment_job_mismatch",
                "message": "That commitment is on a different job.",
            },
        )
    if po.status not in _SUB_OPEN:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "commitment_not_open",
                "message": "A sub change order needs an open commitment (not draft or fully billed).",
            },
        )


def remove_commitment_lines(db: Session, po: PurchaseOrder, co: ChangeOrder) -> None:
    """Reverse append_commitment_lines on void: remove exactly the lines this CO
    added (matched by change_order_id). A line already billed against cannot be
    pulled (409 co_billed). Re-totals and re-evaluates the PO status (back to
    closed when every remaining line is fully billed)."""
    added = (
        db.query(PurchaseOrderLine)
        .filter(
            PurchaseOrderLine.purchase_order_id == po.id,
            PurchaseOrderLine.change_order_id == co.id,
        )
        .all()
    )
    if any(_q(ln.billed_amount) > _ZERO for ln in added):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_billed",
                "message": "A commitment line this change order added has been billed; it cannot be voided.",
            },
        )
    for ln in added:
        db.delete(ln)
    db.flush()
    db.refresh(po)
    from app.routes.purchase_orders import _retotal_commitment

    _retotal_commitment(po)
    # Re-evaluate the status: closed when nothing remains to bill, else partial
    # if anything has been billed; otherwise leave it as issued.
    remaining = sum((_q(ln.amount) - _q(ln.billed_amount) for ln in po.lines), _ZERO)
    billed = sum((_q(ln.billed_amount) for ln in po.lines), _ZERO)
    if po.lines and remaining <= _ZERO:
        po.status = POStatus.CLOSED
    elif billed > _ZERO:
        po.status = POStatus.PARTIAL
    db.flush()


# ── Reference guards ────────────────────────────────────────────────────────


def check_allowance_line(db: Session, job_id: int, sov_line_id) -> SovLine:
    """The allowance SOV line an allowance-funded CO spends: it must exist on
    this job and be an allowance line. Another job's line is reported as not
    found; a non-allowance line of this job is a 400."""
    line = db.get(SovLine, sov_line_id) if sov_line_id is not None else None
    if line is None or line.job_id != job_id:
        raise HTTPException(status_code=404, detail="Allowance line not found")
    if line.kind != "allowance":
        raise HTTPException(
            status_code=400, detail="That schedule-of-values line is not an allowance."
        )
    return line


def check_owner_link(db: Session, job_id: int, owner_id) -> None:
    """A sub CO's backing owner CO must exist, be an owner CO and be on the
    same job."""
    if owner_id is None:
        return
    owner = db.get(ChangeOrder, owner_id)
    if owner is None or owner.job_id != job_id:
        raise HTTPException(status_code=404, detail="Owner change order not found")
    if owner.kind != "owner":
        raise HTTPException(
            status_code=400,
            detail="A sub change order is backed by an owner change order.",
        )


def refuse_with_approved_sub(db: Session, owner: ChangeOrder, action: str) -> None:
    """An owner CO cannot be rejected or voided while an approved sub CO
    stands under it: the commitment it raised would be left with no owner
    change (and, if linked, no budget) behind it. Void the sub CO first."""
    if owner.kind != "owner":
        return
    sub = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.owner_change_order_id == owner.id,
            ChangeOrder.kind == "sub",
            ChangeOrder.status == "approved",
        )
        .first()
    )
    if sub is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approved_sub_co",
                "message": f"{sub.number} is approved under this change order; void it before you {action} this one.",
            },
        )


# ── Apply / reverse ─────────────────────────────────────────────────────────


def _next_sov_line_no(job: Job) -> int:
    return (max((r.line_no for r in job.sov_lines), default=0)) + 1


def _apply_allowance_return(db: Session, co: ChangeOrder, job: Job, sell_total):
    """The schedule-of-values side of returning an allowance's unspent amount.
    The unbilled part comes off the allowance line itself, so its balance to
    finish is what it has left to bill; whatever was already billed cannot
    come off the line, so it becomes a credit line (kind change_order) the
    next pay application bills. Contract and budget move like any
    contract-funded deduct, in the caller."""
    from app.services.pay_applications import billed_completed_by_sov_line

    amount = -_q(sell_total)
    if amount <= _ZERO:
        raise HTTPException(
            status_code=422,
            detail="A return of unspent allowance must take money off the contract.",
        )
    line = check_allowance_line(db, co.job_id, co.allowance_sov_line_id)
    unspent = allowance_unspent(db, job, line)
    if amount > unspent:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "allowance_exceeded",
                "message": (
                    f"This would return ${amount:,.2f}, and the allowance has "
                    f"${unspent:,.2f} unspent."
                ),
            },
        )
    billed = billed_completed_by_sov_line(db, job.id).get(line.id, _ZERO)
    shrink = min(amount, max(_q(line.scheduled_value) - _q(billed), _ZERO))
    credit = amount - shrink
    line.scheduled_value = _q(_q(line.scheduled_value) - shrink)
    if credit > _ZERO:
        db.add(
            SovLine(
                job_id=job.id,
                line_no=_next_sov_line_no(job),
                item_no=None,
                description=f"{co.number} {co.title or ''}".strip(),
                kind="change_order",
                scheduled_value=-credit,
                change_order_id=co.id,
            )
        )


def create_allowance_return(
    db: Session, job: Job, sov_line_id: int, amount=None
) -> ChangeOrder:
    """A draft owner change order that returns an allowance's unspent amount
    (all of it, or `amount`): minus the amount, no markup, funded against the
    allowance. Refused when nothing is unspent, when `amount` is more than is,
    or when another return of this allowance is still open."""
    from app.routes.change_orders import _create_numbered
    from app.services.job_costing import line_cost_type

    line = check_allowance_line(db, job.id, sov_line_id)
    unspent = allowance_unspent(db, job, line)
    if unspent <= _ZERO:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "allowance_nothing_unspent",
                "message": f"{line.description or 'That allowance'} has nothing unspent to return.",
            },
        )
    open_return = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job.id,
            ChangeOrder.allowance_return.is_(True),
            ChangeOrder.allowance_sov_line_id == line.id,
            ChangeOrder.status.in_(("draft", "pending")),
        )
        .first()
    )
    if open_return is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "allowance_return_open",
                "message": f"{open_return.number} already returns this allowance; approve or reject it first.",
            },
        )
    amount = unspent if amount is None else _q(Decimal(str(amount)))
    if amount <= _ZERO or amount > unspent:
        raise HTTPException(
            status_code=422,
            detail=f"Return an amount above zero and no more than the ${unspent:,.2f} unspent.",
        )
    cell = _allowance_cell(db, job.id, line)
    code_id = line.cost_code_id if line.cost_code_id else None
    ctype = (cell.cost_type if cell and cell.cost_type else None) or line_cost_type(
        db, code_id, None
    )
    co = _create_numbered(
        db,
        job,
        kind="owner",
        status="draft",
        title=f"Return unspent allowance: {line.description or 'allowance'}"[:200],
        date=date.today(),
        funding="contract",
        allowance_sov_line_id=line.id,
        markup_pct=_ZERO,
        apply_markup=True,
        allowance_return=True,
    )
    db.add(
        ChangeOrderLine(
            change_order_id=co.id,
            line_order=0,
            description=f"Unspent allowance returned to the owner: {line.description or ''}".strip(),
            cost_type=ctype or "other",
            cost_code_id=code_id,
            quantity=1,
            unit_cost=-amount,
            markup_pct=_ZERO,
        )
    )
    db.flush()
    db.refresh(co)
    recompute(co, job)
    return co


def apply_change_order(
    db: Session, co: ChangeOrder, approved_on: date | None = None
) -> None:
    """Every effect of approving a change order, in the route's transaction.
    Freezes the CO's figures first, then applies them. Raises 409
    contingency_exceeded / allowance_exceeded when a draw would pass its
    limit, and the commitment guards for a sub CO. The change order takes
    effect on `approved_on` (default its own date, else today)."""
    job = co.job
    _freeze(co, job)
    totals = co_totals(co, job)
    sell_total = totals["sell_total"]

    if co.kind == "owner":
        if co.funding == "contract":
            base = Decimal(str(job.contract_amount or 0))
            job.contract_amount = _q(base + sell_total)
            if co.allowance_return:
                _apply_allowance_return(db, co, job, sell_total)
            else:
                db.add(
                    SovLine(
                        job_id=job.id,
                        line_no=_next_sov_line_no(job),
                        item_no=None,
                        description=f"{co.number} {co.title or ''}".strip(),
                        kind="change_order",
                        scheduled_value=sell_total,
                        change_order_id=co.id,
                    )
                )
            _write_change_budgets(db, job.id, co, with_revenue=True)
        elif co.funding == "contingency":
            limit = job.contingency_amount
            if limit is not None:
                already = contingency_used(db, job)  # excludes this pending CO
                if _q(already + sell_total) > _q(limit):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "contingency_exceeded",
                            "message": "This change order would draw more than the contract's contingency.",
                        },
                    )
            _write_change_budgets(db, job.id, co, with_revenue=False)
        elif co.funding == "allowance":
            if co.allowance_sov_line_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="An allowance-funded change order needs an allowance line.",
                )
            sov_line = check_allowance_line(db, co.job_id, co.allowance_sov_line_id)
            already = allowance_used(db, job, sov_line.id)
            held = _q(sov_line.scheduled_value) - allowance_returned_credit(
                db, job, sov_line.id
            )
            if _q(already + sell_total) > _q(held):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "allowance_exceeded",
                        "message": "This change order would spend more than the allowance holds.",
                    },
                )
            _write_change_budgets(db, job.id, co, with_revenue=False)
    elif co.kind == "sub":
        if co.commitment_id is None:
            raise HTTPException(
                status_code=422, detail="A sub change order needs a commitment."
            )
        po = db.get(PurchaseOrder, co.commitment_id)
        if po is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        _check_commitment(po, co)
        if co.owner_change_order_id is not None:
            owner = db.get(ChangeOrder, co.owner_change_order_id)
            if owner is None or owner.status != "approved":
                # A linked sub CO writes no budget of its own; it relies on
                # its owner CO having carried the cost.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "owner_co_not_approved",
                        "message": "Approve the owner change order first; this sub change order is backed by it.",
                    },
                )
            # The commitment can rise by no more than the owner CO carried:
            # approved linked sub COs plus this one, by cost.
            taken = sum(
                (
                    co_totals(o, job)["cost_total"]
                    for o in db.query(ChangeOrder)
                    .filter(
                        ChangeOrder.owner_change_order_id == owner.id,
                        ChangeOrder.kind == "sub",
                        ChangeOrder.status == "approved",
                        ChangeOrder.id != co.id,
                    )
                    .all()
                ),
                Decimal("0"),
            )
            owner_cost = co_totals(owner, job)["cost_total"]
            if _q(taken + totals["cost_total"]) > owner_cost:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "owner_co_budget_exceeded",
                        "message": (
                            f"{owner.number} carries ${owner_cost:,.2f} of cost and "
                            f"${taken:,.2f} is already approved under it; this "
                            f"${totals['cost_total']:,.2f} would raise the commitment past it."
                        ),
                    },
                )
        from app.routes.purchase_orders import append_commitment_lines

        append_commitment_lines(db, po, co.lines, co.id)
        if _writes_budget(co):
            _write_change_budgets(db, job.id, co, with_revenue=False)

    co.status = "approved"
    co.approved_at = datetime.now(timezone.utc)
    co.approved_on = resolve_approved_on(co, approved_on)
    db.flush()


def _refuse_if_co_line_billed(db: Session, co: ChangeOrder) -> None:
    """A change_order SOV line that a pay application has billed (previous +
    this period + stored > 0 on any non-void application) cannot be voided
    out from under the billing (#555, 409 co_billed)."""
    from app.models.pay_applications import PayApplication, PayApplicationLine
    from app.services.pay_applications import billed_completed_by_sov_line

    sov_line_ids = [
        r.id
        for r in db.query(SovLine).filter(
            SovLine.job_id == co.job_id, SovLine.change_order_id == co.id
        )
    ]
    if not sov_line_ids:
        return
    billed = billed_completed_by_sov_line(db, co.job_id)
    if any(_q(billed.get(sid, 0)) > 0 for sid in sov_line_ids):
        _raise_co_billed()
    open_line = (
        db.query(PayApplicationLine)
        .join(
            PayApplication, PayApplication.id == PayApplicationLine.pay_application_id
        )
        .filter(
            PayApplication.job_id == co.job_id,
            PayApplication.status != "void",
            PayApplicationLine.sov_line_id.in_(sov_line_ids),
            (PayApplicationLine.this_period + PayApplicationLine.stored) > 0,
        )
        .first()
    )
    if open_line is not None:
        _raise_co_billed()


def _raise_co_billed() -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "co_billed",
            "message": "A pay application has billed this change order; it cannot be voided.",
        },
    )


def _reverse_allowance_return(db: Session, co: ChangeOrder, sell_total) -> None:
    """Give an allowance line back what its return took off it: the returned
    amount less the part that went to a credit line (deleted by the caller)."""
    line = db.get(SovLine, co.allowance_sov_line_id)
    if line is None:
        return
    credit = _ZERO
    for ln in db.query(SovLine).filter(
        SovLine.job_id == co.job_id, SovLine.change_order_id == co.id
    ):
        credit -= _q(ln.scheduled_value)
    shrink = -_q(sell_total) - credit
    line.scheduled_value = _q(_q(line.scheduled_value) + shrink)


def reverse_change_order(db: Session, co: ChangeOrder) -> None:
    """Undo apply_change_order from the CO's frozen figures, in the route's
    transaction."""
    job = co.job
    refuse_with_approved_sub(db, co, "void")
    totals = co_totals(co, job)  # frozen — the figures approve applied
    sell_total = totals["sell_total"]

    if co.kind == "owner":
        if co.funding == "contract":
            _refuse_if_co_line_billed(db, co)  # 409 co_billed
            base = Decimal(str(job.contract_amount or 0))
            job.contract_amount = _q(base - sell_total)
            if co.allowance_return:
                _reverse_allowance_return(db, co, sell_total)
            db.query(SovLine).filter(
                SovLine.job_id == job.id, SovLine.change_order_id == co.id
            ).delete()
            _remove_change_budgets(db, job.id, co, with_revenue=True)
        elif co.funding in ("contingency", "allowance"):
            _remove_change_budgets(db, job.id, co, with_revenue=False)
    elif co.kind == "sub" and co.commitment_id is not None:
        po = db.get(PurchaseOrder, co.commitment_id)
        if po is not None:
            remove_commitment_lines(db, po, co)  # may 409 co_billed
        if _writes_budget(co):
            _remove_change_budgets(db, job.id, co, with_revenue=False)

    co.status = "void"
    co.voided_at = datetime.now(timezone.utc)
    db.flush()
