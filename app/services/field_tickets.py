# ============================================================================
# Field tickets (construction mode) — the service behind app/routes/
# field_tickets.py and the Field ticket log report.
#
# A T&M ticket posts no cost: labor reaches the job through time entries and
# payroll, equipment through job-cost entries, material through bills, so a
# ticket that posted too would count the work twice. It is a quantity and
# billing record that becomes a change order or invoice lines. A material slip
# posts a Job Cost Entry; a delivery ticket moves received quantity on its PO
# lines. Money is Decimal, quantized to cents half-up.
# ============================================================================

import re
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.attachments import Attachment
from app.models.change_orders import ChangeOrder
from app.models.cost_codes import CostCode
from app.models.field_tickets import FieldTicket, FieldTicketLine
from app.models.items import Item
from app.models.job_costing import Equipment
from app.models.jobs import Job
from app.models.payroll import Employee
from app.services.job_costing import employee_cost_rate, line_cost_type
from app.services.terminology import terms_from_db

_ZERO = Decimal("0")
_CENT = Decimal("0.01")
# The columns a ticket's figures land in. SQLite keeps a figure past them
# without a word and every later read of the ticket failed, so they are
# checked before anything is saved: a line's amount (and the change order,
# invoice or job cost line it becomes) is Numeric(15, 2), and a PO line's
# received quantity Numeric(12, 4). Quantities and rates are capped by the
# schema (app/schemas/field_tickets.py).
MAX_AMOUNT = Decimal("9999999999999.99")
MAX_RECEIVED = Decimal("99999999.9999")
RATE_LIMIT = Decimal("100000000000")

_PREFIX = {"tm": "TM", "material": "MS", "delivery": "DT"}
KIND_LABELS = {
    "tm": "T&M ticket",
    "material": "Material slip",
    "delivery": "Delivery ticket",
}
# A line's cost type when it names none: its own type, else the code's.
_LINE_COST_TYPE = {"labor": "labor", "equipment": "equipment", "material": "material"}


def _q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _q_read(v) -> Decimal:
    """_q for a read: a stored figure too large to round (a ticket saved
    before the limits above) is shown as it is, not a 500 on the list."""
    try:
        return _q(v)
    except InvalidOperation:
        return _d(v)


def require_construction(db: Session) -> None:
    """Field tickets are construction mode's: a Business or Nonprofit company
    writes none (it reads an empty list)."""
    if not terms_from_db(db).is_construction:
        raise HTTPException(
            status_code=409,
            detail="Field tickets are part of construction mode. Set Company Type "
            "to Construction under Settings → Company to use them.",
        )


def next_ticket_number(db: Session, job: Job, kind: str) -> str:
    """<job number>-TM-001 / -MS-001 / -DT-001: each job and kind counts its
    own (a job with no number uses J<id>, as change orders do). The candidate
    is still checked against every number, since two jobs can share a job
    number."""
    stem = f"{job.job_number or 'J' + str(job.id)}-{_PREFIX[kind]}-"
    pattern = re.compile(re.escape(stem) + r"(\d+)$")
    n = 0
    for (number,) in db.query(FieldTicket.number).filter(FieldTicket.job_id == job.id):
        m = pattern.match(number or "")
        if m:
            n = max(n, int(m.group(1)))
    n += 1
    while True:
        candidate = f"{stem}{n:03d}"
        if (
            db.query(FieldTicket.id).filter(FieldTicket.number == candidate).first()
            is None
        ):
            return candidate
        n += 1


# ── Lines ───────────────────────────────────────────────────────────────────


def line_cost(ln: FieldTicketLine) -> Decimal:
    return _q_read(_d(ln.quantity) * _d(ln.cost_rate))


def line_bill(ln: FieldTicketLine) -> Optional[Decimal]:
    if ln.bill_rate is None:
        return None
    return _q_read(_d(ln.quantity) * _d(ln.bill_rate))


def ticket_totals(t: FieldTicket) -> tuple[Decimal, Optional[Decimal]]:
    """(cost total, bill total). The bill total is a T&M ticket's only."""
    cost = sum((line_cost(ln) for ln in t.lines), _ZERO)
    if t.kind != "tm":
        return cost, None
    bill = sum((line_bill(ln) or _ZERO for ln in t.lines), _ZERO)
    return cost, bill


def _get_or_404(db: Session, model, ident, what: str):
    if ident is None:
        return None
    row = db.get(model, ident)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


def _default_cost_rate(line, employee, equipment, item) -> Decimal:
    if line.line_type == "labor" and employee is not None:
        return employee_cost_rate(employee)
    if line.line_type == "equipment" and equipment is not None:
        return _d(equipment.hourly_rate)
    if item is not None:
        avg = _d(item.avg_cost)
        return avg if avg > 0 else _d(item.cost)
    return _ZERO


def _default_name(employee, equipment, item) -> Optional[str]:
    if employee is not None:
        return f"{employee.first_name} {employee.last_name}".strip()
    if equipment is not None:
        return equipment.name
    if item is not None:
        return item.name
    return None


def _check_po_line(db: Session, ticket: FieldTicket, po_line_id) -> None:
    """A T&M or material line that names a purchase order line names one that
    exists and is on this ticket's job (delivery lines are checked against the
    order on the ticket)."""
    if po_line_id is None or ticket.kind == "delivery":
        return
    from app.models.purchase_orders import PurchaseOrderLine

    pol = db.get(PurchaseOrderLine, po_line_id)
    if pol is None:
        raise HTTPException(status_code=404, detail="Purchase order line not found")
    job_id = pol.job_id or (pol.purchase_order.job_id if pol.purchase_order else None)
    if job_id != ticket.job_id:
        raise HTTPException(
            status_code=422, detail="That purchase order line is on another job."
        )


