# ============================================================================
# Jobs — CRUD for the job-costing dimension ("Customer:Job" / Projects).
#
# A job referenced by any posted line can be archived (hidden from pickers)
# but never deleted, so job reports stay stable. Names are unique per
# customer, case-insensitively. GET /{id} folds in the profitability summary
# the Jobs page shows; /{id}/transactions is the job cost detail.
# ============================================================================

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.contacts import Customer
from app.models.cost_codes import CostCode
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine
from app.schemas.job_costing import JobBudgetResponse, JobBudgetSave
from app.schemas.jobs import (
    JobCreate,
    JobDetailResponse,
    JobResponse,
    JobSummary,
    JobUpdate,
)
from app.models.estimates import Estimate
from app.models.job_costing import JobBudget
from app.services.job_costing import (
    budget_vs_actual_all_jobs,
    budgets_from_estimate,
    cost_type_map,
    job_cost_tree,
)
from app.services.jobs_service import (
    committed_cost,
    find_job,
    job_profitability,
    job_transactions,
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
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Job).options(joinedload(Job.customer)).join(Customer)
    if customer_id is not None:
        q = q.filter(Job.customer_id == customer_id)
    if not include_inactive:
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


@router.get("/budget-vs-actual")
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
    base = _to_response(job)
    return JobDetailResponse(**base.model_dump(), summary=JobSummary(**summary))


@router.get("/{job_id}/transactions")
def get_job_transactions(
    job_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
):
    _get(db, job_id)
    return job_transactions(db, job_id, start_date, end_date)


@router.get("/{job_id}/cost-tree")
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


def _budget_response(row: JobBudget) -> JobBudgetResponse:
    data = JobBudgetResponse.model_validate(row)
    data.cost_code_label = row.cost_code.label if row.cost_code else None
    return data


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
    over as manual). Zero-amount rows are dropped."""
    _get(db, job_id)
    types = cost_type_map(db)
    existing = {
        (r.cost_code_id, r.cost_type): r
        for r in db.query(JobBudget).filter(JobBudget.job_id == job_id).all()
    }
    keep: set = set()
    for row in data.rows:
        if row.cost_type and row.cost_type not in types:
            raise HTTPException(
                status_code=422, detail=f"Unknown cost type '{row.cost_type}'"
            )
        if row.cost_code_id and not db.get(CostCode, row.cost_code_id):
            raise HTTPException(status_code=404, detail="Cost code not found")
        key = (row.cost_code_id, row.cost_type)
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


@router.post("", response_model=JobResponse, status_code=201)
def create_job(data: JobCreate, db: Session = Depends(get_db)):
    if not db.get(Customer, data.customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    _check_name_clash(db, data.customer_id, data.name)
    job = Job(**data.model_dump())
    db.add(job)
    db.commit()
    db.refresh(job)
    return _to_response(_get(db, job.id))


@router.put("/{job_id}", response_model=JobResponse)
def update_job(job_id: int, data: JobUpdate, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    changes = data.model_dump(exclude_unset=True)
    customer_id = changes.get("customer_id", job.customer_id)
    if "customer_id" in changes and not db.get(Customer, customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    if "name" in changes or "customer_id" in changes:
        _check_name_clash(db, customer_id, changes.get("name", job.name), job.id)
    for key, value in changes.items():
        setattr(job, key, value)
    db.commit()
    db.refresh(job)
    return _to_response(_get(db, job.id))


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = _get(db, job_id)
    in_use = (
        db.query(TransactionLine.id).filter(TransactionLine.job_id == job_id).first()
        or db.query(Transaction.id).filter(Transaction.job_id == job_id).first()
    )
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Job has posted activity — mark it inactive instead",
        )
    db.delete(job)
    db.commit()
    return {"message": "Job deleted"}
