# ============================================================================
# Bills (Accounts Payable) — enter bills from vendors, track payables
# Feature 1: DR Expense, CR AP (2000) on create; DR AP, CR Bank on payment
# ============================================================================

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes._helpers import clamp_pagination, reject_null_updates
from app.routes.invoices.helpers import _due_date_from_terms
from app.models.bills import Bill, BillLine, BillStatus
from app.models.contacts import Vendor
from app.models.items import Item
from app.models.purchase_orders import PurchaseOrder, PurchaseOrderLine
from app.schemas.bills import (
    BillCreate,
    BillLineCreate,
    BillResponse,
    BillUpdate,
    VendorCompliance,
)
from app.services.accounting import (
    _q,
    create_journal_entry,
    compute_line_totals,
    get_ap_account_id,
    reversing_lines,
)
from app.services.audit import audited_bulk_delete
from app.services.compliance import vendor_compliance
from app.services.payment_terms import DocumentTerms
from app.services.closing_date import check_closing_date
from app.services.job_costing import line_cost_type
from app.services.request_utils import content_disposition
from app.services.purchase_posting import (
    expense_account_for,
    spread,
    unit_cost_with_tax,
)

router = APIRouter(prefix="/api/bills", tags=["bills"])


@router.get("", response_model=list[BillResponse])
def list_bills(
    vendor_id: int = None,
    status: str = None,
    open_only: bool = False,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Newest first, a page at a time (500 by default, at most 1,000).
    open_only: the bills that can still be paid or credited (unpaid or
    partial, with a balance due), filtered here so an old unpaid bill is
    never lost behind the newest page (issue #191)."""
    skip, limit = clamp_pagination(skip, limit)
    # Eager-load vendor + lines so a 500-row list doesn't fire 1001
    # follow-up SELECTs through BillResponse.model_validate.
    q = db.query(Bill).options(
        joinedload(Bill.vendor),
        selectinload(Bill.lines),
    )
    if vendor_id:
        q = q.filter(Bill.vendor_id == vendor_id)
    if status:
        q = q.filter(Bill.status == status)
    if open_only:
        q = q.filter(
            Bill.status.in_((BillStatus.UNPAID, BillStatus.PARTIAL)),
            Bill.balance_due > 0,
        )
    bills = q.order_by(Bill.date.desc(), Bill.id.desc()).offset(skip).limit(limit).all()
    results = []
    terms_reader = DocumentTerms(db, "bill")
    for b in bills:
        resp = BillResponse.model_validate(b)
        terms_reader.fill(resp, b)
        if b.vendor:
            resp.vendor_name = b.vendor.name
            resp.vendor_compliance = VendorCompliance(**vendor_compliance(b.vendor))
        results.append(resp)
    return results


@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    resp = BillResponse.model_validate(bill)
    DocumentTerms(db, "bill").fill(resp, bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
        resp.vendor_compliance = VendorCompliance(**vendor_compliance(bill.vendor))
    return resp


def _bill_html(db: Session, bill_id: int) -> tuple[Bill, str]:
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    from app.services.payment_terms import document_discount_line

    return bill, _render(
        "bill_pdf.html",
        get_all_settings(db),
        bill=bill,
        discount_line=document_discount_line(bill),
    )


@router.get("/{bill_id}/pdf")
def bill_pdf(bill_id: int, db: Session = Depends(get_db)):
    """The bill as a PDF. Save PDF on the bill's view opened this URL long
    before it existed, and got a JSON "Not Found" (skytech W-M8)."""
    from app.services.pdf_service import render_pdf

    bill, html_str = _bill_html(db, bill_id)
    return Response(
        content=render_pdf(html_str),
        media_type="application/pdf",
        headers={
            # The bill number is the vendor's own invoice number — anything
            # can be in it, and it lands in a header.
            "Content-Disposition": content_disposition(
                f"Bill_{bill.bill_number or 'bill'}.pdf"
            )
        },
    )


@router.get("/{bill_id}/print-preview")
def bill_print_preview(bill_id: int, db: Session = Depends(get_db)):
    """The bill as a page that opens the print dialog."""
    _, html_str = _bill_html(db, bill_id)
    return HTMLResponse(
        content=html_str.replace(
            "</body>",
            "<script>window.onload=function(){window.print();}</script></body>",
        )
    )


def _default_bill_number(db: Session, vendor: Vendor, date) -> str:
    """'20260902-GK' for Gin Kee on 2026-09-02; '-2', '-3' … when that
    vendor already has a bill under the same generated number."""
    initials = "".join(
        w[0] for w in re.findall(r"[A-Za-z0-9]+", vendor.name or "")
    ).upper()[:4]
    base = f"{date:%Y%m%d}-{initials or 'BILL'}"
    taken = {
        row[0]
        for row in db.query(Bill.bill_number)
        .filter(Bill.vendor_id == vendor.id, Bill.bill_number.like(f"{base}%"))
        .all()
    }
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _post_bill_lines(
    db: Session,
    bill: Bill,
    vendor: Vendor,
    lines,
    tax_amount: Decimal,
    total: Decimal,
    doc_rate,
    txn_date,
    existing_transaction=None,
    retainage_held: Decimal = Decimal("0"),
):
    """Write the bill's lines, post its journal (DR each line's account or
    Inventory, CR Accounts Payable) and record its inventory receipts. Used
    by create and, with `existing_transaction`, by an edit that re-posts
    the same journal (#225), so both post exactly the same way."""
    from app.services.currency import convert_lines

    # A missing Accounts Payable account is the first thing to say (#119),
    # ahead of anything about one line.
    ap_id = get_ap_account_id(db)

    # Phase 11: track which lines are inventory receipts so we can write
    # InventoryMovement rows after the JE posts.
    from app.services.inventory_service import (
        get_inventory_asset_account_id,
        record_purchase,
    )

    inv_receipts = []  # [(item, quantity, unit_cost, line number), ...]

    # Round per line so stored BillLine.amount matches compute_line_totals
    # and the JE debit lands on the same cents as the rounded AP credit.
    amounts = [_q(Decimal(str(ln.quantity)) * Decimal(str(ln.rate))) for ln in lines]
    # Tax on a purchase is part of what the lines cost: each line's debit
    # carries its share, and nothing is posted to Sales Tax Payable.
    tax_shares = spread(tax_amount, amounts)

    journal_lines = []
    for i, line_data in enumerate(lines):
        amt = amounts[i]
        # The line's cost type, validated and filled from the code when blank.
        eff_cost_type = line_cost_type(db, line_data.cost_code_id, line_data.cost_type)
        item = None
        if line_data.item_id:
            item = db.query(Item).filter(Item.id == line_data.item_id).first()

        # Phase 11: for inventory-tracked items, the DR side goes to the
        # Inventory Asset account (NOT the expense account). The item will
        # move to COGS only when it's sold.
        if item and item.track_inventory:
            posting_acct = get_inventory_asset_account_id(db, item)
            if not posting_acct:
                # AUDIT FIX: don't silently drop the DR side when we can't
                # find an inventory asset account. Refuse the bill with a
                # clear error so the operator can fix the item's
                # asset_account_id or seed #1300.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Item '{item.name}' is inventory-tracked but has no "
                        "asset_account_id and no account #1300 (Inventory) is "
                        "seeded. Either set the item's inventory asset account "
                        "or add the Inventory account to the chart of accounts."
                    ),
                )
            if line_data.quantity > 0:
                inv_receipts.append(
                    (
                        item,
                        Decimal(str(line_data.quantity)),
                        # unit cost from the bill, with the line's tax share
                        unit_cost_with_tax(
                            amt, tax_shares[i], line_data.quantity, line_data.rate
                        ),
                        i + 1,
                    )
                )
        elif amt > 0 or line_data.account_id:
            posting_acct = expense_account_for(
                db,
                line_no=i + 1,
                description=line_data.description,
                account_id=line_data.account_id,
                item=item,
                vendor=vendor,
            )
        else:
            # A description-only line (no amount) posts nothing; keep the
            # account it would have used when there is one.
            posting_acct = (item.expense_account_id if item else None) or (
                vendor.default_expense_account_id
            )

        db.add(
            BillLine(
                bill_id=bill.id,
                item_id=line_data.item_id,
                account_id=posting_acct,
                description=line_data.description,
                quantity=line_data.quantity,
                rate=line_data.rate,
                amount=amt,
                job_id=line_data.job_id,
                class_id=line_data.class_id,
                cost_code_id=line_data.cost_code_id,
                cost_type=eff_cost_type,
                purchase_order_line_id=getattr(
                    line_data, "purchase_order_line_id", None
                ),
                function=line_data.function,
                is_billable=line_data.is_billable,
                line_order=i,
            )
        )

        if amt > 0 and posting_acct:
            journal_lines.append(
                {
                    "account_id": posting_acct,
                    "debit": amt + tax_shares[i],
                    "credit": Decimal("0"),
                    "description": line_data.description or "",
                    "job_id": line_data.job_id,
                    "class_id": line_data.class_id,
                    "cost_code_id": line_data.cost_code_id,
                    "cost_type": eff_cost_type,
                    "is_billable": line_data.is_billable,
                    # absent = default from the fund; explicit null = unassigned
                    **(
                        {"function": line_data.function}
                        if "function" in line_data.model_fields_set
                        else {}
                    ),
                }
            )

    # Credit AP for the net; any retainage held rides on Retainage Payable
    # (2050) instead, so Pay Bills pays only the net (#553). #555 releases it
    # with a debit to 2050.
    if ap_id and journal_lines:
        journal_lines.append(
            {
                "account_id": ap_id,
                "debit": Decimal("0"),
                "credit": _q(total - retainage_held),
                "description": f"Bill {bill.bill_number} - {vendor.name}",
            }
        )
        if retainage_held and retainage_held > 0:
            from app.services.accounting import ensure_construction_accounts

            retainage_acct = ensure_construction_accounts(db)["2050"]
            journal_lines.append(
                {
                    "account_id": retainage_acct.id,
                    "debit": Decimal("0"),
                    "credit": retainage_held,
                    "description": f"Retainage held - {bill.bill_number}",
                    "job_id": bill.job_id,  # so release (#576) can find it by job
                }
            )
        txn = create_journal_entry(
            db,
            txn_date,
            f"Bill {bill.bill_number} - {vendor.name}",
            convert_lines(journal_lines, doc_rate),
            source_type="bill",
            source_id=bill.id,
            class_id=bill.class_id,
            job_id=bill.job_id,
            existing_transaction=existing_transaction,
        )
        bill.transaction_id = txn.id

    # Phase 11: record inventory movements (no additional JE — the bill's
    # existing JE already debits the Inventory Asset account for these lines;
    # a receipt into a count below zero posts what the hole cost under the
    # bill, W-60). The memo names the line, so a reversal can name the
    # receipt it reverses (W-68).
    for item, qty, unit_cost, line_no in inv_receipts:
        record_purchase(
            db,
            item,
            quantity=qty,
            unit_cost=unit_cost,
            source_type="bill",
            source_id=bill.id,
            memo=f"Bill {bill.bill_number} line {line_no}",
            post_journal=False,
            txn_date=txn_date,
        )
    return inv_receipts


BILL_NUMBER_INDEX = "uq_bills_vendor_bill_number"


def _flush_number(db: Session, number: str) -> None:
    """Write the bill's number now, so a second sign-in saving the same
    vendor's same number at the same moment meets the unique index here
    and gets the 409 the check above gives (#501): the check is a read, and
    two reads can both find nothing on PostgreSQL before either writes."""
    try:
        db.flush()
    except IntegrityError as exc:
        text = str(exc.orig)
        if (
            BILL_NUMBER_INDEX not in text
            and "bills.vendor_id, bills.bill_number" not in text
        ):
            raise
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Bill number {number!r} already exists for this vendor",
        )