def set_lines(db: Session, ticket: FieldTicket, lines) -> None:
    """Replace a ticket's lines, filling the defaults: the ticket's cost code,
    a cost type from the line type, the cost rate from the employee /
    equipment / item, and (T&M) the bill rate as the cost rate with the job's
    change-order markup."""
    job = ticket.job or db.get(Job, ticket.job_id)
    markup = _d(job.co_markup_pct) if job is not None else _ZERO
    ticket.lines.clear()
    db.flush()
    for i, line in enumerate(lines):
        employee = _get_or_404(db, Employee, line.employee_id, "Employee")
        equipment = _get_or_404(db, Equipment, line.equipment_id, "Equipment")
        item = _get_or_404(db, Item, line.item_id, "Item")
        _check_po_line(db, ticket, line.po_line_id)
        code_id = line.cost_code_id or ticket.cost_code_id
        _get_or_404(db, CostCode, code_id, "Cost code")
        ctype = line_cost_type(db, None, line.cost_type) if line.cost_type else None
        if ctype is None:
            ctype = (
                _LINE_COST_TYPE.get(line.line_type)
                or line_cost_type(db, code_id, None)
                or "other"
            )
        cost_rate = (
            line.cost_rate
            if line.cost_rate is not None
            else _default_cost_rate(line, employee, equipment, item)
        )
        bill_rate = None
        if ticket.kind == "tm":
            bill_rate = (
                line.bill_rate
                if line.bill_rate is not None
                else (_d(cost_rate) * (1 + markup / 100)).quantize(Decimal("0.0001"))
            )
        ticket.lines.append(
            FieldTicketLine(
                line_order=i,
                line_type=line.line_type,
                employee_id=line.employee_id,
                equipment_id=line.equipment_id,
                item_id=line.item_id,
                po_line_id=line.po_line_id,
                description=line.description
                or _default_name(employee, equipment, item),
                quantity=line.quantity,
                unit=line.unit
                or ("hr" if line.line_type in ("labor", "equipment") else None),
                cost_rate=cost_rate,
                bill_rate=bill_rate,
                cost_code_id=code_id,
                cost_type=ctype,
            )
        )
    db.flush()


# ── Links: what a ticket turned into ────────────────────────────────────────


def live_change_order(db: Session, co: Optional[ChangeOrder]) -> Optional[ChangeOrder]:
    """The change order a ticket's work now sits on: a PCO that was revised is
    followed to its latest revision and one that was promoted to the owner CO
    it rolled into. None when that chain ends rejected or void (or the CO is
    gone): the ticket is free to price again."""
    end = chain_end(db, co)
    if end is None or end.status in ("rejected", "void", "superseded", "promoted"):
        return None
    return end


def chain_end(db: Session, co: Optional[ChangeOrder]) -> Optional[ChangeOrder]:
    """Follow a change order through its revisions and its promotion to the
    last one in the chain, whatever its status."""
    seen = set()
    while co is not None and co.id not in seen:
        seen.add(co.id)
        if co.status == "superseded":
            nxt = (
                db.query(ChangeOrder)
                .filter(ChangeOrder.supersedes_id == co.id)
                .order_by(ChangeOrder.id.desc())
                .first()
            )
        elif co.status == "promoted" and co.promoted_to_id:
            nxt = db.get(ChangeOrder, co.promoted_to_id)
        else:
            return co
        if nxt is None:
            return co
        co = nxt
    return co


def _tickets_on(db: Session, co: ChangeOrder, status: str) -> list[FieldTicket]:
    """The job's T&M tickets in `status` whose change order chain ends at
    `co` (a ticket priced on a PCO that was promoted into it, too)."""
    rows = (
        db.query(FieldTicket)
        .filter(
            FieldTicket.job_id == co.job_id,
            FieldTicket.kind == "tm",
            FieldTicket.status == status,
            FieldTicket.change_order_id.isnot(None),
        )
        .all()
    )
    return [
        t for t in rows if (end := chain_end(db, t.change_order)) and end.id == co.id
    ]


def close_for_change_order(db: Session, co: ChangeOrder) -> None:
    """An owner change order was approved: the signed tickets it prices are
    closed (their work is now in the contract)."""
    if co.kind != "owner":
        return
    for t in _tickets_on(db, co, "signed"):
        t.status = "closed"


def reopen_for_change_order(db: Session, co: ChangeOrder) -> None:
    """An approved owner change order was voided: its tickets go back to
    signed and are free to price or bill again."""
    if co.kind != "owner":
        return
    for t in _tickets_on(db, co, "closed"):
        t.status = "signed"


def release_invoice(db: Session, invoice) -> None:
    """An invoice was voided: the tickets it billed go back to signed, free to
    bill again."""
    for t in db.query(FieldTicket).filter(FieldTicket.invoice_id == invoice.id):
        if t.status == "closed":
            t.status = "signed"
        t.invoice_id = None


# ── T&M ticket → change order or invoice ───────────────────────────────────


