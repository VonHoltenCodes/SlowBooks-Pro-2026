# ============================================================================
# Job costing services (Projects milestone 3).
#
# Cost types · cost-code tree · Job Cost Entry posting / void · time entries
# posted to jobs at a loaded rate with burden · allocations · budgets · the
# budget-vs-actual drill-down tree.
#
# Column vocabulary follows what contractors already read every week
# (Procore's standard budget view, QuickBooks Desktop's Estimates vs Actuals):
#   original  budget as entered / seeded from the estimate
#   changes   budget changes (change orders land here in a later milestone)
#   revised   original + changes
#   committed open purchase orders (sent / partial / received) not yet billed
#   actual    job-to-date posted cost
#   projected actual + committed
#   variance  revised - projected  (positive = under budget)
#   pct_used  projected / revised
# plus estimated vs actual revenue per code, QuickBooks style.
# ============================================================================

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.cost_codes import CostCode
from app.models.estimates import Estimate
from app.models.job_costing import (
    DEFAULT_COST_TYPES,
    CostType,
    Equipment,
    JobBudget,
    JobCost,
    JobCostLine,
)
from app.models.jobs import Job
from app.models.payroll import Employee, PayType
from app.models.purchase_orders import POStatus, PurchaseOrder, PurchaseOrderLine
from app.models.time_entries import TimeEntry, TimeEntryStatus
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import create_journal_entry
from app.services.jobs_service import job_attribution

