# ============================================================================
# Decompiled from qbw32.exe!CReportEngine  Offset: 0x00210000
# The original report engine had its own query language ("QBReportQuery")
# compiled to Btrieve API calls. The P&L report alone generated 14 separate
# Btrieve operations. We just use SQL because it's not the stone age.
# Sales Tax report was added in R3 service pack (0x002108A0).
# General Ledger was CReportEngine::RunGLDetail() at 0x00211400.
# ============================================================================

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc


class SalesTaxPaymentRequest(BaseModel):
    date: Optional[date] = None
    amount: Decimal
    pay_from_account_id: int
    check_number: Optional[str] = ""
    reference: Optional[str] = None


class CollectionLetterRequest(BaseModel):
    letter_type: str = "30"
    customer_ids: Optional[list[int]] = None
    send_email: bool = False

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.contacts import Customer, Vendor
from app.services.pdf_service import generate_statement_pdf, generate_collection_letter_pdf
from app.services.settings_service import get_all_settings as get_settings

router = APIRouter(prefix="/api/reports", tags=["reports"])


# Debit-normal account types. For these, natural balance = debit - credit.
# For the rest (liability, equity, income), natural balance = credit - debit.
_DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS}


def _totals_by_account(db, acct_type, date_start=None, date_end=None):
    """Return a list of {account_name, account_number, amount} rows where amount
    is signed by the account type's natural balance (always positive for a
    normal-balance ledger).
    """
    q = (
        db.query(
            Account.name, Account.account_number,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Account.account_type == acct_type)
    )
    if date_start is not None:
        q = q.filter(Transaction.date >= date_start)
    if date_end is not None:
        q = q.filter(Transaction.date <= date_end)
    q = q.group_by(Account.id, Account.name, Account.account_number)

    rows = []
    for name, number, dr, cr in q.all():
        amount = (dr - cr) if acct_type in _DEBIT_NORMAL else (cr - dr)
        rows.append({
            "account_name": name,
            "account_number": number,
            "amount": float(amount),
        })
    return rows


@router.get("/profit-loss")
def profit_loss(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    income = _totals_by_account(db, AccountType.INCOME, start_date, end_date)
    cogs = _totals_by_account(db, AccountType.COGS, start_date, end_date)
    expenses = _totals_by_account(db, AccountType.EXPENSE, start_date, end_date)

    total_income = sum(i["amount"] for i in income)
    total_cogs = sum(c["amount"] for c in cogs)
    total_expenses = sum(e["amount"] for e in expenses)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "income": income,
        "cogs": cogs,
        "expenses": expenses,
        "total_income": total_income,
        "total_cogs": total_cogs,
        "gross_profit": total_income - total_cogs,
        "total_expenses": total_expenses,
        "net_income": total_income - total_cogs - total_expenses,
    }


@router.get("/balance-sheet")
def balance_sheet(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    if not as_of_date:
        as_of_date = date.today()

    assets = _totals_by_account(db, AccountType.ASSET, date_end=as_of_date)
    liabilities = _totals_by_account(db, AccountType.LIABILITY, date_end=as_of_date)
    equity = _totals_by_account(db, AccountType.EQUITY, date_end=as_of_date)

    total_assets = sum(a["amount"] for a in assets)
    total_liabilities = sum(l["amount"] for l in liabilities)
    total_equity = sum(e["amount"] for e in equity)

    return {
        "as_of_date": as_of_date.isoformat(),
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
    }


@router.get("/ar-aging")
def ar_aging(as_of_date: date = Query(default=None), db: Session = Depends(get_db)):
    if not as_of_date:
        as_of_date = date.today()

    invoices = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.date <= as_of_date)
        .filter(Invoice.balance_due > 0)
        .all()
    )

    customer_names = {c.id: c.name for c in db.query(Customer.id, Customer.name).all()}

    aging = {}
    for inv in invoices:
        cid = inv.customer_id
        if cid not in aging:
            aging[cid] = {
                "customer_name": customer_names.get(cid, "Unknown"), "customer_id": cid,
                "current": Decimal(0), "over_30": Decimal(0),
                "over_60": Decimal(0), "over_90": Decimal(0), "total": Decimal(0),
            }

        days = (as_of_date - inv.due_date).days if inv.due_date else 0
        bal = inv.balance_due
        if days <= 0:
            aging[cid]["current"] += bal
        elif days <= 30:
            aging[cid]["over_30"] += bal
        elif days <= 60:
            aging[cid]["over_60"] += bal
        else:
            aging[cid]["over_90"] += bal
        aging[cid]["total"] += bal

    items = list(aging.values())
    totals = {
        "customer_name": "TOTAL", "customer_id": 0,
        "current": sum(i["current"] for i in items),
        "over_30": sum(i["over_30"] for i in items),
        "over_60": sum(i["over_60"] for i in items),
        "over_90": sum(i["over_90"] for i in items),
        "total": sum(i["total"] for i in items),
    }
    # Convert Decimals to float for JSON
    for item in items:
        for k in ("current", "over_30", "over_60", "over_90", "total"):
            item[k] = float(item[k])
    for k in ("current", "over_30", "over_60", "over_90", "total"):
        totals[k] = float(totals[k])

    return {"as_of_date": as_of_date.isoformat(), "items": items, "totals": totals}


