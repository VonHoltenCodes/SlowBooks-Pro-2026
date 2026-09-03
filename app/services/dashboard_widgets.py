# ============================================================================
# Dashboard widgets — one registry, one builder per card.
#
# The overview used to be one fixed data bundle drawn in one fixed order.
# Now each card is a widget with an id, a title, a size hint and a builder;
# the page asks for the ids in the user's layout and draws them in that
# order. Adding a card = adding a builder here plus a renderer in
# dashboard.js. Builders take a Session and return JSON-safe dicts.
# ============================================================================

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.banking import BankAccount
from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment
from app.models.purchase_orders import POStatus, PurchaseOrder
from app.models.transactions import Transaction, TransactionLine

OPEN_INVOICE = (InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PARTIAL)
OPEN_BILL = (BillStatus.UNPAID, BillStatus.PARTIAL)


def _f(v) -> float:
    return float(v or 0)


# ── builders ─────────────────────────────────────────────────────────────


def receivables(db: Session) -> dict:
    total = (
        db.query(func.coalesce(func.sum(Invoice.balance_due), 0))
        .filter(Invoice.status.in_(OPEN_INVOICE))
        .scalar()
    )
    overdue = (
        db.query(func.count(Invoice.id))
        .filter(
            Invoice.status.in_(OPEN_INVOICE), Invoice.due_date < func.current_date()
        )
        .scalar()
    )
    return {"total": _f(total), "overdue_count": int(overdue or 0)}


def overdue_invoices(db: Session) -> dict:
    rows = (
        db.query(Invoice)
        .filter(
            Invoice.status.in_(OPEN_INVOICE), Invoice.due_date < func.current_date()
        )
        .order_by(Invoice.due_date)
        .limit(5)
        .all()
    )
    count = (
        db.query(func.count(Invoice.id))
        .filter(
            Invoice.status.in_(OPEN_INVOICE), Invoice.due_date < func.current_date()
        )
        .scalar()
    )
    today = date.today()
    return {
        "count": int(count or 0),
        "items": [
            {
                "id": i.id,
                "invoice_number": i.invoice_number,
                "customer": i.customer.name if i.customer else "",
                "balance_due": _f(i.balance_due),
                "days_overdue": (today - i.due_date).days if i.due_date else 0,
            }
            for i in rows
        ],
    }


def active_customers(db: Session) -> dict:
    return {
        "count": int(
            db.query(func.count(Customer.id)).filter(Customer.is_active).scalar() or 0
        )
    }


def payables(db: Session) -> dict:
    total = (
        db.query(func.coalesce(func.sum(Bill.balance_due), 0))
        .filter(Bill.status.in_(OPEN_BILL))
        .scalar()
    )
    overdue = (
        db.query(func.count(Bill.id))
        .filter(Bill.status.in_(OPEN_BILL), Bill.due_date < func.current_date())
        .scalar()
    )
    return {"total": _f(total), "overdue_count": int(overdue or 0)}


def bank_balances(db: Session) -> dict:
    rows = db.query(BankAccount).filter(BankAccount.is_active).all()
    return {
        "accounts": [
            {"id": b.id, "name": b.name, "balance": _f(b.balance)} for b in rows
        ],
        "total": sum(_f(b.balance) for b in rows),
    }


def ar_aging(db: Session) -> dict:
    today = date.today()
    buckets = {
        "current": Decimal(0),
        "d30": Decimal(0),
        "d60": Decimal(0),
        "d90": Decimal(0),
    }
    rows = (
        db.query(Invoice)
        .filter(Invoice.status.in_(OPEN_INVOICE), Invoice.balance_due > 0)
        .all()
    )
    for inv in rows:
        days = (today - inv.due_date).days if inv.due_date else 0
        key = (
            "current"
            if days <= 0
            else "d30" if days <= 30 else "d60" if days <= 60 else "d90"
        )
        buckets[key] += inv.balance_due
    out = {k: float(v) for k, v in buckets.items()}
    out["total"] = sum(out.values())
    return out


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def monthly_revenue(db: Session) -> dict:
    """Last 12 months of income from the ledger (not just invoices)."""
    today = date.today()
    out = []
    for i in range(11, -1, -1):
        year, month = today.year, today.month - i
        while month <= 0:
            month += 12
            year -= 1
        start, end = _month_bounds(year, month)
        total = (
            db.query(
                func.coalesce(
                    func.sum(TransactionLine.credit - TransactionLine.debit), 0
                )
            )
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .join(Account, Account.id == TransactionLine.account_id)
            .filter(Transaction.date >= start, Transaction.date <= end)
            .filter(Account.account_type == AccountType.INCOME)
            .scalar()
        )
        out.append({"month": start.strftime("%b"), "year": year, "amount": _f(total)})
    return {"months": out}


