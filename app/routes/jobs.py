# ============================================================================
# Jobs — CRUD for the job-costing dimension ("Customer:Job" / Projects).
#
# A job referenced by any posted line can be archived (hidden from pickers)
# but never deleted, so job reports stay stable. Names are unique per
# customer, case-insensitively. GET /{id} folds in the profitability summary
# the Jobs page shows; /{id}/transactions is the job cost detail.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from app.models.transactions import LEDGER_BASIS_OPTION, READ_ALL
from app.database import get_db
from app.routes._helpers import refuse_unknown_filters, reject_null_updates
from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.cost_codes import CostCode
from app.models.trade_packages import TradePackage
from app.models.jobs import Job, SovLine
from app.models.transactions import Transaction, TransactionLine
from app.schemas.job_costing import (
    CostToCompleteSave,
    JobBudgetResponse,
    JobBudgetSave,
)
from app.schemas.jobs import (
    ContractResponse,
    ContractSummary,
    ContractTerms,
    JobCreate,
    JobDetailResponse,
    JobResponse,
    JobSummary,
    JobUpdate,
    SovLineOut,
    SovSave,
)
from app.schemas.pay_applications import JobDepositCreate
from app.models.estimates import Estimate
from app.services.contracts import (
    build_sov,
    contract_is_locked,
    contract_summary,
    settle_contract_type,
    sync_contingency_line,
)
from app.services.estimating import estimate_totals
from app.services.accounting import _q
from app.services.closing_date import check_closing_date
from app.models.job_costing import JobBudget
from app.services.job_costing import (
    budget_vs_actual_all_jobs,
    budgets_from_estimate,
    cost_type_map,
    job_cost_tree,
)
from app.services.jobs_service import (
    commitment_figures,
    committed_cost,
    find_job,
    job_profitability,
    job_transactions,
    refuse_moving_a_carried_job,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _to_response(job: Job) -> JobResponse:
    data = JobResponse.model_validate(job)
    data.customer_name = job.customer.name if job.customer else ""
    data.full_name = job.full_name
    return data


def _get(db: Session, job_id: int) -> Job:
    job = (
        db.query(Job).options(joinedload(Job.customer)).filter(Job.id == job_id).first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _check_name_clash(db: Session, customer_id: int, name: str, exclude_id=None):
    clash = find_job(db, customer_id, name)
    if clash and clash.id != exclude_id:
        raise HTTPException(
            status_code=409,
            detail=f"This customer already has a job named '{clash.name}'",
        )


@router.get("", response_model=list[JobResponse])
def list_jobs(
    customer_id: Optional[int] = None,
    include_inactive: bool = False,
    inactive_only: bool = False,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Job).options(joinedload(Job.customer)).join(Customer)
    if customer_id is not None:
        q = q.filter(Job.customer_id == customer_id)
    if inactive_only:  # the list page's Inactive view (#210)
        q = q.filter(Job.is_active.is_(False))
    elif not include_inactive:
        q = q.filter(Job.is_active.is_(True))
    if status:
        q = q.filter(Job.status == status)
    rows = q.order_by(Customer.name, Job.name).all()
    return [_to_response(j) for j in rows]


@router.get("/profitability", response_model=list[JobSummary])
def jobs_profitability(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    customer_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """One row per job with activity in the period (plus the "No job"
    bucket) — the Jobs page's figures. The report route under /reports
    wraps the same data with totals."""
    rows = job_profitability(
        db, start_date, end_date, customer_id=customer_id, include_no_job=True
    )
    committed = committed_cost(db)
    for r in rows:
        r["committed_cost"] = committed.get(r["job_id"], 0.0) if r["job_id"] else 0.0
    return rows


@router.get("/budget-vs-actual", dependencies=[Depends(refuse_unknown_filters)])
def jobs_budget_vs_actual(
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    customer_id: Optional[int] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    """Headline budget / committed / actual / projected / variance per job."""
    return budget_vs_actual_all_jobs(
        db, start_date, end_date, customer_id, include_inactive
    )


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    rows = job_profitability(db, job_ids=[job_id], include_no_job=False)
    summary = (
        rows[0]
        if rows
        else {
            "job_id": job.id,
            "job_name": job.name,
            "customer_id": job.customer_id,
            "customer_name": job.customer.name if job.customer else "",
            "status": job.status,
            "contract_amount": (
                float(job.contract_amount) if job.contract_amount else None
            ),
        }
    )
    summary["committed_cost"] = committed_cost(db, [job_id]).get(job_id, 0.0)
    # The commitment roll-up rides alongside (#553): the whole commitment and
    # what has been billed, additive to the remaining figure above.
    cf = commitment_figures(db, [job_id]).get(job_id, {})
    summary["committed_total"] = cf.get("committed_total", 0.0)
    summary["billed_to_date"] = cf.get("billed_to_date", 0.0)
    base = _to_response(job)
    # The contract roll-up rides along, additive (#552); the SPA shows it in
    # construction mode only, but the summary is harmless for anyone. The cost
    # and billed figures are the ones profitability already computed, so a
    # plain job fetch runs no extra cost-tree query.
    contract = ContractSummary(
        **contract_summary(
            db,
            job,
            cost=Decimal(str(summary.get("total_costs", 0) or 0)),
            billed=Decimal(str(summary.get("income", 0) or 0)),
        )
    )
    return JobDetailResponse(
        **base.model_dump(), summary=JobSummary(**summary), contract=contract
    )


@router.get("/{job_id}/transactions")
def get_job_transactions(
    job_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    _get(db, job_id)
    return job_transactions(db, job_id, start_date, end_date)


@router.get("/{job_id}/cost-tree", dependencies=[Depends(refuse_unknown_filters)])
def get_job_cost_tree(
    job_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The drill-down: cost types → cost-code tree → posted lines, every
    level with budget / committed / actual / projected / variance."""
    _get(db, job_id)
    return job_cost_tree(db, job_id, start_date, end_date)


def _cost_tree_filename(job_id: int, start_date, end_date, ext: str) -> str:
    dates = "".join(f"_{d}" for d in (start_date, end_date) if d)
    return f"job-cost-detail_{job_id}{dates or '_job-to-date'}.{ext}"


@router.get("/{job_id}/cost-tree/pdf", dependencies=[Depends(refuse_unknown_filters)])
def get_job_cost_tree_pdf(
    job_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    expanded: Optional[str] = Query(
        default=None,
        description="The tab's expanded rows, comma-separated (type:<code>, "
        "code:<id>); left out, every row prints",
    ),
    db: Session = Depends(get_db),
):
    """The Cost Detail tab as a landscape PDF (#691): the same figures as
    /cost-tree for the same dates, laid out as the tab shows them."""
    from app.routes.reports.construction import cost_tree_sections
    from app.routes.reports.financial import _pdf_response

    job = _get(db, job_id)
    tree = job_cost_tree(db, job_id, start_date, end_date)
    opened = None if expanded is None else {k for k in expanded.split(",") if k}
    return _pdf_response(
        cost_tree_sections(tree, job.full_name, opened),
        db,
        _cost_tree_filename(job_id, start_date, end_date, "pdf"),
        landscape=True,
    )


@router.get("/{job_id}/cost-tree/csv", dependencies=[Depends(refuse_unknown_filters)])
def get_job_cost_tree_csv(
    job_id: int,
    request: Request,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The Cost Detail tab as a CSV (#691): a row per type and per cost code,
    fully expanded, then the Total row."""
    from app.routes.reports.construction import cost_tree_csv
    from app.routes.reports.financial import _company_name, _csv_download

    job = _get(db, job_id)
    tree = job_cost_tree(db, job_id, start_date, end_date)
    return _csv_download(
        cost_tree_csv(tree, job.full_name, _company_name(db)),
        _cost_tree_filename(job_id, start_date, end_date, "csv"),
        request,
    )


def _budget_response(row: JobBudget) -> JobBudgetResponse:
    data = JobBudgetResponse.model_validate(row)
    data.cost_code_label = row.cost_code.label if row.cost_code else None
    return data


def _locked_budget_409(db: Session, row: JobBudget):
    """The 409 a locked budget row gives. There is no unlock: a row an
    accepted estimate owns is the contract budget, moved only by a new
    estimate or a change order (#551, #554)."""
    est = db.get(Estimate, row.estimate_id) if row.estimate_id else None
    where = f"estimate {est.estimate_number}" if est else "an accepted estimate"
    raise HTTPException(
        status_code=409,
        detail={
            "code": "budget_locked",
            "message": f"Locked by {where}; change it with a change order",
        },
    )


def _refuse_locked_budget(db: Session, job_id: int) -> None:
    """Job-wide refusal: re-seeding from an estimate replaces the whole
    estimate budget, so any locked row blocks it (#551)."""
    locked = (
        db.query(JobBudget)
        .filter(
            JobBudget.job_id == job_id,
            JobBudget.locked.is_(True),
            JobBudget.source != "change",  # a change order's rows are its own (#554)
        )
        .first()
    )
    if locked is not None:
        _locked_budget_409(db, locked)


@router.get("/{job_id}/budgets", response_model=list[JobBudgetResponse])
def get_job_budgets(job_id: int, db: Session = Depends(get_db)):
    _get(db, job_id)
    rows = (
        db.query(JobBudget)
        .options(joinedload(JobBudget.cost_code))
        .filter(JobBudget.job_id == job_id)
        .all()
    )
    return [_budget_response(r) for r in rows]


@router.put("/{job_id}/budgets", response_model=list[JobBudgetResponse])
def save_job_budgets(job_id: int, data: JobBudgetSave, db: Session = Depends(get_db)):
    """Replace the job's MANUAL budget rows with the given set (estimate-
    seeded rows are kept unless the same key is supplied, which then takes
    over as manual). Zero-amount rows are dropped. A row an accepted estimate
    locked (#551) can't be overwritten — only that row's key is refused, so
    manual rows on other codes still save after acceptance."""
    _get(db, job_id)
    types = cost_type_map(db)
    all_rows = db.query(JobBudget).filter(JobBudget.job_id == job_id).all()
    # Change-order rows (source="change", #554) are owned by their change order
    # and keep their own slot under the widened unique key — the manual save
    # neither maps them nor overwrites them.
    existing = {
        (r.cost_code_id, r.cost_type): r for r in all_rows if r.source != "change"
    }
    change_keys = {
        (r.cost_code_id, r.cost_type) for r in all_rows if r.source == "change"
    }
    locked_keys = {k: r for k, r in existing.items() if r.locked}
    keep: set = set()
    for row in data.rows:
        key = (row.cost_code_id, row.cost_type)
        # a locked row's key is refused before anything else — it is the
        # contract budget, moved only by a change order (#551)
        if key in locked_keys:
            _locked_budget_409(db, locked_keys[key])
        if row.cost_type and row.cost_type not in types:
            raise HTTPException(
                status_code=422, detail=f"Unknown cost type '{row.cost_type}'"
            )
        if row.cost_code_id and not db.get(CostCode, row.cost_code_id):
            raise HTTPException(status_code=404, detail="Cost code not found")
        if key in change_keys:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "co_row",
                    "message": "That budget line is owned by a change order; edit it there.",
                },
            )
        if row.amount == 0 and row.revenue_amount == 0:
            continue
        keep.add(key)
        cur = existing.get(key)
        if cur:
            cur.amount, cur.revenue_amount, cur.notes = (
                row.amount,
                row.revenue_amount,
                row.notes,
            )
            cur.source = "manual"
        else:
            db.add(
                JobBudget(
                    job_id=job_id,
                    cost_code_id=row.cost_code_id,
                    cost_type=row.cost_type,
                    amount=row.amount,
                    revenue_amount=row.revenue_amount,
                    notes=row.notes,
                    source="manual",
                )
            )
    for key, cur in existing.items():
        if key not in keep and cur.source == "manual":
            db.delete(cur)
    db.commit()
    return get_job_budgets(job_id, db)


@router.post(
    "/{job_id}/budgets/from-estimate/{estimate_id}",
    response_model=list[JobBudgetResponse],
)
def seed_budget_from_estimate(
    job_id: int, estimate_id: int, db: Session = Depends(get_db)
):
    job = _get(db, job_id)
    _refuse_locked_budget(db, job_id)
    est = db.get(Estimate, estimate_id)
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if est.customer_id != job.customer_id:
        raise HTTPException(
            status_code=422, detail="Estimate belongs to a different customer"
        )
    budgets_from_estimate(db, job, est)
    db.commit()
    return get_job_budgets(job_id, db)


# ---------------------------------------------------------------------------
# The contract on the job (#552) — terms, the summary and the schedule of
# values. Bookkeeper-writable, like the rest of jobs; nothing here is locked
# (change-order revisions are #554).
# ---------------------------------------------------------------------------


def _sov_responses(job: Job) -> list[SovLineOut]:
    out = []
    for r in job.sov_lines:
        data = SovLineOut.model_validate(r)
        data.trade_package_name = r.trade_package.name if r.trade_package else None
        data.cost_code_label = r.cost_code.label if r.cost_code else None
        data.revenue_account_name = (
            r.revenue_account.name if r.revenue_account else None
        )
        out.append(data)
    return out


def _contract_response(db: Session, job: Job) -> ContractResponse:
    return ContractResponse(
        contract=ContractSummary(**contract_summary(db, job)),
        sov=_sov_responses(job),
    )


@router.get("/{job_id}/contract", response_model=ContractResponse)
def get_contract(job_id: int, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    return _contract_response(db, job)


def _stamp_contract_defaults(job: Job) -> None:
    """The one place the contract sum's derived fields are set, shared by the
    contract PUT and the plain job form: a GMP's cap defaults to the contract
    sum, and original_contract_amount is stamped the first time a sum is set
    with a type and never overwritten after. #554's revisions move
    contract_amount, not the original."""
    if (
        job.contract_type == "gmp"
        and job.cap_amount is None
        and job.contract_amount is not None
    ):
        job.cap_amount = job.contract_amount
    if (
        job.original_contract_amount is None
        and job.contract_amount is not None
        and job.contract_type is not None
    ):
        job.original_contract_amount = job.contract_amount


_AMOUNT_FIELDS = ("contract_amount", "fee_amount", "cap_amount", "contingency_amount")


def _refuse_sum_change(db: Session, job: Job, new_amount) -> None:
    """The contract sum edits freely until the job has billing on the schedule
    of values or a change order; from then on only a change order (#554)
    moves it. Re-sending the same sum is fine."""
    if not contract_is_locked(db, job):
        return
    if new_amount is not None and _q(new_amount) == _q(job.contract_amount):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "contract_sum_locked",
            "message": "The contract sum is locked once billing starts or a change order is approved; change it with a change order",
        },
    )


def _refuse_contingency_change(db: Session, job: Job, new_amount) -> None:
    """Same lock as the sum: the contingency is a line of the schedule that
    foots to the sum, so past the lock point it moves by change order."""
    if not contract_is_locked(db, job):
        return
    old = job.contingency_amount
    if (new_amount is None and old is None) or (
        new_amount is not None and old is not None and _q(new_amount) == _q(old)
    ):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "contingency_locked",
            "message": "The contingency is locked once billing starts or a change order is approved; change it with a change order",
        },
    )


def _follow_original(job: Job, changes: dict) -> None:
    """While the sum is still editable, the original contract amount is
    whatever the sum is — a correction before billing is not a change."""
    if changes.get("contract_amount") is not None and (
        job.original_contract_amount is not None
    ):
        job.original_contract_amount = changes["contract_amount"]


@router.put("/{job_id}/contract", response_model=ContractResponse)
def set_contract(job_id: int, data: ContractTerms, db: Session = Depends(get_db)):
    """Set the job's contract terms. A field left out is unchanged; a field
    sent as null clears it. A GMP needs a contract amount (the guaranteed
    maximum the cap defaults to). The SOV's contingency line is kept in step
    with the contingency term."""
    job = _get(db, job_id)
    changes = data.model_dump(exclude_unset=True)
    for key in _AMOUNT_FIELDS:
        if changes.get(key) is not None:
            changes[key] = _q(changes[key])
    if "contract_amount" in changes:
        _refuse_sum_change(db, job, changes["contract_amount"])
    if "contingency_amount" in changes:
        _refuse_contingency_change(db, job, changes["contingency_amount"])
    _follow_original(job, changes)
    for key, value in changes.items():
        setattr(job, key, value)
    if (
        job.contract_type == "gmp"
        and job.cap_amount is None
        and job.contract_amount is None
    ):
        raise HTTPException(
            status_code=422,
            detail="A GMP needs a contract amount — its guaranteed maximum.",
        )
    _stamp_contract_defaults(job)
    if "contingency_amount" in changes:
        sync_contingency_line(db, job)
    db.commit()
    db.refresh(job)
    resp = _contract_response(db, job)
    resp.warnings = _contract_warnings(db, job)
    return resp


def _contract_warnings(db: Session, job: Job) -> list[str]:
    """A sentence for a saved term that does nothing yet: a job whose
    contractor pays the tax on its materials, with no use tax rate (or
    default tax rate) set, accrues no use tax on those bills."""
    from app.services.contracts import contractor_pays_tax
    from app.services.purchase_posting import use_tax_rate

    if contractor_pays_tax(job) and use_tax_rate(db) <= 0:
        return [
            "The contractor pays the tax on this job's materials, but no Use tax "
            "rate or Default tax rate is set, so its material bills will accrue "
            "no use tax. Set the Use tax rate in Settings."
        ]
    return []


@router.post(
    "/{job_id}/contract/from-estimate/{estimate_id}",
    response_model=ContractResponse,
)
def contract_from_estimate(
    job_id: int, estimate_id: int, db: Session = Depends(get_db)
):
    """Set the contract from an accepted estimate and build its schedule of
    values. Defaults the type to lump sum when unset, and the contract sum
    (and the original) to the estimate's bid total when the job has none
    yet. Replaces any existing SOV lines."""
    job = _get(db, job_id)
    est = db.get(Estimate, estimate_id)
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    # build_sov re-checks these, but check first so a bad request mutates
    # nothing on the job. Accepted is checked before the job match, so an
    # estimate not yet on any job reads as "accept it first".
    from app.models.estimates import EstimateStatus

    if est.status != EstimateStatus.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_not_accepted",
                "message": "Accept the estimate before building the schedule of values.",
            },
        )
    if est.job_id != job.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "estimate_job_mismatch",
                "message": "That estimate is not on this job.",
            },
        )
    job.contract_estimate_id = est.id
    if job.contract_type is None:
        job.contract_type = "lump_sum"
    # The contract sum is the estimate's pre-tax bid plus the owner
    # contingency carried inside it (the SOV adds a contingency line), so a
    # freshly built schedule balances. An existing sum is left alone; a sum
    # cleared after the original was stamped goes back to that original
    # rather than earning a new one.
    if job.contract_amount is None:
        if job.original_contract_amount is not None:
            job.contract_amount = job.original_contract_amount
        else:
            bid = _q(estimate_totals(est)["bid_total"])
            contingency = (
                _q(job.contingency_amount) if job.contingency_amount else Decimal("0")
            )
            job.contract_amount = bid + contingency
    # A sum already on the job (the job form) still gets its original stamped.
    _stamp_contract_defaults(job)
    # A GMP/cost-plus fee % defaults from the estimate's fee markup, so the
    # fee tile and the SOV fee line agree. Only when the job has none.
    if not job.fee_pct and est.fee_pct:
        job.fee_pct = est.fee_pct
    build_sov(db, job, est, replace=True)
    db.commit()
    db.refresh(job)
    return _contract_response(db, job)


