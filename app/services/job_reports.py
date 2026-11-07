# ============================================================================
# Construction job reports (#556) — the standalone reports a contractor's books
# grow last: the WIP schedule with over/under billing, cost-to-complete, the
# change-order log, the buyout report and job cost by vendor.
#
# Pure functions over the figures the other services already compute — the
# contract summary (#552), the cost tree and budgets (M3), buyout (#553), change
# orders (#554) and pay applications (#555). Nothing here posts: the WIP
# adjusting entry is computed with its amounts for the accountant to post, never
# written. The one thing stored is the cost-to-complete forecast the user types.
# Money is Decimal, quantized to cents half-up through accounting._q.
# ============================================================================

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from app.models.accounts import Account, AccountType
from app.models.cost_codes import CostCode
from app.models.job_costing import JobBudget, JobCostToComplete
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import _q
from app.services.jobs_service import job_attribution

_ZERO = Decimal("0")
_ONE = Decimal("1")

# Jobs a WIP-style report leaves out unless asked: a lost bid never bills, and
# a closed job is off the board unless include_closed (or an explicit status).
_NEVER_DEFAULT = ("not_awarded",)
_CLOSED = ("closed",)

# Timestamps are stored in UTC (SQLite hands them back naive, with no zone);
# the "as of" day a report is run for is a day on the user's own calendar.
# None means the machine's local zone, which is what a desktop install calls
# local (#644). Tests set it to pin the zone on platforms without tzset.
_LOCAL_TZ = None


