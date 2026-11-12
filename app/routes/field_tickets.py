# ============================================================================
# Field tickets (construction mode) — T&M tickets, material slips and
# delivery tickets on a job. Bookkeeper-writable, like jobs and change
# orders. See app/services/field_tickets.py and
# docs/features/field-tickets/PLAN.md.
# ============================================================================

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.routes._helpers import refuse_unknown_filters
from app.database import get_db
from app.models.cost_codes import CostCode
from app.models.field_tickets import FT_KINDS, FT_STATUSES, FieldTicket
from app.models.jobs import Job
from app.models.purchase_orders import PurchaseOrder
from app.schemas.common import choice_error
from app.schemas.field_tickets import (
    BillIn,
    KIND_LABELS,
    STATUS_LABELS,
    FieldTicketCreate,
    FieldTicketOut,
    FieldTicketUpdate,
    PostIn,
    SignIn,
    TicketIdsIn,
    VoidIn,
)
from app.services import field_tickets as svc
from app.services import file_store

router = APIRouter(prefix="/api/field-tickets", tags=["field-tickets"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _get(db: Session, ticket_id: int) -> FieldTicket:
    t = db.get(FieldTicket, ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Field ticket not found")
    return t


def _out(db: Session, t: FieldTicket) -> FieldTicketOut:
    out = FieldTicketOut.model_validate(svc.ticket_out(db, t))
    out.can_void = svc.can_void(db, t)
    return out


def _check_header(db: Session, job: Job, kind: str, data) -> None:
    """What a ticket points at must be on its job: the cost code exists, a
    sub's commitment is this job's (T&M only)."""
    if data.get("cost_code_id") and not db.get(CostCode, data["cost_code_id"]):
        raise HTTPException(status_code=404, detail="Cost code not found")
    commitment_id = data.get("commitment_id")
    if commitment_id is not None:
        if kind != "tm":
            raise HTTPException(
                status_code=422,
                detail="Only a T&M ticket names a commitment (a sub's ticket).",
            )
        po = db.get(PurchaseOrder, commitment_id)
        if po is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        if po.job_id != job.id:
            raise HTTPException(
                status_code=422, detail="That commitment is on another job."
            )
    if kind == "delivery":
        svc.check_delivery_po(db, job, data.get("purchase_order_id"))
    elif data.get("purchase_order_id") is not None:
        raise HTTPException(
            status_code=422,
            detail="Only a delivery ticket is received against a purchase order.",
        )


def _create_numbered(db: Session, job: Job, kind: str, **fields) -> FieldTicket:
    """Flush a ticket with the next number for its job and kind, retrying on
    a number collision (MAX+1 with no row lock, as change orders do)."""
    last_err = None
    for _ in range(10):
        t = FieldTicket(
            number=svc.next_ticket_number(db, job, kind),
            job_id=job.id,
            kind=kind,
            **fields,
        )
        db.add(t)
        try:
            db.flush()
            return t
        except IntegrityError as e:
            if "number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
    raise HTTPException(
        status_code=503, detail="Could not assign a field ticket number; please retry."
    ) from last_err


def _refuse_unless_draft(t: FieldTicket, action: str) -> None:
    if t.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"A {t.status} field ticket cannot be {action}. Void it and "
            "write a new one.",
        )


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=list[FieldTicketOut],
    dependencies=[Depends(refuse_unknown_filters)],
)
def list_field_tickets(
    job_id: int = Query(None),
    kind: str = Query(None),
    status: str = Query(None),
    db: Session = Depends(get_db),
):
    """Field tickets, newest first: one job's, or every job's. Filter by kind
    (tm, material, delivery) and status (draft, signed, closed, void)."""
    svc.require_construction(db)
    q = db.query(FieldTicket)
    if job_id is not None:
        q = q.filter(FieldTicket.job_id == job_id)
    if kind:
        if kind not in FT_KINDS:
            raise HTTPException(
                status_code=422, detail=str(choice_error("Kind", KIND_LABELS, "kind"))
            )
        q = q.filter(FieldTicket.kind == kind)
    if status:
        if status not in FT_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=str(choice_error("Status", STATUS_LABELS, "status")),
            )
        q = q.filter(FieldTicket.status == status)
    tickets = q.order_by(FieldTicket.date.desc(), FieldTicket.id.desc()).all()
    return [_out(db, t) for t in tickets]


