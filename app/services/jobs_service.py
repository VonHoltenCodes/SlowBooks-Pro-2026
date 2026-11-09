# ============================================================================
# Job dimension helpers — Customer:Job name handling, strict lookup, and the
# profitability aggregation every job report and the job detail share.
# ============================================================================

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, delete, or_, select, update
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.contacts import Customer
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine
from app.services import cash_basis
from app.services.terminology import Terms, terms_from_db

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


def nested_customer_job(name: str) -> bool:
    """Whether a QuickBooks name has more than one level ("A:B:C": a
    sub-job, or a sub-customer under a sub-customer). split_customer_job
    keeps one level, so the import flattens it to "A" with "B:C" under it,
    and says so (#638 review) rather than silently."""
    _cust, child = split_customer_job(name)
    return bool(child) and split_customer_job(child)[1] is not None


def find_customer(db: Session, name: str) -> Optional[Customer]:
    """The top-level customer called ``name``: exact, then in any case, as
    find_job does: an import that spells a customer BOB JONES means the Bob
    Jones already on file (#195).

    A sub-customer (#638) is never matched by its bare name: in QuickBooks'
    spelling a bare name is always a top-level customer, and a sub is
    reached only as "Parent:Sub", through find_sub_customer under its
    parent. A file's "Smith" used to post its invoice and payment to the
    tenant "Oak St:Smith", and "Smith:Unit 2" made a third level under it."""
    if not name:
        return None
    q = db.query(Customer).filter(Customer.parent_customer_id.is_(None))
    row = q.filter(Customer.name == name).first()
    if not row:
        row = (
            q.filter(sqlfunc.lower(Customer.name) == name.lower())
            .order_by(Customer.id)
            .first()
        )
    return row


def find_sub_customer(db: Session, parent_id: int, name: str) -> Optional[Customer]:
    """The sub-customer called ``name`` under this parent (#638): exact,
    then in any case, as find_job does."""
    if not name:
        return None
    q = db.query(Customer).filter(Customer.parent_customer_id == parent_id)
    row = q.filter(Customer.name == name).first()
    if not row:
        row = (
            q.filter(sqlfunc.lower(Customer.name) == name.lower())
            .order_by(Customer.id)
            .first()
        )
    return row


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
    db: Session, full_name: str, create: bool = True, sub_customers: bool = False
) -> tuple[Optional[Customer], Optional[Job]]:
    """Resolve an imported "Customer:Job" name to (customer, job).

    A flat customer literally named "A:B" that already exists keeps
    matching (re-imports of pre-jobs files stay stable); otherwise the
    name splits, the customer is found or created, and the job is found
    or created under it. `create=False` only looks up. The flat match and
    the customer half resolve against top-level customers only
    (find_customer): a bare name is a top-level customer, never a
    sub-customer that happens to share it.

    Sub-customers (#638): a "Parent:Sub" whose Sub is a sub-customer on
    file under Parent resolves to that sub-customer, with no job, whatever
    was asked; ``sub_customers`` says a child never seen before is made a
    sub-customer, not a job (the IIF import's "Customer:Job names are
    sub-customers" choice; QuickBooks Online's export, which has no jobs).
    """
    raw = (full_name or "").strip()
    if not raw:
        return None, None
    flat = find_customer(db, raw)
    if flat:
        return flat, None
    cust_name, job_name = split_customer_job(raw)
    customer = find_customer(db, cust_name)
    if not customer:
        if not create:
            return None, None
        customer = Customer(name=cust_name[:200], is_active=True)
        db.add(customer)
        db.flush()
    if not job_name:
        return customer, None
    sub = find_sub_customer(db, customer.id, job_name)
    if sub:
        return sub, None
    job = find_job(db, customer.id, job_name)
    if not job and create and sub_customers:
        sub = Customer(
            name=job_name[:200], parent_customer_id=customer.id, is_active=True
        )
        db.add(sub)
        db.flush()
        return sub, None
    if not job and create:
        job = get_or_create_job(db, customer.id, job_name)
    return customer, job


def _refuse_foreign_jobs(db: Session, customer_id: int, checks, document, t) -> None:
    """`checks` are (job_id, where) pairs: each job must exist (404) and be
    the customer's (400, with the way out named)."""
    for wanted, where in checks:
        if wanted is None:
            continue
        job = db.get(Job, wanted)
        if job is None:
            raise HTTPException(status_code=404, detail=f"{t('Job')} not found")
        if job.customer_id != customer_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{t('Job')} {job.name} belongs to a different {t('customer')} "
                    f"than this {document}{where} — clear the {t('Job')} field or "
                    f"pick one of this {t('customer')}'s."
                ),
            )


