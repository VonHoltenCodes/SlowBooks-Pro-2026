from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.services.accounting import taxable_subtotal
from app.models.accounts import Account
from app.models.invoices import Invoice, InvoiceStatus
from app.models.contacts import Vendor
from app.routes.reports._router import router


class SalesTaxPaymentRequest(BaseModel):
    date: Optional[date] = None
    amount: Decimal
    pay_from_account_id: int
    check_number: Optional[str] = ""
    reference: Optional[str] = None


@router.get("/sales-tax")
def sales_tax_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Sales Tax report."""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    # joinedload avoids an N+1 on inv.customer access in the loop below.
    from sqlalchemy.orm import joinedload

    invoices = (
        db.query(Invoice)
        .options(joinedload(Invoice.customer))
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .order_by(Invoice.date)
        .all()
    )

    total_sales = Decimal(0)
    total_taxable = Decimal(0)
    total_tax = Decimal(0)
    items = []

    for inv in invoices:
        total_sales += inv.subtotal
        if inv.tax_amount and inv.tax_amount > 0:
            # Only the taxable lines form the base (a labor line on a
            # customer-owned device sits beside a taxed part).
            total_taxable += taxable_subtotal(inv.lines)
            total_tax += inv.tax_amount
        items.append(
            {
                "date": inv.date.isoformat(),
                "invoice_number": inv.invoice_number,
                "customer_name": inv.customer.name if inv.customer else "",
                "subtotal": float(inv.subtotal),
                "tax_rate": float(inv.tax_rate),
                "tax_amount": float(inv.tax_amount),
            }
        )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_sales": float(total_sales),
        "total_taxable": float(total_taxable),
        "total_non_taxable": float(total_sales - total_taxable),
        "total_tax": float(total_tax),
    }


@router.post("/sales-tax/pay")
def pay_sales_tax(data: SalesTaxPaymentRequest, db: Session = Depends(get_db)):
    """Record a sales tax payment — DR Sales Tax Payable, CR Bank Account"""
    from app.services.accounting import create_journal_entry, get_sales_tax_account_id
    from app.services.closing_date import check_closing_date

    pay_date = data.date or date.today()
    check_closing_date(db, pay_date)

    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    bank_account = (
        db.query(Account).filter(Account.id == data.pay_from_account_id).first()
    )
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    tax_account_id = get_sales_tax_account_id(db)
    if not tax_account_id:
        raise HTTPException(
            status_code=400, detail="Sales Tax Payable account (2200) not found"
        )

    journal_lines = [
        {
            "account_id": tax_account_id,
            "debit": data.amount,
            "credit": Decimal("0"),
            "description": "Sales tax payment",
        },
        {
            "account_id": data.pay_from_account_id,
            "debit": Decimal("0"),
            "credit": data.amount,
            "description": "Sales tax payment",
        },
    ]

    reference = data.reference if data.reference is not None else data.check_number

    txn = create_journal_entry(
        db,
        pay_date,
        "Sales Tax Payment",
        journal_lines,
        source_type="sales_tax_payment",
        reference=reference,
    )
    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(data.amount)}


@router.get("/ap-aging")
def ap_aging(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    """AP Aging report — mirrors AR aging but for bills."""
    if not as_of_date:
        as_of_date = date.today()

    try:
        from app.models.bills import Bill, BillStatus
        from app.models.contacts import Vendor

        bills = (
            db.query(Bill)
            .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
            .filter(Bill.balance_due > 0)
            .all()
        )

        vendor_names = {v.id: v.name for v in db.query(Vendor.id, Vendor.name).all()}

        aging = {}
        for bill in bills:
            vid = bill.vendor_id
            if vid not in aging:
                aging[vid] = {
                    "vendor_name": vendor_names.get(vid, "Unknown"),
                    "vendor_id": vid,
                    "current": Decimal(0),
                    "over_30": Decimal(0),
                    "over_60": Decimal(0),
                    "over_90": Decimal(0),
                    "total": Decimal(0),
                }

            days = (as_of_date - bill.due_date).days if bill.due_date else 0
            bal = bill.balance_due
            if days <= 0:
                aging[vid]["current"] += bal
            elif days <= 30:
                aging[vid]["over_30"] += bal
            elif days <= 60:
                aging[vid]["over_60"] += bal
            else:
                aging[vid]["over_90"] += bal
            aging[vid]["total"] += bal

        items = list(aging.values())
        totals = {
            "vendor_name": "TOTAL",
            "vendor_id": 0,
            "current": sum(i["current"] for i in items),
            "over_30": sum(i["over_30"] for i in items),
            "over_60": sum(i["over_60"] for i in items),
            "over_90": sum(i["over_90"] for i in items),
            "total": sum(i["total"] for i in items),
        }
        for item in items:
            for k in ("current", "over_30", "over_60", "over_90", "total"):
                item[k] = float(item[k])
        for k in ("current", "over_30", "over_60", "over_90", "total"):
            totals[k] = float(totals[k])

        return {"as_of_date": as_of_date.isoformat(), "items": items, "totals": totals}
    except ImportError:
        return {
            "as_of_date": as_of_date.isoformat(),
            "items": [],
            "totals": {
                "vendor_name": "TOTAL",
                "vendor_id": 0,
                "current": 0,
                "over_30": 0,
                "over_60": 0,
                "over_90": 0,
                "total": 0,
            },
        }


@router.get("/1099-summary")
def report_1099_summary(
    year: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """1099 Summary: total payments to 1099 vendors for a year."""
    if not year:
        year = date.today().year

    from app.models.bills import BillPayment, BillPaymentAllocation

    vendors_1099 = db.query(Vendor).filter(Vendor.is_1099_vendor).all()
    if not vendors_1099:
        return {"year": year, "items": [], "total": 0, "vendors_above_threshold": 0}

    # Single query: sum bill payment allocations grouped by vendor for the year.
    # Replaces a query-per-vendor loop that made this O(N) in DB round-trips.
    totals_by_vendor = dict(
        db.query(
            BillPayment.vendor_id,
            sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0),
        )
        .join(
            BillPaymentAllocation,
            BillPaymentAllocation.bill_payment_id == BillPayment.id,
        )
        .filter(sqlfunc.extract("year", BillPayment.date) == year)
        .group_by(BillPayment.vendor_id)
        .all()
    )

    items = []
    total = Decimal(0)
    above_threshold = 0

    for vendor in vendors_1099:
        vendor_total = Decimal(str(totals_by_vendor.get(vendor.id, 0) or 0))
        total += vendor_total
        flagged = vendor_total >= 600
        if flagged:
            above_threshold += 1

        items.append(
            {
                "vendor_id": vendor.id,
                "vendor_name": vendor.name,
                "tax_id": vendor.tax_id or "",
                "vendor_1099_type": vendor.vendor_1099_type or "NEC",
                "total_paid": float(vendor_total),
                "above_threshold": flagged,
            }
        )

    items.sort(key=lambda x: x["total_paid"], reverse=True)

    return {
        "year": year,
        "items": items,
        "total": float(total),
        "vendors_above_threshold": above_threshold,
        "threshold": 600.0,
    }
