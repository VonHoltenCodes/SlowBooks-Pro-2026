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
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
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
    return _q(_d(ln.quantity) * _d(ln.cost_rate))


def line_bill(ln: FieldTicketLine) -> Optional[Decimal]:
    if ln.bill_rate is None:
        return None
    return _q(_d(ln.quantity) * _d(ln.bill_rate))


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
    seen = set()
    while co is not None and co.id not in seen:
        seen.add(co.id)
        if co.status == "superseded":
            co = (
                db.query(ChangeOrder)
                .filter(ChangeOrder.supersedes_id == co.id)
                .order_by(ChangeOrder.id.desc())
                .first()
            )
        elif co.status == "promoted" and co.promoted_to_id:
            co = db.get(ChangeOrder, co.promoted_to_id)
        elif co.status in ("rejected", "void"):
            return None
        else:
            return co
    return None


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
        # the CO its work sits on now (a promoted PCO's owner CO)
        "change_order_id": live.id if live else t.change_order_id,
        "change_order_number": (
            live.number if live else (t.change_order.number if t.change_order else None)
        ),
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
                "totals": {k: float(_q(v)) for k, v in totals.items()},
            }
        )
    return {
        "as_of": as_of.isoformat(),
        "jobs": out,
        "totals": {k: float(_q(v)) for k, v in grand.items()},
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


def check_kind_lines(db: Session, t: FieldTicket) -> None:
    """The lines each kind allows. A material slip's lines are items from
    stock; a delivery ticket's lines are its purchase order's lines."""
    if t.kind == "material":
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


def apply_received(db: Session, t: FieldTicket) -> None:
    """A delivery ticket's quantities onto its PO lines (filled in by the
    delivery milestone)."""


def unapply_received(db: Session, t: FieldTicket) -> None:
    """Take a delivery ticket's quantities back off its PO lines."""


def void_ticket(db: Session, t: FieldTicket) -> None:
    """Undo what a ticket led to, or refuse while that is approved."""
    if t.kind == "tm":
        live = live_change_order(db, t.change_order) if t.change_order_id else None
        if live is not None:
            raise HTTPException(
                status_code=409,
                detail=f"This ticket is priced on change order {live.number}. Reject "
                "or void that change order first.",
            )
        if t.invoice is not None and _invoice_live(t.invoice):
            raise HTTPException(
                status_code=409,
                detail=f"This ticket is billed on invoice {t.invoice.invoice_number}. "
                "Void that invoice first.",
            )
    elif t.kind == "delivery":
        unapply_received(db, t)


def _invoice_live(invoice) -> bool:
    status = getattr(invoice.status, "value", invoice.status)
    return status != "void"