def _local_date(ts: datetime) -> date:
    """The local calendar date a stored timestamp falls on, so a forecast typed
    or a change order approved at 23:30 belongs to that evening and not to the
    next UTC day (#644). A naive timestamp is UTC in this codebase."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_LOCAL_TZ).date()


def select_jobs(
    db: Session,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> list[Job]:
    """The jobs a construction report runs over, customer then job name.

    `status` names one job status and wins; otherwise not-awarded jobs are
    always left out and closed ones too unless `include_closed`."""
    q = db.query(Job).options(joinedload(Job.customer))
    if job_id is not None:
        q = q.filter(Job.id == job_id)
    if customer_id is not None:
        q = q.filter(Job.customer_id == customer_id)
    if status:
        q = q.filter(Job.status == status)
    else:
        excluded = list(_NEVER_DEFAULT)
        if not include_closed:
            excluded += list(_CLOSED)
        if excluded:
            q = q.filter(~Job.status.in_(excluded))
    jobs = q.all()
    return sorted(
        jobs,
        key=lambda j: (
            (j.customer.name.lower() if j.customer else ""),
            j.name.lower(),
        ),
    )


def _customer_name(job: Job) -> str:
    return job.customer.name if job.customer else ""


# ── 1. WIP schedule ─────────────────────────────────────────────────────────
#
# Cost-to-cost: percent complete is cost to date ÷ estimated total cost, where
# the estimated total cost is actual + the cost-to-complete forecast (below), so
# an overrun job with work still to do reads under 100%. Earned revenue is the
# revised contract times that percent; billings over earned are a liability
# (billings in excess of costs and estimated earnings), under are an asset
# (costs and estimated earnings in excess of billings).
#
# When the projected total cost goes over the contract the whole projected loss
# is recognised now (the usual treatment of a loss contract): the part not
# already in earned revenue is booked as a provision. wip_adjusting_entry turns
# the rows into the entry with amounts — per job and net. It computes the entry;
# it does not post it.

WIP_OVERBILLING_ACCOUNT = "Billings in Excess of Costs and Estimated Earnings"
WIP_UNDERBILLING_ACCOUNT = "Costs and Estimated Earnings in Excess of Billings"
WIP_REVENUE_ACCOUNT = "Contract Revenue (WIP adjustment)"
WIP_LOSS_EXPENSE_ACCOUNT = "Loss on Uncompleted Contracts"
WIP_LOSS_ACCOUNT = "Provision for Loss on Uncompleted Contracts"

WIP_COLUMNS = [
    {"key": "customer_name", "label": "Customer", "kind": "text"},
    {"key": "job_name", "label": "Job", "kind": "text"},
    {"key": "contract_original", "label": "Contract", "kind": "money"},
    {"key": "approved_change_orders", "label": "Changes", "kind": "money"},
    {"key": "revised_contract", "label": "Revised", "kind": "money"},
    {"key": "estimated_total_cost", "label": "Est. cost", "kind": "money"},
    {"key": "estimated_gross_profit", "label": "Est. GP", "kind": "money"},
    {"key": "margin_pct", "label": "Margin", "kind": "pct"},
    {"key": "cost_to_date", "label": "Cost to date", "kind": "money"},
    {"key": "percent_complete", "label": "% complete", "kind": "pct"},
    {"key": "earned_revenue", "label": "Earned", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
    {"key": "over_billing", "label": "Over billed", "kind": "money"},
    {"key": "under_billing", "label": "Under billed", "kind": "money"},
    {"key": "retainage_outstanding", "label": "Retainage", "kind": "money"},
    {"key": "cost_to_complete", "label": "Cost to complete", "kind": "money"},
    {"key": "projected_gross_profit", "label": "Proj. GP", "kind": "money"},
    {"key": "projected_loss", "label": "Proj. loss", "kind": "money"},
    {"key": "loss_provision", "label": "Loss provision", "kind": "money"},
]

# The figures a totals row sums straight across.
_WIP_SUMMED = (
    "contract_original",
    "approved_change_orders",
    "revised_contract",
    "estimated_total_cost",
    "estimated_gross_profit",
    "cost_to_date",
    "earned_revenue",
    "billed_to_date",
    "over_billing",
    "under_billing",
    "retainage_outstanding",
    "cost_to_complete",
    "projected_gross_profit",
    "projected_loss",
    "loss_provision",
)

# What an unmeasured job (contract, no budget or forecast) still carries into
# the totals: the facts printed in its row. It earns nothing, so the earned,
# over/under and loss figures stay out, along with the estimates it lacks.
_WIP_SUMMED_UNMEASURED = (
    "contract_original",
    "approved_change_orders",
    "revised_contract",
    "cost_to_date",
    "billed_to_date",
    "retainage_outstanding",
)


def _blank_wip_row(job: Job) -> dict:
    row = {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "status": job.status,
        "has_contract": False,
        "billed_basis": None,
    }
    for key in _WIP_SUMMED:
        row[key] = None
    row["margin_pct"] = None
    row["percent_complete"] = None
    return row


def _unmeasured_wip_row(
    job: Job, original, approved_cos, revised, cost_to_date, billed, retainage_out
) -> dict:
    """A job with a contract but no budget or forecast (and cost short of the
    contract): nothing to earn against, so it is listed with its contract, cost
    and billing, no earned or over/under figures, and left out of the adjusting
    entry. The totals carry its contract, cost and billing so those columns
    foot; its earned, over/under and loss figures stay out."""
    row = _blank_wip_row(job)
    row.update(
        has_contract=True,
        excluded=True,
        warning=f"{job.name}: no budget or forecast; nothing earned.",
        contract_original=float(original),
        approved_change_orders=float(approved_cos),
        revised_contract=float(revised),
        cost_to_date=float(cost_to_date),
        billed_to_date=float(billed),
        retainage_outstanding=float(retainage_out),
        earned_revenue=0.0,
        over_billing=0.0,
        under_billing=0.0,
        cost_to_complete=0.0,
        projected_loss=0.0,
        loss_provision=0.0,
    )
    return row


def _contract_changes_after(db: Session, job: Job, as_of: date) -> Decimal:
    """Σ sell of this job's approved, contract-funded owner change orders whose
    approve landed after `as_of` — what to back out of the current contract to
    read the sum as it stood on that day."""
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_effective_date, co_totals

    total = _ZERO
    cos = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id == job.id,
            ChangeOrder.kind == "owner",
            ChangeOrder.status == "approved",
            ChangeOrder.funding == "contract",
        )
        .all()
    )
    for co in cos:
        approved = co_effective_date(co)
        if approved is not None and approved > as_of:
            total += co_totals(co, job)["sell_total"]
    return _q(total)


def wip_row(db: Session, job: Job, as_of: date) -> dict:
    """One WIP line for a job. A job with no contract amount is listed with
    blank figures (the schedule still shows it). Every money figure reads the
    books as of `as_of`: actuals and posted revenue to that date, billing and
    retainage from the approved applications dated that day or earlier, and the
    revised contract with the change orders approved by then."""
    from app.services.contracts import contract_summary, original_contract_sum
    from app.services.job_costing import job_cost_tree
    from app.services.pay_applications import has_approved_application

    if job.contract_amount is None:
        return _blank_wip_row(job)

    totals = job_cost_tree(db, job.id, end_date=as_of)["totals"]
    cost_to_date = _q(totals["actual"])
    # Estimated total cost = actual + the cost-to-complete forecast. A job with
    # no budget and no forecast has nothing to measure against: it counts as
    # 100% complete only once its cost has reached the contract (a loss,
    # recognised once); before that it is listed and left out of the figures.
    forecast = ctc_forecast(db, job, as_of)
    cost_to_complete = _q(forecast["totals"]["cost_to_complete"])
    if forecast["has_basis"]:
        est_total_cost = _q(cost_to_date + cost_to_complete)
    else:
        est_total_cost = cost_to_date
    posted_revenue = _q(totals["act_revenue"])

    summary = contract_summary(
        db, job, cost=cost_to_date, billed=posted_revenue, as_of=as_of
    )
    current_revised = _q(summary["contract_amount"])
    # The original as the pay application prints it (#723): the stamped one,
    # else the sum less the approved change orders (a sum from the job form
    # with no contract type saved was never stamped).
    original = original_contract_sum(db, job)
    # The contract as of the date: back out the contract-funded COs approved
    # after it, so original + approved-as-of = revised-as-of.
    changes_after = _contract_changes_after(db, job, as_of)
    revised = _q(current_revised - changes_after)
    approved_cos = _q(revised - original)
    billed = _q(summary["billed_to_date"])
    retainage_out = _q(summary["retainage"]["outstanding"])

    if not forecast["has_basis"] and cost_to_date < revised:
        return _unmeasured_wip_row(
            job, original, approved_cos, revised, cost_to_date, billed, retainage_out
        )

    if est_total_cost > _ZERO:
        pct = cost_to_date / est_total_cost
        if pct > _ONE:
            pct = _ONE  # capped at 100% — a cost overrun does not earn past the sum
    else:
        pct = _ZERO
    earned = _q(revised * pct)
    over = _q(billed - earned) if billed > earned else _ZERO
    under = _q(earned - billed) if earned > billed else _ZERO
    # Cost to complete is the forecast (never negative), so the projected final
    # cost is cost to date + forecast, which is also the estimated total cost.
    projected_final_cost = _q(cost_to_date + cost_to_complete)
    projected_gp = _q(revised - projected_final_cost)
    estimated_gp = _q(revised - est_total_cost)
    # A loss contract books its whole projected loss now. Earned revenue at the
    # percent complete already carries pct × loss, so the provision is the rest.
    projected_loss = _q(projected_final_cost - revised)
    if projected_loss < _ZERO:
        projected_loss = _ZERO
    loss_provision = _q(projected_loss * (_ONE - pct))
    margin = _q(estimated_gp / revised * 100) if revised else None

    basis = (
        "pay_applications"
        if has_approved_application(db, job.id, as_of)
        else "posted_revenue"
    )
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "status": job.status,
        "has_contract": True,
        "billed_basis": basis,
        "contract_original": float(original),
        "approved_change_orders": float(approved_cos),
        "revised_contract": float(revised),
        "estimated_total_cost": float(est_total_cost),
        "estimated_gross_profit": float(estimated_gp),
        "margin_pct": float(margin) if margin is not None else None,
        "cost_to_date": float(cost_to_date),
        "percent_complete": float(_q(pct * 100)),
        "earned_revenue": float(earned),
        "billed_to_date": float(billed),
        "over_billing": float(over),
        "under_billing": float(under),
        "retainage_outstanding": float(retainage_out),
        "cost_to_complete": float(cost_to_complete),
        "projected_gross_profit": float(projected_gp),
        "projected_loss": float(projected_loss),
        "loss_provision": float(loss_provision),
    }


def _forecast_dating_warnings(db: Session, jobs: list, rows: list, as_of: date):
    """A typed cost-to-complete has no date: say so when the schedule is run as
    of a day before the amount was last entered (on the local calendar)."""
    has_contract = {r["job_id"] for r in rows if r.get("has_contract")}
    out = []
    for job in jobs:
        if job.id not in has_contract:
            continue
        latest = (
            db.query(sqlfunc.max(JobCostToComplete.updated_at))
            .filter(JobCostToComplete.job_id == job.id)
            .scalar()
        )
        entered = _local_date(latest) if latest is not None else None
        if entered is not None and entered > as_of:
            out.append(
                f"{job.name}: the cost-to-complete forecast was entered "
                f"{entered.isoformat()}, after {as_of.isoformat()}; it has "
                "no date, so today's figure is used and percent complete may "
                "read high."
            )
    return out


def wip_schedule(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """The work-in-progress schedule: one row per job, cost-to-cost, with over
    and under billing. Every money figure reads as of `as_of` — actuals and
    posted revenue to that date, billing and retainage from the applications
    approved by then, the revised contract with the change orders approved by
    then. Budgets have no date, so the estimated total cost is job-to-date."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [wip_row(db, job, as_of) for job in jobs]
    warnings = [r["warning"] for r in rows if r.get("excluded")]
    warnings += _forecast_dating_warnings(db, jobs, rows, as_of)

    totals = {key: _ZERO for key in _WIP_SUMMED}
    measured_revised = _ZERO  # the contracts behind the Est. GP total
    for r in rows:
        keys = _WIP_SUMMED_UNMEASURED if r.get("excluded") else _WIP_SUMMED
        for key in keys:
            if r[key] is not None:
                totals[key] += Decimal(str(r[key]))
        if not r.get("excluded") and r["revised_contract"] is not None:
            measured_revised += Decimal(str(r["revised_contract"]))
    out_totals = {key: float(_q(v)) for key, v in totals.items()}
    # Margin on the Total row = Est. GP / the revised contracts that have an
    # estimate; an unmeasured job's contract is in the Revised total but has
    # no GP to put against it.
    out_totals["margin_pct"] = (
        float(_q(totals["estimated_gross_profit"] / measured_revised * 100))
        if measured_revised
        else None
    )
    return {
        "as_of": as_of.isoformat(),
        "method": "cost_to_cost",
        "rows": rows,
        "warnings": warnings,
        "totals": out_totals,
        "columns": WIP_COLUMNS,
        "adjustment_accounts": {
            "over_billing": WIP_OVERBILLING_ACCOUNT,
            "under_billing": WIP_UNDERBILLING_ACCOUNT,
            "revenue": WIP_REVENUE_ACCOUNT,
            "loss_expense": WIP_LOSS_EXPENSE_ACCOUNT,
            "loss_provision": WIP_LOSS_ACCOUNT,
        },
        "adjusting_entry": wip_adjusting_entry(rows, as_of),
    }


