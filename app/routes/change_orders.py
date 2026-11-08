# ============================================================================
# Change orders (#554) — the document and its lifecycle.
#
# A change order is a potential change (PCO), an owner change order or a sub
# change order. It is bookkeeper-writable (like jobs); every effect on the
# contract, the SOV, the budget and the commitment happens on approve and
# reverses on void, in one transaction (app/services/change_orders.py).
# ============================================================================

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.change_orders import ChangeOrder, ChangeOrderLine
from app.models.cost_codes import CostCode
from app.models.jobs import Job, SovLine
from app.models.purchase_orders import PurchaseOrder
from app.models.trade_packages import TradePackage
from app.schemas.change_orders import (
    AllowanceReturnIn,
    ApproveIn,
    ChangeOrderCreate,
    ChangeOrderOut,
    ChangeOrderUpdate,
    PromoteIn,
    SubOrderIn,
)
from app.services.change_orders import (
    _check_commitment,
    apply_change_order,
    check_allowance_line,
    check_owner_link,
    co_effective_date,
    co_totals,
    create_allowance_return,
    recompute,
    refuse_with_approved_sub,
    back_dating_warnings,
    check_approved_on,
    resolve_approved_on,
    reverse_change_order,
)
from app.services.closing_date import check_closing_date
from app.services.job_costing import line_cost_type
from app.services.numbering import next_change_order_number
from app.services.pdf_service import generate_change_order_pdf
from app.services.request_utils import content_disposition
from app.services.settings_service import get_all_settings as get_settings

router = APIRouter(prefix="/api/change-orders", tags=["change-orders"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _get(db: Session, co_id: int) -> ChangeOrder:
    co = db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(status_code=404, detail="Change order not found")
    return co


def _co_out(db: Session, co: ChangeOrder) -> ChangeOrderOut:
    recompute(co, co.job)
    out = ChangeOrderOut.model_validate(co)
    for line_out, line in zip(out.lines, co.lines):
        line_out.cost_code_label = line.cost_code.label if line.cost_code else None
        line_out.trade_package_name = (
            line.trade_package.name if line.trade_package else None
        )
    if co.kind == "owner":
        out.source_pco_ids = [
            r[0]
            for r in db.query(ChangeOrder.id)
            .filter(ChangeOrder.promoted_to_id == co.id)
            .order_by(ChangeOrder.id)
        ]
    totals = co_totals(co, co.job)
    out.cost_total = float(totals["cost_total"])
    out.markup_total = float(totals["markup_total"])
    out.sell_total = float(totals["sell_total"])
    out.by_cost_type = {
        t: {
            "cost": float(v["cost"]),
            "sell": float(v["sell"]),
            "markup": float(v["markup"]),
        }
        for t, v in totals["by_cost_type"].items()
    }
    return out


def _validate_line_refs(db: Session, line) -> str:
    """Validate a line's cost type / code / package, returning the effective
    cost type (required for a change-order line)."""
    if line.cost_code_id and not db.get(CostCode, line.cost_code_id):
        raise HTTPException(status_code=404, detail="Cost code not found")
    if line.trade_package_id and not db.get(TradePackage, line.trade_package_id):
        raise HTTPException(status_code=404, detail="Trade package not found")
    ctype = line_cost_type(db, line.cost_code_id, line.cost_type)
    if ctype is None:
        raise HTTPException(
            status_code=422, detail="A change order line needs a cost type"
        )
    return ctype


def _set_lines(db: Session, co: ChangeOrder, lines) -> None:
    db.query(ChangeOrderLine).filter(ChangeOrderLine.change_order_id == co.id).delete()
    db.flush()
    for i, line in enumerate(lines):
        ctype = _validate_line_refs(db, line)
        db.add(
            ChangeOrderLine(
                change_order_id=co.id,
                line_order=i,
                description=line.description,
                cost_type=ctype,
                cost_code_id=line.cost_code_id,
                trade_package_id=line.trade_package_id,
                quantity=line.quantity,
                unit=line.unit,
                unit_cost=line.unit_cost,
                markup_pct=line.markup_pct,
            )
        )
    db.flush()
    db.refresh(co)
    recompute(co, co.job)


def _create_numbered(db: Session, job: Job, number=None, **fields) -> ChangeOrder:
    """Build and flush a ChangeOrder with the next number for its job and kind
    (or the given `number`, for a PCO revision), with the create_po
    race-retry pattern: next_change_order_number is MAX+1 with no row lock, so two concurrent creates can collide on the `number` UNIQUE
    constraint. Rebuild the row inside the loop after a rollback (a rolled-back
    ORM object cannot be re-flushed)."""
    last_err = None
    for _ in range(10):
        co = ChangeOrder(
            number=number or next_change_order_number(db, job, fields["kind"]),
            job_id=job.id,
            **fields,
        )
        db.add(co)
        try:
            db.flush()
            return co
        except IntegrityError as e:
            if "number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
    raise HTTPException(
        status_code=503, detail="Could not assign a change order number; please retry."
    ) from last_err


def _check_refs(db: Session, co_job_id: int, funding: str, allowance_id, owner_id):
    """Validate what a CO points at: its allowance line (allowance funding)
    and its backing owner CO (sub CO)."""
    if funding == "allowance":
        if allowance_id is None:
            raise HTTPException(
                status_code=422,
                detail="An allowance-funded change order needs an allowance line.",
            )
        check_allowance_line(db, co_job_id, allowance_id)
    check_owner_link(db, co_job_id, owner_id)


def _default_apply_markup(funding: str) -> bool:
    """Markup is on for contract-funded work, off for allowance/contingency."""
    return funding == "contract"


def _copy_lines(
    db: Session, change_order_id: int, lines, start: int = 0, default_markup=None
) -> None:
    """Copy ChangeOrderLine rows onto another change order (promote / sub /
    revise). `default_markup` fills a line that has none of its own."""
    for i, line in enumerate(lines, start):
        db.add(
            ChangeOrderLine(
                change_order_id=change_order_id,
                line_order=i,
                description=line.description,
                cost_type=line.cost_type,
                cost_code_id=line.cost_code_id,
                trade_package_id=line.trade_package_id,
                quantity=line.quantity,
                unit=line.unit,
                unit_cost=line.unit_cost,
                markup_pct=(
                    line.markup_pct if line.markup_pct is not None else default_markup
                ),
            )
        )
    db.flush()


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ChangeOrderOut])
def list_change_orders(
    job_id: int = Query(...),
    history: bool = Query(False),
    db: Session = Depends(get_db),
):
    """The job's change orders. A PCO revision replaces the one before it, so
    the list shows the latest revision only; history=true adds the superseded
    ones."""
    q = db.query(ChangeOrder).filter(ChangeOrder.job_id == job_id)
    if not history:
        q = q.filter(ChangeOrder.status != "superseded")
    cos = q.order_by(ChangeOrder.id).all()
    return [_co_out(db, co) for co in cos]


