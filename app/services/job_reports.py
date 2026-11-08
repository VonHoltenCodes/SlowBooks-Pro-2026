# ============================================================================
# Construction job reports (#556) — the standalone reports a contractor's books
# grow last: the WIP schedule with over/under billing, cost-to-complete, the
# change-order log, the buyout report and job cost by vendor.
#
# Pure functions over the figures the other services already compute — the
# contract summary (#552), the cost tree and budgets (M3), buyout (#553), change
# orders (#554) and pay applications (#555). Nothing here posts: the WIP
# adjusting entry is computed with its amounts, and posting it is the WIP
# schedule's Post (app/services/wip_posting.py). The one thing stored is the
# cost-to-complete forecast the user types.
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
from app.services.jobs_service import job_attribution, not_wip_entry

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


def job_closed_by(job: Job, as_of: Optional[date] = None) -> bool:
    """Whether the job was closed on `as_of`: its status is Closed and it
    went to Closed by then (a job closed with no date on file counts as
    closed on every date). None: closed today."""
    if job.status not in _CLOSED:
        return False
    return as_of is None or job.closed_on is None or job.closed_on <= as_of


def status_on(job: Job, as_of: Optional[date] = None) -> str:
    """The job's status as it read on `as_of`: a job closed after the day was
    still in progress on it."""
    if job.status in _CLOSED and not job_closed_by(job, as_of):
        return "in_progress"
    return job.status


def co_stood_on(co, on: date) -> bool:
    """Whether a change order stood approved on `on`: it had taken effect by
    then and was not voided until after it. A change order counts as of a
    day in the state it had that day, in every report."""
    from app.services.change_orders import co_effective_date

    effective = co_effective_date(co)
    if effective is None or effective > on:
        return False
    if co.status == "approved":
        return True
    return (
        co.status == "void"
        and co.voided_at is not None
        and _local_date(co.voided_at) > on
    )


def voided_since(db: Session, job_ids: list[int], on: date) -> list:
    """The jobs' change orders voided after `on` that stood approved on it:
    their effects are gone from the books today, but not from that day."""
    from app.models.change_orders import ChangeOrder

    if not job_ids:
        return []
    cos = (
        db.query(ChangeOrder)
        .filter(
            ChangeOrder.job_id.in_(job_ids),
            ChangeOrder.status == "void",
            ChangeOrder.voided_at.isnot(None),
        )
        .all()
    )
    return [co for co in cos if co_stood_on(co, on)]


def sub_lines_voided_since(db: Session, po_ids, on: date) -> dict:
    """The commitment lines sub change orders added that stood on `on` and
    were voided since, per commitment id: the void removed them from the
    commitment, but on that day they were part of it. Each is the change
    order's own line (the void refuses a billed one, so none was billed)."""
    from app.models.change_orders import ChangeOrder

    if not po_ids:
        return {}
    out: dict = {}
    for co in db.query(ChangeOrder).filter(
        ChangeOrder.kind == "sub",
        ChangeOrder.status == "void",
        ChangeOrder.commitment_id.in_(list(po_ids)),
    ):
        if co_stood_on(co, on):
            out.setdefault(co.commitment_id, []).extend(co.lines)
    return out


def contract_on(db: Session, job: Job, on: date) -> Optional[Decimal]:
    """The job's contract sum as it stood on `on`: today's, less the
    contract-funded owner change orders that take effect after the day, plus
    those that stood on it and have been voided since."""
    from app.services.change_orders import co_totals
    from app.services.contracts import contract_changes_after

    if job.contract_amount is None:
        return None
    restored = sum(
        (
            co_totals(co, job)["sell_total"]
            for co in voided_since(db, [job.id], on)
            if co.kind == "owner" and co.funding == "contract"
        ),
        _ZERO,
    )
    return _q(_q(job.contract_amount) - contract_changes_after(db, job, on) + restored)


def select_jobs(
    db: Session,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
    as_of: Optional[date] = None,
) -> list[Job]:
    """The jobs a construction report runs over, customer then job name.

    `status` names one job status and wins; otherwise not-awarded jobs are
    always left out and closed ones too unless `include_closed`. With
    `as_of`, a job closed after that day was still open on it, so it stays
    in."""
    q = db.query(Job).options(joinedload(Job.customer))
    if job_id is not None:
        q = q.filter(Job.id == job_id)
    if customer_id is not None:
        q = q.filter(Job.customer_id == customer_id)
    if status:
        q = q.filter(Job.status == status)
    else:
        q = q.filter(~Job.status.in_(_NEVER_DEFAULT))
        if not include_closed:
            closed = Job.status.in_(_CLOSED)
            if as_of is not None:
                closed = closed & (Job.closed_on.is_(None) | (Job.closed_on <= as_of))
            q = q.filter(~closed)
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

