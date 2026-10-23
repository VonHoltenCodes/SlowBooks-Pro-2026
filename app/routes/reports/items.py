"""Sales by Item and Purchases by Item (#435).

Two reports QuickBooks users look for: what sold, by item, and what was
bought, by item. One row per item for the period with quantity, amount and
an average, plus — on the sales side — COGS, gross margin and margin % for
the items whose stock we track.

Sales rows are the InvoiceLines of non-void invoices (sales receipts
included) less the CreditMemoLines of non-void credit memos, in home
currency; voids are left out as the P&L leaves them. Purchases rows are
BillLines of non-void bills less VendorCreditLines of non-void vendor
credits. Expense lines (Transaction/TransactionLine) carry no item, so
Purchases by Item is bills and vendor credits only.

"Ties to the P&L" (a test asserts it): total_amount of Sales by Item equals
the P&L's total_income for the same period in the case where every invoice
line posts to an income account and there are no other income postings — a
manual journal entry to an income account is income this report cannot show.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.bills import Bill, BillStatus
from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor
from app.models.credit_memos import CreditMemo, CreditMemoStatus
from app.models.invoices import Invoice, InvoiceStatus
from app.models.items import InventoryMovement, Item
from app.models.jobs import Job
from app.models.vendor_credits import (
    VendorCredit,
    VendorCreditStatus,
)
from app.routes.reports._router import router
from app.services.contact_balances import home_amount

# The document a line's link opens, by its source type (matches
# bank_register._LINKS; the SPA builds the address from {type, id}).
_LINK_TYPE = {
    "invoice": "invoice",
    "credit_memo": "credit_memo",
    "bill": "bill",
    "vendor_credit": "vendor_credit",
}

_NO_ITEM = "No item"


def _default_dates(start_date, end_date):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()
    return start_date, end_date


def _require(db: Session, model, obj_id: Optional[int], what: str):
    """The row, or a 404 with a sentence. An unknown narrowing id is a 404,
    not an empty report, so a typo reads as a typo."""
    if obj_id is None:
        return None
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"No {what} with id {obj_id}.")
    return obj


def _dim_ok(line_val, header_val, class_id, job_id) -> bool:
    """The class/job narrowing rule, documented once: a line's own class or
    job wins, else the header's — the rule P&L by Job groups by (_pl_pivot's
    coalesce(line, header)). A filter matches when the resolved value equals
    the wanted id."""
    if class_id is not None:
        if (line_val[0] if line_val[0] is not None else header_val[0]) != class_id:
            return False
    if job_id is not None:
        if (line_val[1] if line_val[1] is not None else header_val[1]) != job_id:
            return False
    return True


def _item_meta(db: Session) -> dict:
    """item_id -> (name, item_type, track_inventory). Looked up once."""
    out = {}
    for it in db.query(Item).all():
        out[it.id] = (it.name, it.item_type.value, bool(it.track_inventory))
    return out


# ── Sales by Item ─────────────────────────────────────────────────────────


def _sales_by_item(
    db: Session,
    start_date: date,
    end_date: date,
    customer_id: Optional[int] = None,
    class_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    start_date, end_date = _default_dates(start_date, end_date)
    _require(db, Customer, customer_id, "customer")
    _require(db, TxnClass, class_id, "class")
    _require(db, Job, job_id, "job")
    meta = _item_meta(db)

    invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.lines))
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
    )
    if customer_id is not None:
        invoices = invoices.filter(Invoice.customer_id == customer_id)

    # Per item_id (None groups the no-item lines): quantity, amount, count,
    # and the invoice/credit-memo ids behind it (for COGS from movements).
    rows: dict = {}

    def bucket(item_id):
        return rows.setdefault(
            item_id,
            {
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "line_count": 0,
                "inv_ids": set(),
                "cm_ids": set(),
            },
        )

    for inv in invoices.all():
        for line in inv.lines:
            if not _dim_ok(
                (line.class_id, line.job_id),
                (inv.class_id, inv.job_id),
                class_id,
                job_id,
            ):
                continue
            b = bucket(line.item_id)
            b["quantity"] += Decimal(str(line.quantity or 0))
            b["amount"] += home_amount(line.amount, inv.exchange_rate)
            b["line_count"] += 1
            if line.item_id:
                b["inv_ids"].add(inv.id)

    memos = (
        db.query(CreditMemo)
        .options(joinedload(CreditMemo.lines))
        .filter(CreditMemo.date >= start_date, CreditMemo.date <= end_date)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
    )
    if customer_id is not None:
        memos = memos.filter(CreditMemo.customer_id == customer_id)

    for cm in memos.all():
        # A credit memo's lines carry no class/job of their own, so the
        # header's is the only value to test against the narrowing.
        if not _dim_ok((None, None), (cm.class_id, cm.job_id), class_id, job_id):
            continue
        for line in cm.lines:
            b = bucket(line.item_id)
            b["quantity"] -= Decimal(str(line.quantity or 0))
            b["amount"] -= home_amount(line.amount, None)
            b["line_count"] += 1
            if line.item_id:
                b["cm_ids"].add(cm.id)

    items = []
    for item_id, b in rows.items():
        name, item_type, tracked = (
            meta.get(item_id, (None, None, False))
            if item_id
            else (_NO_ITEM, None, False)
        )
        qty = b["quantity"]
        amount = b["amount"]
        avg_price = float(amount / qty) if qty != 0 else None
        cogs = None
        gross_margin = None
        margin_pct = None
        if item_id and tracked:
            cogs = _item_cogs(db, item_id, b["inv_ids"], b["cm_ids"])
            gross_margin = amount - cogs
            margin_pct = float(gross_margin / amount * 100) if amount != 0 else None
        items.append(
            {
                "item_id": item_id,
                "item_name": name or f"Item {item_id}",
                "item_type": item_type,
                "quantity": float(qty),
                "amount": float(amount),
                "average_price": avg_price,
                "cogs": float(cogs) if cogs is not None else None,
                "gross_margin": (
                    float(gross_margin) if gross_margin is not None else None
                ),
                "margin_pct": margin_pct,
                "line_count": b["line_count"],
            }
        )

    # Amount desc, "No item" always last.
    items.sort(key=lambda r: (r["item_id"] is None, -r["amount"]))

    total_quantity = sum(
        (Decimal(str(r["quantity"])) for r in items if r["item_id"] is not None),
        Decimal("0"),
    )
    total_amount = sum((Decimal(str(r["amount"])) for r in items), Decimal("0"))
    total_cogs = sum(
        (Decimal(str(r["cogs"])) for r in items if r["cogs"] is not None),
        Decimal("0"),
    )
    total_margin = sum(
        (
            Decimal(str(r["gross_margin"]))
            for r in items
            if r["gross_margin"] is not None
        ),
        Decimal("0"),
    )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "customer_id": customer_id,
        "class_id": class_id,
        "job_id": job_id,
        "items": items,
        "total_quantity": float(total_quantity),
        "total_amount": float(total_amount),
        "total_cogs": float(total_cogs),
        "total_margin": float(total_margin),
    }


def _item_cogs(db: Session, item_id: int, inv_ids: set, cm_ids: set) -> Decimal:
    """COGS for a tracked item from its own inventory movements, not
    avg_cost × qty: a sale posts COGS at the avg cost AT THE TIME of the
    sale, which the movement row recorded (inventory_service.record_sale).

    Sales (source_type "invoice") store a NEGATIVE quantity, so
    −Σ(quantity × unit_cost) is the positive cost of what shipped. Credit
    memos that return stock post a RETURN_IN with a positive quantity
    (inventory_hooks.post_return_for_credit_memo), so the same formula backs
    the returned cost out. A return with no such movement (an untracked
    moment, a legacy row) simply isn't counted here, and its COGS is left
    out.
    """
    if not inv_ids and not cm_ids:
        return Decimal("0")
    q = db.query(InventoryMovement).filter(InventoryMovement.item_id == item_id)
    total = Decimal("0")
    for mv in q.all():
        if mv.source_type == "invoice" and mv.source_id in inv_ids:
            total += Decimal(str(mv.quantity)) * Decimal(str(mv.unit_cost))
        elif mv.source_type == "credit_memo" and mv.source_id in cm_ids:
            total += Decimal(str(mv.quantity)) * Decimal(str(mv.unit_cost))
    return -total


def _sales_lines(
    db: Session,
    start_date: date,
    end_date: date,
    item_key,
    customer_id: Optional[int] = None,
    class_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    """The document lines behind one Sales by Item row, for the drill-down.
    `item_key` is an item id, or None for the "No item" row."""
    start_date, end_date = _default_dates(start_date, end_date)
    _require(db, Customer, customer_id, "customer")
    _require(db, TxnClass, class_id, "class")
    _require(db, Job, job_id, "job")

    invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.lines), joinedload(Invoice.customer))
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
    )
    if customer_id is not None:
        invoices = invoices.filter(Invoice.customer_id == customer_id)

    lines = []
    for inv in invoices.all():
        for line in inv.lines:
            if line.item_id != item_key:
                continue
            if not _dim_ok(
                (line.class_id, line.job_id),
                (inv.class_id, inv.job_id),
                class_id,
                job_id,
            ):
                continue
            lines.append(
                {
                    "date": inv.date.isoformat(),
                    "doc_type": "Sales Receipt" if inv.is_sales_receipt else "Invoice",
                    "number": inv.invoice_number,
                    "party": inv.customer.name if inv.customer else None,
                    "description": line.description,
                    "quantity": float(line.quantity or 0),
                    "rate": float(line.rate or 0),
                    "amount": float(home_amount(line.amount, inv.exchange_rate)),
                    "link": {"type": _LINK_TYPE["invoice"], "id": inv.id},
                }
            )

    memos = (
        db.query(CreditMemo)
        .options(joinedload(CreditMemo.lines), joinedload(CreditMemo.customer))
        .filter(CreditMemo.date >= start_date, CreditMemo.date <= end_date)
        .filter(CreditMemo.status != CreditMemoStatus.VOID)
    )
    if customer_id is not None:
        memos = memos.filter(CreditMemo.customer_id == customer_id)

    for cm in memos.all():
        if not _dim_ok((None, None), (cm.class_id, cm.job_id), class_id, job_id):
            continue
        for line in cm.lines:
            if line.item_id != item_key:
                continue
            lines.append(
                {
                    "date": cm.date.isoformat(),
                    "doc_type": "Credit Memo",
                    "number": cm.memo_number,
                    "party": cm.customer.name if cm.customer else None,
                    "description": line.description,
                    "quantity": -float(line.quantity or 0),
                    "rate": float(line.rate or 0),
                    "amount": -float(home_amount(line.amount, None)),
                    "link": {"type": _LINK_TYPE["credit_memo"], "id": cm.id},
                }
            )

    lines.sort(key=lambda line_row: line_row["date"])
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "item_id": item_key,
        "lines": lines,
    }


# ── Purchases by Item ───────────────────────────────────────────────────────


def _purchases_by_item(
    db: Session,
    start_date: date,
    end_date: date,
    vendor_id: Optional[int] = None,
    class_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    start_date, end_date = _default_dates(start_date, end_date)
    _require(db, Vendor, vendor_id, "vendor")
    _require(db, TxnClass, class_id, "class")
    _require(db, Job, job_id, "job")
    meta = _item_meta(db)

    bills = (
        db.query(Bill)
        .options(joinedload(Bill.lines))
        .filter(Bill.date >= start_date, Bill.date <= end_date)
        .filter(Bill.status != BillStatus.VOID)
    )
    if vendor_id is not None:
        bills = bills.filter(Bill.vendor_id == vendor_id)

    rows: dict = {}

    def bucket(item_id):
        return rows.setdefault(
            item_id,
            {"quantity": Decimal("0"), "amount": Decimal("0"), "line_count": 0},
        )

    for bill in bills.all():
        for line in bill.lines:
            if not _dim_ok(
                (line.class_id, line.job_id),
                (bill.class_id, bill.job_id),
                class_id,
                job_id,
            ):
                continue
            b = bucket(line.item_id)
            b["quantity"] += Decimal(str(line.quantity or 0))
            b["amount"] += home_amount(line.amount, bill.exchange_rate)
            b["line_count"] += 1

    credits = (
        db.query(VendorCredit)
        .options(joinedload(VendorCredit.lines))
        .filter(VendorCredit.date >= start_date, VendorCredit.date <= end_date)
        .filter(VendorCredit.status != VendorCreditStatus.VOID)
    )
    if vendor_id is not None:
        credits = credits.filter(VendorCredit.vendor_id == vendor_id)

    for vc in credits.all():
        for line in vc.lines:
            if not _dim_ok(
                (line.class_id, line.job_id),
                (vc.class_id, vc.job_id),
                class_id,
                job_id,
            ):
                continue
            b = bucket(line.item_id)
            b["quantity"] -= Decimal(str(line.quantity or 0))
            b["amount"] -= home_amount(line.amount, None)
            b["line_count"] += 1

    items = []
    for item_id, b in rows.items():
        name, item_type, _ = (
            meta.get(item_id, (None, None, False))
            if item_id
            else (_NO_ITEM, None, False)
        )
        qty = b["quantity"]
        amount = b["amount"]
        items.append(
            {
                "item_id": item_id,
                "item_name": name or f"Item {item_id}",
                "item_type": item_type,
                "quantity": float(qty),
                "amount": float(amount),
                "average_cost": float(amount / qty) if qty != 0 else None,
                "line_count": b["line_count"],
            }
        )

    items.sort(key=lambda r: (r["item_id"] is None, -r["amount"]))

    total_quantity = sum(
        (Decimal(str(r["quantity"])) for r in items if r["item_id"] is not None),
        Decimal("0"),
    )
    total_amount = sum((Decimal(str(r["amount"])) for r in items), Decimal("0"))

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "vendor_id": vendor_id,
        "class_id": class_id,
        "job_id": job_id,
        "items": items,
        "total_quantity": float(total_quantity),
        "total_amount": float(total_amount),
    }


def _purchases_lines(
    db: Session,
    start_date: date,
    end_date: date,
    item_key,
    vendor_id: Optional[int] = None,
    class_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    start_date, end_date = _default_dates(start_date, end_date)
    _require(db, Vendor, vendor_id, "vendor")
    _require(db, TxnClass, class_id, "class")
    _require(db, Job, job_id, "job")

    bills = (
        db.query(Bill)
        .options(joinedload(Bill.lines), joinedload(Bill.vendor))
        .filter(Bill.date >= start_date, Bill.date <= end_date)
        .filter(Bill.status != BillStatus.VOID)
    )
    if vendor_id is not None:
        bills = bills.filter(Bill.vendor_id == vendor_id)

    lines = []
    for bill in bills.all():
        for line in bill.lines:
            if line.item_id != item_key:
                continue
            if not _dim_ok(
                (line.class_id, line.job_id),
                (bill.class_id, bill.job_id),
                class_id,
                job_id,
            ):
                continue
            lines.append(
                {
                    "date": bill.date.isoformat(),
                    "doc_type": "Bill",
                    "number": bill.bill_number,
                    "party": bill.vendor.name if bill.vendor else None,
                    "description": line.description,
                    "quantity": float(line.quantity or 0),
                    "rate": float(line.rate or 0),
                    "amount": float(home_amount(line.amount, bill.exchange_rate)),
                    "link": {"type": _LINK_TYPE["bill"], "id": bill.id},
                }
            )

    credits = (
        db.query(VendorCredit)
        .options(joinedload(VendorCredit.lines), joinedload(VendorCredit.vendor))
        .filter(VendorCredit.date >= start_date, VendorCredit.date <= end_date)
        .filter(VendorCredit.status != VendorCreditStatus.VOID)
    )
    if vendor_id is not None:
        credits = credits.filter(VendorCredit.vendor_id == vendor_id)

    for vc in credits.all():
        for line in vc.lines:
            if line.item_id != item_key:
                continue
            if not _dim_ok(
                (line.class_id, line.job_id),
                (vc.class_id, vc.job_id),
                class_id,
                job_id,
            ):
                continue
            lines.append(
                {
                    "date": vc.date.isoformat(),
                    "doc_type": "Vendor Credit",
                    "number": vc.credit_number,
                    "party": vc.vendor.name if vc.vendor else None,
                    "description": line.description,
                    "quantity": -float(line.quantity or 0),
                    "rate": float(line.rate or 0),
                    "amount": -float(home_amount(line.amount, None)),
                    "link": {"type": _LINK_TYPE["vendor_credit"], "id": vc.id},
                }
            )

    lines.sort(key=lambda line_row: line_row["date"])
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "item_id": item_key,
        "lines": lines,
    }


def _item_key(item_id: Optional[str]):
    """The lines endpoints take item_id=none for the "No item" row; any
    other value is the integer item id."""
    if item_id is None or str(item_id).lower() in ("", "none", "null"):
        return None
    try:
        return int(item_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="item_id must be an integer or none"
        )


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/sales-by-item")
def sales_by_item(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """What sold, by item (#435). See module docstring for the tie to the P&L."""
    return _sales_by_item(db, start_date, end_date, customer_id, class_id, job_id)


@router.get("/sales-by-item/lines")
def sales_by_item_lines(
    item_id: Optional[str] = Query(default=None),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The documents behind one Sales by Item row (item_id=none for No item)."""
    return _sales_lines(
        db, start_date, end_date, _item_key(item_id), customer_id, class_id, job_id
    )


@router.get("/purchases-by-item")
def purchases_by_item(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """What was bought, by item (#435). Bills and vendor credits only —
    expense lines carry no item."""
    return _purchases_by_item(db, start_date, end_date, vendor_id, class_id, job_id)


@router.get("/purchases-by-item/lines")
def purchases_by_item_lines(
    item_id: Optional[str] = Query(default=None),
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The documents behind one Purchases by Item row (item_id=none for No item)."""
    return _purchases_lines(
        db, start_date, end_date, _item_key(item_id), vendor_id, class_id, job_id
    )


# ── PDF / CSV ────────────────────────────────────────────────────────────────


def _filename(report: str, data: dict, ext: str) -> str:
    return f"{report}_{data['start_date']}_{data['end_date']}.{ext}"


def _company_name(db: Session) -> str:
    from app.services.settings_service import get_all_settings

    return get_all_settings(db).get("company_name") or ""


def _csv_download(text: str, filename: str, request: Request):
    from app.routes.csv import _csv_response

    return _csv_response(text, filename, request)


def _pdf_response(sections, db: Session, filename: str):
    from fastapi.responses import Response
    from app.services.pdf_service import generate_report_pdf
    from app.services.settings_service import get_all_settings

    pdf_bytes = generate_report_pdf(sections, get_all_settings(db), landscape=True)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/sales-by-item/pdf")
def sales_by_item_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import sales_by_item_sections

    data = _sales_by_item(db, start_date, end_date, customer_id, class_id, job_id)
    return _pdf_response(
        sales_by_item_sections(data), db, _filename("Sales-by-Item", data, "pdf")
    )


@router.get("/sales-by-item/csv")
def sales_by_item_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import sales_by_item_csv

    data = _sales_by_item(db, start_date, end_date, customer_id, class_id, job_id)
    return _csv_download(
        sales_by_item_csv(data, _company_name(db)),
        _filename("Sales-by-Item", data, "csv"),
        request,
    )


@router.get("/purchases-by-item/pdf")
def purchases_by_item_pdf(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import purchases_by_item_sections

    data = _purchases_by_item(db, start_date, end_date, vendor_id, class_id, job_id)
    return _pdf_response(
        purchases_by_item_sections(data),
        db,
        _filename("Purchases-by-Item", data, "pdf"),
    )


@router.get("/purchases-by-item/csv")
def purchases_by_item_csv_route(
    request: Request,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    class_id: Optional[int] = Query(default=None),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.services.ledger_exports import purchases_by_item_csv

    data = _purchases_by_item(db, start_date, end_date, vendor_id, class_id, job_id)
    return _csv_download(
        purchases_by_item_csv(data, _company_name(db)),
        _filename("Purchases-by-Item", data, "csv"),
        request,
    )