@router.get("/{co_id}", response_model=ChangeOrderOut)
def get_change_order(co_id: int, db: Session = Depends(get_db)):
    return _co_out(db, _get(db, co_id))


@router.post("", response_model=ChangeOrderOut, status_code=201)
def create_change_order(data: ChangeOrderCreate, db: Session = Depends(get_db)):
    job = db.get(Job, data.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if data.kind == "sub" and data.commitment_id is None:
        raise HTTPException(
            status_code=422, detail="A sub change order needs a commitment."
        )
    _check_refs(
        db, job.id, data.funding, data.allowance_sov_line_id, data.owner_change_order_id
    )
    co = _create_numbered(
        db,
        job,
        kind=data.kind,
        status="draft",
        title=data.title,
        description=data.description,
        date=data.date,
        funding=data.funding,
        allowance_sov_line_id=data.allowance_sov_line_id,
        markup_pct=data.markup_pct,
        apply_markup=(
            data.funding == "contract"  # contract work is always marked up
            or (
                _default_apply_markup(data.funding)
                if data.apply_markup is None
                else data.apply_markup
            )
        ),
        commitment_id=data.commitment_id,
        owner_change_order_id=data.owner_change_order_id,
    )
    _set_lines(db, co, data.lines)
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.put("/{co_id}", response_model=ChangeOrderOut)
def update_change_order(
    co_id: int, data: ChangeOrderUpdate, db: Session = Depends(get_db)
):
    co = _get(db, co_id)
    if co.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_locked",
                "message": f"A {co.status} change order cannot be edited.",
            },
        )
    fields = data.model_dump(exclude_unset=True, exclude={"lines"})
    fixed = ("funding", "allowance_sov_line_id", "markup_pct", "apply_markup")
    if co.allowance_return and any(
        k in fields and fields[k] != getattr(co, k) for k in fixed
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "allowance_return_fixed",
                "message": "A return of unspent allowance is contract-funded with no markup; change its amount, title or date, or reject it and make another.",
            },
        )
    if fields.get("funding") not in (None, co.funding) and "apply_markup" not in fields:
        # Changing what pays for the change resets markup to that funding's default.
        fields["apply_markup"] = _default_apply_markup(fields["funding"])
    for key, val in fields.items():
        if key == "apply_markup" and val is None:
            continue
        setattr(co, key, val)
    if co.funding == "contract":
        co.apply_markup = True  # the flag only governs allowance/contingency work
    _check_refs(
        db, co.job_id, co.funding, co.allowance_sov_line_id, co.owner_change_order_id
    )
    if data.lines is not None:
        _set_lines(db, co, data.lines)
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.delete("/{co_id}")
def delete_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_draft",
                "message": "Only a draft change order can be deleted.",
            },
        )
    db.delete(co)
    db.commit()
    return {"deleted": True}