@router.put("/{job_id}/sov", response_model=ContractSummary)
def save_sov(job_id: int, data: SovSave, db: Session = Depends(get_db)):
    """Replace the schedule of values with the given lines, renumbered 1..n
    in the order sent. Returns the contract summary (whose sov_balance
    shows whether the SOV balances to the contract sum)."""
    job = _get(db, job_id)
    for line in data.lines:
        if line.trade_package_id and not db.get(TradePackage, line.trade_package_id):
            raise HTTPException(status_code=404, detail="Trade package not found")
        if line.cost_code_id and not db.get(CostCode, line.cost_code_id):
            raise HTTPException(status_code=404, detail="Cost code not found")
        if line.revenue_account_id:
            acct = db.get(Account, line.revenue_account_id)
            if acct is None:
                raise HTTPException(status_code=404, detail="Account not found")
            if acct.account_type != AccountType.INCOME:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{acct.name} is not an income account. Choose an "
                        "income account for the line's revenue."
                    ),
                )
    # A change_order-kind line is owned by its change order (#554), not the
    # grid: it is never sent by the client and is preserved across a save.
    if any(line.kind == "change_order" for line in data.lines):
        raise HTTPException(
            status_code=422,
            detail="A change-order line's value is owned by its change order.",
        )
    kept = (
        db.query(SovLine)
        .filter(SovLine.job_id == job.id, SovLine.kind == "change_order")
        .all()
    )

    # A pay application that has billed the schedule pins its lines: a billed
    # line must round-trip by id and may not shrink below what it has billed,
    # and it may not be dropped (#555). With no approved application the save
    # is an ordinary full replace — existing lines are rebuilt from scratch.
    from app.services.pay_applications import billed_completed_by_sov_line

    billed = billed_completed_by_sov_line(db, job.id)
    if billed:
        _save_sov_with_billings(db, job, data.lines, kept, billed)
    else:
        _save_sov_replace(db, job, data.lines, kept)
    # A schedule of values makes the sum billable: with no contract type saved
    # it is taken as a lump sum and its original stamped, as building from an
    # estimate does (#723).
    if data.lines:
        settle_contract_type(db, job)
    db.commit()
    db.refresh(job)
    return ContractSummary(**contract_summary(db, job))