# The accounts are construction setup's (CONSTRUCTION_ACCOUNTS), by name.
WIP_OVERBILLING_ACCOUNT = "Billings in Excess of Costs and Estimated Earnings"
WIP_UNDERBILLING_ACCOUNT = "Costs and Estimated Earnings in Excess of Billings"
WIP_REVENUE_ACCOUNT = "Contract Revenue – WIP Adjustment"
WIP_LOSS_EXPENSE_ACCOUNT = "Loss on Uncompleted Contracts"
WIP_LOSS_ACCOUNT = "Provision for Loss on Uncompleted Contracts"
WIP_ACCOUNTS = (
    WIP_UNDERBILLING_ACCOUNT,
    WIP_OVERBILLING_ACCOUNT,
    WIP_REVENUE_ACCOUNT,
    WIP_LOSS_EXPENSE_ACCOUNT,
    WIP_LOSS_ACCOUNT,
)


def wip_account_numbers(db: Session) -> dict:
    """The number each WIP account carries: its number in the chart, else the
    one construction setup (or Post) would give it — the suggested number
    when no account holds it yet, else none. A read: nothing is created."""
    from app.services.accounting import CONSTRUCTION_ACCOUNTS

    suggested = {name: number for number, name, _t, _d in CONSTRUCTION_ACCOUNTS}
    out = {}
    for name in WIP_ACCOUNTS:
        acct = db.query(Account).filter(Account.name == name).first()
        if acct is not None:
            out[name] = acct.account_number
            continue
        number = suggested[name]
        taken = db.query(Account.id).filter(Account.account_number == number).first()
        out[name] = None if taken else number
    return out


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
    {"key": "gross_profit_to_date", "label": "GP to date", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
    {"key": "over_billing", "label": "Over billed", "kind": "money"},
    {"key": "under_billing", "label": "Under billed", "kind": "money"},
    {"key": "retainage_outstanding", "label": "Retainage", "kind": "money"},
    {"key": "backlog", "label": "Backlog", "kind": "money"},
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
    "gross_profit_to_date",
    "billed_to_date",
    "over_billing",
    "under_billing",
    "retainage_outstanding",
    "backlog",
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
    # A closed job is finished: 100% complete, nothing still to spend.
    forecast = ctc_forecast(db, job, as_of)
    closed = forecast["closed"]
    if forecast["has_basis"] or closed:
        cost_to_complete = _q(forecast["totals"]["cost_to_complete"])
    else:
        cost_to_complete = _ZERO
    est_total_cost = _q(cost_to_date + cost_to_complete)
    posted_revenue = _q(totals["act_revenue"])

    summary = contract_summary(
        db, job, cost=cost_to_date, billed=posted_revenue, as_of=as_of
    )
    # The original as the pay application prints it (#723): the stamped one,
    # else the sum less the approved change orders (a sum from the job form
    # with no contract type saved was never stamped).
    original = original_contract_sum(db, job)
    # The contract as of the date: back out the contract-funded COs approved
    # after it and put back those voided since that stood on it, so original
    # + approved-as-of = revised-as-of (the Change Order Log's rule).
    revised = contract_on(db, job, as_of)
    approved_cos = _q(revised - original)
    billed = _q(summary["billed_to_date"])
    retainage_out = _q(summary["retainage"]["outstanding"])

    if not forecast["has_basis"] and not closed and cost_to_date < revised:
        return _unmeasured_wip_row(
            job, original, approved_cos, revised, cost_to_date, billed, retainage_out
        )

    if closed:
        pct = _ONE
    elif est_total_cost > _ZERO:
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
    warnings = _wip_row_warnings(
        db, job, forecast, revised, billed, retainage_out, cost_to_complete
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
        "gross_profit_to_date": float(_q(earned - cost_to_date)),
        "billed_to_date": float(billed),
        "over_billing": float(over),
        "under_billing": float(under),
        "retainage_outstanding": float(retainage_out),
        "backlog": float(_q(revised - earned)),
        "cost_to_complete": float(cost_to_complete),
        "projected_gross_profit": float(projected_gp),
        "projected_loss": float(projected_loss),
        "loss_provision": float(loss_provision),
        "warnings": warnings,
    }


# A job billed at least this share of its revised contract is near its end:
# a budget cell that has had no cost and no forecast is then named in the
# warnings, since its whole budget still counts as cost to come.
_NEARLY_BILLED = Decimal("0.90")


def _money_text(value: Decimal) -> str:
    return f"${_q(value):,.2f}"


def _open_commitments(db: Session, job: Job) -> int:
    """How many issued, not closed commitments (PO or subcontract) the job
    has, on the header or on a line."""
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine

    on_line = (
        db.query(PurchaseOrderLine.purchase_order_id)
        .filter(PurchaseOrderLine.job_id == job.id)
        .subquery()
    )
    return (
        db.query(PurchaseOrder.id)
        .filter(
            (PurchaseOrder.job_id == job.id) | PurchaseOrder.id.in_(on_line),
            PurchaseOrder.status.in_(
                (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
            ),
        )
        .count()
    )


def _wip_row_warnings(
    db: Session, job: Job, forecast: dict, revised, billed, retainage_out, ctc
) -> list[str]:
    """What a measured row should say about its cost to complete: a section
    that never burns down on a job billed near its end (budget, no cost, no
    forecast entered), and a job billed in full, its retainage collected and
    every commitment closed that still shows cost to come."""
    out = []
    if ctc <= _ZERO or forecast["closed"] or revised <= _ZERO:
        return out
    billed_share = billed / revised
    if billed_share >= _NEARLY_BILLED:
        sections: dict = {}
        for r in forecast["rows"]:
            s = sections.setdefault(
                r["cost_code_id"],
                {"label": r["section"], "budget": _ZERO, "actual": _ZERO},
            )
            s["budget"] += Decimal(str(r["budget"]))
            s["actual"] += Decimal(str(r["actual"]))
            s["entered"] = s.get("entered") or r["entered"] is not None
            s["ctc"] = s.get("ctc", _ZERO) + Decimal(str(r["cost_to_complete"]))
        for s in sections.values():
            if s["budget"] > _ZERO and s["actual"] == _ZERO and not s["entered"]:
                out.append(
                    f"{job.name}: {s['label']} has a budget of "
                    f"{_money_text(s['budget'])} with no cost posted and no "
                    f"forecast entered, so {_money_text(s['ctc'])} counts as "
                    f"cost still to come on a job billed "
                    f"{billed_share * 100:.1f}% of its contract. If that work "
                    "is done or was never going to post there, enter 0 for "
                    "it in the Cost-to-Complete Forecast."
                )
    if billed >= revised and retainage_out <= _ZERO and not _open_commitments(db, job):
        out.append(
            f"{job.name}: billed in full, retainage collected and every "
            f"commitment closed, yet {_money_text(ctc)} of cost to complete "
            "remains. If the job is finished, close it (a closed job reads "
            "100% complete) or enter 0 in the Cost-to-Complete Forecast."
        )
    return out


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
    then. The estimated total cost takes the budget with the change orders
    in effect by then, and a job closed after the date reads as it did: open.
    The WIP adjusting entry itself is in none of these figures."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed, as_of)
    # A job whose contract is dated after the day had no contract on it.
    jobs = [j for j in jobs if j.contract_date is None or j.contract_date <= as_of]
    rows = [wip_row(db, job, as_of) for job in jobs]
    for job, r in zip(jobs, rows):
        r["status"] = status_on(job, as_of)
    warnings = [r["warning"] for r in rows if r.get("excluded")]
    for r in rows:
        warnings += r.get("warnings") or []
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
    from app.services.wip_posting import posted_entry_info

    numbers = wip_account_numbers(db)
    entry = wip_adjusting_entry(rows, as_of, numbers)
    # Post books the company's whole schedule for the date; whether that is
    # done is said whatever filter this run has.
    posted_info = posted_entry_info(db, as_of)
    if posted_info is not None:
        # What is on the books is the posted entry: show its lines, and say
        # when the schedule no longer reads the same (a change order approved
        # back-dated, a late bill on or before the date).
        keep = (
            {r["job_id"] for r in rows}
            if job_id is not None or customer_id is not None or status
            else None
        )
        entry, warning = _posted_view(db, posted_info, entry, rows, keep, as_of)
        if warning:
            warnings.append(warning)
    entry["posted_entry"] = posted_info
    entry["posted"] = posted_info is not None
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
        "adjusting_entry": entry,
    }


# ── WIP adjusting entry ───────────────────────────────────────────────────


def _entry_line(account: str, debit: Decimal, credit: Decimal, numbers: dict) -> dict:
    return {
        "account": account,
        "account_number": numbers.get(account),
        "debit": float(debit),
        "credit": float(credit),
    }


def _job_entry_lines(
    under: Decimal, over: Decimal, provision: Decimal, numbers: dict
) -> list:
    """The lines for one job's balances: an under-billing is an asset against
    revenue, an over-billing is revenue deferred to a liability, and the loss
    provision is an expense against its liability."""
    lines = []
    if under > _ZERO:
        lines.append(_entry_line(WIP_UNDERBILLING_ACCOUNT, under, _ZERO, numbers))
    if over > _ZERO:
        lines.append(_entry_line(WIP_OVERBILLING_ACCOUNT, _ZERO, over, numbers))
    net_revenue = under - over  # credit when under-billed, debit when over
    if net_revenue > _ZERO:
        lines.append(_entry_line(WIP_REVENUE_ACCOUNT, _ZERO, net_revenue, numbers))
    elif net_revenue < _ZERO:
        lines.append(_entry_line(WIP_REVENUE_ACCOUNT, -net_revenue, _ZERO, numbers))
    if provision > _ZERO:
        lines.append(_entry_line(WIP_LOSS_EXPENSE_ACCOUNT, provision, _ZERO, numbers))
        lines.append(_entry_line(WIP_LOSS_ACCOUNT, _ZERO, provision, numbers))
    return lines


def wip_adjusting_entry(
    rows: list[dict], as_of: date, numbers: Optional[dict] = None
) -> dict:
    """The WIP adjusting entry with amounts, per job and net, from the schedule
    rows. The balances are carried to `as_of` in full; Post (wip_posting)
    books them with a reversal the next day, so each period's entry stands
    alone. Computed here: nothing is written to the books."""
    numbers = numbers or {}
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
                "lines": _job_entry_lines(under, over, prov, numbers),
            }
        )
    net = _job_entry_lines(_q(t_under), _q(t_over), _q(t_prov), numbers)
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