# ── WIP adjusting entry ───────────────────────────────────────────────────


def _entry_line(account: str, debit: Decimal, credit: Decimal) -> dict:
    return {"account": account, "debit": float(debit), "credit": float(credit)}


def _job_entry_lines(under: Decimal, over: Decimal, provision: Decimal) -> list:
    """The lines for one job's balances: an under-billing is an asset against
    revenue, an over-billing is revenue deferred to a liability, and the loss
    provision is an expense against its liability."""
    lines = []
    if under > _ZERO:
        lines.append(_entry_line(WIP_UNDERBILLING_ACCOUNT, under, _ZERO))
    if over > _ZERO:
        lines.append(_entry_line(WIP_OVERBILLING_ACCOUNT, _ZERO, over))
    net_revenue = under - over  # credit when under-billed, debit when over
    if net_revenue > _ZERO:
        lines.append(_entry_line(WIP_REVENUE_ACCOUNT, _ZERO, net_revenue))
    elif net_revenue < _ZERO:
        lines.append(_entry_line(WIP_REVENUE_ACCOUNT, -net_revenue, _ZERO))
    if provision > _ZERO:
        lines.append(_entry_line(WIP_LOSS_EXPENSE_ACCOUNT, provision, _ZERO))
        lines.append(_entry_line(WIP_LOSS_ACCOUNT, _ZERO, provision))
    return lines


def wip_adjusting_entry(rows: list[dict], as_of: date) -> dict:
    """The WIP adjusting entry with amounts, per job and net, from the schedule
    rows. The balances are carried to `as_of` in full — if an earlier entry was
    posted, the accountant reverses it first or posts the difference. Computed
    only: nothing is written to the books."""
    jobs = []
    t_under = t_over = t_prov = _ZERO
    for r in rows:
        if not r["has_contract"] or r.get("excluded"):
            continue
        under = Decimal(str(r["under_billing"]))
        over = Decimal(str(r["over_billing"]))
        prov = Decimal(str(r["loss_provision"]))
        if under == _ZERO and over == _ZERO and prov == _ZERO:
            continue
        t_under += under
        t_over += over
        t_prov += prov
        jobs.append(
            {
                "job_id": r["job_id"],
                "job_name": r["job_name"],
                "customer_name": r["customer_name"],
                "under_billing": float(under),
                "over_billing": float(over),
                "net_under_over": float(_q(under - over)),
                "projected_loss": r["projected_loss"],
                "loss_provision": float(prov),
                "lines": _job_entry_lines(under, over, prov),
            }
        )
    net = _job_entry_lines(_q(t_under), _q(t_over), _q(t_prov))
    debits = sum((Decimal(str(ln["debit"])) for ln in net), _ZERO)
    credits = sum((Decimal(str(ln["credit"])) for ln in net), _ZERO)
    return {
        "as_of": as_of.isoformat(),
        "posted": False,
        "jobs": jobs,
        "totals": {
            "under_billing": float(_q(t_under)),
            "over_billing": float(_q(t_over)),
            "net_under_over": float(_q(t_under - t_over)),
            "loss_provision": float(_q(t_prov)),
        },
        "lines": net,
        "total_debits": float(_q(debits)),
        "total_credits": float(_q(credits)),
    }


# ── Cost-to-complete forecast (per job, section and cost type) ───────────────
#
# A cell is a section × cost type. Its default cost to complete is the budget
# less the actual cost, floored at zero; a typed amount (job_cost_to_complete)
# replaces the default. Budget and cost with no code share one Unassigned cell,
# so an uncoded budget is measured against uncoded cost.