@router.get("/purchase-orders", dependencies=[Depends(refuse_unknown_filters)])
def job_purchase_orders(job_id: int = Query(...), db: Session = Depends(get_db)):
    """The job's purchase orders and subcontracts a ticket can point at (not
    drafts), each with its lines: ordered, received and billed to date. The
    delivery form picks an open one; a sub's T&M ticket names a commitment.
    Construction mode's, as the tickets are: a Business or Nonprofit company
    is answered 409, as GET /api/field-tickets answers it (#810)."""
    from app.models.purchase_orders import POStatus

    svc.require_construction(db)

    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.job_id == job_id, PurchaseOrder.status != POStatus.DRAFT)
        .order_by(PurchaseOrder.po_number)
        .all()
    )
    return [
        {
            "id": po.id,
            "number": po.po_number,
            "vendor_name": po.vendor.name if po.vendor else "",
            "commitment_type": po.commitment_type,
            "status": po.status.value,
            "lines": [
                {
                    "id": ln.id,
                    "description": ln.description or (ln.item.name if ln.item else ""),
                    "item_id": ln.item_id,
                    "quantity": float(ln.quantity or 0),
                    "received_qty": float(ln.received_qty or 0),
                    "rate": float(ln.rate or 0),
                    "amount": float(ln.amount or 0),
                    "billed_amount": float(ln.billed_amount or 0),
                    "cost_code_id": ln.cost_code_id,
                }
                for ln in po.lines
            ],
        }
        for po in pos
    ]


@router.get("/{ticket_id}", response_model=FieldTicketOut)
def get_field_ticket(ticket_id: int, db: Session = Depends(get_db)):
    return _out(db, _get(db, ticket_id))