def _reject_po_line_links(lines) -> None:
    """A bill line's purchase_order_line_id is set only when a commitment is
    converted (#553); a hand-entered or edited bill may not claim one, or the
    void path would hand dollars back to a commitment it never billed."""
    if any(getattr(ln, "purchase_order_line_id", None) is not None for ln in lines):
        raise HTTPException(
            status_code=422,
            detail="purchase_order_line_id is set by converting a commitment, "
            "not on a bill entered or edited by hand.",
        )


def _linked_amounts(lines, from_request: bool) -> dict[int, Decimal]:
    """Dollars per commitment line a set of bill lines draws on. Request lines
    carry quantity x rate; stored lines carry the amount."""
    out: dict[int, Decimal] = {}
    for ln in lines:
        link = getattr(ln, "purchase_order_line_id", None)
        if link is None:
            continue
        amt = (
            _q(Decimal(str(ln.quantity)) * Decimal(str(ln.rate)))
            if from_request
            else _q(Decimal(str(ln.amount or 0)))
        )
        out[link] = out.get(link, Decimal("0")) + amt
    return out


def _resync_po_status(db: Session, po_ids) -> None:
    """Set each commitment's status from what its lines have billed (#553):
    nothing billed reopens it, something left is partial, all billed closes
    it. DRAFT and RECEIVED are never walked back."""
    from app.models.purchase_orders import POStatus, PurchaseOrder

    for po_id in po_ids:
        po = db.get(PurchaseOrder, po_id)
        if not po or po.status in (POStatus.DRAFT, POStatus.RECEIVED):
            continue
        billed = sum(
            (Decimal(str(ln.billed_amount or 0)) for ln in po.lines), Decimal("0")
        )
        remaining = any(
            Decimal(str(ln.amount or 0)) - Decimal(str(ln.billed_amount or 0)) > 0
            for ln in po.lines
        )
        if billed <= 0:
            po.status = POStatus.SENT
        elif remaining:
            po.status = POStatus.PARTIAL
        else:
            po.status = POStatus.CLOSED