def _sov_mapping(line, row) -> dict:
    """A saved line's revenue account and taxable flag: what the request
    sent, else what the existing line `row` had, else no account (4050 at
    posting) and taxed."""
    sent = line.model_fields_set
    if "revenue_account_id" in sent:
        revenue = line.revenue_account_id
    else:
        revenue = row.revenue_account_id if row is not None else None
    if "is_taxable" in sent and line.is_taxable is not None:
        taxable = line.is_taxable
    else:
        taxable = bool(row.is_taxable) if row is not None else True
    return {"revenue_account_id": revenue, "is_taxable": taxable}


def _save_sov_replace(db: Session, job: Job, lines, kept) -> None:
    """Full replace of the non change-order lines (no billings to protect)."""
    old = {
        r.id: r
        for r in db.query(SovLine).filter(
            SovLine.job_id == job.id, SovLine.kind != "change_order"
        )
    }
    mapping = [_sov_mapping(line, old.get(line.id)) for line in lines]
    db.query(SovLine).filter(
        SovLine.job_id == job.id, SovLine.kind != "change_order"
    ).delete()
    db.flush()
    n = 0
    for line, mapped in zip(lines, mapping):
        n += 1
        item_no = (line.item_no or "").strip() or f"{n:02d}"
        db.add(
            SovLine(
                job_id=job.id,
                line_no=n,
                item_no=item_no,
                description=line.description,
                trade_package_id=line.trade_package_id,
                cost_code_id=line.cost_code_id,
                kind=line.kind,
                scheduled_value=_q(line.scheduled_value),
                **mapped,
            )
        )
    for co_line in kept:  # keep their value and FK, renumbered after the rest
        n += 1
        co_line.line_no = n