def billable_tickets(db: Session, ticket_ids: list[int]) -> tuple[Job, list]:
    """The tickets to make a change order or an invoice from: T&M, signed,
    one job, not already on a live change order or invoice."""
    if not ticket_ids:
        raise HTTPException(status_code=422, detail="Pick at least one ticket.")
    tickets = []
    for tid in dict.fromkeys(ticket_ids):
        t = db.get(FieldTicket, tid)
        if t is None:
            raise HTTPException(status_code=404, detail="Field ticket not found")
        if t.kind != "tm":
            raise HTTPException(
                status_code=409,
                detail=f"{t.number} is not a T&M ticket; only T&M work is priced or billed.",
            )
        if t.status != "signed":
            raise HTTPException(
                status_code=409,
                detail=f"{t.number} is {t.status}; only a signed T&M ticket is "
                "priced or billed.",
            )
        live = live_change_order(db, t.change_order) if t.change_order_id else None
        if live is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{t.number} is already priced on change order {live.number}.",
            )
        if t.invoice is not None and _invoice_live(t.invoice):
            raise HTTPException(
                status_code=409,
                detail=f"{t.number} is already billed on invoice {t.invoice.invoice_number}.",
            )
        tickets.append(t)
    if len({t.job_id for t in tickets}) > 1:
        raise HTTPException(
            status_code=409, detail="The tickets are on different jobs; pick one job's."
        )
    return tickets[0].job, tickets


def _co_cost_type(t: FieldTicket, ln: FieldTicketLine) -> str:
    """A sub's ticket is subcontract cost to the contractor, whatever the sub's
    own line was; the contractor's own crew keeps the line's type."""
    return "subcontract" if t.commitment_id else (ln.cost_type or "other")


def _co_lines(tickets) -> list:
    from app.models.change_orders import ChangeOrderLine

    out = []
    for t in tickets:
        for ln in t.lines:
            if _d(ln.quantity) == 0:
                continue
            out.append(
                ChangeOrderLine(
                    line_order=len(out),
                    description=f"{t.number}: {ln.description or ln.line_type}",
                    cost_type=_co_cost_type(t, ln),
                    cost_code_id=ln.cost_code_id,
                    quantity=ln.quantity,
                    unit=ln.unit,
                    unit_cost=ln.cost_rate,
                )
            )
    return out


def make_change_order(db: Session, ticket_ids: list[int]) -> dict:
    """Price signed T&M tickets as one potential change order to the owner:
    a line per ticket line at cost, marked up by the job's change-order
    markup, each ticket linked to it. A sub's ticket (one with a commitment)
    also goes on a draft sub change order on that commitment, at cost, so
    the sub's contract rises with the owner's. The tickets close when the
    owner change order is approved."""
    from app.routes.change_orders import _create_numbered
    from app.services.change_orders import (
        _check_commitment,
        check_line_figures,
        recompute,
    )

    job, tickets = billable_tickets(db, ticket_ids)
    numbers = ", ".join(t.number for t in tickets)
    lines = _co_lines(tickets)
    if not lines:
        raise HTTPException(
            status_code=422, detail="The tickets have no lines with a quantity."
        )
    pco = _create_numbered(
        db,
        job,
        kind="pco",
        status="draft",
        title=f"T&M {numbers}"[:200],
        description="\n".join(t.description for t in tickets if t.description) or None,
        date=max(t.date for t in tickets),
        funding="contract",
        apply_markup=True,
    )
    for ln in lines:
        ln.change_order_id = pco.id
        db.add(ln)
    db.flush()
    db.refresh(pco)
    recompute(pco, job)
    check_line_figures(pco)  # several tickets can sum past what one CO holds

    subs = []
    by_commitment: dict[int, list] = {}
    for t in tickets:
        if t.commitment_id:
            by_commitment.setdefault(t.commitment_id, []).append(t)
    for commitment_id, group in by_commitment.items():
        po = group[0].commitment
        _check_commitment(po, pco)  # same job, still open
        sub = _create_numbered(
            db,
            job,
            kind="sub",
            status="draft",
            title=f"T&M {', '.join(t.number for t in group)} (under {pco.number})"[
                :200
            ],
            date=max(t.date for t in group),
            funding="contract",
            commitment_id=commitment_id,
        )
        for ln in _co_lines(group):
            ln.change_order_id = sub.id
            db.add(ln)
        db.flush()
        db.refresh(sub)
        recompute(sub, job)
        check_line_figures(sub)
        for t in group:
            t.sub_change_order_id = sub.id
        subs.append(sub)
    # Linked after the numbering retry loops: a collision's rollback would
    # undo it.
    for t in tickets:
        t.change_order_id = pco.id
    db.flush()
    return {"change_order": pco, "sub_change_orders": subs, "tickets": tickets}


def bill_tickets(db: Session, ticket_ids: list[int], invoice_date: Optional[date]):
    """Bill signed T&M tickets on an invoice to the job's customer: a line per
    ticket line at its bill rate, tagged to the job and the line's cost code.
    Only for a job with no schedule of values: a job that bills on pay
    applications prices extra work as a change order instead. The tickets
    close; voiding the invoice opens them again."""
    from app.routes.invoices.crud import create_invoice
    from app.schemas.invoices import InvoiceCreate, InvoiceLineCreate

    job, tickets = billable_tickets(db, ticket_ids)
    if job.sov_lines:
        raise HTTPException(
            status_code=409,
            detail="This job bills by its schedule of values on pay applications. "
            "Make a change order from the tickets instead.",
        )
    lines = []
    for t in tickets:
        for ln in t.lines:
            if _d(ln.quantity) == 0:
                continue
            lines.append(
                InvoiceLineCreate(
                    description=f"{t.number}: {ln.description or ln.line_type}",
                    quantity=ln.quantity,
                    rate=ln.bill_rate or 0,
                    job_id=job.id,
                    cost_code_id=ln.cost_code_id,
                    line_order=len(lines),
                )
            )
    if not lines:
        raise HTTPException(
            status_code=422, detail="The tickets have no lines with a quantity."
        )
    numbers = ", ".join(t.number for t in tickets)
    resp = create_invoice(
        InvoiceCreate(
            customer_id=job.customer_id,
            date=invoice_date or date.today(),
            job_id=job.id,
            notes=f"T&M tickets {numbers}",
            lines=lines,
        ),
        db,
    )
    for t in tickets:
        t = db.get(FieldTicket, t.id)  # the invoice route committed
        t.invoice_id = resp.id
        t.status = "closed"
    db.flush()
    return resp