@router.post("", response_model=BillResponse, status_code=201)
def create_bill(data: BillCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    _reject_po_line_links(data.lines)

    vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # The bill number is the VENDOR's invoice number (the scan pre-fills it
    # when the receipt prints one). A receipt with no number shouldn't block
    # the entry: fall back to date + vendor initials, suffixed if that
    # vendor already has one for the day.
    bill_number = (data.bill_number or "").strip()
    if not bill_number:
        bill_number = _default_bill_number(db, vendor, data.date)
    data.bill_number = bill_number

    # Reject duplicate vendor + bill_number combos. Vendors typically use a
    # monotonically-increasing invoice number; receiving the same one twice is
    # almost always a re-entry mistake, and accepting it silently produces
    # duplicate payables and double-counted expenses. (Scoped to the vendor:
    # two different vendors can both send invoice 111.)
    dup = (
        db.query(Bill)
        .filter(Bill.vendor_id == data.vendor_id, Bill.bill_number == bill_number)
        .first()
    )
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f"Bill number {bill_number!r} already exists for this vendor (bill #{dup.id})",
        )

    # Terms the caller didn't send are the vendor's (Blue Heron is Net 15;
    # Enter Bill used to make it Net 30 — macbase1 F11). The due date follows
    # the terms by the same rule invoices use, so "Due on Receipt" is due the
    # day of the bill rather than 30 days later.
    terms = data.terms
    if "terms" not in data.model_fields_set or not (terms or "").strip():
        terms = vendor.terms or "Net 30"
    due_date = data.due_date or _due_date_from_terms(db, data.date, terms)

    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "A bill must be for more than zero. Enter the amount the "
                "vendor charged, or its quantity and rate, on at least one line."
            ),
        )

    # Retainage held by hand (#553): between zero and the subtotal.
    retainage_held = _q(data.retainage_held or 0)
    if retainage_held > subtotal:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Retainage held ({retainage_held:.2f}) cannot exceed the "
                f"subtotal ({subtotal:.2f})."
            ),
        )

    from app.services.currency import resolve_rate

    doc_currency, doc_rate = resolve_rate(db, data.currency, data.exchange_rate)

    bill = Bill(
        bill_number=data.bill_number,
        currency=doc_currency,
        exchange_rate=doc_rate,
        vendor_id=data.vendor_id,
        date=data.date,
        due_date=due_date,
        terms=terms,
        ref_number=data.ref_number,
        po_id=data.po_id,
        subtotal=subtotal,
        tax_rate=data.tax_rate,
        tax_amount=tax_amount,
        total=total,
        retainage_held=retainage_held,
        retainage_manual="retainage_held" in data.model_fields_set,
        lien_waiver_status=data.lien_waiver_status,
        balance_due=_q(total - retainage_held),
        class_id=data.class_id,
        job_id=data.job_id,
        notes=data.notes,
    )
    db.add(bill)
    _flush_number(db, bill.bill_number)

    _post_bill_lines(
        db,
        bill,
        vendor,
        data.lines,
        tax_amount,
        total,
        doc_rate,
        data.date,
        retainage_held=retainage_held,
    )

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    DocumentTerms(db, "bill").fill(resp, bill)
    resp.vendor_name = vendor.name
    resp.vendor_compliance = VendorCompliance(**vendor_compliance(vendor))
    return resp