@router.post("", response_model=FieldTicketOut, status_code=201)
def create_field_ticket(data: FieldTicketCreate, db: Session = Depends(get_db)):
    svc.require_construction(db)
    job = db.get(Job, data.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    header = data.model_dump(exclude={"lines", "job_id", "kind", "over_receipt"})
    _check_header(db, job, data.kind, header)
    header["date"] = header.get("date") or date.today()
    # A delivery moves received quantity as of its date: creating one in a
    # closed period is refused like editing, deleting or voiding it.
    if data.kind == "delivery":
        from app.services.closing_date import check_closing_date

        check_closing_date(db, header["date"])
    t = _create_numbered(db, job, data.kind, status="draft", **header)
    svc.set_lines(db, t, data.lines)
    svc.check_kind_lines(db, t, data.over_receipt)
    svc.apply_received(db, t)
    db.commit()
    db.refresh(t)
    return _out(db, t)


@router.put("/{ticket_id}", response_model=FieldTicketOut)
def update_field_ticket(
    ticket_id: int, data: FieldTicketUpdate, db: Session = Depends(get_db)
):
    """Edit a draft ticket. A signed ticket is what its signer signed, and a
    closed one has had its effect: neither is edited."""
    svc.require_construction(db)
    t = _get(db, ticket_id)
    _refuse_unless_draft(t, "edited")
    fields = data.model_dump(exclude_unset=True, exclude={"lines", "over_receipt"})
    merged = {
        "cost_code_id": t.cost_code_id,
        "commitment_id": t.commitment_id,
        "purchase_order_id": t.purchase_order_id,
        **fields,
    }
    _check_header(db, t.job, t.kind, merged)
    # A delivery ticket moves received quantity as of its date: both the date
    # it has and the one it is moved to must be open.
    svc.check_delivery_period(db, t, t.date, fields.get("date") or t.date)
    # A delivery ticket's quantities come off the PO lines before the edit and
    # go back on after it, so an edit moves received quantity by the change.
    svc.unapply_received(db, t)
    for key, val in fields.items():
        if key == "date" and val is None:
            continue
        setattr(t, key, val)
    if data.lines is not None:
        svc.set_lines(db, t, data.lines)
    svc.check_kind_lines(db, t, data.over_receipt)
    svc.apply_received(db, t)
    db.commit()
    db.refresh(t)
    return _out(db, t)


@router.delete("/{ticket_id}")
def delete_field_ticket(ticket_id: int, db: Session = Depends(get_db)):
    """Delete a draft ticket (a delivery ticket gives its quantity back)."""
    svc.require_construction(db)
    t = _get(db, ticket_id)
    _refuse_unless_draft(t, "deleted")
    svc.check_delivery_period(db, t)
    svc.unapply_received(db, t)
    # its attachments go with it, so a reused id never shows another ticket's paper
    file_store.delete_record_attachments(db, "field_ticket", t.id)
    db.delete(t)
    db.commit()
    return {"deleted": True}


# ── Lifecycle ─────────────────────────────────────────────────────────────────


@router.post("/{ticket_id}/sign", response_model=FieldTicketOut)
def sign_field_ticket(ticket_id: int, data: SignIn, db: Session = Depends(get_db)):
    """Record who signed the paper: the typed name and the date (attach the
    photo of the signed ticket as well). A T&M ticket becomes billable; a
    delivery ticket records its receiver. A material slip is posted, not
    signed."""
    svc.require_construction(db)
    t = _get(db, ticket_id)
    if t.kind == "material":
        raise HTTPException(
            status_code=409,
            detail="A material slip is not signed; post it to put the material on the job.",
        )
    _refuse_unless_draft(t, "signed")
    if not t.lines:
        raise HTTPException(
            status_code=422, detail="Add the ticket's lines before it is signed."
        )
    t.signed_by_name = data.signed_by_name
    t.signed_at = data.signed_at or date.today()
    t.status = "signed"
    db.commit()
    db.refresh(t)
    return _out(db, t)


@router.post("/{ticket_id}/post", response_model=FieldTicketOut)
def post_material_slip(
    ticket_id: int, data: PostIn | None = None, db: Session = Depends(get_db)
):
    """Post a material slip: a Job Cost Entry (source material_slip) puts the
    stock on the job at each item's average cost, DR job cost / CR the item's
    inventory asset account, and on-hand quantity goes down (a negative
    quantity returns stock). It posts on the slip's own date (a `date` sent
    must be that date). Stock gone short is refused or warned of as an
    invoice's is, by the company's refuse-oversell setting. The slip closes;
    voiding it reverses at the booked cost."""
    svc.require_construction(db)
    t = _get(db, ticket_id)
    warnings = svc.post_material_slip(db, t, data.date if data else None)
    db.commit()
    db.refresh(t)
    out = _out(db, t)
    out.warnings = out.warnings + warnings
    return out


@router.post("/{ticket_id}/void", response_model=FieldTicketOut)
def void_field_ticket(
    ticket_id: int, data: VoidIn | None = None, db: Session = Depends(get_db)
):
    """Void a ticket while nothing it led to is approved: a T&M ticket on a
    live change order or a billed invoice is refused (reject or void that
    first); a posted material slip reverses its job cost at the booked cost;
    a delivery ticket gives its received quantity back."""
    svc.require_construction(db)
    t = _get(db, ticket_id)
    if t.status == "void":
        raise HTTPException(
            status_code=409, detail="This field ticket is already void."
        )
    svc.void_ticket(db, t)
    t.status = "void"
    t.voided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(t)
    return _out(db, t)


# ── T&M ticket → change order or invoice ──────────────────────────────────


@router.post("/make-change-order")
def make_change_order(data: TicketIdsIn, db: Session = Depends(get_db)):
    """Price one or several signed T&M tickets on one job as a potential
    change order to the owner: a line per ticket line at cost, with the
    job's change-order markup. A sub's ticket (one naming a commitment) also
    goes on a draft sub change order on that commitment, at cost. Each ticket
    links to them and closes when the owner change order is approved
    (promote the PCO, then approve)."""
    from app.routes.change_orders import _co_out

    svc.require_construction(db)
    made = svc.make_change_order(db, data.ticket_ids)
    db.commit()
    return {
        "change_order": _co_out(db, made["change_order"]),
        "sub_change_orders": [_co_out(db, s) for s in made["sub_change_orders"]],
        "tickets": [_out(db, t) for t in made["tickets"]],
    }


@router.post("/bill")
def bill_field_tickets(data: BillIn, db: Session = Depends(get_db)):
    """Bill one or several signed T&M tickets on one job: an invoice to the
    job's customer with a line per ticket line at its bill rate. Only for a
    job with no schedule of values (one that bills on pay applications makes
    a change order instead). The tickets close; voiding the invoice opens
    them again."""
    svc.require_construction(db)
    invoice = svc.bill_tickets(db, data.ticket_ids, data.date)
    db.commit()
    tickets = db.query(FieldTicket).filter(FieldTicket.invoice_id == invoice.id).all()
    return {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "total": float(invoice.total),
        "tickets": [_out(db, t) for t in tickets],
    }