def _posted_rows(db: Session, transaction_id: int, rows: list[dict]) -> list[dict]:
    """The posted WIP entry's figures per job, in the shape wip_adjusting_entry
    reads: each job's under- and over-billing and loss provision from the
    lines on its accounts."""
    by_job: dict = {}
    for ln, name in (
        db.query(TransactionLine, Account.name)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(TransactionLine.transaction_id == transaction_id)
    ):
        f = by_job.setdefault(ln.job_id, {"under": _ZERO, "over": _ZERO, "prov": _ZERO})
        net = Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))
        if name == WIP_UNDERBILLING_ACCOUNT:
            f["under"] += net
        elif name == WIP_OVERBILLING_ACCOUNT:
            f["over"] -= net
        elif name == WIP_LOSS_ACCOUNT:
            f["prov"] -= net
    computed = {r["job_id"]: r for r in rows}
    out = []
    for jid, f in by_job.items():
        job = db.get(Job, jid) if jid is not None else None
        r = computed.get(jid, {})
        out.append(
            {
                "job_id": jid,
                "job_name": job.name if job else "No job",
                "customer_name": _customer_name(job) if job else "",
                "has_contract": True,
                "under_billing": float(_q(f["under"])),
                "over_billing": float(_q(f["over"])),
                "loss_provision": float(_q(f["prov"])),
                "projected_loss": r.get("projected_loss") or 0.0,
            }
        )
    out.sort(key=lambda r: (r["customer_name"].lower(), r["job_name"].lower()))
    return out


