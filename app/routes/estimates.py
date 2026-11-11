# ============================================================================
# Estimates — "Convert to Invoice" deep-copies every field and line item,
# then marks the estimate CONVERTED. PDFs are rendered with WeasyPrint.
# ============================================================================

from app.routes._print import print_html
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy.exc import IntegrityError
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload, object_session, selectinload

from app.database import get_db
from app.routes.invoices.helpers import (
    _due_date_from_terms,
    _post_invoice_journal,
    confirm_credit_limit,
    confirm_zero_total,
    opening_status,
    resolve_line_taxable,
)
from app.routes._helpers import (
    clamp_pagination,
    refuse_unknown_filters,
    reject_null_updates,
)
from app.routes.invoice_messages import default_message_body
from app.services.audit import audited_bulk_delete
from app.services.payment_terms import DocumentTerms, term_id_for
from app.services.job_costing import line_cost_type, budgets_from_estimate
from app.services.estimating import (
    estimate_totals,
    estimate_document_totals,
    is_included_line,
    presentation_style,
    priced_rate,
    uncoded_lines,
    uncoded_warning,
    markup_parts,
)
from app.services.sub_customers import invoice_addresses
from app.services.terminology import terms_from_db
from app.models.estimates import Estimate, EstimateLine, EstimateStatus
from app.models.invoices import Invoice, InvoiceLine
from app.models.contacts import Customer
from app.models.job_costing import JobBudget
from app.models.jobs import Job, SovLine
from app.models.pay_applications import PayApplication
from app.schemas.estimates import (
    EstimateCreate,
    EstimateUpdate,
    EstimateResponse,
    EstimateAccept,
    EstimateLost,
    totals_from_dict,
)
from app.schemas.common import StrictModel
from app.schemas.invoices import InvoiceResponse, ZeroTotalConfirmation
from app.services.pdf_service import generate_estimate_pdf
from app.services.numbering import next_estimate_number, next_invoice_number
from app.services.settings_service import get_all_settings as get_settings, set_setting
from app.services.request_utils import content_disposition
from app.services.accounting import (
    _q,
    compute_line_totals,
)
from app.services.jobs_service import (
    own_job,
    refuse_other_customers_jobs,
    refuse_other_customers_jobs_on_edit,
)

router = APIRouter(prefix="/api/estimates", tags=["estimates"])


_ADDRESS_PARTS = ("address1", "address2", "city", "state", "zip")

# The one message a settled (accepted/converted) estimate gives when an edit
# tries to re-price or re-scope it — its numbers are the locked budget (#551).
_SETTLED_MSG = "A settled estimate can't be repriced or re-scoped; make a new one or a change order."

# Fields frozen once an estimate is settled (notes/expiration stay editable).
_FROZEN_ON_SETTLE = {
    "customer_id",
    "job_id",
    "tax_rate",
    "gc_pct",
    "insurance_pct",
    "fee_pct",
    "bond_pct",
    "markup_allowances",
}


def _customer_bill_to(customer) -> dict:
    return {f"bill_{k}": getattr(customer, f"bill_{k}", None) for k in _ADDRESS_PARTS}


_MARKUP_PCTS = ("gc_pct", "insurance_pct", "fee_pct", "bond_pct")


def _enforce_at_cost(lines, is_construction: bool) -> list[str]:
    """The construction pricing rule, driven by the line alone. A base or
    alternate line that carries a unit cost is priced from it: at cost (#551)
    when it has no markup of its own, else unit cost × (1 + markup_pct / 100)
    to the rate's four places (#774). Allowances and unit-price lines keep
    their rate. Any other line keeps the markup it carries without being
    priced by it (no unit cost, an allowance or unit price, or a Business or
    Nonprofit estimate, whose rate is what they charge), so a company that
    switches type and back, or a line whose kind goes and comes back, still
    has the rule that priced it. Returns a sentence for each line sent with a
    price a cent or more above its cost and no markup, which is priced at
    cost: a client written before markup_pct existed is told rather than
    repriced quietly."""
    warnings = []
    for n, ln in enumerate(lines, start=1):
        priced = (
            is_construction
            and ln.line_kind in ("base", "alternate")
            and ln.unit_cost is not None
        )
        if not priced:
            continue
        # a rate left off is 0, never above cost, so it never warns
        sent = ln.rate
        ln.rate = priced_rate(ln.unit_cost, ln.markup_pct)
        if ln.markup_pct is None and _q(abs(Decimal(str(sent)))) > _q(abs(ln.rate)):
            warnings.append(
                f"Line {n} came with no markup, so it is priced at its cost "
                f"({ln.rate:,.2f}), not the {sent:,.2f} sent. Send its "
                "markup_pct to price it above cost."
            )
    return warnings


