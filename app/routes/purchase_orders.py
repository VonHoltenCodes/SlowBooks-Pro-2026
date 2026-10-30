# ============================================================================
# Purchase Orders — CRUD + convert to bill
# Feature 6: Non-posting vendor documents
# ============================================================================

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.routes._helpers import clamp_pagination, reject_null_updates
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine, POStatus
from app.models.contacts import Vendor
from app.schemas.purchase_orders import (
    POConvertToBill,
    POCreate,
    POResponse,
    POUpdate,
)
from app.schemas.pay_applications import ReleaseRetainageRequest
from app.services.accounting import _q, compute_line_totals
from app.services.job_costing import line_cost_type
from app.services.numbering import next_po_number
from app.services.request_utils import content_disposition
from app.services.purchase_posting import (
    expense_account_for,
    spread,
    unit_cost_with_tax,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase_orders"])


def append_commitment_lines(
    db: Session, po: PurchaseOrder, co_lines, change_order_id: int
) -> None:
    """Append lines to a commitment without touching the ones it already has
    (#554 sub change orders). Unlike update_po, which replaces every line,
    this is append-only: each new PurchaseOrderLine starts unbilled, carries
    the change order it came from (so void can remove exactly these), and the
    PO's subtotal/tax/total are recomputed from all its lines.

    `co_lines` are ChangeOrderLine rows; a line's unit cost becomes the PO
    line's rate and its cost becomes the amount (a commitment moves by cost).
    """
    start = len(po.lines)
    for i, line in enumerate(co_lines):
        db.add(
            PurchaseOrderLine(
                purchase_order_id=po.id,
                description=line.description,
                quantity=line.quantity,
                rate=line.unit_cost,
                amount=_q(Decimal(str(line.amount or 0))),
                billed_amount=Decimal("0"),
                job_id=po.job_id,
                cost_code_id=line.cost_code_id,
                cost_type=line.cost_type,
                change_order_id=change_order_id,
                line_order=start + i,
            )
        )
    db.flush()
    db.refresh(po)
    _retotal_commitment(po)
    db.flush()


def _retotal_commitment(po: PurchaseOrder) -> None:
    """Recompute a commitment's subtotal/tax/total from its current lines."""
    subtotal = _q(sum((Decimal(str(ln.amount or 0)) for ln in po.lines), Decimal("0")))
    tax_rate = Decimal(str(po.tax_rate or 0))
    po.subtotal = subtotal
    po.tax_amount = _q(subtotal * tax_rate)
    po.total = _q(subtotal + po.tax_amount)


@router.get("", response_model=list[POResponse])
def list_pos(
    vendor_id: int = None,
    status: str = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load to avoid N+1 on .vendor and .lines during model_validate.
    q = db.query(PurchaseOrder).options(
        joinedload(PurchaseOrder.vendor),
        selectinload(PurchaseOrder.lines),
    )
    if vendor_id:
        q = q.filter(PurchaseOrder.vendor_id == vendor_id)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    pos = (
        q.order_by(PurchaseOrder.date.desc(), PurchaseOrder.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    results = []
    for po in pos:
        resp = POResponse.model_validate(po)
        if po.vendor:
            resp.vendor_name = po.vendor.name
        if po.trade_package:
            resp.trade_package_name = po.trade_package.name
        results.append(resp)
    return results


@router.get("/{po_id}", response_model=POResponse)
def get_po(po_id: int, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    resp = POResponse.model_validate(po)
    if po.vendor:
        resp.vendor_name = po.vendor.name
    if po.trade_package:
        resp.trade_package_name = po.trade_package.name
    return resp


@router.post("", response_model=POResponse, status_code=201)
def create_po(data: POCreate, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor_id = vendor.id
    vendor_name = vendor.name
    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)

    po = None
    po_number = None
    last_err = None
    # Same race as create_invoice / create_estimate: next_po_number is MAX+1
    # without a row-level lock, so two concurrent creates can collide on the
    # po_number UNIQUE constraint. Retry on IntegrityError.
    for _ in range(10):
        po_number = next_po_number(db)
        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor_id,
            date=data.date,
            expected_date=data.expected_date,
            ship_to=data.ship_to,
            subtotal=subtotal,
            tax_rate=data.tax_rate,
            tax_amount=tax_amount,
            total=total,
            notes=data.notes,
            job_id=data.job_id,
            commitment_type=data.commitment_type,
            trade_package_id=data.trade_package_id,
            retainage_pct=data.retainage_pct,
            scope_notes=data.scope_notes,
        )
        db.add(po)
        try:
            db.flush()
            break
        except IntegrityError as e:
            if "po_number" not in str(e.orig).lower():
                raise
            last_err = e
            db.rollback()
            po = None
    if po is None:
        raise HTTPException(
            status_code=503,
            detail="Could not assign a unique PO number; please retry.",
        ) from last_err

    for i, line_data in enumerate(data.lines):
        line = PurchaseOrderLine(
            purchase_order_id=po.id,
            item_id=line_data.item_id,
            description=line_data.description,
            quantity=line_data.quantity,
            rate=line_data.rate,
            amount=_q(Decimal(str(line_data.quantity)) * Decimal(str(line_data.rate))),
            job_id=line_data.job_id,
            cost_code_id=line_data.cost_code_id,
            cost_type=line_cost_type(db, line_data.cost_code_id, line_data.cost_type),
            line_order=i,
        )
        db.add(line)

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    resp.vendor_name = vendor_name
    if po.trade_package:
        resp.trade_package_name = po.trade_package.name
    return resp


@router.put("/{po_id}", response_model=POResponse)
def update_po(po_id: int, data: POUpdate, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    was_closed = po.status == POStatus.CLOSED
    update_data = reject_null_updates(
        PurchaseOrder, data.model_dump(exclude_unset=True, exclude={"lines"})
    )
    for key, val in update_data.items():
        if key == "status":
            setattr(po, key, POStatus(val))
        else:
            setattr(po, key, val)

    if data.lines is not None:
        # Carry each line's billed-to-date (and its bill links) across the
        # edit (#553): a line the request still names by id is updated in
        # place, not dropped and recreated. A billed line that the edit would
        # remove, or whose amount would fall below what has been billed, is a
        # change only a sub change order can make — refuse it (409).
        existing = {ln.id: ln for ln in po.lines}
        incoming_ids = {ld.id for ld in data.lines if ld.id}
        line_amounts = [
            _q(Decimal(str(ld.quantity)) * Decimal(str(ld.rate))) for ld in data.lines
        ]
        _billed = "This commitment has been billed; change it with a sub change order."
        for ln in po.lines:
            if float(ln.billed_amount or 0) > 0 and ln.id not in incoming_ids:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "commitment_billed", "message": _billed},
                )
        # Lines a sub change order added belong to it: a plain edit can
        # neither drop them nor change their amount, job, cost code or type (void the sub CO).
        _co_owned = "A sub change order added a line on this commitment; void that change order to change it."
        for ln in po.lines:
            if ln.change_order_id is not None and ln.id not in incoming_ids:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "commitment_co_line", "message": _co_owned},
                )
        for ld, amt in zip(data.lines, line_amounts):
            if ld.id and ld.id in existing:
                cur = existing[ld.id]
                if cur.change_order_id is not None and (
                    amt != _q(cur.amount)
                    or (ld.job_id is not None and ld.job_id != cur.job_id)
                    or (
                        ld.cost_code_id is not None
                        and ld.cost_code_id != cur.cost_code_id
                    )
                    or (ld.cost_type is not None and ld.cost_type != cur.cost_type)
                    or Decimal(str(ld.quantity)) != Decimal(str(cur.quantity))
                    or Decimal(str(ld.rate)) != Decimal(str(cur.rate))
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "commitment_co_line", "message": _co_owned},
                    )
                billed = Decimal(str(existing[ld.id].billed_amount or 0))
                if billed > 0 and amt < billed:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "commitment_billed", "message": _billed},
                    )

        # A fully billed (closed) commitment cannot take more dollars: it
        # would stay closed and the extra could never be billed.
        if was_closed and sum(line_amounts, Decimal("0")) > sum(
            (Decimal(str(ln.amount or 0)) for ln in po.lines), Decimal("0")
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "commitment_closed",
                    "message": (
                        "This commitment is fully billed; raise it with a "
                        "sub change order."
                    ),
                },
            )

        for ln in list(po.lines):
            if ln.id not in incoming_ids:
                db.delete(ln)
        for i, (line_data, amt) in enumerate(zip(data.lines, line_amounts)):
            ct = line_cost_type(db, line_data.cost_code_id, line_data.cost_type)
            if line_data.id and line_data.id in existing:
                ln = existing[line_data.id]
                ln.item_id = line_data.item_id
                ln.description = line_data.description
                ln.quantity = line_data.quantity
                ln.rate = line_data.rate
                ln.amount = amt
                if ln.change_order_id is None:  # a CO line keeps its job/code/type
                    ln.job_id = line_data.job_id
                    ln.cost_code_id = line_data.cost_code_id
                    ln.cost_type = ct
                ln.line_order = i
            else:
                db.add(
                    PurchaseOrderLine(
                        purchase_order_id=po_id,
                        item_id=line_data.item_id,
                        description=line_data.description,
                        quantity=line_data.quantity,
                        rate=line_data.rate,
                        amount=amt,
                        job_id=line_data.job_id,
                        cost_code_id=line_data.cost_code_id,
                        cost_type=ct,
                        line_order=i,
                    )
                )
        tax_rate = data.tax_rate if data.tax_rate is not None else po.tax_rate
        subtotal, tax_amount, total = compute_line_totals(data.lines, tax_rate)
        po.subtotal = subtotal
        po.tax_amount = tax_amount
        po.total = total
    elif data.tax_rate is not None:
        # A new rate without the lines re-totals the stored lines. It changed
        # the rate and kept the old tax and total, and the bill the order
        # became carried that tax.
        subtotal, tax_amount, total = compute_line_totals(po.lines, data.tax_rate)
        po.subtotal = subtotal
        po.tax_amount = tax_amount
        po.total = total

    db.commit()
    db.refresh(po)
    resp = POResponse.model_validate(po)
    if po.vendor:
        resp.vendor_name = po.vendor.name
    if po.trade_package:
        resp.trade_package_name = po.trade_package.name
    return resp