def recent_invoices(db: Session) -> dict:
    rows = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(5).all()
    return {
        "items": [
            {
                "id": i.id,
                "invoice_number": i.invoice_number,
                "customer": i.customer.name if i.customer else "",
                "total": _f(i.total),
                "balance_due": _f(i.balance_due),
                "status": i.status.value,
                "date": i.date.isoformat(),
            }
            for i in rows
        ]
    }


def recent_payments(db: Session) -> dict:
    rows = db.query(Payment).order_by(Payment.created_at.desc()).limit(5).all()
    return {
        "items": [
            {
                "id": p.id,
                "customer": p.customer.name if getattr(p, "customer", None) else "",
                "amount": _f(p.amount),
                "date": p.date.isoformat(),
                "method": p.method,
            }
            for p in rows
        ]
    }


def _pl_for(db: Session, start: date, end: date) -> dict:
    rows = (
        db.query(
            Account.account_type,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(Transaction.date >= start, Transaction.date <= end)
        .filter(
            Account.account_type.in_(
                (AccountType.INCOME, AccountType.COGS, AccountType.EXPENSE)
            )
        )
        .group_by(Account.account_type)
        .all()
    )
    income = expenses = Decimal(0)
    for atype, dr, cr in rows:
        dr, cr = Decimal(str(dr)), Decimal(str(cr))
        if atype == AccountType.INCOME:
            income += cr - dr
        else:
            expenses += dr - cr
    return {
        "income": float(income),
        "expenses": float(expenses),
        "net": float(income - expenses),
    }


def pnl_month(db: Session) -> dict:
    """This month vs last, from posted lines."""
    today = date.today()
    this_start, this_end = _month_bounds(today.year, today.month)
    prev_end = this_start - timedelta(days=1)
    prev_start, _ = _month_bounds(prev_end.year, prev_end.month)
    cur, prev = _pl_for(db, this_start, this_end), _pl_for(db, prev_start, prev_end)
    return {
        "this_month": {"label": this_start.strftime("%B %Y"), **cur},
        "last_month": {"label": prev_start.strftime("%B %Y"), **prev},
        "net_change": cur["net"] - prev["net"],
    }


def cash_position(db: Session) -> dict:
    """Cash on hand (active bank accounts) and a simple 30-day forecast:
    cash + receivables due within 30 days − payables due within 30 days.
    A forecast, not a promise — it assumes customers pay on the due date."""
    today = date.today()
    horizon = today + timedelta(days=30)
    cash = sum(
        _f(b.balance) for b in db.query(BankAccount).filter(BankAccount.is_active).all()
    )
    ar_due = _f(
        db.query(func.coalesce(func.sum(Invoice.balance_due), 0))
        .filter(Invoice.status.in_(OPEN_INVOICE), Invoice.due_date <= horizon)
        .scalar()
    )
    ap_due = _f(
        db.query(func.coalesce(func.sum(Bill.balance_due), 0))
        .filter(Bill.status.in_(OPEN_BILL), Bill.due_date <= horizon)
        .scalar()
    )
    return {
        "cash": cash,
        "ar_due_30": ar_due,
        "ap_due_30": ap_due,
        "forecast_30": cash + ar_due - ap_due,
        "as_of": today.isoformat(),
    }


def open_pos(db: Session) -> dict:
    """Open purchase orders = committed but not yet billed."""
    open_status = (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED)
    rows = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(open_status))
        .order_by(PurchaseOrder.date.desc())
        .limit(5)
        .all()
    )
    total = _f(
        db.query(func.coalesce(func.sum(PurchaseOrder.total), 0))
        .filter(PurchaseOrder.status.in_(open_status))
        .scalar()
    )
    count = (
        db.query(func.count(PurchaseOrder.id))
        .filter(PurchaseOrder.status.in_(open_status))
        .scalar()
    )
    return {
        "count": int(count or 0),
        "total": total,
        "items": [
            {
                "id": po.id,
                "po_number": po.po_number,
                "vendor": po.vendor.name if po.vendor else "",
                "job": po.job.full_name if getattr(po, "job", None) else "",
                "status": po.status.value,
                "total": _f(po.total),
                "date": po.date.isoformat(),
            }
            for po in rows
        ],
    }