def _section_id(code: CostCode, code_by_id: dict) -> int:
    """The section a code rolls up to: itself or its nearest ancestor at level
    'section'; a code with no section above it is its own cell."""
    cur, seen = code, set()
    while cur is not None and cur.id not in seen:
        if cur.level == "section":
            return cur.id
        seen.add(cur.id)
        cur = code_by_id.get(cur.parent_id)
    return code.id


def ctc_forecast(db: Session, job: Job, as_of: Optional[date] = None) -> dict:
    """The forecast table for one job: a row per section × cost type with the
    budget, actual (to `as_of`), the default cost to complete, the typed
    amount (or None) and the cost to complete that counts. `has_basis` is False
    when the job has no budget and no typed amounts — nothing to measure
    percent complete against."""
    from app.services.job_costing import read_cost_type_map

    types = read_cost_type_map(db)
    code_by_id = {c.id: c for c in db.query(CostCode).all()}

    def eff_type(code_id, line_type):
        t = line_type or (
            code_by_id[code_id].cost_type if code_id in code_by_id else ""
        )
        return t if t in types else "other"

    def key_for(code_id, line_type):
        if code_id and code_id in code_by_id:
            return (
                _section_id(code_by_id[code_id], code_by_id),
                eff_type(code_id, line_type),
            )
        return (None, "")

    cells: dict = {}

    def cell(key):
        return cells.setdefault(key, {"budget": _ZERO, "actual": _ZERO, "typed": None})

    for b in db.query(JobBudget).filter(JobBudget.job_id == job.id):
        cell(key_for(b.cost_code_id, b.cost_type))["budget"] += Decimal(str(b.amount))

    q = (
        db.query(
            TransactionLine.cost_code_id,
            TransactionLine.cost_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            job_attribution() == job.id,
            Account.account_type.in_((AccountType.COGS, AccountType.EXPENSE)),
        )
    )
    if as_of is not None:
        q = q.filter(Transaction.date <= as_of)
    for code_id, line_type, dr, cr in q.group_by(
        TransactionLine.cost_code_id, TransactionLine.cost_type
    ).all():
        cell(key_for(code_id, line_type))["actual"] += Decimal(str(dr)) - Decimal(
            str(cr)
        )

    saved = db.query(JobCostToComplete).filter(JobCostToComplete.job_id == job.id).all()
    for row in saved:
        cell((row.cost_code_id, row.cost_type or ""))["typed"] = _q(row.amount)

    rows = []
    for (section_id, cost_type), c in cells.items():
        budget, actual = _q(c["budget"]), _q(c["actual"])
        default = max(_q(budget - actual), _ZERO)
        ctc = c["typed"] if c["typed"] is not None else default
        section = code_by_id.get(section_id)
        rows.append(
            {
                "cost_code_id": section_id,
                "section": section.label if section else "Unassigned",
                "cost_type": cost_type,
                "cost_type_name": (types[cost_type].name if cost_type in types else ""),
                "budget": float(budget),
                "actual": float(actual),
                "default_cost_to_complete": float(default),
                "entered": float(c["typed"]) if c["typed"] is not None else None,
                "cost_to_complete": float(ctc),
                "projected_final": float(_q(actual + ctc)),
            }
        )
    rows.sort(key=lambda r: (r["cost_code_id"] is None, r["section"], r["cost_type"]))

    def total(key):
        return float(_q(sum((Decimal(str(r[key])) for r in rows), _ZERO)))

    totals = {
        k: total(k) for k in ("budget", "actual", "cost_to_complete", "projected_final")
    }
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "as_of": as_of.isoformat() if as_of else None,
        "has_basis": bool(saved) or totals["budget"] > 0,
        "rows": rows,
        "totals": totals,
    }


def set_ctc_entries(db: Session, job: Job, entries: list[dict]) -> dict:
    """Save typed cost-to-complete amounts. Each entry names a section
    (cost_code_id, or None with cost_type "" for the Unassigned cell), a cost
    type and an amount; an amount of None removes the entry so the cell goes
    back to its default. Raises 422 on a negative amount, an unknown type or a
    code that is not a section cell of the chart."""
    from fastapi import HTTPException

    from app.services.job_costing import cost_type_map

    types = cost_type_map(db)
    code_by_id = {c.id: c for c in db.query(CostCode).all()}
    for e in entries:
        code_id = e.get("cost_code_id")
        cost_type = e.get("cost_type") or ""
        amount = e.get("amount")
        if code_id is None:
            if cost_type != "":
                raise HTTPException(422, "The Unassigned cell takes no cost type")
        else:
            code = code_by_id.get(code_id)
            if code is None:
                raise HTTPException(422, f"Unknown cost code {code_id}")
            if _section_id(code, code_by_id) != code.id:
                raise HTTPException(
                    422, f"{code.label} is under a section; enter it on the section"
                )
            if cost_type not in types:
                raise HTTPException(422, f"Unknown cost type '{cost_type}'")
        if amount is not None and Decimal(str(amount)) < _ZERO:
            raise HTTPException(422, "Cost to complete cannot be negative")

        row = (
            db.query(JobCostToComplete)
            .filter(
                JobCostToComplete.job_id == job.id,
                (
                    JobCostToComplete.cost_code_id.is_(None)
                    if code_id is None
                    else JobCostToComplete.cost_code_id == code_id
                ),
                JobCostToComplete.cost_type == cost_type,
            )
            .first()
        )
        if amount is None:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(
                JobCostToComplete(
                    job_id=job.id,
                    cost_code_id=code_id,
                    cost_type=cost_type,
                    amount=_q(amount),
                )
            )
        else:
            row.amount = _q(amount)
    db.commit()
    return ctc_forecast(db, job, date.today())


CTCF_COLUMNS = [
    {"key": "section", "label": "Section", "kind": "text"},
    {"key": "cost_type_name", "label": "Cost type", "kind": "text"},
    {"key": "budget", "label": "Budget", "kind": "money"},
    {"key": "actual", "label": "Actual", "kind": "money"},
    {"key": "default_cost_to_complete", "label": "Default", "kind": "money"},
    {"key": "entered", "label": "Entered", "kind": "money"},
    {"key": "cost_to_complete", "label": "Cost to complete", "kind": "money"},
    {"key": "projected_final", "label": "Projected final", "kind": "money"},
]