def ticket_out(db: Session, t: FieldTicket) -> dict:
    """The ticket as the API returns it: its lines with amounts, the names of
    what they point at, its totals and what it turned into."""
    cost, bill = ticket_totals(t)
    live = live_change_order(db, t.change_order) if t.change_order_id else None
    lines = []
    for ln in t.lines:
        b = line_bill(ln)
        po_line = ln.po_line
        lines.append(
            {
                "id": ln.id,
                "line_order": ln.line_order,
                "line_type": ln.line_type,
                "employee_id": ln.employee_id,
                "employee_name": _default_name(ln.employee, None, None),
                "equipment_id": ln.equipment_id,
                "equipment_name": ln.equipment.name if ln.equipment else None,
                "item_id": ln.item_id,
                "item_name": ln.item.name if ln.item else None,
                "po_line_id": ln.po_line_id,
                "description": ln.description,
                "quantity": float(_d(ln.quantity)),
                "unit": ln.unit,
                "cost_rate": float(_d(ln.cost_rate)),
                "bill_rate": float(ln.bill_rate) if ln.bill_rate is not None else None,
                "cost_code_id": ln.cost_code_id,
                "cost_code_label": ln.cost_code.label if ln.cost_code else None,
                "cost_type": ln.cost_type,
                "cost_amount": float(line_cost(ln)),
                "bill_amount": float(b) if b is not None else None,
                "ordered_qty": float(_d(po_line.quantity)) if po_line else None,
                "received_qty": float(_d(po_line.received_qty)) if po_line else None,
                "over_receipt": bool(ln.over_receipt),
            }
        )
    attachments = (
        db.query(Attachment.id)
        .filter(
            Attachment.entity_type == "field_ticket",
            Attachment.entity_id == t.id,
            Attachment.employee_id.is_(None),
        )
        .count()
    )
    return {
        "id": t.id,
        "job_id": t.job_id,
        "kind": t.kind,
        "number": t.number,
        "date": t.date,
        "status": t.status,
        "cost_code_id": t.cost_code_id,
        "cost_code_label": t.cost_code.label if t.cost_code else None,
        "description": t.description,
        "submitted_by": t.submitted_by,
        "signed_by_name": t.signed_by_name,
        "signed_at": t.signed_at,
        "commitment_id": t.commitment_id,
        "commitment_number": t.commitment.po_number if t.commitment else None,
        "purchase_order_id": t.purchase_order_id,
        "purchase_order_number": (
            t.purchase_order.po_number if t.purchase_order else None
        ),
        "vendor_ticket_ref": t.vendor_ticket_ref,
        # the CO its work sits on now (a promoted PCO's owner CO); none once
        # that change order is rejected or void
        "change_order_id": live.id if live else None,
        "change_order_number": live.number if live else None,
        "sub_change_order_id": t.sub_change_order_id,
        "sub_change_order_number": (
            t.sub_change_order.number if t.sub_change_order else None
        ),
        "invoice_id": t.invoice_id,
        "invoice_number": t.invoice.invoice_number if t.invoice else None,
        "job_cost_id": t.job_cost_id,
        "job_cost_number": t.job_cost.number if t.job_cost else None,
        "voided_at": t.voided_at,
        "cost_total": float(cost),
        "bill_total": float(bill) if bill is not None else None,
        "attachment_count": attachments,
        "warnings": over_receipt_warnings(t),
        "lines": lines,
    }


# ── Field ticket log ────────────────────────────────────────────────────────

FT_LOG_COLUMNS = [
    {"key": "number", "label": "Ticket", "kind": "text"},
    {"key": "date", "label": "Date", "kind": "text"},
    {"key": "status", "label": "Status", "kind": "text"},
    {"key": "description", "label": "Description", "kind": "text"},
    {"key": "signed_by", "label": "Signed by", "kind": "text"},
    {"key": "priced_on", "label": "Priced on", "kind": "text"},
    {"key": "unsigned", "label": "Unsigned", "kind": "money"},
    {"key": "signed_unbilled", "label": "Signed, not billed", "kind": "money"},
]
_FT_LOG_SUMMED = ("unsigned", "signed_unbilled")