def refuse_other_customers_jobs(
    db: Session,
    customer_id: int,
    job_id: Optional[int],
    lines=(),
    document: str = "invoice",
    terms: Optional[Terms] = None,
) -> None:
    """A customer document carries its own customer's jobs only: the job on
    its header and the job on each of its lines.

    An invoice could be saved with another customer's job (2.22.0 gate,
    NEW-36): the form hid other customers' jobs on the hidden select's
    focus, which the type-ahead in front of it never gives it, and the API
    checked nothing, so P&L by Job, the job page and Job Profitability
    counted the invoice under a job that was not the customer's. Refused
    with 400 the way a payment to another customer's invoice is (#189); a
    job that does not exist is 404.

    `lines` are the request's line models or the stored rows, either with
    a job_id; `document` is the document's name for the message, in the
    company's own words ("pledge", "donation" for a nonprofit).
    """
    t = terms or terms_from_db(db)
    checks = [(job_id, "")]
    checks += [
        (getattr(line, "job_id", None), f" (line {n})")
        for n, line in enumerate(lines, start=1)
    ]
    _refuse_foreign_jobs(db, customer_id, checks, document, t)


def refuse_other_customers_jobs_on_edit(
    db: Session,
    row,
    changes: dict,
    lines=None,
    document: str = "invoice",
    terms: Optional[Terms] = None,
) -> None:
    """What an edit CHANGES must leave the document on its customer's jobs;
    what it leaves alone is not judged.

    A new customer: every job the document has after the edit is theirs.
    A new header job: that job. A line's new job — one no stored line of
    the document carried — that job. A resent line with a job some stored
    line carried (its own, or another line's: a client that deletes line
    1 and sends old line 2 back changed no job), a header job sent
    unchanged, a note, a date: nothing to judge — so a document carrying a
    mismatch from before the rule (books the old fixture seeded) still
    saves from the form, which always sends every field, and a line's
    stored mismatch is cleared by sending the line without it.

    `row` is the stored document (customer_id, job_id, lines), `changes`
    the edit's set fields, `lines` the resent lines or None.
    """
    t = terms or terms_from_db(db)
    customer_after = changes.get("customer_id") or row.customer_id
    header_after = changes["job_id"] if "job_id" in changes else row.job_id
    if customer_after != row.customer_id:
        lines_after = lines if lines is not None else list(row.lines)
        refuse_other_customers_jobs(
            db, customer_after, header_after, lines_after, document, t
        )
        return
    checks = []
    if "job_id" in changes and changes["job_id"] != row.job_id:
        checks.append((changes["job_id"], ""))
    if lines is not None:
        carried = {ln.job_id for ln in row.lines if ln.job_id is not None}
        for n, line in enumerate(lines, start=1):
            after = getattr(line, "job_id", None)
            if after is not None and after not in carried:
                checks.append((after, f" (line {n})"))
    _refuse_foreign_jobs(db, customer_after, checks, document, t)


def own_job(
    db: Session, customer_id: int, job_id: Optional[int], left_off: list
) -> Optional[int]:
    """The job, if it is the customer's; else None, and the job's name goes
    on `left_off`. A copy of a document — an estimate converted, an invoice
    duplicated, a scheduled invoice generated — carries only the customer's
    jobs: a job of another customer stored on the original (books from
    before the rule) is left off the copy rather than minted again, and
    the copy is never refused for it."""
    if job_id is None:
        return None
    job = db.get(Job, job_id)
    if job is not None and job.customer_id == customer_id:
        return job_id
    name = job.full_name if job is not None else f"#{job_id}"
    if name not in left_off:
        left_off.append(name)
    return None