def _locked_a_budget(db: Session, estimate: Estimate) -> bool:
    """True when the estimate is settled AND its acceptance locked a job
    budget: a construction company, a job, and budget rows locked to this
    estimate. Only then is it frozen (#551)."""
    if estimate.status not in (EstimateStatus.ACCEPTED, EstimateStatus.CONVERTED):
        return False
    if estimate.job_id is None or not terms_from_db(db).is_construction:
        return False
    return (
        db.query(JobBudget.id)
        .filter(JobBudget.estimate_id == estimate.id, JobBudget.locked.is_(True))
        .first()
        is not None
    )


def _frozen_changes(db: Session, estimate: Estimate, changes: dict) -> set:
    """The frozen header fields an edit actually changes: a value sent back
    the same as stored (the form resends every field) is not a change."""
    touched = set()
    for name in _FROZEN_ON_SETTLE & set(changes):
        sent, stored = changes[name], getattr(estimate, name)
        if isinstance(stored, (int, float, Decimal)) and not isinstance(stored, bool):
            if sent is None or Decimal(str(sent)) != Decimal(str(stored)):
                touched.add(name)
        elif sent != stored:
            touched.add(name)
    return touched


_LINE_FIELDS = (
    "item_id",
    "quantity",
    "rate",
    "class_name",
    "job_id",
    "cost_code_id",
    "cost_type",
    "unit_cost",
    "markup_pct",
    "trade_package_id",
    "line_kind",
    "unit",
    "alternate_accepted",
    "is_taxable",
)


def _line_key(line) -> tuple:
    """A line's content as the store keeps it: numbers as Decimals, an empty
    description as none, the same normal form from a request or a stored row."""
    key = []
    for name in _LINE_FIELDS:
        val = getattr(line, name)
        if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
            val = Decimal(str(val))
        key.append(val)
    key.append(getattr(line, "description", None) or "")
    return tuple(key)


def _lines_changed(db: Session, estimate: Estimate, sent_lines) -> bool:
    """True when the sent lines differ from the stored ones. The sent lines
    get the same settling a save gives them (tax flag, at-cost rate, cost
    type) on copies, so an unchanged form payload compares equal."""
    sent = [ln.model_copy() for ln in sent_lines]
    resolve_line_taxable(db, sent, db.get(Customer, estimate.customer_id))
    _enforce_at_cost(sent, terms_from_db(db).is_construction)
    for ln in sent:
        ln.cost_type = line_cost_type(db, ln.cost_code_id, ln.cost_type)
    stored = sorted(estimate.lines, key=lambda ln: (ln.line_order or 0, ln.id))
    return [_line_key(ln) for ln in sent] != [_line_key(ln) for ln in stored]


def _pay_app_job_ids(db: Session, job_ids) -> set:
    """Of these jobs, the ones that bill by pay application: a contract type, a
    schedule of values, or any pay application (#552, #555). Converting an
    estimate on one would post the whole contract a second time."""
    ids = {j for j in job_ids if j is not None}
    if not ids:
        return set()
    found = {
        row[0]
        for row in db.query(Job.id)
        .filter(Job.id.in_(ids), Job.contract_type.isnot(None))
        .all()
    }
    for model in (SovLine, PayApplication):
        found |= {
            row[0]
            for row in db.query(model.job_id).filter(model.job_id.in_(ids)).distinct()
        }
    return found


def _estimate_response(est: Estimate) -> EstimateResponse:
    """A single estimate's response: the customer name, each line's package
    name, and the bid math (#551). The list endpoint skips the totals."""
    resp = EstimateResponse.model_validate(est)
    if est.customer:
        resp.customer_name = est.customer.name
    if est.job is not None:
        resp.job_name = est.job.name
        resp.job_site_address = est.job.site_address
    by_line = {ln.id: ln for ln in est.lines}
    for line_resp in resp.lines:
        line = by_line.get(line_resp.id)
        if line is not None and line.trade_package is not None:
            line_resp.trade_package_name = line.trade_package.name
    resp.totals = totals_from_dict(estimate_totals(est))
    resp.uncoded_lines = uncoded_lines(est)
    db = object_session(est)
    is_construction = db is not None and terms_from_db(db).is_construction
    resp.bills_by_pay_app = bool(
        is_construction
        and est.job_id is not None
        and _pay_app_job_ids(db, [est.job_id])
    )
    if is_construction:
        resp.style = presentation_style(est, get_settings(db).get("estimate_style"))
    return resp