def ctc_forecast_report(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """The forecast table for every selected job, with totals per job and a
    grand total — the table WIP reads its estimated total cost from."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [ctc_forecast(db, job, as_of) for job in jobs]
    grand = {
        k: float(_q(sum((Decimal(str(j["totals"][k])) for j in rows), _ZERO)))
        for k in ("budget", "actual", "cost_to_complete", "projected_final")
    }
    return {
        "as_of": as_of.isoformat(),
        "jobs": rows,
        "totals": grand,
        "columns": CTCF_COLUMNS,
    }


# ── 2. Cost to complete ───────────────────────────────────────────────────

CTC_COLUMNS = [
    {"key": "label", "label": "Cost code", "kind": "text"},
    {"key": "budget_revised", "label": "Budget", "kind": "money"},
    {"key": "committed", "label": "Committed", "kind": "money"},
    {"key": "actual", "label": "Actual", "kind": "money"},
    {"key": "cost_to_complete", "label": "Cost to complete", "kind": "money"},
    {"key": "projected_final", "label": "Projected final", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "pct_complete", "label": "% complete", "kind": "pct"},
]


def _ctc_figures(raw: dict) -> dict:
    """Derive the cost-to-complete columns from the aggregated raw figures of a
    node (budget original/changes, committed remaining, actual)."""
    revised = _q(raw["orig"] + raw["chg"])
    committed = _q(raw["committed"])
    actual = _q(raw["actual"])
    remaining_to_budget = revised - actual - committed
    if remaining_to_budget < _ZERO:
        remaining_to_budget = _ZERO
    cost_to_complete = _q(remaining_to_budget + committed)
    projected_final = _q(actual + cost_to_complete)
    variance = _q(revised - projected_final)
    pct = _q(actual / revised * 100) if revised else None
    return {
        "budget_revised": float(revised),
        "committed": float(committed),
        "actual": float(actual),
        "cost_to_complete": float(cost_to_complete),
        "projected_final": float(projected_final),
        "variance": float(variance),
        "pct_complete": float(pct) if pct is not None else None,
    }


def _blank_raw() -> dict:
    return {"orig": _ZERO, "chg": _ZERO, "committed": _ZERO, "actual": _ZERO}


def _ctc_totals(budget: Decimal, committed: Decimal, actual: Decimal, parts) -> dict:
    """Totals that foot the rows: budget / committed / actual are the raw sums,
    but cost to complete, projected final and variance are the SUM of the
    already-clamped per-row figures (Σ max(…) ≠ max(Σ …) once a code runs over),
    and percent complete is recomputed on the summed cost and budget."""
    ctc = _q(sum((Decimal(str(p["cost_to_complete"])) for p in parts), _ZERO))
    projected = _q(sum((Decimal(str(p["projected_final"])) for p in parts), _ZERO))
    variance = _q(sum((Decimal(str(p["variance"])) for p in parts), _ZERO))
    budget, committed, actual = _q(budget), _q(committed), _q(actual)
    pct = _q(actual / budget * 100) if budget else None
    return {
        "budget_revised": float(budget),
        "committed": float(committed),
        "actual": float(actual),
        "cost_to_complete": float(ctc),
        "projected_final": float(projected),
        "variance": float(variance),
        "pct_complete": float(pct) if pct is not None else None,
    }


def _ctc_job(db: Session, job: Job, as_of: date, tree: list) -> dict:
    """Cost-to-complete for one job: the cost-code tree (division › section)
    with budget / committed / actual rolled up, plus an Uncoded row and totals.
    `tree` is the shared code_tree, built once by the caller."""
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine

    raw: dict = {}  # cost_code_id (or None) -> raw figures

    def bucket(code_id):
        return raw.setdefault(code_id, _blank_raw())

    for b in db.query(JobBudget).filter(JobBudget.job_id == job.id):
        d = bucket(b.cost_code_id)
        if b.source == "change":
            d["chg"] += Decimal(str(b.amount))
        else:
            d["orig"] += Decimal(str(b.amount))

    # actual cost (COGS + expense; income is not a cost) as of the date
    cost_types = (AccountType.COGS, AccountType.EXPENSE)
    q = (
        db.query(
            TransactionLine.cost_code_id,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(job_attribution() == job.id, Account.account_type.in_(cost_types))
    )
    if as_of is not None:
        q = q.filter(Transaction.date <= as_of)
    for code_id, dr, cr in q.group_by(TransactionLine.cost_code_id).all():
        bucket(code_id)["actual"] += Decimal(str(dr)) - Decimal(str(cr))

    # committed: the remaining (unbilled) part of each open commitment line
    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    cq = (
        db.query(
            PurchaseOrderLine.cost_code_id,
            sqlfunc.coalesce(
                sqlfunc.sum(PurchaseOrderLine.amount - PurchaseOrderLine.billed_amount),
                0,
            ),
        )
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
        .filter(
            line_job == job.id,
            PurchaseOrder.status.in_(
                (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
            ),
        )
    )
    for code_id, amt in cq.group_by(PurchaseOrderLine.cost_code_id).all():
        bucket(code_id)["committed"] += Decimal(str(amt))

    raw_grand = _blank_raw()

    def node_for(n):
        c = n["code"]
        # Build and keep the real children first — an empty section returns
        # None and must not reach the aggregation (it has no _raw).
        children = [built for built in (node_for(ch) for ch in n["children"]) if built]
        own = raw.get(c.id, _blank_raw())
        agg = {k: own[k] + sum((ch["_raw"][k] for ch in children), _ZERO) for k in own}
        if not any(agg.values()):
            return None
        return {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "label": c.label,
            "level": c.level,
            "figures": _ctc_figures(agg),
            "children": children,
            "_raw": agg,
        }

    tree_nodes = []
    for n in tree:
        built = node_for(n)
        if built is not None:
            tree_nodes.append(built)

    def accrue(node):
        for k in raw_grand:
            raw_grand[k] += node["_raw"][k]

    def strip(node):
        for ch in node.get("children", []):
            strip(ch)
        node.pop("_raw", None)

    for node in tree_nodes:
        accrue(node)

    uncoded_raw = raw.get(None, _blank_raw())
    for k in raw_grand:
        raw_grand[k] += uncoded_raw[k]

    uncoded = _ctc_figures(uncoded_raw) if any(uncoded_raw.values()) else None

    # Job totals foot the printed rows: the top-level nodes and Uncoded carry
    # the clamped cost-to-complete / projected / variance; budget, committed and
    # actual are the raw sums.
    parts = [node["figures"] for node in tree_nodes]
    if uncoded is not None:
        parts.append(uncoded)
    totals = _ctc_totals(
        raw_grand["orig"] + raw_grand["chg"],
        raw_grand["committed"],
        raw_grand["actual"],
        parts,
    )

    for node in tree_nodes:
        strip(node)
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "tree": tree_nodes,
        "uncoded": uncoded,
        "totals": totals,
    }


def cost_to_complete(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Cost to complete per job, per cost code, with grand totals that foot the
    jobs."""
    from app.services.job_costing import code_tree

    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    tree = code_tree(db.query(CostCode).all())
    rows = [_ctc_job(db, job, as_of, tree) for job in jobs]

    budget = sum((Decimal(str(j["totals"]["budget_revised"])) for j in rows), _ZERO)
    committed = sum((Decimal(str(j["totals"]["committed"])) for j in rows), _ZERO)
    actual = sum((Decimal(str(j["totals"]["actual"])) for j in rows), _ZERO)
    totals = _ctc_totals(budget, committed, actual, [j["totals"] for j in rows])
    return {
        "as_of": as_of.isoformat(),
        "jobs": rows,
        "totals": totals,
        "columns": CTC_COLUMNS,
    }


# ── 3. Change-order log ───────────────────────────────────────────────────

CO_COLUMNS = [
    {"key": "number", "label": "Number", "kind": "text"},
    {"key": "kind", "label": "Kind", "kind": "text"},
    {"key": "status", "label": "Status", "kind": "text"},
    {"key": "title", "label": "Title", "kind": "text"},
    {"key": "date", "label": "Date", "kind": "text"},
    {"key": "funding", "label": "Funding", "kind": "text"},
    {"key": "cost", "label": "Cost", "kind": "money"},
    {"key": "markup", "label": "Markup", "kind": "money"},
    {"key": "sell", "label": "Sell", "kind": "money"},
    {"key": "running_approved", "label": "Approved to date", "kind": "money"},
]


def _co_job(db: Session, job: Job, as_of: date) -> dict:
    from app.models.change_orders import ChangeOrder
    from app.services.change_orders import co_effective_date, co_totals

    cos = (
        db.query(ChangeOrder)
        .filter(ChangeOrder.job_id == job.id)
        .order_by(ChangeOrder.number)
        .all()
    )
    counts: dict[str, int] = {}
    pending_total = _ZERO
    running = _ZERO
    approved_changes = _ZERO  # owner, contract-funded, approved by as_of
    out_rows = []
    for co in cos:
        counts[co.status] = counts.get(co.status, 0) + 1
        figs = co_totals(co, job)
        cost = figs["cost_total"]
        sell = figs["sell_total"]
        markup = figs["markup_total"]
        approved_on = co_effective_date(co)
        approved_by_as_of = (
            co.status == "approved" and approved_on is not None and approved_on <= as_of
        )
        # The running approved total follows the contract as of the date: an
        # approved, contract-funded owner CO approved by then adds its sell.
        if approved_by_as_of and co.kind == "owner" and co.funding == "contract":
            running = _q(running + sell)
            approved_changes = running
        # Pending is read as of today (a live figure, not dated).
        if co.status == "pending" and co.kind == "owner":
            pending_total += sell
        commitment = None
        if co.kind == "sub" and co.commitment is not None:
            commitment = {
                "id": co.commitment.id,
                "number": co.commitment.po_number,
                "vendor_name": (
                    co.commitment.vendor.name if co.commitment.vendor else ""
                ),
            }
        out_rows.append(
            {
                "id": co.id,
                "number": co.number,
                "kind": co.kind,
                "status": co.status,
                "title": co.title or "",
                "date": co.date.isoformat() if co.date else None,
                "funding": co.funding,
                "cost": float(cost),
                "markup": float(markup),
                "sell": float(sell),
                "approved_at": co.approved_at.isoformat() if co.approved_at else None,
                "approved_on": approved_on.isoformat() if approved_on else None,
                "voided_at": co.voided_at.isoformat() if co.voided_at else None,
                "source_pco_id": co.source_pco_id,
                "commitment": commitment,
                "running_approved": float(running),
            }
        )

    # The contract read straight off the job's own columns (no cost tree): the
    # revised sum as of the date is the original plus the changes approved by
    # then, so it agrees with the running total above.
    # (#723) With no original stamped the original is the sum less the
    # approved change orders, not the sum itself — that counted them twice.
    from app.services.contracts import original_contract_sum

    original = original_contract_sum(db, job)
    revised = _q(original + approved_changes) if original is not None else None
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "contract": {
            "original": float(original) if original is not None else None,
            "approved_changes": float(_q(approved_changes)),
            "revised": float(revised) if revised is not None else None,
        },
        "pending_total": float(_q(pending_total)),
        "counts": counts,
        "rows": out_rows,
    }