def _save_sov_with_billings(db: Session, job: Job, lines, kept, billed) -> None:
    """Save the schedule while a pay application has billed it: update a
    billed line in place by id (never dropping it or shrinking it below its
    billed completed_to_date), add new lines, drop only unbilled ones (#555)."""
    existing = {
        r.id: r
        for r in db.query(SovLine).filter(
            SovLine.job_id == job.id, SovLine.kind != "change_order"
        )
    }
    sent_ids = {line.id for line in lines if line.id is not None}
    for line_id, row in existing.items():
        # A deduct line bills down: a negative billed amount pins it as well.
        if line_id not in sent_ids and _q(billed.get(line_id, 0)) != 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Line {row.item_no or row.line_no} has been billed and "
                    "cannot be dropped from the schedule."
                ),
            )
    n = 0
    seen = set()
    for line in lines:
        n += 1
        item_no = (line.item_no or "").strip() or f"{n:02d}"
        row = existing.get(line.id) if line.id is not None else None
        if row is not None:
            seen.add(row.id)
            floor = _q(billed.get(row.id, 0))
            # In the line's own direction: a deduct line billed -6,000 cannot
            # be cut back to -3,000.
            value = _q(line.scheduled_value)
            if (value < floor) if floor >= 0 else (value > floor):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Line {item_no} has billed {floor:.2f}; its scheduled "
                        f"value cannot {'drop below' if floor >= 0 else 'rise above'} that."
                    ),
                )
            row.line_no = n
            row.item_no = item_no
            row.description = line.description
            row.trade_package_id = line.trade_package_id
            row.cost_code_id = line.cost_code_id
            row.kind = line.kind
            row.scheduled_value = line.scheduled_value
            for field, value in _sov_mapping(line, row).items():
                setattr(row, field, value)
        else:
            db.add(
                SovLine(
                    job_id=job.id,
                    line_no=n,
                    item_no=item_no,
                    description=line.description,
                    trade_package_id=line.trade_package_id,
                    cost_code_id=line.cost_code_id,
                    kind=line.kind,
                    scheduled_value=line.scheduled_value,
                    **_sov_mapping(line, None),
                )
            )
    for line_id, row in existing.items():
        if line_id not in seen:  # unbilled and not resent — drop it
            db.delete(row)
    db.flush()
    for co_line in kept:
        n += 1
        co_line.line_no = n