def documents_carrying_job(db: Session, job_id: int, t: Terms) -> list[tuple[str, int]]:
    """How many live customer documents carry the job, on their header or
    a line, by the noun each document is refused with (document_label's:
    invoice / pledge / sales receipt / donation receipt; credit memo;
    estimate; recurring invoice / pledge; in-kind gift), in the order a
    person reads them. Void ones are left out: they cannot be edited, so
    they could never be moved."""
    from sqlalchemy import or_

    from app.models.credit_memos import CreditMemo, CreditMemoLine, CreditMemoStatus
    from app.models.estimates import Estimate, EstimateLine
    from app.models.in_kind import InKindGift, InKindGiftLine
    from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
    from app.models.recurring import RecurringInvoice
    from app.services.donor_documents import document_label

    on_invoice_line = db.query(InvoiceLine.invoice_id).filter(
        InvoiceLine.job_id == job_id
    )
    live_invoices = (
        db.query(Invoice)
        .filter(
            or_(Invoice.job_id == job_id, Invoice.id.in_(on_invoice_line)),
            Invoice.status != InvoiceStatus.VOID,
        )
        .all()
    )
    on_estimate_line = db.query(EstimateLine.estimate_id).filter(
        EstimateLine.job_id == job_id
    )
    on_gift_line = db.query(InKindGiftLine.gift_id).filter(
        InKindGiftLine.job_id == job_id
    )
    counts: dict[str, int] = {}
    for inv in live_invoices:
        noun = document_label(inv, t).lower()
        counts[noun] = counts.get(noun, 0) + 1
    on_memo_line = db.query(CreditMemoLine.credit_memo_id).filter(
        CreditMemoLine.job_id == job_id
    )
    counts["credit memo"] = (
        db.query(CreditMemo)
        .filter(
            or_(CreditMemo.job_id == job_id, CreditMemo.id.in_(on_memo_line)),
            CreditMemo.status != CreditMemoStatus.VOID,
        )
        .count()
    )
    counts["estimate"] = (
        db.query(Estimate)
        .filter(or_(Estimate.job_id == job_id, Estimate.id.in_(on_estimate_line)))
        .count()
    )
    counts["recurring " + t("invoice")] = (
        db.query(RecurringInvoice).filter(RecurringInvoice.job_id == job_id).count()
    )
    counts["in-kind gift"] = (
        db.query(InKindGift)
        .filter(
            or_(InKindGift.job_id == job_id, InKindGift.id.in_(on_gift_line)),
            InKindGift.status != "void",
        )
        .count()
    )
    order = (
        "invoice",
        "pledge",
        "sales receipt",
        "donation receipt",
        "credit memo",
        "estimate",
        "recurring " + t("invoice"),
        "in-kind gift",
    )
    return [(noun, counts.get(noun, 0)) for noun in order]


def refuse_moving_a_carried_job(db: Session, job: Job, terms=None) -> None:
    """A job moves to another customer only while no customer document
    carries it: moving it would put those documents on another customer's
    job, which the rule above refuses to create. 400, naming what carries
    it by the noun each is refused with ("1 pledge and 2 donation
    receipts"); a job with no documents still moves."""
    t = terms or terms_from_db(db)
    carried = [(noun, n) for noun, n in documents_carrying_job(db, job.id, t) if n]
    if not carried:
        return
    parts = [f"{n} {noun}{'' if n == 1 else 's'}" for noun, n in carried]
    listed = (
        parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    )
    who = job.customer.name if job.customer else f"its {t('customer')}"
    raise HTTPException(
        status_code=400,
        detail=f"{t('Job')} {job.name} is on {listed} for {who}; move those first.",
    )


def job_attribution():
    """SQL expression for the job a posted line belongs to: the line's own
    job, else the transaction header's. Reports group on this so header-
    tagged and line-tagged documents reconcile identically."""
    return sqlfunc.coalesce(TransactionLine.job_id, Transaction.job_id)


# The WIP adjusting entry's journals (app/services/wip_posting.py): the entry,
# its next-day reversal and the voids of both. Their lines carry the job, so
# P&L by Job and Job Profitability tie to the WIP schedule; every figure the
# schedule is computed from (cost to date, posted revenue, cost to complete,
# the cost tree) leaves them out, so posting never moves the schedule.
WIP_SOURCE_TYPES = (
    "wip_adjustment",
    "wip_reversal",
    "wip_adjustment_void",
    "wip_reversal_void",
)


def not_wip_entry():
    """SQL filter: the transaction is no part of a WIP adjusting entry (a
    journal with no source type counts as not)."""
    return or_(
        Transaction.source_type.is_(None),
        Transaction.source_type.notin_(WIP_SOURCE_TYPES),
    )