def _po_html(db: Session, po_id: int) -> tuple[PurchaseOrder, str]:
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po, _render("purchase_order_pdf.html", get_all_settings(db), po=po)


@router.get("/{po_id}/pdf")
def po_pdf(po_id: int, db: Session = Depends(get_db)):
    """The purchase order as a PDF, to send to the vendor. A PO could be
    created and turned into a bill, but never seen or sent (skytech W-L19)."""
    from app.services.pdf_service import render_pdf

    po, html_str = _po_html(db, po_id)
    return Response(
        content=render_pdf(html_str),
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"PurchaseOrder_{po.po_number}.pdf"
            )
        },
    )


@router.get("/{po_id}/print-preview")
def po_print_preview(po_id: int, db: Session = Depends(get_db)):
    """The purchase order as a page that opens the print dialog."""
    _, html_str = _po_html(db, po_id)
    return HTMLResponse(
        content=html_str.replace(
            "</body>",
            "<script>window.onload=function(){window.print();}</script></body>",
        )
    )


def _commitment_retainage(db: Session, po: PurchaseOrder):
    """(held_to_date, released_to_date) for a commitment: Σ retainage_held of
    its non-void bills, and what has been released so far."""
    from app.models.bills import Bill, BillStatus

    held = _q(
        sum(
            (
                _q(b.retainage_held or 0)
                for b in db.query(Bill).filter(
                    Bill.po_id == po.id, Bill.status != BillStatus.VOID
                )
            ),
            Decimal("0"),
        )
    )
    return held, _q(po.retainage_released or 0)