def field_ticket_log(
    db: Session,
    as_of: Optional[date] = None,
    job_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    include_closed: bool = False,
) -> dict:
    """Per job, the T&M tickets dated by `as_of` that are still money on the
    table: open (unsigned) tickets and signed tickets not yet billed, each at
    its bill rates. A signed ticket priced on a change order that is not yet
    approved still counts as not billed (the CO is named). A closed or void
    ticket is not listed. The ticket's status is today's."""
    from app.services.job_reports import _customer_name, select_jobs

    as_of = as_of or date.today()
    jobs = select_jobs(db, job_id, customer_id, status, include_closed)
    out = []
    grand = {k: _ZERO for k in _FT_LOG_SUMMED}
    for job in jobs:
        tickets = (
            db.query(FieldTicket)
            .filter(
                FieldTicket.job_id == job.id,
                FieldTicket.kind == "tm",
                FieldTicket.status.in_(("draft", "signed")),
                FieldTicket.date <= as_of,
            )
            .order_by(FieldTicket.date, FieldTicket.number)
            .all()
        )
        if not tickets:
            continue
        rows = []
        totals = {k: _ZERO for k in _FT_LOG_SUMMED}
        for t in tickets:
            _cost, bill = ticket_totals(t)
            live = live_change_order(db, t.change_order) if t.change_order_id else None
            signed = t.status == "signed"
            key = "signed_unbilled" if signed else "unsigned"
            totals[key] += bill
            rows.append(
                {
                    "id": t.id,
                    "number": t.number,
                    "date": t.date.isoformat(),
                    "status": "Signed" if signed else "Unsigned",
                    "description": t.description or "",
                    "signed_by": t.signed_by_name or "",
                    "priced_on": live.number if live else "",
                    "unsigned": None if signed else float(bill),
                    "signed_unbilled": float(bill) if signed else None,
                }
            )
        for k in _FT_LOG_SUMMED:
            grand[k] += totals[k]
        out.append(
            {
                "job_id": job.id,
                "job_name": job.name,
                "customer_id": job.customer_id,
                "customer_name": _customer_name(job),
                "rows": rows,
                "totals": {k: float(_q_read(v)) for k, v in totals.items()},
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "jobs": out,
        "totals": {k: float(_q_read(v)) for k, v in grand.items()},
        "columns": FT_LOG_COLUMNS,
    }


# ── Per-kind rules ──────────────────────────────────────────────────────────


def check_delivery_po(db: Session, job: Job, po_id: Optional[int]) -> None:
    """A delivery ticket is received against an open purchase order on its
    job."""
    from app.models.purchase_orders import POStatus, PurchaseOrder

    if po_id is None:
        raise HTTPException(
            status_code=422,
            detail="A delivery ticket is received against a purchase order: pick one.",
        )
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if po.job_id != job.id:
        raise HTTPException(
            status_code=422, detail="That purchase order is on another job."
        )
    if po.status in (POStatus.DRAFT, POStatus.CLOSED):
        raise HTTPException(
            status_code=409,
            detail=f"Purchase order {po.po_number} is {po.status.value}; a delivery "
            "is received only on a sent or partly received order.",
        )


def check_kind_lines(db: Session, t: FieldTicket, over_receipt: bool = False) -> None:
    """The lines each kind allows. A T&M ticket's quantities are not
    negative; a material slip's lines are items from stock; a delivery
    ticket's lines are its purchase order's lines, and it takes none past
    what was ordered unless `over_receipt` says the supplier really
    delivered more (the line is then marked and the ticket warns). Called
    with the ticket's own quantities off the PO lines."""
    check_line_figures(t)
    if t.kind == "tm":
        for ln in t.lines:
            if _d(ln.quantity) < 0:
                raise HTTPException(
                    status_code=422,
                    detail="A T&M ticket line cannot have a negative quantity. "
                    "A return slip is the only negative kind.",
                )
    elif t.kind == "material":
        for ln in t.lines:
            if ln.item_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Each material slip line names the stock item taken to the job.",
                )
            if not ln.item.track_inventory:
                raise HTTPException(
                    status_code=422,
                    detail=f"{ln.item.name} is not an inventory item; a material slip "
                    "moves stock the company keeps on hand.",
                )
            if _d(ln.quantity) == 0:
                raise HTTPException(
                    status_code=422,
                    detail="A material slip line needs a quantity (negative returns it to stock).",
                )
            ln.line_type = "material"
    elif t.kind == "delivery":
        seen = set()
        for ln in t.lines:
            if (
                ln.po_line is None
                or ln.po_line.purchase_order_id != t.purchase_order_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Each delivery line is one of the purchase order's lines.",
                )
            if ln.po_line_id in seen:
                raise HTTPException(
                    status_code=422,
                    detail="A purchase order line appears once on a delivery ticket.",
                )
            seen.add(ln.po_line_id)
            if _d(ln.quantity) <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="A delivery line's quantity received must be more than zero.",
                )
            ordered = _d(ln.po_line.quantity)
            before = _d(ln.po_line.received_qty)
            after = before + _d(ln.quantity)
            if after > MAX_RECEIVED:  # received_qty's column, over_receipt or not
                raise HTTPException(
                    status_code=422,
                    detail=f"{_line_name(ln)}: {_plain(before)} already received, so "
                    f"{_plain(ln.quantity)} more would make {_plain(after)}; a "
                    f"purchase order line holds at most {MAX_RECEIVED:,} received.",
                )
            # A line kept from an earlier save keeps its mark (None on a new one)
            meant = over_receipt or bool(ln.over_receipt)
            ln.over_receipt = after > ordered
            if ln.over_receipt and not meant:
                _refuse_over_receipt(ln, ordered, before, after)


