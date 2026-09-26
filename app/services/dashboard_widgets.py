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
    # What customers owe net of the credits they hold (unapplied payments,
    # credit memos), in home currency: the A/R Aging total and account 1100.
    # Summing invoice balances alone read $782.13 against a balance sheet of
    # $555.74 (explore 2.17.3, F17).
    from app.services.contact_balances import customer_balances

    total = sum(customer_balances(db).values(), Decimal(0))
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


def _bank_ledger_rows(db: Session) -> list[dict]:
    """Bank and card accounts with their ledger balances (issue #114: the
    register's stored balance is gone; the ledger is the number)."""
    from app.services.bank_register import gl_balances

    accounts = (
        db.query(Account)
        .filter(Account.bank_kind.isnot(None), Account.is_active)
        .order_by(Account.account_number)
        .all()
    )
    balances = gl_balances(db, [a.id for a in accounts])
    return [
        {
            "id": a.id,
            "name": a.name,
            "kind": a.bank_kind,
            "balance": _f(balances.get(a.id, 0)),
        }
        for a in accounts
    ]


def bank_balances(db: Session) -> dict:
    rows = _bank_ledger_rows(db)
    # cards are owed, not cash: the total is the bank side only
    return {
        "accounts": rows,
        "total": sum(r["balance"] for r in rows if r["kind"] == "bank"),
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


def pnl_ytd(db: Session) -> dict:
    """Year-to-date income, expenses and net, plus the cumulative net
    by month within the year (current month is month-to-date)."""
    today = date.today()
    totals = _pl_for(db, date(today.year, 1, 1), today)

    months = []
    running = 0.0
    for m in range(1, today.month + 1):
        start, end = _month_bounds(today.year, m)
        if end > today:
            end = today
        net = _pl_for(db, start, end)["net"]
        running += net
        months.append(
            {"month": start.strftime("%b"), "net": net, "cumulative": running}
        )
    return {
        "year": today.year,
        "income": totals["income"],
        "expenses": totals["expenses"],
        "net": totals["net"],
        "months": months,
    }


def _totals_by_type_to(db: Session, date_end: date) -> dict:
    """Cumulative debit/credit totals per account type, from inception
    through date_end. One grouped query, at most six rows."""
    rows = (
        db.query(
            Account.account_type,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .filter(Transaction.date <= date_end)
        .group_by(Account.account_type)
        .all()
    )
    return {atype: (Decimal(str(dr)), Decimal(str(cr))) for atype, dr, cr in rows}


def balance_sheet_trend(db: Session) -> dict:
    """Assets, liabilities and equity at each of the last 12 month-ends.

    Mirrors /api/reports/balance-sheet semantics: balance-sheet accounts
    carry their natural-balance cumulative total, and current net income
    (income − cogs − expenses, which this app never closes into equity)
    folds into equity so the series actually balances.
    """
    today = date.today()
    ends = []
    year, month = today.year, today.month
    for _ in range(12):
        # the current month stops at today: the card says month-to-date, and a
        # post-dated entry later this month is not a balance anyone holds yet
        ends.append(min(_month_bounds(year, month)[1], today))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    ends.reverse()  # oldest → newest; the last is the current month-to-date

    months = []
    for e in ends:
        by_type = _totals_by_type_to(db, e)

        def _net(acct_type: AccountType, debit_normal: bool) -> Decimal:
            dr, cr = by_type.get(acct_type, (Decimal(0), Decimal(0)))
            return (dr - cr) if debit_normal else (cr - dr)

        assets = _net(AccountType.ASSET, True)
        liabilities = _net(AccountType.LIABILITY, False)
        equity_base = _net(AccountType.EQUITY, False)
        net_income = (
            _net(AccountType.INCOME, False)
            - _net(AccountType.COGS, True)
            - _net(AccountType.EXPENSE, True)
        )
        months.append(
            {
                "month": e.strftime("%b"),
                "year": e.year,
                "as_of": e.isoformat(),
                "assets": float(assets),
                "liabilities": float(liabilities),
                "equity": float(equity_base + net_income),
            }
        )
    return {"months": months, "as_of": today.isoformat()}


def cash_position(db: Session) -> dict:
    """Cash on hand (active bank accounts) and a simple 30-day forecast:
    cash + receivables due within 30 days − payables due within 30 days.
    A forecast, not a promise — it assumes customers pay on the due date."""
    today = date.today()
    horizon = today + timedelta(days=30)
    cash = sum(r["balance"] for r in _bank_ledger_rows(db) if r["kind"] == "bank")
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
    from app.services import ocr_service

    try:
        entries = ocr_service.list_intake()
    except Exception:
        entries = []
    return {
        "count": len(entries),
        "items": [
            {
                "intake_id": e["intake_id"],
                "filename": e["original_filename"],
                "created_at": e["created_at"],
                "expires_in_hours": round(
                    ocr_service.INTAKE_TTL_HOURS - e["age_hours"], 1
                ),
            }
            for e in entries[:8]
        ],
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
    "pnl_ytd": (
        "P&L: Year to Date",
        "half",
        "Income, expenses and net for the year so far, with cumulative net by month",
        pnl_ytd,
    ),
    "balance_sheet_trend": (
        "Balance Sheet Trend",
        "full",
        "Assets, liabilities and equity at each month end, last 12 months",
        balance_sheet_trend,
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


# What a nonprofit sees first: pledges and donors, no purchase orders,
# the grant (job) budget card on by default.
NONPROFIT_DEFAULT_LAYOUT = [
    "receivables",
    "overdue_invoices",
    "active_customers",
    "bank_balances",
    "monthly_revenue",
    "recent_payments",
    "pnl_month",
    "cash_position",
    "job_budget_vs_actual",
]


def default_layout(t=None) -> list[str]:
    if t is not None and t.is_nonprofit:
        return list(NONPROFIT_DEFAULT_LAYOUT)
    return list(DEFAULT_LAYOUT)


def catalog(t=None) -> list[dict]:
    """The card catalog in the company's words (t = Terms; None = business)."""
    if t is None:
        from app.services.terminology import Terms

        t = Terms()
    return [
        {"id": wid, "title": t(title), "size": size, "description": t.text(desc)}
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
            from app.services.safe_errors import safe_message

            out[wid] = {"error": safe_message(exc, f"dashboard widget {wid}")}
    return out