def receipts_review(db: Session) -> dict:
    """Scanned receipts sitting in the intake bucket, not yet attached to a
    document (they expire after INTAKE_TTL_HOURS)."""
    import json
    from datetime import datetime

    from app.services import ocr_service

    items = []
    try:
        base = ocr_service._intake_dir()
        now = datetime.now()
        for meta_path in sorted(
            base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(meta["created_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            age_h = (now - created).total_seconds() / 3600
            if age_h > ocr_service.INTAKE_TTL_HOURS:
                continue
            items.append(
                {
                    "intake_id": meta.get("intake_id"),
                    "filename": meta.get("original_filename", ""),
                    "created_at": meta["created_at"],
                    "expires_in_hours": round(ocr_service.INTAKE_TTL_HOURS - age_h, 1),
                }
            )
    except Exception:
        items = []
    return {
        "count": len(items),
        "items": items[:8],
        "ttl_hours": ocr_service.INTAKE_TTL_HOURS,
    }


def job_budget_vs_actual(db: Session) -> dict:
    """Active jobs ranked by how far over or under budget they project."""
    from app.services.job_costing import budget_vs_actual_all_jobs

    rows = budget_vs_actual_all_jobs(db)
    rows = [r for r in rows if r["revised"] or r["actual"] or r["committed"]]
    rows.sort(key=lambda r: (r["variance"] if r["revised"] else 0))
    return {
        "count": len(rows),
        "items": [
            {
                k: r[k]
                for k in (
                    "job_id",
                    "job_name",
                    "customer_name",
                    "status",
                    "revised",
                    "committed",
                    "actual",
                    "projected",
                    "variance",
                    "pct_used",
                )
            }
            for r in rows[:6]
        ],
        "totals": {
            k: sum(r[k] for r in rows)
            for k in ("revised", "committed", "actual", "projected", "variance")
        },
    }


# ── registry ─────────────────────────────────────────────────────────────

# id → (title, size, description, builder). Size is a layout hint the page
# uses for the grid: "stat" = small number tile, "half" = half-width panel,
# "full" = full-width panel.
WIDGETS: dict[str, tuple[str, str, str, Callable[[Session], dict]]] = {
    "receivables": (
        "Total Receivables",
        "stat",
        "Open invoice balances, with the overdue count",
        receivables,
    ),
    "overdue_invoices": (
        "Overdue Invoices",
        "half",
        "The oldest overdue invoices and who owes them",
        overdue_invoices,
    ),
    "active_customers": (
        "Active Customers",
        "stat",
        "How many customers are active",
        active_customers,
    ),
    "payables": (
        "Total Payables",
        "stat",
        "Open bill balances, with the overdue count",
        payables,
    ),
    "bank_balances": (
        "Bank Balances",
        "full",
        "Every active bank account and its balance",
        bank_balances,
    ),
    "ar_aging": ("A/R Aging", "half", "Receivables by age bucket", ar_aging),
    "monthly_revenue": (
        "Monthly Revenue",
        "half",
        "Income from the ledger, last 12 months",
        monthly_revenue,
    ),
    "recent_invoices": (
        "Recent Invoices",
        "half",
        "The last five invoices",
        recent_invoices,
    ),
    "recent_payments": (
        "Recent Payments",
        "half",
        "The last five payments received",
        recent_payments,
    ),
    "pnl_month": (
        "P&L: This Month vs Last",
        "half",
        "Income, expenses and net for this month beside last month",
        pnl_month,
    ),
    "cash_position": (
        "Cash Position",
        "half",
        "Cash on hand and a 30-day forecast from what's due",
        cash_position,
    ),
    "open_pos": (
        "Open Purchase Orders",
        "half",
        "Committed but not yet billed",
        open_pos,
    ),
    "receipts_review": (
        "Receipts to Review",
        "half",
        "Scanned receipts waiting to be turned into a bill or expense",
        receipts_review,
    ),
    "job_budget_vs_actual": (
        "Jobs: Budget vs Actual",
        "full",
        "Active jobs ranked by projected variance",
        job_budget_vs_actual,
    ),
}

# The order and set a company gets before anyone customises anything —
# the pre-2.8 overview, exactly.
DEFAULT_LAYOUT = [
    "receivables",
    "overdue_invoices",
    "active_customers",
    "payables",
    "bank_balances",
    "ar_aging",
    "monthly_revenue",
    "recent_invoices",
    "recent_payments",
]


def catalog() -> list[dict]:
    return [
        {"id": wid, "title": title, "size": size, "description": desc}
        for wid, (title, size, desc, _) in WIDGETS.items()
    ]


def build(db: Session, ids: list[str]) -> dict[str, dict]:
    """Data for the requested widgets. Unknown ids are skipped; a builder
    that raises reports its error in place of data so one broken card
    never takes the page down."""
    out: dict[str, dict] = {}
    for wid in ids:
        entry = WIDGETS.get(wid)
        if not entry:
            continue
        try:
            out[wid] = entry[3](db)
        except Exception as exc:  # pragma: no cover - defensive
            out[wid] = {"error": str(exc)}
    return out
