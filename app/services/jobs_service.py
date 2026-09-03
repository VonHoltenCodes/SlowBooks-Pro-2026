# ============================================================================
# Job dimension helpers — Customer:Job name handling, strict lookup, and the
# profitability aggregation every job report and the job detail share.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine

NO_JOB_LABEL = "No job"


def split_customer_job(name: str) -> tuple[str, Optional[str]]:
    """QuickBooks' "Customer:Job" → ("Customer", "Job").

    Only the first colon splits; a Desktop sub-job "A:B:C" becomes job
    "B:C" under customer "A" — one level of flattening is honest, and
    sub-jobs are rare. No colon → (name, None).
    """
    raw = (name or "").strip()
    if ":" not in raw:
        return raw, None
    cust, job = raw.split(":", 1)
    cust, job = cust.strip(), job.strip()
    if not cust or not job:
        return raw, None
    return cust, job


def find_job(db: Session, customer_id: int, name: str) -> Optional[Job]:
    """Exact, then case-insensitive match within one customer."""
    if not name:
        return None
    row = db.query(Job).filter(Job.customer_id == customer_id, Job.name == name).first()
    if not row:
        row = (
            db.query(Job)
            .filter(Job.customer_id == customer_id, Job.name.ilike(name))
            .first()
        )
    return row


def get_or_create_job(db: Session, customer_id: int, name: str) -> Job:
    row = find_job(db, customer_id, name)
    if row:
        return row
    row = Job(customer_id=customer_id, name=name.strip()[:200])
    db.add(row)
    db.flush()
    return row


def resolve_customer_and_job(
    db: Session, full_name: str, create: bool = True
) -> tuple[Optional[Customer], Optional[Job]]:
    """Resolve an imported "Customer:Job" name to (customer, job).

    A flat customer literally named "A:B" that already exists keeps
    matching (re-imports of pre-jobs files stay stable); otherwise the
    name splits, the customer is found or created, and the job is found
    or created under it. `create=False` only looks up.
    """
    raw = (full_name or "").strip()
    if not raw:
        return None, None
    flat = db.query(Customer).filter(Customer.name == raw).first()
    if flat:
        return flat, None
    cust_name, job_name = split_customer_job(raw)
    customer = db.query(Customer).filter(Customer.name == cust_name).first()
    if not customer:
        if not create:
            return None, None
        customer = Customer(name=cust_name[:200], is_active=True)
        db.add(customer)
        db.flush()
    if not job_name:
        return customer, None
    job = find_job(db, customer.id, job_name)
    if not job and create:
        job = get_or_create_job(db, customer.id, job_name)
    return customer, job


def job_attribution():
    """SQL expression for the job a posted line belongs to: the line's own
    job, else the transaction header's. Reports group on this so header-
    tagged and line-tagged documents reconcile identically."""
    return sqlfunc.coalesce(TransactionLine.job_id, Transaction.job_id)


def job_profitability(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    job_ids: Optional[list[int]] = None,
    customer_id: Optional[int] = None,
    include_no_job: bool = True,
) -> list[dict]:
    """Income / COGS / expenses per job from posted lines.

    Untagged activity lands in the "No job" bucket (job_id None) so the
    report's totals equal the plain P&L for the same period.
    """
    pl_types = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
    attributed = job_attribution().label("job")
    q = (
        db.query(
            attributed,
            Account.account_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .select_from(TransactionLine)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Account.account_type.in_(pl_types))
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    if job_ids is not None:
        q = q.filter(job_attribution().in_(job_ids))
    rows = q.group_by("job", Account.account_type).all()

    jobs = {j.id: j for j in db.query(Job).all()}
    customers = {c.id: c.name for c in db.query(Customer).all()}
    buckets: dict[Optional[int], dict] = {}
    for job_id, acct_type, dr, cr in rows:
        job = jobs.get(job_id)
        if customer_id is not None and (job is None or job.customer_id != customer_id):
            continue
        if job_id is None and not include_no_job:
            continue
        b = buckets.setdefault(
            job_id,
            {
                "job_id": job_id,
                "job_name": job.name if job else NO_JOB_LABEL,
                "customer_id": job.customer_id if job else None,
                "customer_name": customers.get(job.customer_id, "") if job else "",
                "status": job.status if job else None,
                "contract_amount": (
                    float(job.contract_amount) if job and job.contract_amount else None
                ),
                "income": Decimal("0"),
                "cogs": Decimal("0"),
                "expenses": Decimal("0"),
            },
        )
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        if acct_type == AccountType.INCOME:
            b["income"] += cr - dr
        elif acct_type == AccountType.COGS:
            b["cogs"] += dr - cr
        else:
            b["expenses"] += dr - cr

    out = []
    for b in sorted(
        buckets.values(),
        key=lambda x: (
            x["job_id"] is not None,
            x["customer_name"].lower(),
            x["job_name"].lower(),
        ),
    ):
        income, cogs, expenses = b["income"], b["cogs"], b["expenses"]
        costs = cogs + expenses
        net = income - costs
        b.update(
            {
                "income": float(income),
                "cogs": float(cogs),
                "expenses": float(expenses),
                "total_costs": float(costs),
                "gross_profit": float(income - cogs),
                "net_income": float(net),
                "margin_pct": (float(net / income * 100) if income else None),
            }
        )
        out.append(b)
    return out


def job_transactions(
    db: Session,
    job_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """Every posted P&L line attributed to the job — the job cost detail."""
    pl_types = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
    q = (
        db.query(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(job_attribution() == job_id, Account.account_type.in_(pl_types))
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    rows = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()
    out = []
    for line, txn, acct in rows:
        if acct.account_type == AccountType.INCOME:
            kind, amount = "income", Decimal(str(line.credit)) - Decimal(
                str(line.debit)
            )
        else:
            kind, amount = "cost", Decimal(str(line.debit)) - Decimal(str(line.credit))
        out.append(
            {
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "source_type": txn.source_type,
                "source_id": txn.source_id,
                "reference": txn.reference,
                "description": line.description or txn.description or "",
                "account_id": acct.id,
                "account_name": acct.name,
                "account_type": acct.account_type.value,
                "kind": kind,
                "amount": float(amount),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Milestone 2: cost codes and committed cost
# ---------------------------------------------------------------------------


def committed_cost(
    db: Session, job_ids: Optional[list[int]] = None
) -> dict[int, float]:
    """Open purchase-order value per job: ordered but not yet billed.

    A PO is committed once it has left draft and until it is closed
    (conversion to a bill closes it, moving the cost into the ledger). A
    line's job is its own, else the PO header's."""
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine

    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    q = (
        db.query(
            line_job.label("job"),
            sqlfunc.coalesce(sqlfunc.sum(PurchaseOrderLine.amount), 0),
        )
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
        .filter(
            PurchaseOrder.status.in_(
                (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
            ),
            line_job.isnot(None),
        )
    )
    if job_ids is not None:
        q = q.filter(line_job.in_(job_ids))
    return {int(j): float(v) for j, v in q.group_by("job").all()}