@router.put("/{bill_id}", response_model=BillResponse)
def update_bill(bill_id: int, data: BillUpdate, db: Session = Depends(get_db)):
    """Edit a posted bill the way an invoice is edited (#225): the header,
    the lines, or both. The journal keeps its identity and is re-posted
    through create's own path; an inventory line's earlier receipt is
    reversed and the new quantity received, so the stock ledger nets to
    the edit. A voided bill, a total below what's been paid, a posting in
    a completed reconciliation, and a date in a closed period are each
    refused before anything is written."""
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="Cannot edit a voided bill")
    # A converted bill's lines keep their link to the commitment line they
    # draw on (#553); an edit may carry those links, never invent a new one.
    old_links = _linked_amounts(bill.lines, from_request=False)
    if data.lines is not None:
        stray = {
            ln.purchase_order_line_id
            for ln in data.lines
            if ln.purchase_order_line_id is not None
        } - set(old_links)
        if stray:
            raise HTTPException(
                status_code=422,
                detail="purchase_order_line_id is set by converting a commitment, "
                "not on a bill entered or edited by hand.",
            )
    # A lien waiver is metadata, not a posting: saving only that moves no
    # money, so it is allowed on a closed-period bill (#553).
    if data.model_fields_set - {"lien_waiver_status"}:
        check_closing_date(db, bill.date)

    # The edit form sends a cleared number as null; the generate step below
    # fills it in, so it is the one NOT NULL column null may reach.
    update_data = reject_null_updates(
        Bill,
        data.model_dump(exclude_unset=True, exclude={"lines"}),
        allow_null=("bill_number",),
    )
    # A retainage the request names is set by hand from here on: later edits
    # of the lines, tax or date keep it instead of re-deriving it (#597).
    if "retainage_held" in update_data:
        update_data["retainage_manual"] = True

    vendor = bill.vendor
    if "vendor_id" in update_data and update_data["vendor_id"] != bill.vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == update_data["vendor_id"]).first()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")

    # A renumbered bill: the vendor's invoice number, blank → generated, and
    # never a second copy of one the vendor already has (as on create).
    new_date = update_data.get("date") or bill.date
    if "bill_number" in update_data:
        number = (update_data["bill_number"] or "").strip()
        if not number:
            number = _default_bill_number(db, vendor, new_date)
        update_data["bill_number"] = number
    number = update_data.get("bill_number", bill.bill_number)
    if number != bill.bill_number or vendor.id != bill.vendor_id:
        dup = (
            db.query(Bill)
            .filter(
                Bill.vendor_id == vendor.id,
                Bill.bill_number == number,
                Bill.id != bill.id,
            )
            .first()
        )
        if dup:
            raise HTTPException(
                status_code=409,
                detail=f"Bill number {number!r} already exists for this vendor (bill #{dup.id})",
            )

    date_changed = new_date != bill.date

    if "currency" in update_data or "exchange_rate" in update_data:
        from app.services.currency import resolve_rate

        currency = update_data.get("currency", bill.currency)
        # A new currency cannot inherit the previous currency's rate.
        rate = update_data.get(
            "exchange_rate",
            bill.exchange_rate if currency == bill.currency else None,
        )
        update_data["currency"], update_data["exchange_rate"] = resolve_rate(
            db, currency, rate
        )

    repost_fields = {
        "tax_rate",
        "currency",
        "exchange_rate",
        "class_id",
        "job_id",
        "vendor_id",
        "bill_number",
        "retainage_held",  # moves the A/P vs 2050 split (#553)
    }
    needs_repost = data.lines is not None or any(
        key in update_data and update_data[key] != getattr(bill, key)
        for key in repost_fields
    )

    # The lines to post: the ones sent, else the bill's own. A header-only
    # edit that re-posts (a class, a job, a new vendor or number, the tax
    # rate, the currency) passed the stored ORM lines to _post_bill_lines,
    # which reads request lines (model_fields_set): a 500 for any such edit
    # sent without its lines. They are copied into the request shape here,
    # before their rows are deleted below, and posted as they are stored.
    if data.lines is not None:
        effective_lines = data.lines
    else:
        effective_lines = []
        for ln in sorted(bill.lines, key=lambda x: (x.line_order or 0, x.id)):
            fields = dict(
                item_id=ln.item_id,
                account_id=ln.account_id,
                job_id=ln.job_id,
                class_id=ln.class_id,
                cost_code_id=ln.cost_code_id,
                cost_type=ln.cost_type,
                # Keep the commitment back-link across a repost (#553), so a
                # later void still restores the PO line's billed_amount.
                purchase_order_line_id=ln.purchase_order_line_id,
                is_billable=bool(ln.is_billable),
                description=ln.description,
                # the stored Decimals, not a float round trip (everything
                # below reads them through Decimal(str(...)))
                quantity=ln.quantity if ln.quantity is not None else Decimal("1"),
                rate=ln.rate if ln.rate is not None else Decimal("0"),
                line_order=ln.line_order or 0,
            )
            # A stored function is sent; none stored is left unsent, so the
            # journal line takes its fund's default function, as it did when
            # the bill was entered and as the form's own edit does.
            if ln.function is not None:
                fields["function"] = ln.function
            effective_lines.append(BillLineCreate.model_construct(**fields))
    amount_paid = bill.amount_paid or Decimal("0")
    retally: dict[int, Decimal] = {}
    if needs_repost:
        tax_rate = data.tax_rate if data.tax_rate is not None else bill.tax_rate
        subtotal, tax_amount, total = compute_line_totals(effective_lines, tax_rate)
        if total <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A bill must be for more than zero. Enter the amount the "
                    "vendor charged, or its quantity and rate, on at least one line."
                ),
            )
        if total < amount_paid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Bill total cannot be less than the amount already paid "
                    f"({amount_paid:.2f}). Void the payment first, or keep the "
                    "total at least that much."
                ),
            )
        # Retainage held (#553) stays between zero and the subtotal, and what
        # is left payable after it cannot fall below what has been paid.
        # Checked here, before anything is torn down, so a bad edit mutates
        # nothing.
        retainage_held = _q(update_data.get("retainage_held", bill.retainage_held) or 0)
        # A bill converted from a commitment with a retainage rate follows its
        # lines: held is the rate on the new subtotal, unless the request names
        # an amount or one was set by hand earlier (retainage_manual, #597).
        # The flag, not a comparison with the old figure, decides: after the
        # commitment's rate changes, the stored figure is no longer the rate
        # on the old subtotal, yet it was never set by hand.
        if (
            "retainage_held" not in update_data
            and bill.po_id
            and not bill.retainage_manual
        ):
            from app.models.purchase_orders import PurchaseOrder

            po = db.get(PurchaseOrder, bill.po_id)
            if po and po.retainage_pct:
                rate = Decimal(str(po.retainage_pct)) / Decimal("100")
                retainage_held = _q(subtotal * rate)
        if retainage_held > subtotal:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Retainage held ({retainage_held:.2f}) cannot exceed the "
                    f"subtotal ({subtotal:.2f})."
                ),
            )
        if _q(total - retainage_held) < amount_paid:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Retainage held ({retainage_held:.2f}) would leave less "
                    f"than the {amount_paid:.2f} already paid. Lower the "
                    "retainage or void the payment first."
                ),
            )
        # Re-tally the commitment (#553): what these lines draw on replaces
        # what the old ones did, and may not pass a line's scheduled value.
        # Locked and checked here so a refused edit moves nothing.
        new_links = _linked_amounts(effective_lines, from_request=True)
        retally: dict[int, Decimal] = {}
        for link in set(old_links) | set(new_links):
            delta = new_links.get(link, Decimal("0")) - old_links.get(
                link, Decimal("0")
            )
            if delta == 0:
                continue
            poline = (
                db.query(PurchaseOrderLine)
                .filter(PurchaseOrderLine.id == link)
                .with_for_update()
                .first()
            )
            if not poline:
                continue
            billed = _q(Decimal(str(poline.billed_amount or 0))) + delta
            if billed > _q(Decimal(str(poline.amount or 0))):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"That edit bills {billed:.2f} against "
                        f"'{poline.description or 'a line'}', more than its "
                        f"{_q(Decimal(str(poline.amount or 0))):.2f} on the commitment."
                    ),
                )
            retally[link] = max(Decimal("0"), billed)

    # Check every posting before any state moves (as the invoice edit does).
    from app.models.transactions import Transaction
    from app.services.bank_posting import assert_not_reconciled

    txn = db.get(Transaction, bill.transaction_id) if bill.transaction_id else None
    if (needs_repost or date_changed) and txn:
        assert_not_reconciled(txn)
    if date_changed:
        check_closing_date(db, new_date)
        if txn:
            check_closing_date(db, txn.date)

    # The old receipts come out and the new lines go in (below): a net take
    # past what is on hand is said in the response, or refused with the
    # setting on, the way a sale past the stock is (W-67), before anything
    # is written.
    stock_warnings: list[str] = []
    if needs_repost:
        from app.routes.invoices.helpers import warn_or_refuse_shorts
        from app.services.inventory_hooks import oversold_by_reversal

        stock_warnings = warn_or_refuse_shorts(
            db, oversold_by_reversal(db, bill, effective_lines), "edit"
        )

    renumbered = number != bill.bill_number or vendor.id != bill.vendor_id
    for key, val in update_data.items():
        setattr(bill, key, val)
    if renumbered:
        _flush_number(db, number)

    # Terms or date changed without a due date: the due date follows the
    # terms, as on create; a cleared due date is derived the same way.
    if (
        "terms" in update_data or "date" in update_data
    ) and "due_date" not in update_data:
        bill.due_date = _due_date_from_terms(db, bill.date, bill.terms or "Net 30")
    if "due_date" in update_data and bill.due_date is None:
        bill.due_date = _due_date_from_terms(db, bill.date, bill.terms or "Net 30")

    if needs_repost:
        from app.routes.invoices.helpers import _reverse_and_delete_journal
        from app.services.inventory_hooks import reverse_receipts_for_bill

        # each old line leaves a DELETE event with its amounts (#289)
        audited_bulk_delete(db, db.query(BillLine).filter(BillLine.bill_id == bill.id))
        db.flush()
        if txn:
            _reverse_and_delete_journal(db, txn.id)
            txn.date = bill.date
            txn.class_id = bill.class_id
            txn.job_id = bill.job_id

        # retainage_held was validated above, before the teardown.
        bill.retainage_held = retainage_held
        bill.subtotal = subtotal
        bill.tax_rate = tax_rate
        bill.tax_amount = tax_amount
        bill.total = total
        bill.balance_due = _q(total - retainage_held - amount_paid)

        # What the old lines received into stock comes back out, each
        # receipt at the cost it went in at (#646), before the new lines are
        # received below, so the stock ledger nets to the edit (an invoice
        # edit posts the delta of its sales the same way).
        reverse_receipts_for_bill(
            db,
            bill,
            source_type="bill_edit",
            txn_date=bill.date,
            memo="Edit {receipt}: previous receipt reversed",
        )
        _post_bill_lines(
            db,
            bill,
            vendor,
            effective_lines,
            tax_amount,
            total,
            bill.exchange_rate,
            bill.date,
            existing_transaction=txn,
            retainage_held=retainage_held,
        )
        db.flush()
        db.refresh(bill)

        # Its payments recognized the expense as the lines stood; rewrite that
        # from the lines as they now stand (#483).
        from app.services import cash_basis

        cash_basis.recompute_bill_cash_lines(db, bill)

        touched_pos = set()
        for link, billed in retally.items():
            poline = db.get(PurchaseOrderLine, link)
            poline.billed_amount = billed
            touched_pos.add(poline.purchase_order_id)
        _resync_po_status(db, touched_pos)

        # Net of retainage is what's payable, so a bill paid down to the held
        # amount is PAID, not stuck PARTIAL (#553).
        if bill.balance_due <= 0 and amount_paid >= _q(total - retainage_held):
            bill.status = BillStatus.PAID
        elif amount_paid > 0:
            bill.status = BillStatus.PARTIAL
        else:
            bill.status = BillStatus.UNPAID
    elif date_changed and txn:
        # A date-only edit moves the posting without replacing its lines.
        txn.date = bill.date

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    DocumentTerms(db, "bill").fill(resp, bill)
    resp.vendor_name = vendor.name
    resp.vendor_compliance = VendorCompliance(**vendor_compliance(vendor))
    resp.warnings = stock_warnings
    return resp