def _entry_figures(entry: dict) -> dict:
    """Each job's (under, over, provision) in an entry, for comparing two."""
    return {
        j["job_id"]: (j["under_billing"], j["over_billing"], j["loss_provision"])
        for j in entry["jobs"]
    }


def _posted_view(
    db: Session, info: dict, computed: dict, rows: list, keep, as_of: date
) -> tuple[dict, Optional[str]]:
    """The adjusting entry as posted for the date (only the jobs in `keep`
    when the run is filtered), with `posted_differs`, the schedule's own
    figures under `computed` and, when they differ, a sentence to say so."""
    posted_rows = [
        r
        for r in _posted_rows(db, info["transaction_id"], rows)
        if keep is None or r["job_id"] in keep
    ]
    entry = wip_adjusting_entry(posted_rows, as_of, wip_account_numbers(db))
    differs = _entry_figures(entry) != _entry_figures(computed)
    entry["posted_differs"] = differs
    entry["posted_warning"] = None
    entry["computed"] = {
        k: computed[k]
        for k in ("jobs", "totals", "lines", "total_debits", "total_credits")
    }
    if not differs:
        return entry, None
    p, c = entry["totals"], computed["totals"]
    warning = (
        f"The WIP entry posted for {as_of.isoformat()} (journal entry "
        f"{info['transaction_id']}) books over-billings "
        f"{_money_text(Decimal(str(p['over_billing'])))}, under-billings "
        f"{_money_text(Decimal(str(p['under_billing'])))} and a loss provision "
        f"of {_money_text(Decimal(str(p['loss_provision'])))}; the schedule now "
        f"reads {_money_text(Decimal(str(c['over_billing'])))}, "
        f"{_money_text(Decimal(str(c['under_billing'])))} and "
        f"{_money_text(Decimal(str(c['loss_provision'])))}, because something "
        f"dated on or before {as_of.isoformat()} changed after it was posted "
        "(a change order approved back-dated, a late bill). Void the posted "
        "entry and post it again to book the schedule's figures."
    )
    entry["posted_warning"] = warning
    return entry, warning