def check_line_figures(t: FieldTicket) -> None:
    """Each line's amount (quantity × cost rate, and × bill rate on a T&M
    ticket) and the ticket's totals fit a money column, and a bill rate the
    job's markup filled in fits its own: a 422 before the ticket is saved
    (L bug 1: 1e15 × 1e15 was saved behind a 500 and then failed the list)."""
    totals = {"cost": _ZERO, "bill": _ZERO}
    for n, ln in enumerate(t.lines, 1):
        if ln.bill_rate is not None and _d(ln.bill_rate) >= RATE_LIMIT:
            raise HTTPException(
                status_code=422,
                detail=f"Line {n}: its bill rate with the job's markup comes to "
                f"{_d(ln.bill_rate):,.4f}; a rate must be less than "
                f"{RATE_LIMIT:,}.",
            )
        for kind, rate in (("cost", ln.cost_rate), ("bill", ln.bill_rate)):
            if rate is None:
                continue
            amount = _d(ln.quantity) * _d(rate)
            if abs(amount) > MAX_AMOUNT:
                raise HTTPException(
                    status_code=422,
                    detail=f"Line {n}: {_plain(ln.quantity)} at {_plain(rate)} "
                    f"comes to {amount:,.2f}, more than a line can be "
                    f"({MAX_AMOUNT:,.2f}). Check the quantity and the {kind} rate.",
                )
            totals[kind] += amount
    for kind, total in totals.items():
        if abs(total) > MAX_AMOUNT:
            raise HTTPException(
                status_code=422,
                detail=f"The ticket's lines come to {total:,.2f} at {kind} rates, "
                f"more than a ticket can be ({MAX_AMOUNT:,.2f}). Split the work "
                "across tickets or check the quantities.",
            )


def _plain(v) -> str:
    """A quantity in plain figures: 100, 2.5."""
    text = format(_d(v).normalize(), "f")
    return "0" if text in ("0", "-0") else text


def _line_name(ln: FieldTicketLine) -> str:
    return ln.po_line.description or (ln.item.name if ln.item else None) or "A line"


def _refuse_over_receipt(ln, ordered: Decimal, before: Decimal, after: Decimal):
    """409 naming what was ordered and what the ticket would make received;
    the page asks the question and sends the ticket again with
    `over_receipt: true`."""
    message = (
        f"{_line_name(ln)}: {_plain(ordered)} ordered and {_plain(before)} already "
        f"received, so {_plain(ln.quantity)} more would make {_plain(after)} received."
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "over_receipt",
            "message": message + " Save it with over_receipt: true if the supplier "
            "really delivered more.",
            "question": message + " Record the over-delivery anyway?",
        },
    )


def over_receipt_warnings(t: FieldTicket) -> list[str]:
    """A delivery ticket's lines that took a PO line past what was ordered,
    one sentence each (read from the line's mark and the PO line's totals)."""
    if t.kind != "delivery" or t.status == "void":
        return []
    return [
        f"{_line_name(ln)}: {_plain(ln.po_line.received_qty)} received against "
        f"{_plain(ln.po_line.quantity)} ordered (over-delivery recorded)."
        for ln in t.lines
        if ln.over_receipt and ln.po_line is not None
    ]


def apply_received(db: Session, t: FieldTicket) -> None:
    """A saved delivery ticket's quantities count as received on its PO lines
    (PurchaseOrderLine.received_qty), and the order moves to partial or
    received. Nothing posts: received-not-billed is the accountant's."""
    _move_received(db, t, 1)


def unapply_received(db: Session, t: FieldTicket) -> None:
    """Take a delivery ticket's quantities back off its PO lines (an edit, a
    delete, a void)."""
    _move_received(db, t, -1)


def _move_received(db: Session, t: FieldTicket, sign: int) -> None:
    if t.kind != "delivery" or t.status == "void":
        return
    for ln in t.lines:
        if ln.po_line is None:
            continue
        ln.po_line.received_qty = _d(ln.po_line.received_qty) + sign * _d(ln.quantity)
    db.flush()
    if t.purchase_order is not None:
        receipt_status(t.purchase_order)


def receipt_status(po) -> None:
    """A purchase order's status from what has arrived: every line received in
    full is `received`; something received (or billed) is `partial`; nothing
    is `sent`. A draft or a closed (fully billed) order is left alone."""
    from app.models.purchase_orders import POStatus

    if po.status in (POStatus.DRAFT, POStatus.CLOSED):
        return
    lines = [ln for ln in po.lines if _d(ln.quantity) > 0]
    received = [_d(ln.received_qty) for ln in lines]
    if lines and all(r >= _d(ln.quantity) for r, ln in zip(received, lines)):
        po.status = POStatus.RECEIVED
    elif any(r > 0 for r in received) or any(
        _d(ln.billed_amount) > 0 for ln in po.lines
    ):
        po.status = POStatus.PARTIAL
    else:
        po.status = POStatus.SENT


def delivery_tickets_on(db: Session, po_line_id: int) -> list[str]:
    """The numbers of the delivery tickets (not void) that received against a
    PO line."""
    rows = (
        db.query(FieldTicket.number)
        .join(FieldTicketLine, FieldTicketLine.field_ticket_id == FieldTicket.id)
        .filter(
            FieldTicketLine.po_line_id == po_line_id,
            FieldTicket.kind == "delivery",
            FieldTicket.status != "void",
        )
        .order_by(FieldTicket.number)
        .all()
    )
    return [n for (n,) in rows]


def received_line_refusal(
    db: Session, ln, new_quantity: Optional[Decimal] = None
) -> Optional[str]:
    """The sentence a purchase order edit is refused with when it drops a line
    that delivery tickets received on (new_quantity None), or cuts one below
    what they received; None when the edit leaves the deliveries standing.
    Like a billed line, it is changed by voiding the ticket first."""
    got = _d(ln.received_qty)
    tickets = delivery_tickets_on(db, ln.id)
    if not tickets and got <= 0:
        return None
    name = ln.description or "This line"
    on = f"delivery ticket {', '.join(tickets)}" if tickets else "a delivery ticket"
    if new_quantity is None:
        return (
            f"{name} was received on {on}. Void that ticket before removing the line."
        )
    if new_quantity >= got:
        return None
    return (
        f"{name} has {_plain(got)} received on {on}, so it cannot be cut to "
        f"{_plain(new_quantity)}. Void the ticket, or keep at least {_plain(got)}."
    )