def job_profitability(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    job_ids: Optional[list[int]] = None,
    customer_id: Optional[int] = None,
    include_no_job: bool = True,
    basis: str = "accrual",
    include_wip: bool = True,
) -> list[dict]:
    """Income / COGS / expenses per job from posted lines (`basis`: accrual
    or cash, #483).

    Untagged activity lands in the "No job" bucket (job_id None) so the
    report's totals equal the plain P&L for the same period. The WIP
    adjusting entry counts on its jobs (the report ties to the WIP schedule)
    unless `include_wip` is False, for a reader that compares a job's own
    cost with its budget; a cost-plus fee is never earned on it.
    """
    # The untagged bucket is labelled in the business word; a nonprofit's
    # report reads "No grant" (vocabulary audit, 2.13.x).
    from app.services.terminology import terms_from_db

    no_job_label = terms_from_db(db).text(NO_JOB_LABEL)
    pl_types = (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
    attributed = job_attribution().label("job")
    is_wip = case((Transaction.source_type.in_(WIP_SOURCE_TYPES), 1), else_=0).label(
        "wip"
    )
    q = (
        db.query(
            attributed,
            Account.account_type,
            is_wip,
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
    rows = (
        q.group_by("job", Account.account_type, "wip")
        .execution_options(**cash_basis.reading(basis))
        .all()
    )

    jobs = {j.id: j for j in db.query(Job).all()}
    customers = {c.id: c.name for c in db.query(Customer).all()}
    buckets: dict[Optional[int], dict] = {}
    for job_id, acct_type, wip, dr, cr in rows:
        if wip and not include_wip:
            continue
        job = jobs.get(job_id)
        if customer_id is not None and (job is None or job.customer_id != customer_id):
            continue
        if job_id is None and not include_no_job:
            continue
        b = buckets.setdefault(
            job_id,
            {
                "job_id": job_id,
                "job_name": job.name if job else no_job_label,
                "customer_id": job.customer_id if job else None,
                "customer_name": customers.get(job.customer_id, "") if job else "",
                "status": job.status if job else None,
                "contract_amount": (
                    float(job.contract_amount) if job and job.contract_amount else None
                ),
                # The contract type rides along for the Job Profitability report
                # (#552); a fee is added below for cost-plus / GMP jobs.
                "contract_type": job.contract_type if job else None,
                "income": Decimal("0"),
                "cogs": Decimal("0"),
                "expenses": Decimal("0"),
                "_wip_cost": Decimal("0"),
            },
        )
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        if acct_type == AccountType.INCOME:
            b["income"] += cr - dr
        elif acct_type == AccountType.COGS:
            b["cogs"] += dr - cr
        else:
            b["expenses"] += dr - cr
        if wip and acct_type != AccountType.INCOME:
            b["_wip_cost"] += dr - cr

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
        wip_cost = b.pop("_wip_cost")
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
        # Fee on a cost-plus / GMP job (#552): the fee percent on this
        # PERIOD's actual cost — hence fee_period, a period figure like the
        # costs beside it (the job-to-date contract fee lives on the contract
        # summary). Additive; lump sum, plain and fixed-fee jobs omit it.
        job = jobs.get(b["job_id"])
        if job is not None and job.contract_type in ("cost_plus", "gmp"):
            # A fixed fee is a job-level figure, not a period one — it would
            # repeat whole on every period — so it shows no fee_period.
            if job.fee_amount is None and job.fee_pct is not None:
                # (a WIP loss provision is no cost the fee is earned on)
                b["fee_period"] = float(
                    (
                        (costs - wip_cost) * Decimal(str(job.fee_pct)) / Decimal("100")
                    ).quantize(Decimal("0.01"))
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
    """The remaining open commitment per job: ordered but not yet billed.

    A commitment (a PO or subcontract) is committed once it has left draft and
    until it is closed, and only the part not yet billed still counts —
    partial billing moves each period's dollars into the ledger (#553). So the
    figure is Σ (amount − billed_amount) over the lines of non-draft,
    non-closed commitments, which keeps the number meaning "open PO value not
    yet billed". A line's job is its own, else the header's."""
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine

    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    q = (
        db.query(
            line_job.label("job"),
            sqlfunc.coalesce(
                sqlfunc.sum(PurchaseOrderLine.amount - PurchaseOrderLine.billed_amount),
                0,
            ),
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


def commitment_figures(
    db: Session, job_ids: Optional[list[int]] = None
) -> dict[int, dict]:
    """Per job, the commitment roll-up that rides alongside committed_cost
    (#553): committed_total (Σ amount of every non-draft commitment line) and
    billed_to_date (Σ billed_amount of the same). A commitment leaves draft
    once sent; a closed one keeps its total and its billing in these two, so
    a buyout stays visible after the work is fully billed. The remaining
    figure is committed_cost()."""
    from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine

    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    q = (
        db.query(
            line_job.label("job"),
            sqlfunc.coalesce(sqlfunc.sum(PurchaseOrderLine.amount), 0),
            sqlfunc.coalesce(sqlfunc.sum(PurchaseOrderLine.billed_amount), 0),
        )
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
        .filter(
            PurchaseOrder.status != POStatus.DRAFT,
            line_job.isnot(None),
        )
    )
    if job_ids is not None:
        q = q.filter(line_job.in_(job_ids))
    return {
        int(j): {"committed_total": float(total), "billed_to_date": float(billed)}
        for j, total, billed in q.group_by("job").all()
    }


# --- Deleting a job ----------------------------------------------------------

# The job's own setup, not records made for it: it goes with the job. The
# schedule of values goes by the Job.sov_lines relationship's cascade.
_JOB_SETUP_TABLES = ("job_budgets", "job_cost_to_complete")
# Refused before this is asked: posted activity and memorized transactions.
_CHECKED_FIRST = {
    "transactions",
    "transaction_lines",
    "memorized_transactions",
    "memorized_transaction_lines",
    "sov_lines",
}
# What a record made for a job is called in the refusal, by its table. A
# line table is counted as its record (a bill with one line on the job is
# one bill).
_RECORD_NAMES = {
    "change_orders": ("change order", "change orders"),
    "pay_applications": ("pay application", "pay applications"),
    "field_tickets": ("field ticket", "field tickets"),
    "job_deposits": ("deposit", "deposits"),
    "job_costs": ("job cost entry", "job cost entries"),
    "estimates": ("estimate", "estimates"),
    "invoices": ("invoice", "invoices"),
    "recurring_invoices": ("recurring invoice", "recurring invoices"),
    "credit_memos": ("credit memo", "credit memos"),
    "purchase_orders": ("purchase order", "purchase orders"),
    "bills": ("bill", "bills"),
    "vendor_credits": ("vendor credit", "vendor credits"),
    "time_entries": ("time entry", "time entries"),
    "in_kind_gifts": ("in-kind gift", "in-kind gifts"),
    "bank_rules": ("bank rule", "bank rules"),
    "allocation_rules": ("allocation rule", "allocation rules"),
}


def _job_keys():
    """Every column in the schema that holds a job's id, with its table."""
    from app.database import Base

    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "jobs":
                yield table, fk


def _record_of(table):
    """The record a row of ``table`` belongs to: the table itself when it
    is one, else the parent its lines cascade from."""
    if table.name in _RECORD_NAMES:
        return table.name, table.c.id
    for fk in table.foreign_keys:
        if fk.ondelete == "CASCADE" and fk.column.table.name in _RECORD_NAMES:
            return fk.column.table.name, fk.parent
    return table.name, table.c.id


def records_on_job(db: Session, job_id: int) -> dict[str, int]:
    """How many of each kind of record were made for the job, by name."""
    found: dict[str, set] = {}
    for table, fk in _job_keys():
        if (
            table.name in _CHECKED_FIRST
            or table.name in _JOB_SETUP_TABLES
            or fk.ondelete == "SET NULL"
        ):
            continue
        record, id_col = _record_of(table)
        ids = db.execute(select(id_col).where(fk.parent == job_id)).scalars().all()
        if ids:
            found.setdefault(record, set()).update(ids)
    return {record: len(ids) for record, ids in found.items()}


def _counted(record: str, n: int) -> str:
    one, many = _RECORD_NAMES.get(record, ("other record", "other records"))
    return f"{n} {one if n == 1 else many}"


def refuse_deleting_job_with_records(db: Session, job_id: int) -> None:
    """A job is deleted only when nothing was made for it (409 otherwise),
    the way a class or cost code with posted lines is refused. The keys
    from a change order, pay application or field ticket to its job say
    CASCADE, but SQLite runs no cascade here: they stayed under the deleted
    job's id, and the next job given that id took them over. On PostgreSQL
    the cascade ran and left their attached files behind."""
    counts = records_on_job(db, job_id)
    if not counts:
        return
    order = list(_RECORD_NAMES)
    parts = [
        _counted(r, counts[r])
        for r in sorted(counts, key=lambda r: order.index(r) if r in order else 99)
    ]
    listed = (
        parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    )
    raise HTTPException(
        status_code=409,
        detail=(
            f"This job still has {listed}. Delete those first, or mark the "
            "job inactive instead."
        ),
    )


def delete_job_setup(db: Session, job_id: int) -> None:
    """The job's own setup goes with it, and a record whose key to the job
    says SET NULL lets go of it: SQLite runs neither rule itself. Caller
    deletes the job and commits."""
    for table, fk in _job_keys():
        if table.name in _JOB_SETUP_TABLES:
            db.execute(delete(table).where(fk.parent == job_id))
        elif fk.ondelete == "SET NULL":
            db.execute(
                update(table).where(fk.parent == job_id).values({fk.parent.name: None})
            )