@router.get("/{job_id}/cost-to-complete-forecast")
def get_cost_to_complete_forecast(
    job_id: int, as_of: date = Query(default=None), db: Session = Depends(get_db)
):
    """The job's cost-to-complete forecast (#556): per section and cost type,
    the budget, actual, default (budget − actual, floored at zero), entered
    amount and the cost to complete the WIP schedule uses."""
    from app.services.job_reports import ctc_forecast

    return ctc_forecast(db, _get(db, job_id), as_of or date.today())


@router.put("/{job_id}/cost-to-complete-forecast")
def save_cost_to_complete_forecast(
    job_id: int, data: CostToCompleteSave, db: Session = Depends(get_db)
):
    """Save entered cost-to-complete amounts; an amount of null puts that cell
    back to its default. Returns the refreshed forecast."""
    from app.services.job_reports import set_ctc_entries

    return set_ctc_entries(db, _get(db, job_id), [e.model_dump() for e in data.entries])


@router.get("/{job_id}/buyout")
def get_buyout(job_id: int, db: Session = Depends(get_db)):
    """Bid vs buyout per trade package (#553): the bid cost from the accepted
    estimate, what has been bought on the job, the variance, and the
    commitments behind each package with their compliance flags.
    Construction mode only, as the Report Center's Buyout Report is."""
    from app.routes.reports.construction import _construction_only
    from app.services.buyout import job_buyout

    _construction_only(db)
    job = _get(db, job_id)
    return job_buyout(db, job)