@router.get("/sales-tax")
def sales_tax_report(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """CReportEngine::RunSalesTax() @ 0x002108A0"""
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
            total_taxable += inv.subtotal
            total_tax += inv.tax_amount
        items.append({
            "date": inv.date.isoformat(),
            "invoice_number": inv.invoice_number,
            "customer_name": inv.customer.name if inv.customer else "",
            "subtotal": float(inv.subtotal),
            "tax_rate": float(inv.tax_rate),
            "tax_amount": float(inv.tax_amount),
        })

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

    bank_account = db.query(Account).filter(Account.id == data.pay_from_account_id).first()
    if not bank_account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    tax_account_id = get_sales_tax_account_id(db)
    if not tax_account_id:
        raise HTTPException(status_code=400, detail="Sales Tax Payable account (2200) not found")

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
        db, pay_date, "Sales Tax Payment",
        journal_lines, source_type="sales_tax_payment",
        reference=reference,
    )
    db.commit()
    return {"status": "ok", "transaction_id": txn.id, "amount": float(data.amount)}


@router.get("/general-ledger")
def general_ledger(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    account_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """CReportEngine::RunGLDetail() @ 0x00211400"""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    q = (
        db.query(TransactionLine, Transaction, Account)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
    )
    if account_id:
        q = q.filter(TransactionLine.account_id == account_id)

    q = q.order_by(Account.account_number, Transaction.date)
    results = q.all()

    entries_by_account = {}
    for tl, txn, acct in results:
        key = acct.id
        if key not in entries_by_account:
            entries_by_account[key] = {
                "account_id": acct.id,
                "account_number": acct.account_number,
                "account_name": acct.name,
                "account_type": acct.account_type.value,
                "entries": [],
                "total_debit": Decimal(0),
                "total_credit": Decimal(0),
            }
        entries_by_account[key]["entries"].append({
            "date": txn.date.isoformat(),
            "description": txn.description or tl.description or "",
            "reference": txn.reference or "",
            "debit": float(tl.debit),
            "credit": float(tl.credit),
        })
        entries_by_account[key]["total_debit"] += tl.debit
        entries_by_account[key]["total_credit"] += tl.credit

    accounts_list = list(entries_by_account.values())
    for a in accounts_list:
        a["total_debit"] = float(a["total_debit"])
        a["total_credit"] = float(a["total_credit"])

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "accounts": accounts_list,
    }


@router.get("/income-by-customer")
def income_by_customer(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """CReportEngine::RunIncomeByCustomer() @ 0x00212000"""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    invoices = (
        db.query(Invoice)
        .filter(Invoice.date >= start_date, Invoice.date <= end_date)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .all()
    )

    by_customer = {}
    for inv in invoices:
        cid = inv.customer_id
        if cid not in by_customer:
            cname = inv.customer.name if inv.customer else "Unknown"
            by_customer[cid] = {
                "customer_id": cid,
                "customer_name": cname,
                "invoice_count": 0,
                "total_sales": Decimal(0),
                "total_paid": Decimal(0),
                "total_balance": Decimal(0),
            }
        by_customer[cid]["invoice_count"] += 1
        by_customer[cid]["total_sales"] += inv.total
        by_customer[cid]["total_paid"] += inv.amount_paid
        by_customer[cid]["total_balance"] += inv.balance_due

    items = sorted(by_customer.values(), key=lambda x: float(x["total_sales"]), reverse=True)
    for item in items:
        item["total_sales"] = float(item["total_sales"])
        item["total_paid"] = float(item["total_paid"])
        item["total_balance"] = float(item["total_balance"])

    grand_sales = sum(i["total_sales"] for i in items)
    grand_paid = sum(i["total_paid"] for i in items)
    grand_balance = sum(i["total_balance"] for i in items)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_sales": grand_sales,
        "total_paid": grand_paid,
        "total_balance": grand_balance,
    }


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
                    "vendor_name": vendor_names.get(vid, "Unknown"), "vendor_id": vid,
                    "current": Decimal(0), "over_30": Decimal(0),
                    "over_60": Decimal(0), "over_90": Decimal(0), "total": Decimal(0),
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
            "vendor_name": "TOTAL", "vendor_id": 0,
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
        return {"as_of_date": as_of_date.isoformat(), "items": [], "totals": {
            "vendor_name": "TOTAL", "vendor_id": 0,
            "current": 0, "over_30": 0, "over_60": 0, "over_90": 0, "total": 0,
        }}