def _accept_estimate(db: Session, estimate: Estimate, job_id: Optional[int]):
    """Accept an estimate (#551): lock its job's budget to it. Shared by the
    POST /accept endpoint and a PUT that sets status=accepted, so there is one
    code path. The budget is seeded (manual rows win) and the estimate's own
    rows are marked locked; from then on only a change order moves them."""
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=409, detail="Estimate already converted")
    if estimate.status == EstimateStatus.ACCEPTED:
        raise HTTPException(status_code=409, detail="Estimate already accepted")
    job_id = job_id if job_id is not None else estimate.job_id
    if job_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "no_job", "message": "Accepting needs a job to budget."},
        )
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.customer_id != estimate.customer_id:
        raise HTTPException(
            status_code=422, detail="Job belongs to a different customer"
        )
    # A budget already locked by a DIFFERENT estimate stays its contract.
    other_lock = (
        db.query(JobBudget)
        .filter(
            JobBudget.job_id == job.id,
            JobBudget.locked.is_(True),
            JobBudget.estimate_id != estimate.id,
        )
        .first()
    )
    if other_lock is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "budget_locked",
                "message": "This job's budget is already locked to another estimate.",
            },
        )

    estimate.job_id = job.id
    estimate.status = EstimateStatus.ACCEPTED
    estimate.accepted_at = datetime.now(timezone.utc)
    # The job is won: a pending job is awarded (the first approved pay
    # application moves it on to in progress).
    if job.status == "pending":
        job.status = "awarded"
    budgets_from_estimate(db, job, estimate)
    # Lock exactly the rows this acceptance seeded; manual rows stay editable.
    db.query(JobBudget).filter(
        JobBudget.job_id == job.id,
        JobBudget.source == "estimate",
        JobBudget.estimate_id == estimate.id,
    ).update({JobBudget.locked: True}, synchronize_session=False)