def change_order_log(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Every change order per job, with the running approved total (as of
    `as_of` — the change orders approved by then) and the contract it builds
    (original + approved changes = revised). Pending is read as of today."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [_co_job(db, job, as_of) for job in jobs]
    counts: dict[str, int] = {}
    pending_total = _ZERO
    for job_data in rows:
        for st, n in job_data["counts"].items():
            counts[st] = counts.get(st, 0) + n
        pending_total += Decimal(str(job_data["pending_total"]))
    return {
        "as_of": as_of.isoformat(),
        "jobs": rows,
        "totals": {"counts": counts, "pending_total": float(_q(pending_total))},
        "columns": CO_COLUMNS,
    }


# ── 4. Buyout report ──────────────────────────────────────────────────────

BUYOUT_COLUMNS = [
    {"key": "package_name", "label": "Package", "kind": "text"},
    {"key": "bid_cost", "label": "Bid", "kind": "money"},
    {"key": "bought", "label": "Bought", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "committed_remaining", "label": "Remaining", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
]

_BUYOUT_SUMMED = (
    "bid_cost",
    "bought",
    "variance",
    "committed_remaining",
    "billed_to_date",
)


def buyout_report(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """The job_buyout rows across jobs, totals per job and grand (#553 is the
    per-job seam; this foots it across the company). As of `as_of`, only the
    commitments dated by then count and billing is the bills dated by then."""
    from app.services.buyout import job_buyout

    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = []
    grand = {k: _ZERO for k in _BUYOUT_SUMMED}
    for job in jobs:
        data = job_buyout(db, job, as_of=as_of)
        for k in _BUYOUT_SUMMED:
            grand[k] += Decimal(str(data["totals"][k]))
        rows.append(
            {
                "job_id": job.id,
                "job_name": job.name,
                "customer_id": job.customer_id,
                "customer_name": _customer_name(job),
                "bid_estimate_id": data["bid_estimate_id"],
                "rows": data["rows"],
                "totals": data["totals"],
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "jobs": rows,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
        "columns": BUYOUT_COLUMNS,
    }


# ── 5. Job cost by vendor ─────────────────────────────────────────────────

VENDOR_COLUMNS = [
    {"key": "vendor_name", "label": "Vendor", "kind": "text"},
    {"key": "cost", "label": "Cost", "kind": "money"},
    {"key": "bills_count", "label": "Bills", "kind": "num"},
    {"key": "paid_to_date", "label": "Paid", "kind": "money"},
    {"key": "retainage_outstanding", "label": "Retainage held", "kind": "money"},
    {"key": "open_ap", "label": "Open A/P", "kind": "money"},
]

_VENDOR_SUMMED = (
    "cost",
    "bills_count",
    "paid_to_date",
    "retainage_outstanding",
    "open_ap",
)

_NO_VENDOR = "No vendor"


def _bill_job_share(bill, job_id: int) -> Decimal:
    """The job's share of a bill: the bill's line amounts carrying the job (a
    line's own job, else the bill's header) over all its line amounts. A
    header-only bill (no line amounts, or none tagged) on the job is a full 1;
    on another job, 0. So a bill split across jobs splits its payment,
    retainage and A/P by the money on each."""
    total = _ZERO
    job_amt = _ZERO
    for ln in bill.lines:
        amt = Decimal(str(ln.amount or 0))
        total += amt
        ln_job = ln.job_id if ln.job_id is not None else bill.job_id
        if ln_job == job_id:
            job_amt += amt
    if total == _ZERO:
        return _ONE if bill.job_id == job_id else _ZERO
    return job_amt / total


def _vendor_bill_figures(db: Session, job_id: int, vendor_id: int) -> dict:
    """For one vendor on one job: the bills carrying the job and the payments,
    retainage held-less-released and open A/P on them — each prorated by the
    job's share of the bill's line amounts (a header-only bill is a whole 1),
    so a bill split across two jobs lands half on each. A bill carries the job
    on its header or on any of its lines."""
    from sqlalchemy import or_

    from app.models.bills import (
        Bill,
        BillLine,
        BillPayment,
        BillPaymentAllocation,
    )

    on_line = db.query(BillLine.bill_id).filter(BillLine.job_id == job_id)
    bills = (
        db.query(Bill)
        .filter(
            Bill.vendor_id == vendor_id,
            or_(Bill.job_id == job_id, Bill.id.in_(on_line)),
        )
        .all()
    )
    # payments allocated to each bill (non-void), by bill id
    paid_by_bill: dict[int, Decimal] = {}
    bill_ids = [b.id for b in bills]
    if bill_ids:
        rows = (
            db.query(
                BillPaymentAllocation.bill_id,
                sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0),
            )
            .select_from(BillPaymentAllocation)
            .join(
                BillPayment,
                BillPaymentAllocation.bill_payment_id == BillPayment.id,
            )
            .filter(
                BillPaymentAllocation.bill_id.in_(bill_ids),
                BillPayment.is_voided.is_(False),
            )
            .group_by(BillPaymentAllocation.bill_id)
            .all()
        )
        paid_by_bill = {bid: Decimal(str(amt)) for bid, amt in rows}

    count = _ZERO
    retainage = _ZERO
    open_ap = _ZERO
    paid = _ZERO
    for b in bills:
        share = _bill_job_share(b, job_id)
        if share == _ZERO:
            continue
        count += share
        retainage += share * (
            Decimal(str(b.retainage_held or 0))
            - Decimal(str(b.retainage_released or 0))
        )
        open_ap += share * Decimal(str(b.balance_due or 0))
        paid += share * paid_by_bill.get(b.id, _ZERO)
    return {
        "bills_count": float(_q(count)),
        "paid_to_date": _q(paid),
        "retainage_outstanding": _q(retainage),
        "open_ap": _q(open_ap),
    }


def _vendor_job(
    db: Session,
    job: Job,
    start_date: Optional[date],
    end_date: Optional[date],
) -> dict:
    from app.models.contacts import Vendor

    cost_types = (AccountType.COGS, AccountType.EXPENSE)
    q = (
        db.query(
            Transaction.vendor_id,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(job_attribution() == job.id, Account.account_type.in_(cost_types))
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    by_vendor = q.group_by(Transaction.vendor_id).all()

    vendor_ids = [v for v, _dr, _cr in by_vendor if v is not None]
    names = {
        v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()
    }

    rows = []
    for vendor_id, dr, cr in by_vendor:
        cost = Decimal(str(dr)) - Decimal(str(cr))
        if vendor_id is None:
            rows.append(
                {
                    "vendor_id": None,
                    "vendor_name": _NO_VENDOR,
                    "cost": float(_q(cost)),
                    "bills_count": 0,
                    "paid_to_date": 0.0,
                    "retainage_outstanding": 0.0,
                    "open_ap": 0.0,
                }
            )
            continue
        bf = _vendor_bill_figures(db, job.id, vendor_id)
        rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": names.get(vendor_id, ""),
                "cost": float(_q(cost)),
                "bills_count": bf["bills_count"],
                "paid_to_date": float(bf["paid_to_date"]),
                "retainage_outstanding": float(bf["retainage_outstanding"]),
                "open_ap": float(bf["open_ap"]),
            }
        )
    # named vendors first by name, the No vendor bucket last
    rows.sort(key=lambda r: (r["vendor_id"] is None, (r["vendor_name"] or "").lower()))

    totals = {k: _ZERO for k in _VENDOR_SUMMED}
    for r in rows:
        for k in _VENDOR_SUMMED:
            totals[k] += Decimal(str(r[k]))
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "rows": rows,
        "totals": {k: float(_q(v)) for k, v in totals.items()},
    }


def job_cost_by_vendor(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Posted job cost grouped job → vendor (the transaction header's vendor; a
    No vendor bucket holds the rest), with the vendor's bills, paid to date,
    retainage held outstanding and open A/P on the job. Cost is period-filtered
    by transaction date; the bill figures are job-to-date and **prorated by the
    job's share of each bill's line amounts** (a header-only bill counts whole,
    a bill split across jobs splits), so bills can read fractional."""
    from fastapi import HTTPException

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date is after end_date")
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    rows = [_vendor_job(db, job, start_date, end_date) for job in jobs]
    grand = {k: _ZERO for k in _VENDOR_SUMMED}
    for job_data in rows:
        for k in _VENDOR_SUMMED:
            grand[k] += Decimal(str(job_data["totals"][k]))
    return {
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "jobs": rows,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
        "columns": VENDOR_COLUMNS,
    }


# ── 6. Commitment status ──────────────────────────────────────────────────

COMMITMENT_COLUMNS = [
    {"key": "number", "label": "Commitment", "kind": "text"},
    {"key": "vendor_name", "label": "Vendor", "kind": "text"},
    {"key": "type", "label": "Type", "kind": "text"},
    {"key": "status", "label": "Status", "kind": "text"},
    {"key": "committed", "label": "Committed", "kind": "money"},
    {"key": "billed", "label": "Billed to date", "kind": "money"},
    {"key": "retainage_held", "label": "Retainage held", "kind": "money"},
    {"key": "remaining", "label": "Remaining", "kind": "money"},
]

_COMMITMENT_SUMMED = ("committed", "billed", "retainage_held", "remaining")


def _commitment_row(
    po, bills: list, as_of: date, co_by_id: dict, billed_later: bool = False
) -> dict:
    """One commitment as of a date. Committed is the pre-tax value; billed is
    the work billed on it by then (a retainage-release bill pays money already
    billed, so it is not counted); retainage held is held less released on those
    bills; remaining is committed less billed while the commitment is open."""
    from app.models.purchase_orders import POStatus
    from app.services.change_orders import co_effective_date

    billed = held = released = _ZERO
    for b in bills:
        released_here = Decimal(str(b.retainage_released or 0))
        released += released_here
        if released_here > _ZERO:
            continue
        billed += Decimal(str(b.subtotal or 0))
        held += Decimal(str(b.retainage_held or 0))
    # Committed as of the date: lines added by a sub change order approved
    # after it are not yet part of the commitment.
    later = _ZERO
    for ln in po.lines:
        co = ln.change_order_id and co_by_id.get(ln.change_order_id)
        if co is not None and (co_effective_date(co) or as_of) > as_of:
            later += Decimal(str(ln.amount or 0))
    committed = _q(Decimal(str(po.subtotal or 0)) - later)
    # A closed commitment counts closed on every date, unless a bill against it
    # is dated after `as_of`: then it was still taking bills, so still open.
    is_open = po.status in (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED) or (
        po.status == POStatus.CLOSED and billed_later
    )
    remaining = _q(committed - billed) if is_open else _ZERO
    status = po.status.value if hasattr(po.status, "value") else po.status
    # A commitment the later bill closed was still open on this date, and one
    # billed in part had no bill yet before its first: on either, the status
    # on the date is what the bills by then had made it (2.30.0 gate, #644).
    if po.status == POStatus.PARTIAL or (po.status == POStatus.CLOSED and billed_later):
        status = (POStatus.PARTIAL if billed > _ZERO else POStatus.SENT).value
    return {
        "id": po.id,
        "number": po.po_number,
        "vendor_name": po.vendor.name if po.vendor else "",
        "type": po.commitment_type,
        "status": status,
        "committed": float(committed),
        "billed": float(_q(billed)),
        "retainage_held": float(_q(held - released)),
        "remaining": float(remaining),
    }


def commitment_status(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Every non-draft commitment (PO or subcontract) on each job dated by
    `as_of`: committed, billed to date, retainage held and remaining, with
    totals per job and grand. Only bills dated by `as_of` count."""
    from app.models.bills import Bill, BillStatus
    from app.models.change_orders import ChangeOrder
    from app.models.purchase_orders import POStatus, PurchaseOrder

    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    out = []
    grand = {k: _ZERO for k in _COMMITMENT_SUMMED}
    for job in jobs:
        pos = (
            db.query(PurchaseOrder)
            .filter(
                PurchaseOrder.job_id == job.id,
                PurchaseOrder.status != POStatus.DRAFT,
                PurchaseOrder.date <= as_of,
            )
            .order_by(PurchaseOrder.po_number)
            .all()
        )
        co_ids = {ln.change_order_id for po in pos for ln in po.lines} - {None}
        co_by_id = {
            co.id: co
            for co in db.query(ChangeOrder).filter(ChangeOrder.id.in_(co_ids)).all()
        }
        rows = []
        totals = {k: _ZERO for k in _COMMITMENT_SUMMED}
        for po in pos:
            bills = (
                db.query(Bill)
                .filter(
                    Bill.po_id == po.id,
                    Bill.status != BillStatus.VOID,
                    Bill.date <= as_of,
                )
                .all()
            )
            billed_later = (
                db.query(Bill.id)
                .filter(
                    Bill.po_id == po.id,
                    Bill.status != BillStatus.VOID,
                    Bill.date > as_of,
                    # a retainage release pays money already billed
                    sqlfunc.coalesce(Bill.retainage_released, 0) == 0,
                )
                .first()
                is not None
            )
            row = _commitment_row(po, bills, as_of, co_by_id, billed_later)
            rows.append(row)
            for k in _COMMITMENT_SUMMED:
                totals[k] += Decimal(str(row[k]))
        for k in _COMMITMENT_SUMMED:
            grand[k] += totals[k]
        out.append(
            {
                "job_id": job.id,
                "job_name": job.name,
                "customer_id": job.customer_id,
                "customer_name": _customer_name(job),
                "rows": rows,
                "totals": {k: float(_q(v)) for k, v in totals.items()},
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "jobs": out,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
        "columns": COMMITMENT_COLUMNS,
    }
