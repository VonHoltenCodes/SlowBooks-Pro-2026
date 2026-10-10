# ============================================================================
# Bills (Accounts Payable) — enter bills from vendors, track payables
# Feature 1: DR Expense, CR AP (2000) on create; DR AP, CR Bank on payment
# ============================================================================

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.routes._helpers import clamp_pagination
from app.routes.invoices.helpers import _due_date_from_terms
from app.models.bills import Bill, BillLine, BillStatus
from app.models.contacts import Vendor
from app.models.items import Item
from app.schemas.bills import BillCreate, BillLineCreate, BillResponse, BillUpdate
from app.services.accounting import (
    _q,
    create_journal_entry,
    compute_line_totals,
    get_ap_account_id,
    reversing_lines,
)
from app.services.closing_date import check_closing_date
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
    for b in bills:
        resp = BillResponse.model_validate(b)
        if b.vendor:
            resp.vendor_name = b.vendor.name
        results.append(resp)
    return results


@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp


def _bill_html(db: Session, bill_id: int) -> tuple[Bill, str]:
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    bill = db.query(Bill).filter(Bill.id == bill_id).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    return bill, _render("bill_pdf.html", get_all_settings(db), bill=bill)


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

    inv_receipts = []  # [(item, quantity, unit_cost), ...]

    # Round per line so stored BillLine.amount matches compute_line_totals
    # and the JE debit lands on the same cents as the rounded AP credit.
    amounts = [_q(Decimal(str(ln.quantity)) * Decimal(str(ln.rate))) for ln in lines]
    # Tax on a purchase is part of what the lines cost: each line's debit
    # carries its share, and nothing is posted to Sales Tax Payable.
    tax_shares = spread(tax_amount, amounts)

    journal_lines = []
    for i, line_data in enumerate(lines):
        amt = amounts[i]
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
                function=line_data.function,
                is_billable=line_data.is_billable,
                line_order=line_data.line_order or i,
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
                    "is_billable": line_data.is_billable,
                    # absent = default from the fund; explicit null = unassigned
                    **(
                        {"function": line_data.function}
                        if "function" in line_data.model_fields_set
                        else {}
                    ),
                }
            )

    # Credit AP
    if ap_id and journal_lines:
        journal_lines.append(
            {
                "account_id": ap_id,
                "debit": Decimal("0"),
                "credit": total,
                "description": f"Bill {bill.bill_number} - {vendor.name}",
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
    # existing JE already debits the Inventory Asset account for these lines)
    for item, qty, unit_cost in inv_receipts:
        record_purchase(
            db,
            item,
            quantity=qty,
            unit_cost=unit_cost,
            source_type="bill",
            source_id=bill.id,
            memo=f"Bill {bill.bill_number}",
            post_journal=False,
            txn_date=txn_date,
        )
    return inv_receipts


@router.post("", response_model=BillResponse, status_code=201)
def create_bill(data: BillCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)

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
    due_date = data.due_date or _due_date_from_terms(data.date, terms)

    subtotal, tax_amount, total = compute_line_totals(data.lines, data.tax_rate)
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "A bill must be for more than zero. Enter the quantity and "
                "rate the vendor charged on at least one line."
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
        balance_due=total,
        class_id=data.class_id,
        job_id=data.job_id,
        notes=data.notes,
    )
    db.add(bill)
    db.flush()

    _post_bill_lines(
        db,
        bill,
        vendor,
        data.lines,
        tax_amount,
        total,
        doc_rate,
        data.date,
    )

    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    resp.vendor_name = vendor.name
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
    check_closing_date(db, bill.date)

    update_data = data.model_dump(exclude_unset=True, exclude={"lines"})

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
    if needs_repost:
        tax_rate = data.tax_rate if data.tax_rate is not None else bill.tax_rate
        subtotal, tax_amount, total = compute_line_totals(effective_lines, tax_rate)
        if total <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A bill must be for more than zero. Enter the quantity and "
                    "rate the vendor charged on at least one line."
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

    for key, val in update_data.items():
        setattr(bill, key, val)

    # Terms or date changed without a due date: the due date follows the
    # terms, as on create; a cleared due date is derived the same way.
    if (
        "terms" in update_data or "date" in update_data
    ) and "due_date" not in update_data:
        bill.due_date = _due_date_from_terms(bill.date, bill.terms or "Net 30")
    if "due_date" in update_data and bill.due_date is None:
        bill.due_date = _due_date_from_terms(bill.date, bill.terms or "Net 30")

    if needs_repost:
        from app.routes.invoices.helpers import _reverse_and_delete_journal
        from app.services.inventory_service import _append_movement
        from app.models.items import MovementType as _MovementType

        # What the old lines received into stock, to be reversed below so
        # the stock ledger nets to the edit (an invoice edit posts the delta
        # of its sales the same way).
        old_receipts: dict[int, tuple[Decimal, Decimal]] = {}
        for ln in bill.lines:
            if ln.item_id and ln.quantity and ln.quantity > 0:
                it = db.query(Item).filter(Item.id == ln.item_id).first()
                if it and it.track_inventory:
                    q, _c = old_receipts.get(ln.item_id, (Decimal("0"), Decimal("0")))
                    old_receipts[ln.item_id] = (
                        q + Decimal(str(ln.quantity)),
                        Decimal(str(ln.rate)),
                    )

        db.query(BillLine).filter(BillLine.bill_id == bill.id).delete()
        db.flush()
        if txn:
            _reverse_and_delete_journal(db, txn.id)
            txn.date = bill.date
            txn.class_id = bill.class_id
            txn.job_id = bill.job_id

        bill.subtotal = subtotal
        bill.tax_rate = tax_rate
        bill.tax_amount = tax_amount
        bill.total = total
        bill.balance_due = _q(total - amount_paid)

        for item_id, (qty, unit_cost) in old_receipts.items():
            it = db.query(Item).filter(Item.id == item_id).first()
            _append_movement(
                db,
                it,
                _MovementType.VOID,
                quantity=-qty,
                unit_cost=unit_cost,
                source_type="bill_edit",
                source_id=bill.id,
                memo=f"Edit Bill {bill.bill_number}: previous receipt reversed",
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
        )
        db.flush()
        db.refresh(bill)

        if bill.balance_due == 0 and amount_paid >= bill.total:
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
    resp.vendor_name = vendor.name
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

    if bill.transaction_id:
        from app.models.transactions import TransactionLine

        original_lines = (
            db.query(TransactionLine)
            .filter(TransactionLine.transaction_id == bill.transaction_id)
            .all()
        )
        reverse_lines = reversing_lines(original_lines)
        if reverse_lines:
            create_journal_entry(
                db,
                bill.date,
                f"VOID Bill {bill.bill_number}",
                reverse_lines,
                source_type="bill_void",
                source_id=bill.id,
                class_id=bill.class_id,
                job_id=bill.job_id,
            )

    # Phase 11: reverse inventory receipts (the reversing JE already undoes
    # the Inventory Asset side; we just need the movement rows)
    from app.services.inventory_service import _append_movement
    from app.models.items import MovementType as _MovementType

    for line in bill.lines:
        if not line.item_id:
            continue
        item = db.query(Item).filter(Item.id == line.item_id).first()
        if item and item.track_inventory and line.quantity > 0:
            _append_movement(
                db,
                item,
                _MovementType.VOID,
                quantity=-Decimal(str(line.quantity)),
                unit_cost=Decimal(str(line.rate)),
                source_type="bill_void",
                source_id=bill.id,
                memo=f"VOID Bill {bill.bill_number}",
            )

    bill.status = BillStatus.VOID
    bill.balance_due = Decimal("0")
    db.commit()
    db.refresh(bill)
    resp = BillResponse.model_validate(bill)
    if bill.vendor:
        resp.vendor_name = bill.vendor.name
    return resp