@router.get("/customer-statement/{customer_id}/pdf")
def customer_statement_pdf(
    customer_id: int,
    as_of_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """CStatementPrintLayout::RenderPage() @ 0x00224000"""
    if not as_of_date:
        as_of_date = date.today()

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    invoices = (
        db.query(Invoice)
        .filter(Invoice.customer_id == customer_id)
        .filter(Invoice.status != InvoiceStatus.VOID)
        .filter(Invoice.date <= as_of_date)
        .order_by(Invoice.date)
        .all()
    )

    payments = (
        db.query(Payment)
        .filter(Payment.customer_id == customer_id)
        .filter(Payment.date <= as_of_date)
        .order_by(Payment.date)
        .all()
    )

    company = get_settings(db)
    pdf_bytes = generate_statement_pdf(customer, invoices, payments, company, as_of_date)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Statement_{customer.name}.pdf"},
    )


# ============================================================================
# Phase 10: Quick Wins — Trial Balance, Cash Flow, Batch Email,
#            Collection Letters, 1099 Summary
# ============================================================================

@router.get("/trial-balance")
def trial_balance(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Trial Balance: sum all debits/credits per account for a date range."""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    results = (
        db.query(
            Account.id, Account.account_number, Account.name, Account.account_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
        .group_by(Account.id, Account.account_number, Account.name, Account.account_type)
        .order_by(Account.account_number)
        .all()
    )

    items = []
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    for acct_id, acct_num, acct_name, acct_type, debit, credit in results:
        total_debit += debit
        total_credit += credit
        items.append({
            "account_id": acct_id,
            "account_number": acct_num or "",
            "account_name": acct_name,
            "account_type": acct_type.value,
            "total_debit": float(debit),
            "total_credit": float(credit),
            "net_balance": float(debit - credit),
        })

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "items": items,
        "total_debit": float(total_debit),
        "total_credit": float(total_credit),
        "difference": float(total_debit - total_credit),
    }


@router.get("/cash-flow")
def cash_flow(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    db: Session = Depends(get_db),
):
    """Cash Flow Statement: Operating, Investing, Financing sections."""
    if not start_date:
        start_date = date(date.today().year, 1, 1)
    if not end_date:
        end_date = date.today()

    # Map account types to cash flow sections
    section_map = {
        AccountType.INCOME: "operating",
        AccountType.EXPENSE: "operating",
        AccountType.COGS: "operating",
        AccountType.ASSET: "investing",
        AccountType.LIABILITY: "financing",
        AccountType.EQUITY: "financing",
    }

    results = (
        db.query(
            Account.name, Account.account_number, Account.account_type,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit - TransactionLine.debit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
        .group_by(Account.id, Account.name, Account.account_number, Account.account_type)
        .order_by(Account.account_number)
        .all()
    )

    sections = {"operating": [], "investing": [], "financing": []}
    totals = {"operating": Decimal(0), "investing": Decimal(0), "financing": Decimal(0)}

    for acct_name, acct_num, acct_type, net_change in results:
        section = section_map.get(acct_type, "operating")
        amount = float(net_change)
        # For investing (assets), net cash flow is negative of net change
        # (buying assets = cash outflow)
        if section == "investing":
            amount = -amount
        sections[section].append({
            "account_name": acct_name,
            "account_number": acct_num or "",
            "amount": amount,
        })
        totals[section] += Decimal(str(amount))

    net_change = totals["operating"] + totals["investing"] + totals["financing"]

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "operating": sections["operating"],
        "investing": sections["investing"],
        "financing": sections["financing"],
        "total_operating": float(totals["operating"]),
        "total_investing": float(totals["investing"]),
        "total_financing": float(totals["financing"]),
        "net_change": float(net_change),
    }


@router.post("/batch-email-statements")
def batch_email_statements(db: Session = Depends(get_db)):
    """Email statements to all customers with overdue invoices."""
    from app.services.email_service import send_email

    settings = get_settings(db)
    as_of_date = date.today()

    overdue_invoices = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date < as_of_date)
        .all()
    )

    # Group by customer
    by_customer = {}
    for inv in overdue_invoices:
        by_customer.setdefault(inv.customer_id, []).append(inv)

    sent = 0
    failed = 0
    errors = []

    for cid, invs in by_customer.items():
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if not customer or not customer.email:
            errors.append(f"Customer {customer.name if customer else cid}: no email address")
            failed += 1
            continue

        try:
            payments = (
                db.query(Payment)
                .filter(Payment.customer_id == cid)
                .filter(Payment.date <= as_of_date)
                .order_by(Payment.date)
                .all()
            )
            all_invoices = (
                db.query(Invoice)
                .filter(Invoice.customer_id == cid)
                .filter(Invoice.status != InvoiceStatus.VOID)
                .filter(Invoice.date <= as_of_date)
                .order_by(Invoice.date)
                .all()
            )

            pdf_bytes = generate_statement_pdf(customer, all_invoices, payments, settings, as_of_date)

            send_email(
                db=db,
                to_email=customer.email,
                subject=f"Account Statement — {settings.get('company_name', 'Our Company')}",
                html_body=f"<p>Dear {customer.name},</p><p>Please find your account statement attached.</p><p>{settings.get('company_name', '')}</p>",
                attachment_bytes=pdf_bytes,
                attachment_name=f"Statement_{customer.name}.pdf",
                entity_type="statement",
                entity_id=cid,
            )
            sent += 1
        except Exception:
            logger.exception("Failed to send statement to customer %s", customer.id)
            errors.append(f"Customer {customer.name}: unable to send statement")
            failed += 1

    return {"sent": sent, "failed": failed, "errors": errors}


@router.post("/collection-letters")
def collection_letters(data: CollectionLetterRequest, db: Session = Depends(get_db)):
    """Generate and optionally email collection letters."""
    from app.services.email_service import send_email

    letter_type = data.letter_type
    customer_ids = data.customer_ids
    send_email_flag = data.send_email
    settings = get_settings(db)
    today = date.today()

    # Map letter type to minimum days overdue
    min_days = {"30": 30, "60": 60, "90": 90}.get(letter_type, 30)

    q = (
        db.query(Invoice)
        .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
        .filter(Invoice.balance_due > 0)
        .filter(Invoice.due_date <= today - timedelta(days=min_days))
    )
    if customer_ids:
        q = q.filter(Invoice.customer_id.in_(customer_ids))

    overdue_invoices = q.all()

    # Group by customer
    by_customer = {}
    for inv in overdue_invoices:
        by_customer.setdefault(inv.customer_id, []).append(inv)

    generated = 0
    emailed = 0
    errors = []
    pdfs = []

    for cid, invs in by_customer.items():
        customer = db.query(Customer).filter(Customer.id == cid).first()
        if not customer:
            continue

        # Add days_overdue to each invoice for the template
        for inv in invs:
            inv.days_overdue = (today - inv.due_date).days if inv.due_date else 0

        total_due = sum(float(inv.balance_due) for inv in invs)

        try:
            pdf_bytes = generate_collection_letter_pdf(
                customer, invs, settings, letter_type, total_due
            )
            generated += 1

            if send_email_flag and customer.email:
                type_labels = {"30": "Payment Reminder", "60": "Second Notice", "90": "Final Notice"}
                send_email(
                    db=db,
                    to_email=customer.email,
                    subject=f"{type_labels.get(letter_type, 'Collection Notice')} — {settings.get('company_name', '')}",
                    html_body=f"<p>Dear {customer.name},</p><p>Please see the attached collection notice regarding your outstanding balance of ${total_due:,.2f}.</p>",
                    attachment_bytes=pdf_bytes,
                    attachment_name=f"Collection_{letter_type}day_{customer.name}.pdf",
                    entity_type="collection",
                    entity_id=cid,
                )
                emailed += 1
        except Exception:
            logger.exception("Failed to generate collection letter for customer %s", customer.id)
            errors.append(f"{customer.name}: unable to generate collection letter")

    return {"generated": generated, "emailed": emailed, "errors": errors}


@router.get("/1099-summary")
def report_1099_summary(
    year: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """1099 Summary: total payments to 1099 vendors for a year."""
    if not year:
        year = date.today().year

    from app.models.bills import Bill, BillPayment, BillPaymentAllocation

    vendors_1099 = db.query(Vendor).filter(Vendor.is_1099_vendor == True).all()
    if not vendors_1099:
        return {"year": year, "items": [], "total": 0, "vendors_above_threshold": 0}

    # Single query: sum bill payment allocations grouped by vendor for the year.
    # Replaces a query-per-vendor loop that made this O(N) in DB round-trips.
    totals_by_vendor = dict(
        db.query(
            BillPayment.vendor_id,
            sqlfunc.coalesce(sqlfunc.sum(BillPaymentAllocation.amount), 0),
        )
        .join(BillPaymentAllocation, BillPaymentAllocation.bill_payment_id == BillPayment.id)
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

        items.append({
            "vendor_id": vendor.id,
            "vendor_name": vendor.name,
            "tax_id": vendor.tax_id or "",
            "vendor_1099_type": vendor.vendor_1099_type or "NEC",
            "total_paid": float(vendor_total),
            "above_threshold": flagged,
        })

    items.sort(key=lambda x: x["total_paid"], reverse=True)

    return {
        "year": year,
        "items": items,
        "total": float(total),
        "vendors_above_threshold": above_threshold,
        "threshold": 600.0,
    }