# ── Lifecycle ─────────────────────────────────────────────────────────────────


@router.post("/{co_id}/submit", response_model=ChangeOrderOut)
def submit_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "draft":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_draft",
                "message": "Only a draft can be submitted.",
            },
        )
    co.status = "pending"
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/allowance-return", response_model=ChangeOrderOut, status_code=201)
def return_unspent_allowance(data: AllowanceReturnIn, db: Session = Depends(get_db)):
    """Make a draft owner change order that returns an allowance's unspent
    amount to the owner (all of it, or `amount`): minus the amount, no markup,
    funded against that allowance. Approving it takes the amount off the
    contract and the allowance line, so the line has nothing left to bill.
    `date` (default today) dates the change order: not before the contract
    date, and not in a closed period."""
    line = db.get(SovLine, data.allowance_sov_line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Allowance line not found")
    job = db.get(Job, line.job_id)
    if data.date is not None:
        contract_date = job.contract_date
        if contract_date is not None and data.date < contract_date:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The return is dated {data.date.isoformat()}, before the "
                    f"contract is dated ({contract_date.isoformat()}). Choose "
                    f"{contract_date.isoformat()} or a later day."
                ),
            )
        check_closing_date(db, data.date)
    co = create_allowance_return(db, job, line.id, amount=data.amount, on=data.date)
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/approve", response_model=ChangeOrderOut)
def approve_change_order(
    co_id: int, data: ApproveIn | None = None, db: Session = Depends(get_db)
):
    """Approve a pending change order. It takes effect on `approved_on` (the
    change order's own date by default, else today); a date in a closed
    period is refused like a bill or a pay application there."""
    co = _get(db, co_id)
    if co.kind == "pco":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "promote_instead",
                "message": "A potential change order is promoted to an owner change order, not approved.",
            },
        )
    if co.status != "pending":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_pending",
                "message": f"A {co.status} change order cannot be approved.",
            },
        )
    given = data.approved_on if data else None
    on = resolve_approved_on(co, given)
    check_approved_on(co, given, on)
    check_closing_date(db, on)
    apply_change_order(db, co, on)  # may 409 contingency/allowance_exceeded
    db.commit()
    db.refresh(co)
    out = _co_out(db, co)
    out.warnings = back_dating_warnings(co, given, on)
    return out


@router.post("/{co_id}/reject", response_model=ChangeOrderOut)
def reject_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_pending",
                "message": f"A {co.status} change order cannot be rejected.",
            },
        )
    refuse_with_approved_sub(db, co, "reject")
    co.status = "rejected"
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/void", response_model=ChangeOrderOut)
def void_change_order(co_id: int, db: Session = Depends(get_db)):
    co = _get(db, co_id)
    if co.status != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_approved",
                "message": "Only an approved change order can be voided.",
            },
        )
    # Voiding takes the change order out as of the day it took effect, so a
    # closed period refuses it as approving it there would.
    effective = co_effective_date(co)
    if effective is not None:
        check_closing_date(db, effective)
    reverse_change_order(db, co)  # may 409 co_billed
    db.commit()
    db.refresh(co)
    return _co_out(db, co)