# ---------------------------------------------------------------------------
# Owner deposits (#555) — money taken before (or ahead of) the work, held on
# Customer Deposits (2210) until a pay application applies it.
# ---------------------------------------------------------------------------


@router.get("/{job_id}/deposits")
def list_deposits(job_id: int, db: Session = Depends(get_db)):
    from app.models.job_deposits import JobDeposit
    from app.schemas.pay_applications import JobDepositOut, JobDepositsResponse
    from app.services.pay_applications import deposits_figures

    _get(db, job_id)
    rows = (
        db.query(JobDeposit)
        .filter(JobDeposit.job_id == job_id)
        .order_by(JobDeposit.date, JobDeposit.id)
        .all()
    )
    figures = deposits_figures(db, job_id)
    return JobDepositsResponse(
        deposits=[JobDepositOut.model_validate(r) for r in rows],
        received=float(figures["received"]),
        applied=float(figures["applied"]),
        unapplied=float(figures["unapplied"]),
    )


@router.post("/{job_id}/deposits", status_code=201)
def create_deposit(job_id: int, data: JobDepositCreate, db: Session = Depends(get_db)):
    from app.models.accounts import Account
    from app.models.job_deposits import JobDeposit
    from app.schemas.pay_applications import JobDepositOut
    from app.services.accounting import (
        create_journal_entry,
        ensure_construction_accounts,
    )

    job = _get(db, job_id)
    check_closing_date(db, data.date)
    bank = db.get(Account, data.deposit_to_account_id)
    if bank is None:
        raise HTTPException(status_code=404, detail="Deposit-to account not found")
    deposits_acct = ensure_construction_accounts(db)["2210"]
    dep = JobDeposit(
        job_id=job.id,
        date=data.date,
        amount=_q(data.amount),
        deposit_to_account_id=bank.id,
        memo=data.memo,
    )
    db.add(dep)
    db.flush()
    # DR bank / CR Customer Deposits (2210).
    txn = create_journal_entry(
        db,
        data.date,
        f"Deposit — {job.full_name}",
        [
            {
                "account_id": bank.id,
                "debit": _q(data.amount),
                "credit": Decimal("0"),
                "description": f"Deposit — {job.full_name}",
                "job_id": job.id,
            },
            {
                "account_id": deposits_acct.id,
                "debit": Decimal("0"),
                "credit": _q(data.amount),
                "description": f"Customer deposit — {job.full_name}",
                "job_id": job.id,
            },
        ],
        source_type="job_deposit",
        source_id=dep.id,
        job_id=job.id,
    )
    dep.transaction_id = txn.id
    db.commit()
    db.refresh(dep)
    return JobDepositOut.model_validate(dep)