TWO = Decimal("0.01")


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(TWO, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Cost types
# ---------------------------------------------------------------------------


def ensure_default_cost_types(db: Session) -> None:
    """Seed the five starting types on first use (fresh test databases and
    companies created before the migration converge on the same rows)."""
    existing = {r.code for r in db.query(CostType).all()}
    added = False
    for code, name, is_labor, order in DEFAULT_COST_TYPES:
        if code not in existing:
            db.add(CostType(code=code, name=name, is_labor=is_labor, sort_order=order))
            added = True
    if added:
        db.flush()


def cost_type_map(db: Session) -> dict[str, CostType]:
    ensure_default_cost_types(db)
    return {r.code: r for r in db.query(CostType).all()}


# ---------------------------------------------------------------------------
# Cost-code tree
# ---------------------------------------------------------------------------


def code_tree(codes: list[CostCode]) -> list[dict]:
    """Nest codes by parent_id. Orphans (parent inactive/missing) surface at
    the top so nothing disappears from a report."""
    by_id = {c.id: {"code": c, "children": []} for c in codes}
    roots = []
    for node in by_id.values():
        pid = node["code"].parent_id
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    def sort(nodes):
        nodes.sort(key=lambda n: (n["code"].code, n["code"].name))
        for n in nodes:
            sort(n["children"])

    sort(roots)
    return roots


def code_depth(db: Session, code: CostCode) -> int:
    depth, seen, cur = 0, set(), code
    while cur.parent_id and cur.parent_id not in seen:
        seen.add(cur.id)
        cur = db.get(CostCode, cur.parent_id)
        if not cur:
            break
        depth += 1
    return depth


def would_cycle(db: Session, code_id: int, new_parent_id: Optional[int]) -> bool:
    cur = new_parent_id
    seen = set()
    while cur:
        if cur == code_id or cur in seen:
            return True
        seen.add(cur)
        parent = db.get(CostCode, cur)
        cur = parent.parent_id if parent else None
    return False


def import_cost_codes(db: Session, rows: list[dict]) -> dict:
    """Rows of {code, name, cost_type?, parent_code?}. Parents may appear
    after children; a second pass links them. Existing codes are updated
    (name / type / parent), never duplicated."""
    types = cost_type_map(db)
    created = updated = 0
    errors: list[str] = []
    by_code: dict[str, CostCode] = {c.code.lower(): c for c in db.query(CostCode).all()}
    for i, row in enumerate(rows, 1):
        code = (row.get("code") or "").strip()[:20]
        name = (row.get("name") or "").strip()[:200]
        if not code or not name:
            errors.append(f"row {i}: code and name are required")
            continue
        ctype = (row.get("cost_type") or "other").strip().lower()
        if ctype not in types:
            errors.append(f"row {i}: unknown cost type '{ctype}'")
            continue
        existing = by_code.get(code.lower())
        if existing:
            existing.name, existing.cost_type = name, ctype
            updated += 1
        else:
            existing = CostCode(code=code, name=name, cost_type=ctype)
            db.add(existing)
            db.flush()
            by_code[code.lower()] = existing
            created += 1
    for i, row in enumerate(rows, 1):
        code = (row.get("code") or "").strip().lower()
        parent_code = (row.get("parent_code") or "").strip().lower()
        if not code or code not in by_code:
            continue
        if parent_code:
            parent = by_code.get(parent_code)
            if not parent:
                errors.append(f"row {i}: parent '{parent_code}' not found")
                continue
            if parent.id == by_code[code].id or would_cycle(
                db, by_code[code].id, parent.id
            ):
                errors.append(f"row {i}: parent would create a cycle")
                continue
            by_code[code].parent_id = parent.id
    db.flush()
    return {"created": created, "updated": updated, "errors": errors}


def parse_cost_code_csv(text: str) -> list[dict]:
    """'code,name,cost_type,parent_code' with or without a header line."""
    import csv
    import io

    rows = []
    reader = csv.reader(io.StringIO(text))
    for raw in reader:
        if not raw or not any(cell.strip() for cell in raw):
            continue
        cells = [c.strip() for c in raw] + ["", "", "", ""]
        if cells[0].lower() == "code" and cells[1].lower() == "name":
            continue
        rows.append(
            {
                "code": cells[0],
                "name": cells[1],
                "cost_type": cells[2] or "other",
                "parent_code": cells[3],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Job Cost Entry
# ---------------------------------------------------------------------------


def next_job_cost_number(db: Session) -> str:
    last = (
        db.query(JobCost.number)
        .filter(JobCost.number.like("JC-%"))
        .order_by(JobCost.id.desc())
        .first()
    )
    n = 0
    if last:
        try:
            n = int(last[0].split("-")[1])
        except (IndexError, ValueError):
            n = db.query(sqlfunc.count(JobCost.id)).scalar() or 0
    return f"JC-{n + 1:06d}"


def resolve_line_accounts(
    db: Session,
    types: dict[str, CostType],
    cost_code: Optional[CostCode],
    cost_type: Optional[str],
    debit_account_id: Optional[int],
    credit_account_id: Optional[int],
    is_burden: bool = False,
    equipment: Optional[Equipment] = None,
) -> tuple[int, int]:
    """Debit = the given account, else the code's, else the type's default.
    Credit = the given account, else the equipment's recovery account, else
    the type's offset (burden offset for burden lines). Raises ValueError
    with the Settings fix when nothing resolves."""
    ctype = types.get(cost_type or (cost_code.cost_type if cost_code else "other"))
    debit = debit_account_id or (cost_code.account_id if cost_code else None)
    if not debit and ctype:
        debit = ctype.default_account_id
    if not debit:
        raise ValueError(
            "No cost account for this line — pick one, or set a default account on "
            f"the '{ctype.name if ctype else cost_type}' cost type or the cost code in Settings"
        )
    credit = credit_account_id
    if not credit and equipment is not None:
        credit = equipment.recovery_account_id
    if not credit and ctype:
        credit = (
            ctype.burden_offset_account_id or ctype.offset_account_id
            if is_burden
            else ctype.offset_account_id
        )
    if not credit:
        raise ValueError(
            "No offset account for this line — pick one, or set the offset account on "
            f"the '{ctype.name if ctype else cost_type}' cost type in Settings "
            "(Settings → Cost Types → Create default offset accounts)"
        )
    return int(debit), int(credit)


def post_job_cost(db: Session, jc: JobCost) -> Transaction:
    """One balanced entry: DR cost account per line (tagged to the line's
    job, code and type), CR the offset per line (untagged — the offset is
    company-side, so it lands in "No job" and the P&L still reconciles)."""
    lines: list[dict] = []
    job = jc.job
    total = Decimal("0")
    for ln in jc.lines:
        amt = _q(ln.amount)
        if amt == 0:
            continue
        total += amt
        job_id = ln.job_id or jc.job_id
        desc = (
            ln.description or (ln.cost_code.label if ln.cost_code else "") or "Job cost"
        )
        lines.append(
            {
                "account_id": ln.debit_account_id,
                "debit": amt,
                "credit": Decimal("0"),
                "description": desc,
                "job_id": job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
                "is_billable": ln.is_billable,
            }
        )
        lines.append(
            {
                "account_id": ln.credit_account_id,
                "debit": Decimal("0"),
                "credit": amt,
                "description": f"Offset: {desc}",
                "job_id": None,
            }
        )
    if not lines:
        raise ValueError("A job cost entry needs at least one line with an amount")
    label = job.full_name if job else "allocation"
    txn = create_journal_entry(
        db,
        jc.date,
        f"Job cost {jc.number} - {label}",
        lines,
        source_type="job_cost",
        source_id=jc.id,
        reference=jc.number,
    )
    jc.transaction_id = txn.id
    jc.total = total
    return txn


def void_job_cost(db: Session, jc: JobCost) -> None:
    if jc.status == "void":
        raise ValueError("Job cost entry is already void")
    txn = jc.transaction
    if txn is not None:
        reverse = [
            {
                "account_id": ln.account_id,
                "debit": ln.credit,
                "credit": ln.debit,
                "description": f"VOID: {ln.description or ''}",
                "job_id": ln.job_id,
                "cost_code_id": ln.cost_code_id,
                "cost_type": ln.cost_type,
            }
            for ln in txn.lines
        ]
        create_journal_entry(
            db,
            jc.date,
            f"VOID {txn.description or jc.number}",
            reverse,
            source_type="job_cost_void",
            source_id=jc.id,
            reference=jc.number,
        )
    jc.status = "void"
    for te in db.query(TimeEntry).filter(TimeEntry.job_cost_id == jc.id).all():
        te.job_cost_id = None


# ---------------------------------------------------------------------------
# Time entries → job labor cost at a loaded rate, with burden
# ---------------------------------------------------------------------------

OT_MULT = Decimal("1.5")
DT_MULT = Decimal("2")
ANNUAL_HOURS = Decimal("2080")


def employee_cost_rate(emp: Employee) -> Decimal:
    if emp.cost_rate is not None and Decimal(str(emp.cost_rate)) > 0:
        return Decimal(str(emp.cost_rate))
    rate = Decimal(str(emp.pay_rate or 0))
    if emp.pay_type == PayType.SALARY:
        return (rate / ANNUAL_HOURS).quantize(Decimal("0.0001"))
    return rate


def labor_cost_for_entry(
    emp: Employee, entry: TimeEntry
) -> tuple[Decimal, Decimal, Decimal]:
    """(cost hours, rate, base cost). Overtime and double-time count at
    their pay multipliers so the job carries what the hours actually cost.

    Known simplification (Keith, #86 review): the burden % is then applied
    to this whole amount, premium included. Workers' comp premiums mostly
    exclude the overtime premium (FICA/FUTA don't), so a heavy-OT job
    carries slightly more burden than a class-by-class calculation would.
    Acceptable as a first cut; the payroll milestone (M4) replaces the flat
    % with the benefits engine's per-code rules."""
    rate = employee_cost_rate(emp)
    hours = (
        Decimal(str(entry.hours_regular or 0))
        + Decimal(str(entry.hours_overtime or 0)) * OT_MULT
        + Decimal(str(entry.hours_doubletime or 0)) * DT_MULT
    )
    return hours, rate, _q(hours * rate)


def post_time_entry_to_job(db: Session, entry: TimeEntry) -> JobCost:
    if entry.job_cost_id:
        raise ValueError("Time entry is already posted to its job")
    if not entry.job_id:
        raise ValueError("Time entry has no job")
    if entry.status not in (TimeEntryStatus.APPROVED, TimeEntryStatus.SUBMITTED):
        raise ValueError("Only submitted or approved time entries post to a job")
    emp = entry.employee or db.get(Employee, entry.employee_id)
    types = cost_type_map(db)
    labor = types.get("labor")
    code = db.get(CostCode, entry.cost_code_id) if entry.cost_code_id else None
    hours, rate, base = labor_cost_for_entry(emp, entry)
    if base <= 0:
        raise ValueError("Time entry has no hours, or the employee has no cost rate")
    debit, credit = resolve_line_accounts(db, types, code, "labor", None, None)

    jc = JobCost(
        number=next_job_cost_number(db),
        date=entry.date,
        job_id=entry.job_id,
        memo=f"Labor — {emp.full_name} — {entry.date.isoformat()}",
        source="time_entry",
    )
    db.add(jc)
    db.flush()
    jc.lines.append(
        JobCostLine(
            job_cost_id=jc.id,
            cost_code_id=code.id if code else None,
            cost_type="labor",
            description=f"{emp.full_name}: {hours} cost hrs @ {rate}",
            quantity=hours,
            rate=rate,
            amount=base,
            debit_account_id=debit,
            credit_account_id=credit,
            employee_id=emp.id,
            time_entry_id=entry.id,
            line_order=0,
        )
    )
    pct = (
        emp.burden_pct
        if emp.burden_pct is not None
        else (labor.burden_pct if labor else None)
    )
    if pct and Decimal(str(pct)) > 0:
        burden = _q(base * Decimal(str(pct)) / Decimal("100"))
        if burden > 0:
            bdebit, bcredit = resolve_line_accounts(
                db, types, code, "labor", None, None, is_burden=True
            )
            jc.lines.append(
                JobCostLine(
                    job_cost_id=jc.id,
                    cost_code_id=code.id if code else None,
                    cost_type="labor",
                    description=f"Labor burden {pct}% — {emp.full_name}",
                    quantity=1,
                    rate=burden,
                    amount=burden,
                    debit_account_id=bdebit,
                    credit_account_id=bcredit,
                    employee_id=emp.id,
                    time_entry_id=entry.id,
                    is_burden=True,
                    line_order=1,
                )
            )
    db.flush()
    post_job_cost(db, jc)
    entry.job_cost_id = jc.id
    return jc


# ---------------------------------------------------------------------------
# Allocations: spread one amount across jobs
# ---------------------------------------------------------------------------


def allocation_weights(
    db: Session,
    method: str,
    job_ids: list[int],
    start_date: Optional[date],
    end_date: Optional[date],
    explicit: Optional[dict[int, Decimal]] = None,
) -> dict[int, Decimal]:
    if method == "percent":
        return {
            j: Decimal(str(w))
            for j, w in (explicit or {}).items()
            if Decimal(str(w)) > 0
        }
    if method == "equal":
        return {j: Decimal("1") for j in job_ids}
    if method == "hours":
        q = db.query(
            TimeEntry.job_id,
            sqlfunc.coalesce(
                sqlfunc.sum(
                    TimeEntry.hours_regular
                    + TimeEntry.hours_overtime
                    + TimeEntry.hours_doubletime
                ),
                0,
            ),
        ).filter(TimeEntry.job_id.in_(job_ids))
        if start_date:
            q = q.filter(TimeEntry.date >= start_date)
        if end_date:
            q = q.filter(TimeEntry.date <= end_date)
        return {
            int(j): Decimal(str(h)) for j, h in q.group_by(TimeEntry.job_id).all() if h
        }
    # revenue / costs from posted lines
    from app.services.jobs_service import job_profitability

    rows = job_profitability(
        db, start_date, end_date, job_ids=job_ids, include_no_job=False
    )
    key = "income" if method == "revenue" else "total_costs"
    return {r["job_id"]: Decimal(str(r[key])) for r in rows if r[key] and r[key] > 0}


def allocate_cost(
    db: Session,
    *,
    txn_date: date,
    amount: Decimal,
    method: str,
    job_ids: list[int],
    cost_code_id: Optional[int],
    cost_type: Optional[str],
    debit_account_id: Optional[int],
    credit_account_id: Optional[int],
    memo: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
    explicit: Optional[dict[int, Decimal]] = None,
) -> JobCost:
    amount = _q(amount)
    if amount <= 0:
        raise ValueError("Allocation amount must be positive")
    weights = allocation_weights(db, method, job_ids, start_date, end_date, explicit)
    if not weights:
        raise ValueError("Nothing to allocate on: no jobs carry weight for that method")
    total_w = sum(weights.values())
    types = cost_type_map(db)
    code = db.get(CostCode, cost_code_id) if cost_code_id else None
    debit, credit = resolve_line_accounts(
        db, types, code, cost_type, debit_account_id, credit_account_id
    )
    jc = JobCost(
        number=next_job_cost_number(db),
        date=txn_date,
        job_id=None,
        memo=memo or f"Allocation by {method}",
        source="allocation",
    )
    db.add(jc)
    db.flush()
    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    allocated = Decimal("0")
    for i, (job_id, w) in enumerate(ordered):
        share = _q(amount * w / total_w)
        if i == len(ordered) - 1:
            share = amount - allocated  # rounding remainder to the last job
        allocated += share
        jc.lines.append(
            JobCostLine(
                job_cost_id=jc.id,
                job_id=job_id,
                cost_code_id=code.id if code else None,
                cost_type=cost_type or (code.cost_type if code else "other"),
                description=memo or f"Allocated by {method}",
                quantity=1,
                rate=share,
                amount=share,
                debit_account_id=debit,
                credit_account_id=credit,
                line_order=i,
            )
        )
    db.flush()
    post_job_cost(db, jc)
    return jc


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def budgets_from_estimate(db: Session, job: Job, estimate: Estimate) -> list[JobBudget]:
    """One budget row per cost code on the estimate: cost = qty × unit cost
    (0 when no cost is entered — budgeting the sale price as cost would
    hide the margin), revenue = the line amount. Lines without a code roll
    into a whole-job row. Re-seeding replaces the estimate rows; a row the
    operator edited by hand (source "manual") wins and is left alone."""
    totals: dict[Optional[int], list[Decimal]] = {}
    for ln in estimate.lines:
        qty = Decimal(str(ln.quantity or 0))
        cost = (
            _q(qty * Decimal(str(ln.unit_cost)))
            if ln.unit_cost is not None
            else Decimal("0")
        )
        rev = _q(ln.amount or 0)
        bucket = totals.setdefault(ln.cost_code_id, [Decimal("0"), Decimal("0")])
        bucket[0] += cost
        bucket[1] += rev
    db.query(JobBudget).filter(
        JobBudget.job_id == job.id, JobBudget.source == "estimate"
    ).delete()
    manual_codes = {
        r.cost_code_id
        for r in db.query(JobBudget)
        .filter(
            JobBudget.job_id == job.id,
            JobBudget.cost_type.is_(None),
            JobBudget.source == "manual",
        )
        .all()
    }
    rows = []
    for code_id, (cost, rev) in totals.items():
        if code_id in manual_codes:
            continue
        row = JobBudget(
            job_id=job.id,
            cost_code_id=code_id,
            amount=cost,
            revenue_amount=rev,
            source="estimate",
            estimate_id=estimate.id,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


# ---------------------------------------------------------------------------
# Drill-down: budget vs actual tree
# ---------------------------------------------------------------------------


def _blank() -> dict:
    return {
        "original": Decimal("0"),
        "changes": Decimal("0"),
        "committed": Decimal("0"),
        "actual": Decimal("0"),
        "est_revenue": Decimal("0"),
        "act_revenue": Decimal("0"),
    }


def _finish(f: dict) -> dict:
    revised = f["original"] + f["changes"]
    projected = f["actual"] + f["committed"]
    variance = revised - projected
    out = {k: float(v) for k, v in f.items()}
    out.update(
        {
            "revised": float(revised),
            "projected": float(projected),
            "variance": float(variance),
            "pct_used": (float(projected / revised * 100) if revised else None),
            "revenue_diff": float(f["act_revenue"] - f["est_revenue"]),
        }
    )
    return out


def job_cost_tree(
    db: Session,
    job_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """The whole drill-down in one call: cost types → code tree → lines,
    every level carrying budget / committed / actual / projected /
    variance and the revenue pair. Budgets and committed cost are
    job-to-date (a budget has no period); actuals honour the period."""
    types = cost_type_map(db)
    ordered_types = sorted(types.values(), key=lambda t: (t.sort_order, t.code))
    codes = db.query(CostCode).all()
    code_by_id = {c.id: c for c in codes}

    # actual cost / revenue lines
    q = (
        db.query(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            job_attribution() == job_id,
            Account.account_type.in_(
                (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
            ),
        )
    )
    if start_date:
        q = q.filter(Transaction.date >= start_date)
    if end_date:
        q = q.filter(Transaction.date <= end_date)
    lines = q.order_by(Transaction.date, Transaction.id, TransactionLine.id).all()

    # committed: open PO lines on this job, by code
    line_job = sqlfunc.coalesce(PurchaseOrderLine.job_id, PurchaseOrder.job_id)
    committed_rows = (
        db.query(
            PurchaseOrderLine.cost_code_id,
            sqlfunc.coalesce(sqlfunc.sum(PurchaseOrderLine.amount), 0),
        )
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
        .filter(
            line_job == job_id,
            PurchaseOrder.status.in_(
                (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
            ),
        )
        .group_by(PurchaseOrderLine.cost_code_id)
        .all()
    )
    budgets = db.query(JobBudget).filter(JobBudget.job_id == job_id).all()

    # per-code figures (own lines only; roll-ups come after)
    fig: dict = {}  # key: ("code", id) | ("uncoded", type_code) | ("job", None)
    line_rows: dict = {}

    def key_for(code_id, cost_type):
        if code_id and code_id in code_by_id:
            return ("code", code_id)
        return ("uncoded", cost_type or "other")

    for ln, txn, acct in lines:
        ctype = ln.cost_type or (
            code_by_id[ln.cost_code_id].cost_type
            if ln.cost_code_id in code_by_id
            else None
        )
        k = key_for(ln.cost_code_id, ctype)
        f = fig.setdefault(k, _blank())
        if acct.account_type == AccountType.INCOME:
            amt = Decimal(str(ln.credit)) - Decimal(str(ln.debit))
            f["act_revenue"] += amt
            kind = "income"
        else:
            amt = Decimal(str(ln.debit)) - Decimal(str(ln.credit))
            f["actual"] += amt
            kind = "cost"
        line_rows.setdefault(k, []).append(
            {
                "transaction_id": txn.id,
                "date": txn.date.isoformat(),
                "source_type": txn.source_type,
                "source_id": txn.source_id,
                "reference": txn.reference or "",
                "description": ln.description or txn.description or "",
                "account_name": acct.name,
                "kind": kind,
                "amount": float(amt),
                "is_billable": bool(ln.is_billable),
            }
        )
    for code_id, amt in committed_rows:
        k = key_for(
            code_id, code_by_id[code_id].cost_type if code_id in code_by_id else None
        )
        fig.setdefault(k, _blank())["committed"] += Decimal(str(amt))
    job_level = _blank()
    type_level: dict[str, dict] = {}
    for b in budgets:
        if b.cost_code_id:
            k = (
                ("code", b.cost_code_id)
                if b.cost_code_id in code_by_id
                else ("uncoded", "other")
            )
            f = fig.setdefault(k, _blank())
        elif b.cost_type:
            f = type_level.setdefault(b.cost_type, _blank())
        else:
            f = job_level
        if b.source == "change":
            f["changes"] += Decimal(str(b.amount))
        else:
            f["original"] += Decimal(str(b.amount))
        f["est_revenue"] += Decimal(str(b.revenue_amount or 0))

    # build the code tree per cost type with roll-ups
    def node_for(n) -> dict:
        c = n["code"]
        own = fig.get(("code", c.id), _blank())
        children = [node_for(ch) for ch in n["children"]]
        agg = {
            k: own[k] + sum((ch["_raw"][k] for ch in children), Decimal("0"))
            for k in own
        }
        return {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "label": c.label,
            "cost_type": c.cost_type,
            "is_active": c.is_active,
            "own": _finish(own),
            "figures": _finish(agg),
            "lines": line_rows.get(("code", c.id), []),
            "children": children,
            "_raw": agg,
        }

    tree = code_tree(codes)
    type_nodes = []
    grand = _blank()
    for t in ordered_types:
        roots = [node_for(n) for n in tree if n["code"].cost_type == t.code]
        uncoded_own = fig.get(("uncoded", t.code), _blank())
        agg = {
            k: uncoded_own[k] + sum((r["_raw"][k] for r in roots), Decimal("0"))
            for k in uncoded_own
        }
        tl = type_level.get(t.code)
        if tl:
            for k in ("original", "changes", "est_revenue"):
                agg[k] += tl[k]
        has_anything = (
            roots or any(v for v in agg.values()) or line_rows.get(("uncoded", t.code))
        )
        if not has_anything:
            continue
        for k in grand:
            grand[k] += agg[k]
        type_nodes.append(
            {
                "cost_type": t.code,
                "name": t.name,
                "is_labor": bool(t.is_labor),
                "figures": _finish(agg),
                "codes": roots,
                "uncoded": {
                    "figures": _finish(uncoded_own),
                    "lines": line_rows.get(("uncoded", t.code), []),
                },
            }
        )
    # codes whose type is unknown to the table still show up
    for k in job_level:
        grand[k] += job_level[k]

    def strip(node):
        node.pop("_raw", None)
        for ch in node.get("children", []):
            strip(ch)

    for t in type_nodes:
        for r in t["codes"]:
            strip(r)
    return {
        "job_id": job_id,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "types": type_nodes,
        "job_level_budget": _finish(job_level),
        "totals": _finish(grand),
    }


def budget_vs_actual_all_jobs(
    db: Session,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[int] = None,
    include_inactive: bool = False,
) -> list[dict]:
    """One row per job with the headline figures, for the report card."""
    from app.services.jobs_service import committed_cost, job_profitability

    q = db.query(Job)
    if not include_inactive:
        q = q.filter(Job.is_active.is_(True))
    if customer_id:
        q = q.filter(Job.customer_id == customer_id)
    jobs = q.all()
    prof = {
        r["job_id"]: r
        for r in job_profitability(db, start_date, end_date, include_no_job=False)
    }
    committed = committed_cost(db)
    budgets = (
        db.query(JobBudget).filter(JobBudget.job_id.in_([j.id for j in jobs])).all()
        if jobs
        else []
    )
    bsum: dict[int, dict] = {}
    for b in budgets:
        f = bsum.setdefault(b.job_id, _blank())
        if b.source == "change":
            f["changes"] += Decimal(str(b.amount))
        else:
            f["original"] += Decimal(str(b.amount))
        f["est_revenue"] += Decimal(str(b.revenue_amount or 0))
    out = []
    for j in sorted(
        jobs,
        key=lambda x: (x.customer.name.lower() if x.customer else "", x.name.lower()),
    ):
        f = bsum.get(j.id, _blank())
        p = prof.get(j.id, {})
        f["actual"] = Decimal(str(p.get("total_costs", 0)))
        f["act_revenue"] = Decimal(str(p.get("income", 0)))
        f["committed"] = Decimal(str(committed.get(j.id, 0)))
        fin = _finish(f)
        fin.update(
            {
                "job_id": j.id,
                "job_name": j.name,
                "customer_name": j.customer.name if j.customer else "",
                "status": j.status,
                "contract_amount": (
                    float(j.contract_amount) if j.contract_amount else None
                ),
            }
        )
        out.append(fin)
    return out