def received_value(ln) -> Decimal:
    """What has arrived on a PO line, in money: received quantity × rate."""
    return _q(_d(ln.received_qty) * _d(ln.rate))


def over_received_warnings(po, this_period: dict) -> list[str]:
    """Bill-from-PO: a line that would bill more than has been received, as a
    warning (never a refusal). Only on an order that is being received by
    delivery tickets (some line has a received quantity)."""
    if not any(_d(ln.received_qty) > 0 for ln in po.lines):
        return []
    out = []
    for ln in po.lines:
        amt = this_period.get(ln.id) or _ZERO
        if amt <= 0:
            continue
        billed = _q(_d(ln.billed_amount) + amt)
        got = received_value(ln)
        if billed > got:
            out.append(
                f"{ln.description or 'Line'}: billed ${billed:,.2f} against "
                f"${got:,.2f} received ({_d(ln.received_qty).normalize():f} of "
                f"{_d(ln.quantity).normalize():f} delivered)."
            )
    return out


def received_by(db: Session, po_id: int, as_of: date) -> Decimal:
    """The value received on a commitment by `as_of`: its delivery tickets
    dated by then (not void), each line's quantity at its PO line's rate."""
    total = _ZERO
    tickets = (
        db.query(FieldTicket)
        .filter(
            FieldTicket.purchase_order_id == po_id,
            FieldTicket.kind == "delivery",
            FieldTicket.status != "void",
            FieldTicket.date <= as_of,
        )
        .all()
    )
    for t in tickets:
        for ln in t.lines:
            if ln.po_line is not None:
                total += _d(ln.quantity) * _d(ln.po_line.rate)
    return _q(total)


def void_blocker(db: Session, t: FieldTicket) -> Optional[str]:
    """The sentence a void is refused with while what the ticket led to is
    approved or billed, else None. Reads only, so ticket_out can tell the page
    whether to offer Void."""
    if t.kind == "tm":
        live = live_change_order(db, t.change_order) if t.change_order_id else None
        if live is not None:
            return (
                f"This ticket is priced on change order {live.number}. Reject "
                "or void that change order first."
            )
        if t.invoice is not None and _invoice_live(t.invoice):
            return (
                f"This ticket is billed on invoice {t.invoice.invoice_number}. "
                "Void that invoice first."
            )
    return None


def _locked_for_good(db: Session, on: Optional[date]) -> bool:
    """A date in a closed period on a company with no closing-date password:
    a change dated then is refused outright. (With a password it can still be
    made, so the page keeps offering it.)"""
    from app.models.settings import Settings
    from app.services.closing_date import get_closing_date

    closing = get_closing_date(db)
    if on is None or closing is None or on > closing:
        return False
    row = db.query(Settings).filter(Settings.key == "closing_date_password").first()
    return not (row and row.value)


def can_void(db: Session, t: FieldTicket) -> bool:
    """Whether Void would be accepted now: not void already, nothing it led to
    approved or billed, and (a posted slip, a delivery) not dated in a closed
    period."""
    if t.status == "void" or void_blocker(db, t) is not None:
        return False
    if t.kind == "material" and t.status == "closed" and t.job_cost is not None:
        return not _locked_for_good(db, t.job_cost.date)
    if t.kind == "delivery":
        return not _locked_for_good(db, t.date)
    return True


def check_delivery_period(db: Session, t: FieldTicket, *dates: date) -> None:
    """A delivery ticket changes received quantity as of its date, so saving,
    editing, deleting or voiding it in a closed period is refused like any
    other entry there. Other kinds move nothing on their date."""
    from app.services.closing_date import check_closing_date

    if t.kind != "delivery":
        return
    for d in dict.fromkeys(dates or (t.date,)):
        check_closing_date(db, d)


def void_ticket(db: Session, t: FieldTicket) -> None:
    """Undo what a ticket led to, or refuse while that is approved."""
    blocker = void_blocker(db, t)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    if t.kind == "material" and t.status == "closed":
        reverse_material_slip(db, t)
    elif t.kind == "delivery":
        check_delivery_period(db, t)
        unapply_received(db, t)


def _invoice_live(invoice) -> bool:
    status = getattr(invoice.status, "value", invoice.status)
    return status != "void"


# ── Material slip → job cost ────────────────────────────────────────────────