@router.get("/{po_id}/pay-app")
def commitment_pay_app(po_id: int, db: Session = Depends(get_db)):
    """A commitment's schedule for a sub pay application (#555): per line
    scheduled / previous billed / remaining, the retainage held and released
    to date, and its bills with their lien-waiver status (warn-only)."""
    from app.models.bills import Bill, BillStatus

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    lines = []
    for ln in po.lines:
        scheduled = _q(ln.amount or 0)
        billed = _q(ln.billed_amount or 0)
        lines.append(
            {
                "line_id": ln.id,
                "description": ln.description or "",
                "scheduled": float(scheduled),
                "previous_billed": float(billed),
                "remaining": float(_q(scheduled - billed)),
            }
        )
    held, released = _commitment_retainage(db, po)
    bills = (
        db.query(Bill)
        .filter(Bill.po_id == po.id, Bill.status != BillStatus.VOID)
        .order_by(Bill.date, Bill.id)
        .all()
    )
    return {
        "po_id": po.id,
        "po_number": po.po_number,
        "vendor_name": po.vendor.name if po.vendor else "",
        "retainage_pct": (
            float(po.retainage_pct) if po.retainage_pct is not None else None
        ),
        "lines": lines,
        "scheduled_total": float(
            _q(sum((Decimal(str(ln.amount or 0)) for ln in po.lines), Decimal("0")))
        ),
        "billed_total": float(
            _q(
                sum(
                    (Decimal(str(ln.billed_amount or 0)) for ln in po.lines),
                    Decimal("0"),
                )
            )
        ),
        "retainage_held_to_date": float(held),
        "retainage_released_to_date": float(released),
        "retainage_outstanding": float(_q(held - released)),
        "bills": [
            {
                "bill_id": b.id,
                "bill_number": b.bill_number,
                "date": b.date.isoformat() if b.date else None,
                "retainage_held": float(_q(b.retainage_held or 0)),
                "retainage_released": float(_q(b.retainage_released or 0)),
                "lien_waiver_status": b.lien_waiver_status,
            }
            for b in bills
        ],
    }