# ── Cost-to-complete forecast (per job, section and cost type) ───────────────
#
# A cell is a section × cost type. The default cost to complete nets the cost
# types within a section — the section's budget less its actual, floored at
# zero — and shows it against the cells with budget left; a typed amount
# (job_cost_to_complete) replaces its cell's share. A section whose budget is
# below zero (an accepted deduct alternate with nothing in its section to
# deduct from) keeps that budget as a negative cost to complete, which takes
# it off the cost still to come elsewhere, never past zero for the job.
# Budget and cost with no code share one Unassigned cell, so an uncoded budget
# is measured against uncoded cost. A closed job has no cost to complete.


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
    from app.services.job_costing import budget_rows, read_cost_type_map

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

    for b in budget_rows(db, [job.id], as_of):
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
            not_wip_entry(),
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

    defaults = _netted_defaults(cells)
    counted = _cap_deducts(
        {
            k: (c["typed"] if c["typed"] is not None else defaults[k])
            for k, c in cells.items()
        }
    )
    closed = job_closed_by(job, as_of)
    rows = []
    for (section_id, cost_type), c in cells.items():
        budget, actual = _q(c["budget"]), _q(c["actual"])
        default = defaults[(section_id, cost_type)]
        ctc = counted[(section_id, cost_type)]
        if closed:
            # a closed job has nothing left to spend, whatever the budget
            default = ctc = _ZERO
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
    # A budget written by a change order alone is no estimate of the job: a
    # job whose only budget rows came from change orders (a hand-typed SOV
    # and one owner CO) has nothing to measure against, as one with none.
    contract_budget = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(JobBudget.amount), 0))
        .filter(JobBudget.job_id == job.id, JobBudget.source != "change")
        .scalar()
    )
    return {
        "job_id": job.id,
        "job_name": job.name,
        "customer_id": job.customer_id,
        "customer_name": _customer_name(job),
        "as_of": as_of.isoformat() if as_of else None,
        "has_basis": bool(saved) or Decimal(str(contract_budget)) > _ZERO,
        "closed": closed,
        "rows": rows,
        "totals": totals,
    }


def _netted_defaults(cells: dict) -> dict:
    """The default cost to complete of each section × cost type cell. Cost
    types net within a section: its default is the section's budget less its
    actual, floored at zero, so a trade bid as subcontract and bought as
    material does not keep its whole subcontract budget still to spend. The
    section's default is shown against the cells that still have budget left,
    in proportion to what each has left, the last taking the rounding.

    A section whose budget nets below zero is a deduct with nothing in its
    section to deduct from: its default is that budget (cost posted there is
    cost beyond the plan, so it does not deepen the deduct), shown against
    its negative cells."""
    by_section: dict = {}
    for key in cells:
        by_section.setdefault(key[0], []).append(key)
    out = {}
    for keys in by_section.values():
        budget = sum((cells[k]["budget"] for k in keys), _ZERO)
        actual = sum((cells[k]["actual"] for k in keys), _ZERO)
        if budget < _ZERO:
            pool = _q(budget)
            left = {k: -min(cells[k]["budget"], _ZERO) for k in keys}
        else:
            pool = max(_q(budget - actual), _ZERO)
            left = {
                k: max(_q(cells[k]["budget"] - cells[k]["actual"]), _ZERO) for k in keys
            }
        weight = sum(left.values(), _ZERO)
        shared = [k for k in keys if left[k] > _ZERO]
        given = _ZERO
        for k in keys:
            out[k] = _ZERO
        for i, k in enumerate(shared):
            if i == len(shared) - 1:
                out[k] = _q(pool - given)
            else:
                out[k] = _q(pool * left[k] / weight)
                given += out[k]
    return out


def _cap_deducts(ctc: dict) -> dict:
    """The cost to complete that counts per cell: a deduct's negative amount
    comes off the cost still to come in the other cells, but the job's total
    never goes below zero (a deduct cannot un-spend cost already posted).
    When the deducts are more than what is still to come, each is scaled
    back in proportion, the last taking the rounding."""
    to_come = sum((v for v in ctc.values() if v > _ZERO), _ZERO)
    deducts = [k for k, v in ctc.items() if v < _ZERO]
    owed = -sum((ctc[k] for k in deducts), _ZERO)
    if not deducts or owed <= to_come:
        return dict(ctc)
    out = dict(ctc)
    given = _ZERO
    for i, k in enumerate(deducts):
        if i == len(deducts) - 1:
            out[k] = _q(-to_come - given)
        else:
            out[k] = _q(ctc[k] * to_come / owed)
            given += out[k]
    return out


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
    jobs = select_jobs(db, job_id, customer_id, status, include_closed, as_of)
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
    if revised < _ZERO:
        # a deduct alternate: its budget is cost the job will not spend, so
        # it reads no variance until cost posts against it
        remaining_to_budget = revised
    elif remaining_to_budget < _ZERO:
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
    `tree` is the shared code_tree, built once by the caller. Budgets take
    the change orders in effect by `as_of`, and committed is Commitment
    Status's remaining that day."""
    from app.services.job_costing import budget_rows, commitment_lines

    raw: dict = {}  # cost_code_id (or None) -> raw figures

    def bucket(code_id):
        return raw.setdefault(code_id, _blank_raw())

    for b in budget_rows(db, [job.id], as_of):
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
        .filter(
            job_attribution() == job.id,
            Account.account_type.in_(cost_types),
            not_wip_entry(),
        )
    )
    if as_of is not None:
        q = q.filter(Transaction.date <= as_of)
    for code_id, dr, cr in q.group_by(TransactionLine.cost_code_id).all():
        bucket(code_id)["actual"] += Decimal(str(dr)) - Decimal(str(cr))

    # committed: the remaining (unbilled) part of each open commitment line
    for ln in commitment_lines(db, [job.id], as_of):
        bucket(ln["cost_code_id"])["committed"] += ln["remaining"]

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
    jobs = select_jobs(db, job_id, customer_id, status, include_closed, as_of)
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