def _stamp(day: date) -> datetime:
    """A movement's timestamp for a document dated `day` (midday, so no time
    zone moves it to the next or previous day)."""
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def post_material_slip(
    db: Session, t: FieldTicket, post_date: Optional[date]
) -> list[str]:
    """Put a material slip's stock on the job: one Job Cost Entry
    (source "material_slip"), DR job cost (material, the line's code; the
    account is the code's, else the material cost type's default account) /
    CR the item's inventory asset account, at the item's average cost, and
    the item's on-hand quantity goes down by the slip's quantity. A negative
    quantity is a return: the same entry the other way, the stock back on
    the shelf at the average cost. The slip closes.

    It posts on the slip's own date, the closing date checked on that date,
    and its stock movements carry that date too. A `post_date` that is not
    the slip's date is refused (change the slip's date first). Stock gone
    short follows the company's refuse-oversell setting as an invoice does:
    refused when it is on, else the returned sentences are the warnings."""
    from app.models.items import MovementType
    from app.models.job_costing import JobCost, JobCostLine
    from app.services.accounting import create_journal_entry
    from app.services.closing_date import check_closing_date
    from app.services.inventory_service import (
        _append_at_cost,
        get_inventory_asset_account_id,
    )
    from app.services.job_costing import (
        cost_type_map,
        next_job_cost_number,
        resolve_line_accounts,
    )
    from app.routes.invoices.helpers import warn_or_refuse_oversell
    from app.services.safe_errors import DataProblem

    if t.kind != "material":
        raise HTTPException(
            status_code=409, detail="Only a material slip is posted to job cost."
        )
    if t.status != "draft":
        raise HTTPException(
            status_code=409, detail=f"This material slip is already {t.status}."
        )
    if not t.lines:
        raise HTTPException(
            status_code=422, detail="Add the stock items before posting the slip."
        )
    if post_date is not None and post_date != t.date:
        raise HTTPException(
            status_code=422,
            detail=f"A material slip posts on its own date ({t.date}). Change the "
            "slip's date first, then post it.",
        )
    post_date = t.date
    check_closing_date(db, post_date)
    # Before anything is written, so a refused slip leaves no trace
    warnings = warn_or_refuse_oversell(db, t.lines, action="slip")
    types = cost_type_map(db)
    jc = JobCost(
        number=next_job_cost_number(db),
        date=post_date,
        job_id=t.job_id,
        memo=f"Material slip {t.number}"
        + (f": {t.description}" if t.description else ""),
        source="material_slip",
    )
    db.add(jc)
    db.flush()
    entry, issues = [], []
    for i, ln in enumerate(t.lines):
        item = ln.item
        qty = _d(ln.quantity)
        unit_cost = _d(item.avg_cost).quantize(Decimal("0.0001"))
        amount = _q(qty * unit_cost)
        if amount == 0:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name} has no average cost yet, so the slip would post "
                "nothing. Receive it on a bill (or set its opening quantity) first.",
            )
        asset_id = get_inventory_asset_account_id(db, item)
        if not asset_id:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name} has no inventory asset account; set one on the item.",
            )
        try:
            debit, credit = resolve_line_accounts(
                db, types, ln.cost_code, "material", None, asset_id
            )
        except DataProblem as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        desc = ln.description or item.name
        jc.lines.append(
            JobCostLine(
                job_id=t.job_id,
                cost_code_id=ln.cost_code_id,
                cost_type="material",
                description=desc,
                quantity=qty,
                rate=unit_cost,
                amount=amount,
                debit_account_id=debit,
                credit_account_id=credit,
                line_order=i,
            )
        )
        tagged = {
            "account_id": debit,
            "description": desc,
            "job_id": t.job_id,
            "cost_code_id": ln.cost_code_id,
            "cost_type": "material",
        }
        stock = {"account_id": credit, "description": f"Stock: {desc}", "job_id": None}
        if amount > 0:
            entry += [
                {**tagged, "debit": amount, "credit": _ZERO},
                {**stock, "debit": _ZERO, "credit": amount},
            ]
        else:  # a return: the job's cost comes off, the stock goes back
            entry += [
                {**stock, "debit": -amount, "credit": _ZERO},
                {**tagged, "debit": _ZERO, "credit": -amount},
            ]
        issues.append((item, qty, unit_cost))
    txn = create_journal_entry(
        db,
        post_date,
        f"Job cost {jc.number} - material slip {t.number}",
        entry,
        source_type="job_cost",
        source_id=jc.id,
        reference=t.number,
    )
    jc.transaction_id = txn.id
    jc.total = sum((_q(ln.amount) for ln in jc.lines), _ZERO)
    for item, qty, unit_cost in issues:
        mv = _append_at_cost(
            db,
            item,
            MovementType.ADJUSTMENT,
            quantity=-qty,
            unit_cost=unit_cost,
            # the movement belongs to the Job Cost Entry, which links to it
            source_type="job_cost",
            source_id=jc.id,
            txn_date=post_date,
            transaction_id=txn.id,
            memo=f"Material slip {t.number}",
        )
        mv.date = _stamp(post_date)
    t.job_cost_id = jc.id
    t.status = "closed"
    db.flush()
    return warnings


def reverse_material_slip(db: Session, t: FieldTicket) -> None:
    """Void a posted slip at the cost it was booked at, not today's average:
    the Job Cost Entry's own entry reversed, and each stock movement put back
    at its own unit cost."""
    from app.models.items import InventoryMovement, MovementType
    from app.models.transactions import Transaction
    from app.services.closing_date import check_closing_date
    from app.services.inventory_service import _append_at_cost
    from app.services.job_costing import void_job_cost

    jc = t.job_cost
    if jc is None or jc.status == "void":
        return
    check_closing_date(db, jc.date)
    void_job_cost(db, jc)
    db.flush()
    void_txn = (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "job_cost_void", Transaction.source_id == jc.id
        )
        .order_by(Transaction.id.desc())
        .first()
    )
    moves = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.source_type == "job_cost",
            InventoryMovement.source_id == jc.id,
            InventoryMovement.reverses_movement_id.is_(None),
        )
        .all()
    )
    for mv in moves:
        reversal = _append_at_cost(
            db,
            mv.item,
            MovementType.VOID,
            quantity=-_d(mv.quantity),
            unit_cost=_d(mv.unit_cost),
            source_type="job_cost_void",
            source_id=jc.id,
            txn_date=jc.date,
            transaction_id=void_txn.id if void_txn else None,
            memo=f"VOID material slip {t.number}",
            reverses_movement_id=mv.id,
        )
        reversal.date = _stamp(jc.date)