@router.post("/{po_id}/release-retainage")
def release_retainage(
    po_id: int,
    data: Optional[ReleaseRetainageRequest] = None,
    db: Session = Depends(get_db),
):
    """Release retainage held on a commitment (#555): a bill for the vendor
    with one line carrying no expense account, DR Retainage Payable (2050) /
    CR A/P for the amount, payable through Pay Bills. 422 over what is held."""
    from app.models.bills import Bill, BillLine, BillStatus
    from app.routes.invoices.helpers import _due_date_from_terms
    from app.services.accounting import (
        create_journal_entry,
        ensure_construction_accounts,
        get_ap_account_id,
    )
    from app.services.closing_date import check_closing_date

    if data is None:
        data = ReleaseRetainageRequest()
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    held, released = _commitment_retainage(db, po)
    outstanding = _q(held - released)
    amount = _q(data.amount) if data.amount is not None else outstanding
    if amount <= 0:
        raise HTTPException(
            status_code=422, detail="No retainage is outstanding to release."
        )
    if amount > outstanding:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Release {amount:.2f} is more than the {outstanding:.2f} of "
                "retainage held on this commitment."
            ),
        )
    when = data.date or po.date
    check_closing_date(db, when)
    vendor = po.vendor
    terms = (vendor.terms if vendor else None) or "Net 30"
    ap_id = get_ap_account_id(db)
    retainage_acct = ensure_construction_accounts(db)["2050"]

    base = f"RET-{po.po_number}"
    taken = {
        n
        for (n,) in db.query(Bill.bill_number).filter(
            Bill.vendor_id == po.vendor_id, Bill.bill_number.like(f"{base}%")
        )
    }
    bill_number, k = base, 1
    while bill_number in taken:
        k += 1
        bill_number = f"{base}-{k}"

    bill = Bill(
        bill_number=bill_number,
        vendor_id=po.vendor_id,
        status=BillStatus.UNPAID,
        po_id=po.id,
        date=when,
        terms=terms,
        due_date=_due_date_from_terms(db, when, terms),
        subtotal=amount,
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=amount,
        retainage_released=amount,
        balance_due=amount,
        notes=f"Retainage release — {po.po_number}",
        job_id=po.job_id,
    )
    db.add(bill)
    db.flush()
    db.add(
        BillLine(
            bill_id=bill.id,
            description=f"Retainage release — {po.po_number}",
            quantity=Decimal("1"),
            rate=amount,
            amount=amount,
            job_id=po.job_id,
            line_order=0,
        )
    )
    txn = create_journal_entry(
        db,
        when,
        f"Retainage release — {po.po_number}",
        [
            {
                "account_id": retainage_acct.id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"Retainage release — {po.po_number}",
                "job_id": po.job_id,
            },
            {
                "account_id": ap_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"Retainage release — {po.po_number} - {vendor.name if vendor else ''}",
            },
        ],
        source_type="bill",
        source_id=bill.id,
        job_id=po.job_id,
    )
    bill.transaction_id = txn.id
    po.retainage_released = _q(released + amount)
    db.commit()
    return {
        "bill_id": bill.id,
        "amount": float(amount),
        "message": f"Retainage release bill created from {po.po_number}",
    }