@router.post("/{co_id}/promote", response_model=ChangeOrderOut)
def promote_change_order(
    co_id: int, data: PromoteIn | None = None, db: Session = Depends(get_db)
):
    """Promote a PCO to a new owner change order, copying its lines. With
    `pco_ids`, several PCOs (this one included) roll up into the one owner CO:
    its lines are all of theirs, so its amount is their sum. Each PCO becomes
    `promoted` and links to the owner CO; the owner CO is returned."""
    first = _get(db, co_id)
    extra = dict.fromkeys(
        i for i in (data.pco_ids if data else None) or [] if i != co_id
    )
    pcos = [first] + [_get(db, i) for i in extra]
    for pco in pcos:
        if pco.kind != "pco":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "not_a_pco",
                    "message": "Only a potential change order can be promoted.",
                },
            )
        if pco.status not in ("draft", "pending"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "co_not_promotable",
                    "message": f"A {pco.status} potential change order cannot be promoted.",
                },
            )

    # One owner CO has one job and one source of money.
    def sig(p):
        return (p.job_id, p.funding, p.allowance_sov_line_id, p.apply_markup)

    if any(sig(p) != sig(first) for p in pcos[1:]):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pco_mismatch",
                "message": "PCOs rolled into one change order must share a job, funding, allowance line and markup setting.",
            },
        )
    same_markup = len({p.markup_pct for p in pcos}) == 1
    owner = _create_numbered(
        db,
        db.get(Job, first.job_id),
        kind="owner",
        status="draft",
        title=" / ".join(p.title for p in pcos if p.title)[:200] or None,
        description=(
            first.description
            if len(pcos) == 1
            else "\n".join(p.description for p in pcos if p.description) or None
        ),
        date=first.date,
        funding=first.funding,
        allowance_sov_line_id=first.allowance_sov_line_id,
        apply_markup=first.apply_markup,
        markup_pct=first.markup_pct if same_markup else None,
        source_pco_id=first.id,
    )
    order = 0
    for pco in pcos:
        # A line without its own markup would otherwise inherit the owner CO's
        # one default; keep what its PCO priced it at.
        _copy_lines(
            db,
            owner.id,
            pco.lines,
            start=order,
            default_markup=(None if same_markup else pco.markup_pct),
        )
        order += len(pco.lines)
    # Set after the numbering retry loop: a collision's rollback would undo it.
    for pco in pcos:
        pco.status = "promoted"
        pco.promoted_to_id = owner.id
    db.commit()
    db.refresh(owner)
    return _co_out(db, owner)


@router.post("/{co_id}/revise", response_model=ChangeOrderOut, status_code=201)
def revise_pco(co_id: int, db: Session = Depends(get_db)):
    """Revise a PCO: a new draft revision (same lines, to edit) replaces the
    current one, which stays as history (`superseded`). Only the latest
    revision can be revised or promoted."""
    prior = _get(db, co_id)
    if prior.kind != "pco":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_a_pco",
                "message": "Only a potential change order can be revised.",
            },
        )
    if prior.status not in ("draft", "pending"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "co_not_revisable",
                "message": f"A {prior.status} potential change order cannot be revised.",
            },
        )
    root = re.sub(r"-R\d+$", "", prior.number)
    rev = prior.revision + 1
    new = _create_numbered(
        db,
        db.get(Job, prior.job_id),
        number=f"{root}-R{rev}",
        kind="pco",
        status="draft",
        title=prior.title,
        description=prior.description,
        date=prior.date,
        funding=prior.funding,
        allowance_sov_line_id=prior.allowance_sov_line_id,
        markup_pct=prior.markup_pct,
        apply_markup=prior.apply_markup,
        revision=rev,
        supersedes_id=prior.id,
    )
    _copy_lines(db, new.id, prior.lines)
    prior.status = "superseded"
    db.commit()
    db.refresh(new)
    return _co_out(db, new)


@router.post("/{co_id}/sub-order", response_model=ChangeOrderOut)
def sub_order_from_owner(co_id: int, data: SubOrderIn, db: Session = Depends(get_db)):
    """From an owner change order, create a linked draft sub CO on a chosen
    commitment, copying the chosen lines (or the subcontract lines by
    default). A sub CO moves the commitment by cost; it carries no markup."""
    owner = _get(db, co_id)
    if owner.kind != "owner":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_an_owner_co",
                "message": "A sub change order is made from an owner change order.",
            },
        )
    po = db.get(PurchaseOrder, data.commitment_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Commitment not found")
    _check_commitment(po, owner)  # same job, open commitment
    if data.line_ids is not None:
        chosen = [ln for ln in owner.lines if ln.id in set(data.line_ids)]
    else:
        chosen = [ln for ln in owner.lines if ln.cost_type == "subcontract"]
    if not chosen:
        raise HTTPException(
            status_code=422,
            detail="No lines to carry to the sub change order.",
        )
    sub = _create_numbered(
        db,
        db.get(Job, owner.job_id),
        kind="sub",
        status="draft",
        title=owner.title,
        description=owner.description,
        date=owner.date,
        funding="contract",
        commitment_id=po.id,
        owner_change_order_id=owner.id,
    )

    _copy_lines(db, sub.id, chosen)
    db.commit()
    db.refresh(sub)
    return _co_out(db, sub)


@router.get("/{co_id}/pdf")
def change_order_pdf(co_id: int, db: Session = Depends(get_db)):
    """The owner CO / PCO document: title, description, the price (sell) per
    line and per cost type, and the total. Cost and markup stay in the app —
    they are the contractor's own and are not on the owner's document."""
    co = _get(db, co_id)
    recompute(co, co.job)
    company = get_settings(db)
    totals = co_totals(co, co.job)
    pdf_bytes = generate_change_order_pdf(co, totals, company)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(f"ChangeOrder_{co.number}.pdf")
        },
    )