@router.post("/{job_id}/deposits/{deposit_id}/void")
def void_deposit(job_id: int, deposit_id: int, db: Session = Depends(get_db)):
    from app.models.job_deposits import JobDeposit
    from app.schemas.pay_applications import JobDepositOut
    from app.services.accounting import create_journal_entry, reversing_lines
    from app.services.pay_applications import deposits_figures

    _get(db, job_id)
    dep = db.get(JobDeposit, deposit_id)
    if dep is None or dep.job_id != job_id:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if dep.voided_at is not None:
        raise HTTPException(status_code=400, detail="Deposit already voided")
    # An applied deposit can't be voided — it is funding an approved billing.
    figures = deposits_figures(db, job_id)
    if figures["applied"] > 0 and figures["unapplied"] < Decimal(str(dep.amount)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "deposit_applied",
                "message": "Some of this deposit has been applied to a pay application.",
            },
        )
    check_closing_date(db, dep.date)
    if dep.transaction_id:
        from app.models.transactions import TransactionLine

        lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == dep.transaction_id)
            .all()
        )
        if lines:
            create_journal_entry(
                db,
                dep.date,
                f"VOID Deposit — {_get(db, job_id).full_name}",
                reversing_lines(lines),
                source_type="job_deposit_void",
                source_id=dep.id,
                job_id=job_id,
            )
    from datetime import datetime as _dt, timezone as _tz

    dep.voided_at = _dt.now(_tz.utc)
    db.commit()
    db.refresh(dep)
    return JobDepositOut.model_validate(dep)


