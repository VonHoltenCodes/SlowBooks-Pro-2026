from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.banking import BankAccount
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db), auth=Depends(require_permissions())):
    total_receivables = db.query(func.coalesce(func.sum(Invoice.balance_due), 0)).filter(
        Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL])
    ).scalar()

    overdue_count = db.query(func.count(Invoice.id)).filter(
        Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]),
        Invoice.due_date < func.current_date(),
    ).scalar()

    customer_count = db.query(func.count(Customer.id)).filter(Customer.is_active == True).scalar()
    recent_invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(5).all()
    recent_payments = db.query(Payment).order_by(Payment.created_at.desc()).limit(5).all()
    bank_balances = db.query(BankAccount).filter(BankAccount.is_active == True).all()

    total_payables = 0.0
    overdue_bills = 0
    try:
        from app.models.bills import Bill, BillStatus

        total_payables = float(db.query(func.coalesce(func.sum(Bill.balance_due), 0)).filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL])).scalar())
        overdue_bills = db.query(func.count(Bill.id)).filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]), Bill.due_date < func.current_date()).scalar()
    except Exception:
        pass

    return {
        "total_receivables": float(total_receivables),
        "overdue_count": overdue_count,
        "customer_count": customer_count,
        "total_payables": total_payables,
        "overdue_bills": overdue_bills,
        "recent_invoices": [{"id": inv.id, "invoice_number": inv.invoice_number, "customer_id": inv.customer_id, "total": float(inv.total), "balance_due": float(inv.balance_due), "status": inv.status.value, "date": inv.date.isoformat()} for inv in recent_invoices],
        "recent_payments": [{"id": p.id, "customer_id": p.customer_id, "amount": float(p.amount), "date": p.date.isoformat(), "method": p.method} for p in recent_payments],
        "bank_balances": [{"id": ba.id, "name": ba.name, "balance": float(ba.balance)} for ba in bank_balances],
    }


@router.get("/charts")
def get_dashboard_charts(db: Session = Depends(get_db), auth=Depends(require_permissions())):
    today = date.today()
    invoices = db.query(Invoice).filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]), Invoice.balance_due > 0).all()

    aging_current = Decimal(0)
    aging_30 = Decimal(0)
    aging_60 = Decimal(0)
    aging_90 = Decimal(0)

    for inv in invoices:
        days = (today - inv.due_date).days if inv.due_date else 0
        if days <= 0:
            aging_current += inv.balance_due
        elif days <= 30:
            aging_30 += inv.balance_due
        elif days <= 60:
            aging_60 += inv.balance_due
        else:
            aging_90 += inv.balance_due

    monthly_revenue = []
    for i in range(11, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        _, last_day = monthrange(year, month)
        start = date(year, month, 1)
        end = date(year, month, last_day)
        total = db.query(func.coalesce(func.sum(Invoice.total), 0)).filter(Invoice.date >= start, Invoice.date <= end, Invoice.status != InvoiceStatus.VOID).scalar()
        monthly_revenue.append({"month": start.strftime("%b"), "amount": float(total)})

    return {
        "aging_current": float(aging_current),
        "aging_30": float(aging_30),
        "aging_60": float(aging_60),
        "aging_90": float(aging_90),
        "monthly_revenue": monthly_revenue,
    }