# The change order log's export carries these too: the day each change
# order took effect and was voided, the PCO it came from and a sub CO's
# commitment (the export's columns are CO_COLUMNS then these).
CO_EXPORT_EXTRA = [
    {"key": "approved_on", "label": "Approved on", "kind": "text"},
    {"key": "voided_on", "label": "Voided on", "kind": "text"},
    {"key": "source_pco", "label": "Source PCO", "kind": "text"},
    {"key": "commitment_number", "label": "Commitment", "kind": "text"},
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
    pco_ids = {co.source_pco_id for co in cos} - {None}
    pco_numbers = {
        c_id: number
        for c_id, number in db.query(ChangeOrder.id, ChangeOrder.number).filter(
            ChangeOrder.id.in_(pco_ids)
        )
    }
    counts: dict[str, int] = {}
    pending_total = _ZERO
    running = _ZERO
    approved_changes = _ZERO  # owner, contract-funded, approved by as_of
    out_rows = []
    for co in cos:
        st = _co_status_on(co, as_of)
        if st is None:
            continue  # dated after the day: not on its log
        counts[st] = counts.get(st, 0) + 1
        figs = co_totals(co, job)
        cost = figs["cost_total"]
        sell = figs["sell_total"]
        markup = figs["markup_total"]
        approved_on = co_effective_date(co)
        # The running approved total follows the contract as of the date: a
        # contract-funded owner CO that stood approved then adds its sell,
        # the same rule the counts and the WIP schedule read.
        if st == "approved" and co.kind == "owner" and co.funding == "contract":
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
                "status": st,
                "status_today": co.status,
                "title": co.title or "",
                "date": co.date.isoformat() if co.date else None,
                "funding": co.funding,
                "cost": float(cost),
                "markup": float(markup),
                "sell": float(sell),
                "approved_at": co.approved_at.isoformat() if co.approved_at else None,
                "approved_on": approved_on.isoformat() if approved_on else None,
                "voided_at": co.voided_at.isoformat() if co.voided_at else None,
                "voided_on": (
                    _local_date(co.voided_at).isoformat() if co.voided_at else None
                ),
                "source_pco_id": co.source_pco_id,
                "source_pco": pco_numbers.get(co.source_pco_id, ""),
                "commitment": commitment,
                "commitment_number": commitment["number"] if commitment else "",
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


def _co_status_on(co, as_of: date) -> Optional[str]:
    """The status a change order had on `as_of`: approved when it stood
    approved that day (co_stood_on), pending when it was approved or voided
    after it; a draft, pending or rejected one reads as it is today (those
    carry no date of their own). None for one dated after the day: it is not
    on that day's log."""
    from app.services.change_orders import co_effective_date

    if co_stood_on(co, as_of):
        return "approved"
    if co.date is not None and co.date > as_of:
        return None
    effective = co_effective_date(co)
    voided = _local_date(co.voided_at) if co.voided_at else None
    if co.status == "void" and voided is not None and voided > as_of:
        return "pending"
    if co.status == "approved" and effective is not None and effective > as_of:
        return "pending"
    return co.status


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
    (original + approved changes = revised). Counts by status are as of the
    date (a change order approved or voided after it counts as it stood);
    the pending total is read as of today."""
    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed, as_of)
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
        "export_columns": CO_COLUMNS + CO_EXPORT_EXTRA,
    }


# ── 4. Buyout report ──────────────────────────────────────────────────────

BUYOUT_COLUMNS = [
    {"key": "package_name", "label": "Package", "kind": "text"},
    {"key": "bid_cost", "label": "Bid", "kind": "money"},
    {"key": "changes", "label": "Changes", "kind": "money"},
    {"key": "bought", "label": "Bought", "kind": "money"},
    {"key": "variance", "label": "Variance", "kind": "money"},
    {"key": "spent_no_commitment", "label": "Spent, no commitment", "kind": "money"},
    {"key": "committed_remaining", "label": "Remaining", "kind": "money"},
    {"key": "billed_to_date", "label": "Billed", "kind": "money"},
]

_BUYOUT_SUMMED = (
    "bid_cost",
    "changes",
    "bought",
    "variance",
    "spent_no_commitment",
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


def _vendor_bill_figures(
    db: Session, job_id: int, as_of: Optional[date]
) -> dict[int, dict]:
    """Per vendor on one job: the bills carrying the job and the payments,
    retainage held-less-released and open A/P on them — each prorated by the
    job's share of the bill's line amounts (a header-only bill is a whole 1),
    so a bill split across two jobs lands half on each. A bill carries the job
    on its header or on any of its lines. Open A/P is less the vendor's
    credits on the job not yet applied to a bill (prorated the same way), as
    A/P Aging nets them, so cost = paid + retainage held + open A/P (M bug 7:
    a $500 credit read cost 37,500 against paid 38,000 and open A/P 0). Read
    as of `as_of` (bills dated by then, payments dated by then, open as A/P
    Aging would age it) and in home currency at each bill's booked rate, so
    the figures tie to A/P Aging and Commitment Status for the same job and
    date; with no date, everything."""
    from sqlalchemy import or_

    from app.models.bills import (
        Bill,
        BillLine,
        BillPayment,
        BillPaymentAllocation,
        BillStatus,
    )
    from app.services.contact_balances import home_amount
    from app.services.open_balances import (
        open_bills_as_of,
        unapplied_vendor_credits_as_of,
    )

    out: dict[int, dict] = {}

    def figures(vendor_id) -> dict:
        return out.setdefault(
            vendor_id,
            {
                "bills_count": _ZERO,
                "paid_to_date": _ZERO,
                "retainage_outstanding": _ZERO,
                "open_ap": _ZERO,
            },
        )

    for vc, left in unapplied_vendor_credits_as_of(db, as_of or date.max):
        share = _bill_job_share(vc, job_id)
        if share != _ZERO:
            figures(vc.vendor_id)["open_ap"] -= share * left

    on_line = db.query(BillLine.bill_id).filter(BillLine.job_id == job_id)
    q = (
        db.query(Bill)
        .filter(or_(Bill.job_id == job_id, Bill.id.in_(on_line)))
        .filter(Bill.status.notin_([BillStatus.VOID, BillStatus.DRAFT]))
    )
    if as_of is not None:
        q = q.filter(Bill.date <= as_of)
    bills = q.all()
    bill_ids = {b.id for b in bills}
    open_by_bill = {
        b.id: amt for b, amt in open_bills_as_of(db, as_of or date.max, bill_ids)
    }
    # payments allocated to each bill (non-void, dated by as_of), by bill id
    paid_q = (
        db.query(
            BillPaymentAllocation.bill_id,
            sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0),
        )
        .select_from(BillPaymentAllocation)
        .join(BillPayment, BillPaymentAllocation.bill_payment_id == BillPayment.id)
        .filter(
            BillPaymentAllocation.bill_id.in_(bill_ids),
            BillPayment.is_voided.is_(False),
        )
    )
    if as_of is not None:
        paid_q = paid_q.filter(BillPayment.date <= as_of)
    paid_by_bill = {
        bid: Decimal(str(amt))
        for bid, amt in paid_q.group_by(BillPaymentAllocation.bill_id)
    }

    for b in bills:
        share = _bill_job_share(b, job_id)
        if share == _ZERO:
            continue
        f = figures(b.vendor_id)
        rate = b.exchange_rate

        def home(amount):
            return share * home_amount(amount, rate)

        f["bills_count"] += share
        f["retainage_outstanding"] += home(
            Decimal(str(b.retainage_held or 0))
            - Decimal(str(b.retainage_released or 0))
        )
        f["open_ap"] += home(open_by_bill.get(b.id, _ZERO))
        f["paid_to_date"] += home(paid_by_bill.get(b.id, _ZERO))
    return {
        vid: {
            "bills_count": float(_q(f["bills_count"])),
            "paid_to_date": _q(f["paid_to_date"]),
            "retainage_outstanding": _q(f["retainage_outstanding"]),
            "open_ap": _q(f["open_ap"]),
        }
        for vid, f in out.items()
    }


# The documents whose postings name their vendor: a bill (and its edit and
# void), a bill payment, a vendor credit.
_BILL_SOURCES = ("bill", "bill_edit", "bill_void")
_PAYMENT_SOURCES = ("bill_payment", "bill_payment_void")
_CREDIT_SOURCES = ("vendor_credit", "vendor_credit_void")


def _transaction_vendors(db: Session, txns: list) -> dict[int, Optional[int]]:
    """The vendor of each posted transaction, read from the document behind
    it, so books posted before any vendor was stamped read right. `txns` are
    (id, vendor_id, source_type, source_id) rows. The header's own vendor wins
    (bank matching sets it); else the bill's, the bill payment's or the vendor
    credit's; an expense keeps its payee vendor as its source id. A journal or
    a job cost entry has none."""
    from app.models.bills import Bill, BillPayment
    from app.models.vendor_credits import VendorCredit

    def ids_of(kinds):
        return {sid for _t, _v, st, sid in txns if st in kinds and sid is not None}

    bill_vendor = {}
    if ids_of(_BILL_SOURCES):
        bill_vendor = dict(
            db.query(Bill.id, Bill.vendor_id).filter(Bill.id.in_(ids_of(_BILL_SOURCES)))
        )
    payment_vendor = {}
    if ids_of(_PAYMENT_SOURCES):
        payment_vendor = dict(
            db.query(BillPayment.id, BillPayment.vendor_id).filter(
                BillPayment.id.in_(ids_of(_PAYMENT_SOURCES))
            )
        )
    credit_vendor = {}
    if ids_of(_CREDIT_SOURCES):
        credit_vendor = dict(
            db.query(VendorCredit.id, VendorCredit.vendor_id).filter(
                VendorCredit.id.in_(ids_of(_CREDIT_SOURCES))
            )
        )
    # a voided expense names the expense it reverses, which names the payee
    expense_ids = ids_of(("expense",))
    voided_expense = ids_of(("expense_void",))
    original_payee = {}
    if voided_expense:
        original_payee = dict(
            db.query(Transaction.id, Transaction.source_id).filter(
                Transaction.id.in_(voided_expense)
            )
        )
    payees = expense_ids | {v for v in original_payee.values() if v is not None}
    from app.models.contacts import Vendor

    real_payees = (
        {r[0] for r in db.query(Vendor.id).filter(Vendor.id.in_(payees))}
        if payees
        else set()
    )

    out: dict[int, Optional[int]] = {}
    for tid, vendor_id, st, sid in txns:
        if vendor_id is None:
            if st in _BILL_SOURCES:
                vendor_id = bill_vendor.get(sid)
            elif st in _PAYMENT_SOURCES:
                vendor_id = payment_vendor.get(sid)
            elif st in _CREDIT_SOURCES:
                vendor_id = credit_vendor.get(sid)
            elif st == "expense" and sid in real_payees:
                vendor_id = sid
            elif st == "expense_void" and original_payee.get(sid) in real_payees:
                vendor_id = original_payee[sid]
        out[tid] = vendor_id
    return out


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
            Transaction.id,
            Transaction.vendor_id,
            Transaction.source_type,
            Transaction.source_id,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            job_attribution() == job.id,
            Account.account_type.in_(cost_types),
            not_wip_entry(),
        )
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    posted = q.group_by(
        Transaction.id,
        Transaction.vendor_id,
        Transaction.source_type,
        Transaction.source_id,
    ).all()

    # the vendor of each entry comes from its document, then totals per vendor
    vendor_of = _transaction_vendors(db, [p[:4] for p in posted])
    cost_by_vendor: dict[Optional[int], Decimal] = {}
    for tid, _v, _st, _sid, dr, cr in posted:
        vid = vendor_of[tid]
        cost_by_vendor[vid] = cost_by_vendor.get(vid, _ZERO) + (
            Decimal(str(dr)) - Decimal(str(cr))
        )

    bill_figures = _vendor_bill_figures(db, job.id, end_date)
    vendor_ids = {v for v in cost_by_vendor if v is not None} | set(bill_figures)
    names = {
        v.id: v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()
    }

    rows = []
    if None in cost_by_vendor:
        rows.append(
            {
                "vendor_id": None,
                "vendor_name": _NO_VENDOR,
                "cost": float(_q(cost_by_vendor[None])),
                "bills_count": 0,
                "paid_to_date": 0.0,
                "retainage_outstanding": 0.0,
                "open_ap": 0.0,
            }
        )
    for vendor_id in vendor_ids:
        bf = bill_figures.get(vendor_id) or {
            "bills_count": 0,
            "paid_to_date": _ZERO,
            "retainage_outstanding": _ZERO,
            "open_ap": _ZERO,
        }
        rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": names.get(vendor_id, ""),
                "cost": float(_q(cost_by_vendor.get(vendor_id, _ZERO))),
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
    """Posted job cost grouped job → vendor (each entry's vendor read from the
    bill, bill payment, vendor credit or expense behind it; a No vendor bucket
    holds journals and job cost entries), with the vendor's bills, paid to
    date, retainage held outstanding and open A/P on the job. Cost is
    period-filtered by transaction date; the bill figures are job-to-date as of
    `end_date` and **prorated by the job's share of each bill's line amounts**
    (a header-only bill counts whole, a bill split across jobs splits), so
    bills can read fractional."""
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
    # What has arrived on the field's delivery tickets, at the PO line rates
    {"key": "received", "label": "Received", "kind": "money"},
    {"key": "billed", "label": "Billed to date", "kind": "money"},
    {"key": "retainage_held", "label": "Retainage held", "kind": "money"},
    {"key": "remaining", "label": "Remaining", "kind": "money"},
]

_COMMITMENT_SUMMED = ("committed", "received", "billed", "retainage_held", "remaining")


def _commitment_row(
    po,
    bills: list,
    as_of: date,
    co_by_id: dict,
    billed_later: bool = False,
    voided_lines: list = (),
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
    # ...and lines a sub change order voided since were part of it then.
    restored = sum((Decimal(str(ln.amount or 0)) for ln in voided_lines), _ZERO)
    committed = _q(Decimal(str(po.subtotal or 0)) - later + restored)
    # A closed commitment counts closed on every date, unless a bill against it
    # is dated after `as_of`, or a voided sub change order's unbilled lines
    # were still on it: then it was still open.
    billed_later = billed_later or bool(voided_lines)
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
    `as_of`: committed, received (delivery tickets dated by then, at the line
    rates), billed to date, retainage held and remaining, with totals per job
    and grand. Only bills dated by `as_of` count."""
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
        voided = sub_lines_voided_since(db, {po.id for po in pos}, as_of)
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
            row = _commitment_row(
                po, bills, as_of, co_by_id, billed_later, voided.get(po.id, [])
            )
            from app.services.field_tickets import received_by

            row["received"] = float(received_by(db, po.id, as_of))
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