def _stamp_closed_on(job: Job, was: Optional[str]) -> None:
    """The day the job went to Closed (today), so a back-dated WIP reads it
    open before then; reopening clears it."""
    if job.status == "closed" and was != "closed":
        job.closed_on = date.today()
    elif job.status != "closed":
        job.closed_on = None


@router.post("", response_model=JobResponse, status_code=201)
def create_job(data: JobCreate, db: Session = Depends(get_db)):
    if not db.get(Customer, data.customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    _check_name_clash(db, data.customer_id, data.name)
    job = Job(**data.model_dump())
    _stamp_closed_on(job, None)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _to_response(_get(db, job.id))


@router.put("/{job_id}", response_model=JobResponse)
def update_job(job_id: int, data: JobUpdate, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    changes = reject_null_updates(Job, data.model_dump(exclude_unset=True))
    customer_id = changes.get("customer_id", job.customer_id)
    if "customer_id" in changes and not db.get(Customer, customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    # A job moves to another customer only while no invoice, sales
    # receipt, credit memo, estimate, schedule or in-kind gift carries it
    # (NEW-36): moving it would put those on another customer's job.
    if customer_id != job.customer_id:
        refuse_moving_a_carried_job(db, job)
    if "name" in changes or "customer_id" in changes:
        _check_name_clash(db, customer_id, changes.get("name", job.name), job.id)
    if "contract_amount" in changes:
        if changes["contract_amount"] is not None:
            changes["contract_amount"] = _q(changes["contract_amount"])
        _refuse_sum_change(db, job, changes["contract_amount"])
    _follow_original(job, changes)
    was = job.status
    for key, value in changes.items():
        setattr(job, key, value)
    _stamp_closed_on(job, was)
    # The job form's plain contract amount runs through the same stamp as the
    # Contract tab (#552): set the amount with a type already on the job and
    # the original is stamped once here too.
    if "contract_amount" in changes:
        _stamp_contract_defaults(job)
    db.commit()
    db.refresh(job)
    return _to_response(_get(db, job.id))


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    in_use = (
        db.query(TransactionLine.id)
        .filter(TransactionLine.job_id == job_id)
        .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        .first()
        or db.query(Transaction.id).filter(Transaction.job_id == job_id).first()
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Job has posted activity — mark it inactive instead",
        )
    from app.services.memorized_service import refuse_if_memorized

    refuse_if_memorized(db, "Job", "job_id", job_id)
    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}