@router.post("/{bill_id}/void", response_model=BillResponse)
def void_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.status == BillStatus.VOID:
        raise HTTPException(status_code=400, detail="Bill already voided")
    # Voiding a bill with payments applied would reverse the full A/P
    # while the bill payment's cash JE + allocations stay on the books —
    # double-counting cash and reversing A/P twice. Require the payment(s)
    # to be voided first so the ledger stays consistent (mirrors void_invoice).
    if (bill.amount_paid or Decimal("0")) > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot void a bill with payments applied. Void the "
                "bill payment(s) first, then void the bill."
            ),
        )
    # Voids post a reversing entry dated to the bill — must respect the
    # closing date like invoice/payment/journal voids already do, or a bill
    # can be reversed into a locked period.
    check_closing_date(db, bill.date)

    # The receipts still in stock come back out: past what is on hand, the
    # void says so in the response, or is refused with the setting on, the
    # way a sale past the stock is (W-67), before anything is written.
    from app.routes.invoices.helpers import warn_or_refuse_shorts
    from app.services.inventory_hooks import oversold_by_reversal

    stock_warnings = warn_or_refuse_shorts(db, oversold_by_reversal(db, bill), "void")

    reversal_id = None
    if bill.transaction_id:
        from app.models.transactions import TransactionLine

        original_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == bill.transaction_id)
            .all()
        )
        reverse_lines = reversing_lines(original_lines)
        if reverse_lines:
            reversal_id = create_journal_entry(
                db,
                bill.date,
                f"VOID Bill {bill.bill_number}",
                reverse_lines,
                source_type="bill_void",
                source_id=bill.id,
                class_id=bill.class_id,
                job_id=bill.job_id,
            ).id

    # Phase 11: the receipts come back out at the cost they went in at, one
    # reversal per receipt naming it and linked to the reversing entry, which
    # already undoes the Inventory side (#646; what the pool cannot absorb is
    # posted by the hook, #647).
    from app.services.inventory_hooks import reverse_receipts_for_bill

    reverse_receipts_for_bill(
        db,
        bill,
        source_type="bill_void",
        txn_date=bill.date,
        memo="VOID {receipt}",
        transaction_id=reversal_id,
    )

    # Give the billed dollars back to the commitment this bill drew on, and
    # reopen it (#553): the reversing JE above already backed out the A/P and
    # any Retainage Payable (2050) credits.
    touched_pos = set()
    for line in bill.lines:
        if not line.purchase_order_line_id:
            continue
        poline = db.get(PurchaseOrderLine, line.purchase_order_line_id)
        if not poline:
            continue
        poline.billed_amount = _q(
            max(
                Decimal("0"),
                Decimal(str(poline.billed_amount or 0))
                - Decimal(str(line.amount or 0)),
            )
        )
        touched_pos.add(poline.purchase_order_id)
    _resync_po_status(db, touched_pos)

    # A retainage-release bill (#555) gave the figure back to its commitment;
    # voiding it (the reversing JE above already backed out the 2050/A-P split)
    # restores the outstanding retainage on the commitment.
    if (bill.retainage_released or Decimal("0")) > 0 and bill.po_id:
        po = db.get(PurchaseOrder, bill.po_id)
        if po is not None:
            po.retainage_released = _q(
                max(
                    Decimal("0"),
                    Decimal(str(po.retainage_released or 0))
                    - Decimal(str(bill.retainage_released or 0)),
                )
            )

    bill.status = BillStatus.VOID
    bill.balance_due = Decimal("0")
    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    DocumentTerms(db, "bill").fill(resp, bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
        resp.vendor_compliance = VendorCompliance(**vendor_compliance(bill.vendor))
    resp.warnings = stock_warnings
    return resp