@router.post("/{po_id}/convert-to-bill")
def convert_to_bill(
    po_id: int,
    data: Optional[POConvertToBill] = None,
    db: Session = Depends(get_db),
):
    """Convert a PO to a bill — creates bill with PO's line items AND the
    corresponding double-entry journal + inventory movements.

    Pre-Phase-11 this function created an orphan Bill row with no JE at all
    (expense + AP side were both missing). That was a silent accounting bug.

    Each line posts where Enter Bill would post it (see
    services/purchase_posting.py): the account chosen for it in the To Bill
    dialog (``lines: [{line_id, account_id}]``), else its item's expense
    account, else the vendor's default — and a line none of them names is
    refused rather than booked to account 6000 (Advertising). Tax on the PO
    is part of the lines' cost, never a debit to Sales Tax Payable. The bill
    takes the vendor's terms and a due date from them.
    """
    from app.models.bills import Bill, BillLine, BillStatus
    from app.models.items import Item as ItemModel
    from app.routes.invoices.helpers import _due_date_from_terms
    from app.services.accounting import (
        create_journal_entry,
        ensure_construction_accounts,
        get_ap_account_id,
    )
    from app.services.closing_date import check_closing_date
    from app.services.inventory_service import (
        get_inventory_asset_account_id,
        record_purchase,
    )

    # Row lock for the read-check-write on billed_amount, as bill_payments
    # does for bills. No-op on SQLite.
    po = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.id == po_id)
        .with_for_update()
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    # The bill is dated when it is billed (the To Bill dialog's date, default
    # today), not to the PO: a progress bill months into a subcontract ages
    # from its own date. Without a date the PO's own is used, as before.
    # The closing-date check covers the date actually posted.
    bill_date = (data.date if data and data.date else None) or po.date
    check_closing_date(db, bill_date)
    if po.status == POStatus.CLOSED:
        raise HTTPException(status_code=400, detail="PO already closed")
    if Decimal(str(po.total or 0)) <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{po.po_number} comes to $0.00, so there is nothing to bill. "
                "Enter the prices on its lines, then turn it into a bill."
            ),
        )

    # The dialog's choices per line: the account to post to, and this period's
    # amount (#553 partial billing). An amount left out bills the line's full
    # remaining (amount - billed_amount), which is the whole line the first
    # time — so a plain PO with no amounts given bills in full as it always
    # did and closes.
    chosen: dict[int, int] = {}
    asked: dict[int, Decimal] = {}
    po_line_ids = {ln.id for ln in po.lines}
    for pick in data.lines if data else []:
        if pick.line_id not in po_line_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Line id {pick.line_id} is not a line of {po.po_number}. "
                    "Open the order again and choose the accounts from there."
                ),
            )
        if pick.account_id is not None:
            chosen[pick.line_id] = pick.account_id
        if pick.amount is not None:
            asked[pick.line_id] = _q(pick.amount)

    # This period's amount per line. With no amounts given at all (the
    # business To Bill dialog, or no body), every line bills its full
    # remaining (amount − billed_amount), as it always did. Once any amount is
    # given, the request is a partial bill and chooses lines explicitly: an
    # amount bills that line (negative is refused, over-remaining is 422), a
    # zero means skip it this period, and a line with no amount is left for a
    # later period. The construction dialog sends an amount for every line
    # (zeros included), so an all-zero submit bills nothing → 409.
    lines = list(po.lines)
    partial_request = bool(asked)
    this_period: dict[int, Decimal] = {}
    for poline in lines:
        remaining = _q(Decimal(str(poline.amount or 0))) - _q(
            Decimal(str(poline.billed_amount or 0))
        )
        if poline.id in asked:
            amt = asked[poline.id]
            if amt < 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"Line amount {amt:.2f} cannot be negative.",
                )
            if amt > remaining:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Line amount {amt:.2f} is more than the "
                        f"{remaining:.2f} left to bill on that line."
                    ),
                )
            this_period[poline.id] = amt  # a zero skips the line this period
        elif partial_request:
            this_period[poline.id] = Decimal("0")
        else:
            this_period[poline.id] = remaining if remaining > 0 else Decimal("0")

    billable = [ln for ln in lines if this_period[ln.id] > 0]
    if not billable:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{po.po_number} has nothing left to bill — enter an amount on "
                "at least one line, or bill the remaining periods."
            ),
        )

    vendor = po.vendor
    terms = (vendor.terms if vendor else None) or "Net 30"
    ap_id = get_ap_account_id(db)  # a missing A/P account is said first (#119)

    # The bill's money comes from this period's amounts, not the whole PO, so
    # a partial bill posts only what is billed now. Tax follows the subtotal
    # at the PO's rate (POs have no per-line tax flag); for a full first bill
    # this reproduces the PO's own subtotal/tax/total exactly.
    amounts = [this_period[ln.id] for ln in billable]
    subtotal = _q(sum(amounts, Decimal("0")))
    tax_rate = Decimal(str(po.tax_rate or 0))
    tax_amount = _q(subtotal * tax_rate)
    total = _q(subtotal + tax_amount)
    tax_shares = spread(tax_amount, amounts)

    # Where each line posts, decided before anything is written so a refused
    # line leaves no bill behind.
    plan = []
    for i, poline in enumerate(billable):
        amt = amounts[i]
        item = (
            db.query(ItemModel).filter(ItemModel.id == poline.item_id).first()
            if poline.item_id
            else None
        )
        if item and item.track_inventory:
            posting_acct = get_inventory_asset_account_id(db, item)
            if not posting_acct:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Item '{item.name}' is inventory-tracked but has no "
                        "asset account and no #1300 is seeded."
                    ),
                )
        else:
            posting_acct = expense_account_for(
                db,
                line_no=i + 1,
                description=poline.description,
                account_id=chosen.get(poline.id),
                item=item,
                vendor=vendor,
                fix_hint="Choose an account for it when you turn the order into a bill",
            )
        plan.append((poline, item, amt, tax_shares[i], posting_acct))

    # A vendor has one bill per number (#501's unique index): a hand-entered
    # "BILL-PO-0001" already there makes this one "BILL-PO-0001-2".
    bill_number = f"BILL-{po.po_number}"
    taken = {
        n
        for (n,) in db.query(Bill.bill_number).filter(
            Bill.vendor_id == po.vendor_id, Bill.bill_number.like(f"{bill_number}%")
        )
    }
    base, n = bill_number, 1
    while bill_number in taken:
        n += 1
        bill_number = f"{base}-{n}"

    # Retainage: a commitment with a retainage_pct holds that percent of this
    # bill's subtotal back on Retainage Payable (2050) instead of A/P, so Pay
    # Bills pays only the net. #555 releases it (a debit to 2050).
    retainage_held = Decimal("0")
    if po.retainage_pct:
        retainage_held = _q(subtotal * Decimal(str(po.retainage_pct)) / Decimal("100"))

    # Give the bill a currency + rate like Enter Bill does; a converted bill
    # with neither made PUT /api/bills/{id} 500 in currency.convert_lines.
    from app.services.currency import resolve_rate

    doc_currency, doc_rate = resolve_rate(db, None, None)

    bill = Bill(
        bill_number=bill_number,
        vendor_id=po.vendor_id,
        status=BillStatus.UNPAID,
        po_id=po.id,
        date=bill_date,
        currency=doc_currency,
        exchange_rate=doc_rate,
        # The vendor's terms, and the due date they give — a converted bill
        # had "Net 30" and no due date, so it never aged (macbase1 F10).
        terms=terms,
        due_date=_due_date_from_terms(db, bill_date, terms),
        subtotal=subtotal,
        tax_rate=po.tax_rate,
        tax_amount=tax_amount,
        total=total,
        retainage_held=retainage_held,
        balance_due=_q(total - retainage_held),
        notes=f"From {po.po_number}",
        job_id=po.job_id,
    )
    db.add(bill)
    db.flush()

    journal_lines: list[dict] = []
    inv_receipts: list[tuple] = []
    for poline, item, amt, share, posting_acct in plan:
        full_amt = _q(Decimal(str(poline.amount or 0)))
        # Bill line keeps the PO line's quantity/rate when it bills the line
        # in one go; a partial draw stores the dollars as a single unit.
        is_whole = amt == full_amt and not poline.billed_amount
        qty = Decimal(str(poline.quantity)) if is_whole else Decimal("1")
        rate = Decimal(str(poline.rate)) if is_whole else amt
        if item and item.track_inventory and qty and qty > 0:
            inv_receipts.append((item, qty, unit_cost_with_tax(amt, share, qty, rate)))
        db.add(
            BillLine(
                bill_id=bill.id,
                job_id=poline.job_id or po.job_id,
                cost_code_id=poline.cost_code_id,
                cost_type=poline.cost_type,
                purchase_order_line_id=poline.id,
                item_id=poline.item_id,
                account_id=posting_acct,
                description=poline.description,
                quantity=qty,
                rate=rate,
                amount=amt,
                line_order=poline.line_order,
            )
        )
        if amt > 0 and posting_acct:
            journal_lines.append(
                {
                    "account_id": posting_acct,
                    "debit": amt + share,
                    "credit": Decimal("0"),
                    "description": poline.description or "",
                    "job_id": poline.job_id or po.job_id,
                    "cost_code_id": poline.cost_code_id,
                    "cost_type": poline.cost_type,
                }
            )

    if journal_lines:
        # A/P takes the net; retainage held rides on 2050 (#553).
        journal_lines.append(
            {
                "account_id": ap_id,
                "debit": Decimal("0"),
                "credit": _q(total - retainage_held),
                "description": f"From PO {po.po_number}",
            }
        )
        if retainage_held > 0:
            retainage_acct = ensure_construction_accounts(db)["2050"]
            journal_lines.append(
                {
                    "account_id": retainage_acct.id,
                    "debit": Decimal("0"),
                    "credit": retainage_held,
                    "description": f"Retainage held - {po.po_number}",
                    # so retainage release (#576) can find it by job
                    "job_id": po.job_id,
                }
            )
        txn = create_journal_entry(
            db,
            bill_date,
            f"Bill {bill.bill_number} - {vendor.name if vendor else ''}",
            journal_lines,
            source_type="bill",
            source_id=bill.id,
            job_id=po.job_id,
        )
        bill.transaction_id = txn.id

    # Write inventory movement rows for tracked receipts
    for item, qty, unit_cost in inv_receipts:
        record_purchase(
            db,
            item,
            quantity=qty,
            unit_cost=unit_cost,
            source_type="bill",
            source_id=bill.id,
            memo=f"PO {po.po_number}",
            post_journal=False,
            txn_date=bill_date,
        )

    # Tally what each line has now billed, and close the commitment when every
    # line is fully billed; while anything remains it is partial.
    for poline in billable:
        poline.billed_amount = _q(
            Decimal(str(poline.billed_amount or 0)) + this_period[poline.id]
        )
    fully_billed = all(
        _q(Decimal(str(ln.amount or 0))) - _q(Decimal(str(ln.billed_amount or 0))) <= 0
        for ln in lines
    )
    if fully_billed:
        po.status = POStatus.CLOSED
    elif po.status != POStatus.RECEIVED:
        # RECEIVED (goods in hand) trumps a partial-billing state; billing in
        # parts doesn't walk it back.
        po.status = POStatus.PARTIAL
    db.commit()
    return {"bill_id": bill.id, "message": f"Bill created from {po.po_number}"}