@router.get(
    "",
    response_model=list[EstimateResponse],
    dependencies=[Depends(refuse_unknown_filters)],
)
def list_estimates(
    status: str = None,
    customer_id: int = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load .customer and .lines to avoid N+1 during model_validate.
    q = db.query(Estimate).options(
        joinedload(Estimate.customer),
        selectinload(Estimate.lines),
    )
    if status:
        # "lost" is the bid log's word for a rejected estimate
        word = "rejected" if status == "lost" else status
        if word not in [s.value for s in EstimateStatus]:
            raise HTTPException(
                status_code=422,
                detail="status must be one of pending, accepted, lost, converted.",
            )
        q = q.filter(Estimate.status == word)
    if customer_id:
        q = q.filter(Estimate.customer_id == customer_id)
    estimates = (
        q.order_by(Estimate.date.desc(), Estimate.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    pay_app_jobs = (
        _pay_app_job_ids(db, [e.job_id for e in estimates])
        if terms_from_db(db).is_construction
        else set()
    )
    results = []
    for est in estimates:
        resp = EstimateResponse.model_validate(est)
        if est.customer:
            resp.customer_name = est.customer.name
        resp.bills_by_pay_app = est.job_id in pay_app_jobs
        results.append(resp)
    return results


@router.get("/{estimate_id}", response_model=EstimateResponse)
def get_estimate(estimate_id: int, db: Session = Depends(get_db)):
    est = (
        db.query(Estimate)
        .options(
            joinedload(Estimate.customer),
            # the lines' package feeds both trade_package_name and the bid
            # math's by_package grouping, so load it in one go (#551)
            selectinload(Estimate.lines).joinedload(EstimateLine.trade_package),
        )
        .filter(Estimate.id == estimate_id)
        .first()
    )
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    return _estimate_response(est)


@router.post("", response_model=EstimateResponse, status_code=201)
def create_estimate(data: EstimateCreate, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    # The job, on the header or a line, is this customer's (NEW-36).
    refuse_other_customers_jobs(db, customer.id, data.job_id, data.lines, "estimate")

    cust_id = customer.id
    # The estimate is addressed to the customer, as an invoice is; it was
    # never filled, so its PDF and the invoice it became had no address.
    bill_to = _customer_bill_to(customer)
    resolve_line_taxable(db, data.lines, customer)
    # The markups are a construction-mode field: a Business or Nonprofit
    # estimate stores 0 whatever an API caller sent, so its totals, PDF and
    # the pure bid math all agree without a company gate inside the math.
    is_construction = terms_from_db(db).is_construction
    if not is_construction:
        for name in _MARKUP_PCTS:
            setattr(data, name, Decimal("0"))
        data.markup_allowances = False
        data.show_markup_rows = True
        data.terms = None
    elif data.terms is None:
        # a new construction estimate starts with the company's default terms
        data.terms = get_settings(db).get("estimate_terms") or None
    # Construction lines are priced from their cost (at cost, or plus their
    # own markup) before anything is totalled (#551, #774).
    priced_warnings = _enforce_at_cost(data.lines, is_construction)
    # The document totals cover the bought scope and the four markups: lines
    # at cost, the markups under the subtotal, tax on both. A unit-price line
    # is a rate card and an un-accepted alternate is not in the bid, so the
    # helper leaves them out (#551).
    subtotal, tax_amount, total = estimate_document_totals(
        data.lines,
        data.tax_rate,
        data.gc_pct,
        data.insurance_pct,
        data.fee_pct,
        data.bond_pct,
        data.markup_allowances,
    )

    estimate = None
    estimate_number = None
    last_err = None
    # Same race as create_invoice: next_estimate_number's check-then-insert
    # window lets two concurrent creates pick the same number. Retry on
    # IntegrityError; the UNIQUE constraint is the safety net.
    for _ in range(10):
        estimate_number = next_estimate_number(db)
        estimate = Estimate(
            estimate_number=estimate_number,
            customer_id=cust_id,
            date=data.date,
            expiration_date=data.expiration_date,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=data.notes,
            class_id=data.class_id,
            job_id=data.job_id,
            gc_pct=data.gc_pct,
            insurance_pct=data.insurance_pct,
            fee_pct=data.fee_pct,
            bond_pct=data.bond_pct,
            markup_allowances=data.markup_allowances,
            show_markup_rows=data.show_markup_rows,
            terms=data.terms,
            **bill_to,
        )
        db.add(estimate)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "estimate_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            estimate = None
    if estimate is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique estimate number; please retry.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = EstimateLine(
            estimate_id=estimate.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            class_name=line_data.class_name,
            job_id=line_data.job_id,
            cost_code_id=line_data.cost_code_id,
            cost_type=line_cost_type(db, line_data.cost_code_id, line_data.cost_type),
            unit_cost=line_data.unit_cost,
            markup_pct=line_data.markup_pct,
            trade_package_id=line_data.trade_package_id,
            line_kind=line_data.line_kind,
            unit=line_data.unit,
            alternate_accepted=line_data.alternate_accepted,
            is_taxable=(
                line_data.is_taxable if line_data.is_taxable is not None else True
            ),
            line_order=i,
        )
        db.add(line)

    numeric_part = estimate_number.removeprefix(
        get_settings(db).get("estimate_prefix", "E-")
    )
    if numeric_part.isdigit():
        set_setting(db, "estimate_next_number", str(int(numeric_part) + 1))

    db.commit()
    db.refresh(estimate)
    resp = _estimate_response(estimate)
    resp.warnings = priced_warnings
    return resp


@router.put("/{estimate_id}", response_model=EstimateResponse)
def update_estimate(
    estimate_id: int, data: EstimateUpdate, db: Session = Depends(get_db)
):
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")

    priced_warnings: list[str] = []
    changes = reject_null_updates(
        Estimate, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    # Accepting through a PUT takes the one accept code path, after the other
    # changes (a new job on the same request) land (#551).
    accepting = changes.get("status") == EstimateStatus.ACCEPTED
    new_status = changes.get("status")
    if accepting:
        changes.pop("status")

    # A settled estimate (accepted or converted) that locked a job budget is
    # the basis of that budget, so its scope and price are frozen: a changed
    # line, markup, customer, job or tax is refused (notes and the expiration
    # date still edit, values sent back unchanged are fine, and re-setting
    # status=accepted is a no-op). Change it with a new estimate or, later, a
    # change order (#551). Business and Nonprofit estimates lock no budget and
    # keep editing freely.
    if _locked_a_budget(db, estimate):
        touched = _frozen_changes(db, estimate, changes)
        # moving the status off accepted/converted would let the next accept
        # re-seed the locked budget; re-setting the same status is a no-op
        if new_status is not None and new_status != estimate.status:
            touched.add("status")
        if data.lines is not None and _lines_changed(db, estimate, data.lines):
            touched.add("lines")
        if touched:
            raise HTTPException(status_code=409, detail=_SETTLED_MSG)

    if changes.get("customer_id") not in (None, estimate.customer_id):
        customer = db.get(Customer, changes["customer_id"])
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        # a different customer, so their address
        changes.update(_customer_bill_to(customer))
    # What the edit changes — the customer, the header's job, a line's job —
    # must leave the estimate on its customer's jobs (NEW-36); what it
    # leaves alone is not judged.
    refuse_other_customers_jobs_on_edit(db, estimate, changes, data.lines, "estimate")
    for key, val in changes.items():
        setattr(estimate, key, val)

    # The markups are a construction-mode field: a Business or Nonprofit
    # estimate keeps them at 0 whatever an API caller sent (#551).
    is_construction = terms_from_db(db).is_construction
    if not is_construction:
        for name in _MARKUP_PCTS:
            setattr(estimate, name, Decimal("0"))
        estimate.markup_allowances = False
        estimate.show_markup_rows = True
        estimate.terms = None

    if data.lines is not None:
        # Each line's tax is settled before it is stored, the way create does
        # it: a non-taxable customer's lines are never taxable, and a line
        # sent without a flag takes its item's. The flags used to be stored
        # as sent (a missing one as taxable) and settled only for the
        # totals, so an edit stored taxable lines on an exempt customer's
        # estimate, and a non-taxable item's line stored as taxable was
        # taxed on the invoice the estimate became (found integrating the
        # 2.17.3 exploratory fixes). The customer is the one the estimate
        # has after this edit.
        resolve_line_taxable(db, data.lines, db.get(Customer, estimate.customer_id))
        # construction lines are priced from their cost before they are
        # stored (#551, #774)
        priced_warnings = _enforce_at_cost(data.lines, is_construction)
        # each old line leaves a DELETE event with its amounts (#289)
        audited_bulk_delete(
            db, db.query(EstimateLine).filter(EstimateLine.estimate_id == estimate_id)
        )
        for i, line_data in enumerate(data.lines):
            line = EstimateLine(
                estimate_id=estimate_id,
                item_id=line_data.item_id,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=_q(
                    Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))
                ),
                class_name=line_data.class_name,
                job_id=line_data.job_id,
                cost_code_id=line_data.cost_code_id,
                cost_type=line_cost_type(
                    db, line_data.cost_code_id, line_data.cost_type
                ),
                unit_cost=line_data.unit_cost,
                markup_pct=line_data.markup_pct,
                # the bid shape rides the edit too (#551): without these a PUT
                # stored every line as a base/unassigned line and dropped an
                # accept, while the totals were computed from the sent kinds
                trade_package_id=line_data.trade_package_id,
                line_kind=line_data.line_kind,
                unit=line_data.unit,
                alternate_accepted=line_data.alternate_accepted,
                is_taxable=(
                    line_data.is_taxable if line_data.is_taxable is not None else True
                ),
                line_order=i,
            )
            db.add(line)

        tax_rate = data.tax_rate if data.tax_rate is not None else estimate.tax_rate
        subtotal, tax_amount, total = estimate_document_totals(
            data.lines,
            tax_rate,
            estimate.gc_pct,
            estimate.insurance_pct,
            estimate.fee_pct,
            estimate.bond_pct,
            estimate.markup_allowances,
        )
        estimate.subtotal = subtotal
        estimate.tax_amount = tax_amount
        estimate.total = total
    elif any(
        k in changes
        for k in ("tax_rate", "customer_id", "markup_allowances", *_MARKUP_PCTS)
    ):
        # A new rate, customer or markup percent without resending the lines:
        # the stored lines are re-totalled (a rate- or markup-only edit left
        # the old tax and total in place), and a non-taxable customer's
        # exemption covers them.
        stored = list(estimate.lines)
        if "customer_id" in changes:
            resolve_line_taxable(db, stored, db.get(Customer, estimate.customer_id))
        subtotal, tax_amount, total = estimate_document_totals(
            stored,
            estimate.tax_rate,
            estimate.gc_pct,
            estimate.insurance_pct,
            estimate.fee_pct,
            estimate.bond_pct,
            estimate.markup_allowances,
        )
        estimate.subtotal = subtotal
        estimate.tax_amount = tax_amount
        estimate.total = total

    if accepting:
        db.flush()
        # Lock the job budget only when a job resolves and this is the first
        # acceptance; without a job it is a plain status change (a business
        # company has no job), and re-setting accepted is a no-op (#551).
        if estimate.job_id is not None and estimate.status != EstimateStatus.ACCEPTED:
            _accept_estimate(db, estimate, estimate.job_id)
        else:
            estimate.status = EstimateStatus.ACCEPTED

    db.commit()
    db.refresh(estimate)
    resp = _estimate_response(estimate)
    resp.warnings = priced_warnings
    return resp


@router.post("/{estimate_id}/accept", response_model=EstimateResponse)
def accept_estimate(
    estimate_id: int,
    data: Optional[EstimateAccept] = None,
    db: Session = Depends(get_db),
):
    """Accept the estimate and lock its job's budget to it (#551). Needs a
    job (on the body or the estimate); refuses an already accepted/converted
    estimate, or a job whose budget another estimate already locked."""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    _accept_estimate(db, estimate, data.job_id if data else None)
    db.commit()
    db.refresh(estimate)
    resp = _estimate_response(estimate)
    # Accept goes ahead; lines with no code budget on none, and it says so.
    resp.warnings = [w for w in (uncoded_warning(resp.uncoded_lines),) if w]
    return resp


@router.post("/{estimate_id}/lost", response_model=EstimateResponse)
def mark_estimate_lost(
    estimate_id: int,
    data: Optional[EstimateLost] = None,
    db: Session = Depends(get_db),
):
    """Mark a bid lost: status rejected, with an optional reason kept in the
    notes. An accepted or converted estimate won the job and can't be lost."""
    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status in (EstimateStatus.ACCEPTED, EstimateStatus.CONVERTED):
        raise HTTPException(
            status_code=409,
            detail="An accepted or converted estimate won the job; it can't be marked lost.",
        )
    if estimate.status == EstimateStatus.REJECTED:
        raise HTTPException(status_code=409, detail="Estimate is already marked lost")
    estimate.status = EstimateStatus.REJECTED
    reason = (data.reason or "").strip() if data else ""
    if reason:
        line = f"Lost {date.today().isoformat()}: {reason}"
        estimate.notes = f"{estimate.notes}\n{line}" if estimate.notes else line
    db.commit()
    db.refresh(estimate)
    return _estimate_response(estimate)


@router.get("/{estimate_id}/pdf")
def estimate_pdf(estimate_id: int, db: Session = Depends(get_db)):
    """Generate the estimate PDF."""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    pdf_bytes = generate_estimate_pdf(est, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"Estimate_{est.estimate_number}.pdf"
            )
        },
    )


@router.get("/{estimate_id}/print-preview")
def estimate_print_preview(estimate_id: int, db: Session = Depends(get_db)):
    """Render estimate as HTML page for browser print dialog (window.print())"""
    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    company = get_settings(db)
    from fastapi.responses import HTMLResponse

    from app.services.pdf_service import _render

    # The PDF's own renderer: one set of filters and helpers for both.
    html_str = _render("estimate_pdf.html", company, est=est)
    html_str = print_html(html_str)
    return HTMLResponse(content=html_str)


class _EmailEstimateRequest(StrictModel):
    recipient: Optional[str] = None
    subject: Optional[str] = None
    message: Optional[str] = None
    cc: Optional[str] = Field(default=None, max_length=2000)
    bcc: Optional[str] = Field(default=None, max_length=2000)


@router.post("/{estimate_id}/email")
def email_estimate(
    estimate_id: int,
    data: _EmailEstimateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Email the estimate PDF as an attachment, by the path an invoice uses:
    the same address checks, the same send_email (EmailLog row, always-copy
    settings) and the same 502 when SMTP is not set up."""
    import html as _html

    from app.routes._roles import is_admin
    from app.services.email_errors import where_to_see_why
    from app.services.email_service import (
        _get_smtp_settings,
        bad_addresses,
        copy_lists,
        send_email,
    )

    est = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not est:
        raise HTTPException(status_code=404, detail="Estimate not found")
    recipient = (data.recipient or "").strip() or (
        (est.customer.email or "").strip() if est.customer else ""
    )
    if not recipient:
        raise HTTPException(
            status_code=400,
            detail=(
                "This estimate has no email address to send to. Add one to the "
                "customer or type one here."
            ),
        )
    rejected = bad_addresses(data.cc) + bad_addresses(data.bcc)
    if rejected:
        raise HTTPException(
            status_code=400,
            detail="Not an email address: " + ", ".join(rejected[:5]) + ".",
        )
    try:
        copy_lists(_get_smtp_settings(db), data.cc, data.bcc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    company = get_settings(db)
    name = (company.get("display_name") or company.get("company_name") or "").strip()
    subject = (data.subject or "").strip() or (
        f"Estimate #{est.estimate_number}" + (f" from {name}" if name else "")
    )
    note = (data.message or "").strip()
    body = (f"<p>{_html.escape(note)}</p>\n" if note else "") + (
        f"<p>Please find estimate #{_html.escape(est.estimate_number)} attached.</p>"
        + (f"<p>{_html.escape(name)}</p>" if name else "")
    )
    sent = send_email(
        db=db,
        to_email=recipient,
        subject=subject,
        html_body=body,
        attachment_bytes=generate_estimate_pdf(est, company),
        attachment_name=f"Estimate_{est.estimate_number}.pdf",
        entity_type="estimate",
        entity_id=est.id,
        cc=data.cc,
        bcc=data.bcc,
    )
    # send_email() returns False (and logs why) when SMTP is not set up or the
    # send fails; reporting "sent" regardless would be a lie.
    if not sent:
        admin = is_admin(request)
        detail = "Email could not be sent. " + (
            "Check the SMTP settings under Settings → Email (SMTP). " if admin else ""
        )
        raise HTTPException(status_code=502, detail=detail + where_to_see_why(admin))
    return {"status": "sent"}


@router.post("/{estimate_id}/convert", response_model=InvoiceResponse)
def convert_to_invoice(
    estimate_id: int,
    data: Optional[ZeroTotalConfirmation] = None,
    db: Session = Depends(get_db),
):
    """Convert to invoice — deep-copies all fields and lines."""
    from app.services.closing_date import check_closing_date

    estimate = db.query(Estimate).filter(Estimate.id == estimate_id).first()
    if not estimate:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if estimate.status == EstimateStatus.CONVERTED:
        raise HTTPException(status_code=400, detail="Estimate already converted")
    # A contract job bills by pay application, with retainage; the whole bid
    # as an invoice would post the contract's income a second time.
    if (
        estimate.job_id is not None
        and terms_from_db(db).is_construction
        and _pay_app_job_ids(db, [estimate.job_id])
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "job_bills_by_pay_application",
                "message": (
                    "This job bills by pay application; converting the "
                    "estimate would bill the contract a second time. Bill it "
                    "from the job's Pay apps tab."
                ),
            },
        )
    # The invoice is dated the day it is made, as QuickBooks does: keeping
    # the estimate's date back-dated the sale and its due date (2.17.3
    # exploratory W-L10). Its journal posts on that date, so that is the
    # date the closing lock is checked against.
    today = date.today()
    check_closing_date(db, today)

    # A new invoice gets the customer's CURRENT tax treatment, computed —
    # not the estimate's stored tax (see taxed_copy_lines).
    from types import SimpleNamespace

    from app.services.accounting import taxed_copy_lines

    # The bid's own lines only: unit prices are a rate card and an alternate
    # becomes scope only once accepted, so neither converts (#551).
    kept = [ln for ln in estimate.lines if is_included_line(ln)]
    copied = taxed_copy_lines(kept, estimate.customer)
    # The four markups ride the invoice as their own lines under the copied
    # scope, so the invoice foots to the bid (#551): one line per non-zero
    # markup, split into the part on taxable lines and the rest as the
    # estimate taxes them (estimating.markup_parts); none is taxed when no
    # copied line is (an exempt customer).
    any_taxed = any(cline.is_taxable for cline in copied)
    markup_lines = [
        SimpleNamespace(
            description=part["description"],
            amount=part["amount"],
            is_taxable=part["is_taxable"] and any_taxed,
        )
        for part in markup_parts(estimate)
    ]
    for ml in markup_lines:
        ml.quantity = Decimal("1")
        ml.rate = ml.amount
    subtotal, tax_amount, total = compute_line_totals(
        list(copied) + markup_lines, estimate.tax_rate
    )
    confirm_zero_total(
        total,
        bool(data and data.allow_zero_total),
        "estimate",
        "converting it",
        question="This estimate adds up to $0.00. Convert it to an invoice anyway?",
    )
    from app.services.contact_balances import home_amount

    confirm_credit_limit(
        db,
        estimate.customer,
        home_amount(total, getattr(estimate, "exchange_rate", None)),
        bool(data and data.allow_over_credit_limit),
        "invoice",
        question=None,
        # the action at hand: Convert, not Save (2.31.1 gate, a round-3 note)
        ask="Convert it anyway?",
    )

    invoice_number = next_invoice_number(db)

    # Terms and due date as a new invoice for this customer gets them: the
    # customer's terms, else the company's (the form does the same).
    settings = get_settings(db)
    customer = estimate.customer
    terms = (customer.terms if customer else None) or settings.get(
        "default_terms", "Net 30"
    )
    due_date = _due_date_from_terms(db, today, terms)

    # The invoice is billed as a new one is, as of now (2.31.1 gate, W-80):
    # the Bill To whole is the payer's, the street with the name and
    # country, so a tenant billed with its property gets the property's
    # street under the property's name and "For: the tenant"; the ship-to
    # is the customer's. (The estimate keeps its own copy of the customer's
    # street, as it was saved.) The notes are the estimate's, else the
    # company's default invoice notes; the converted invoice printed with
    # no address and without the default notes (2.17.3 exploratory F21).
    address = invoice_addresses(db, customer) if customer else {}

    # The copy carries the customer's jobs only; another customer's job
    # stored on the estimate (books from before the rule) is left off,
    # and the response says which (NEW-36).
    left_off: list[str] = []
    header_job_id = own_job(db, estimate.customer_id, estimate.job_id, left_off)
    # The markup lines carry the header's job and the scope's class, so their
    # income posts under the job (not "No job") in P&L by Job (#551).
    markup_class = next((ln.class_name for ln in kept if ln.class_name), None)
    invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=estimate.customer_id,
        status=opening_status(total),
        date=today,
        due_date=due_date,
        terms=terms,
        payment_term_id=term_id_for(db, terms),
        subtotal=subtotal,
        tax_rate=estimate.tax_rate,
        tax_amount=tax_amount,
        total=total,
        balance_due=total,
        class_id=estimate.class_id,
        job_id=header_job_id,
        # the estimate's own notes; else what a new invoice starts with (#392)
        notes=estimate.notes
        or default_message_body(db)
        or settings.get("invoice_notes")
        or None,
        **address,
    )
    db.add(invoice)
    db.flush()

    new_lines = []
    for eline, cline in zip(kept, copied):
        iline = InvoiceLine(
            invoice_id=invoice.id,
            item_id=eline.item_id,
            description=eline.description,
            quantity=eline.quantity,
            rate=eline.rate,
            amount=eline.amount,
            class_name=eline.class_name,
            job_id=own_job(db, estimate.customer_id, eline.job_id, left_off),
            cost_code_id=eline.cost_code_id,
            # the cost basis the estimate carried, kept on the invoice (#551)
            cost_type=eline.cost_type,
            unit_cost=eline.unit_cost,
            is_taxable=cline.is_taxable,
            line_order=eline.line_order,
        )
        db.add(iline)
        new_lines.append(iline)

    # Each non-zero markup becomes its own invoice line after the scope, so
    # the invoice's subtotal / tax / total foot to the bid; an item-less line
    # posts to the default income account (#551).
    next_order = max((ln.line_order for ln in kept), default=-1) + 1
    for ml in markup_lines:
        iline = InvoiceLine(
            invoice_id=invoice.id,
            item_id=None,
            description=ml.description,
            quantity=ml.quantity,
            rate=ml.rate,
            amount=_q(ml.amount),
            class_name=markup_class,
            job_id=header_job_id,
            cost_code_id=None,
            is_taxable=ml.is_taxable,
            line_order=next_order,
        )
        db.add(iline)
        new_lines.append(iline)
        next_order += 1

    estimate.status = EstimateStatus.CONVERTED
    estimate.converted_invoice_id = invoice.id

    # The posting a new invoice gets — DR A/R, CR income per line with the
    # line's job, class and cost code, CR sales tax — through the same
    # path as create and duplicate. The route's own builder posted the
    # header's job only, so a converted estimate's line-level jobs counted
    # under "No job" in P&L by Job (2.22.0 gate, found fixing NEW-36).
    txn = _post_invoice_journal(
        db, invoice, new_lines, customer.name if customer else ""
    )
    # A $0.00 conversion posts no journal, so it keeps no transaction.
    invoice.transaction_id = txn.id if txn is not None else None

    # Phase 11 (audit fix): estimate→invoice conversion is a NEW sale from
    # an accounting standpoint. Post inventory movements for each inventory
    # line so the ledger stays consistent with the A/R journal entry.
    db.flush()
    db.refresh(invoice)
    from app.services.inventory_hooks import post_sale_for_invoice

    post_sale_for_invoice(db, invoice, txn_date=today)

    db.commit()
    db.refresh(invoice)
    resp = InvoiceResponse.model_validate(invoice)
    DocumentTerms(db, "invoice").fill(resp, invoice)
    if invoice.customer:
        resp.customer_name = invoice.customer.name
    resp.jobs_left_off = left_off
    return resp
