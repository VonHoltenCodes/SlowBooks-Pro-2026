# ============================================================================
# Slowbooks Pro 2026 — Analytics Engine
# Built 2026-04-14; integrated 2026-04-15.
#
# Not "decompiled" from QB2003 — this is net-new. The original QuickBooks 2003
# shipped a "Company Snapshot" (CCompanySnapshot @ 0x00305800) that was really
# just 4 Crystal Reports stitched together. This is the modern replacement:
# real-time SQL aggregates instead of cached Btrieve rollups.
#
# Design notes:
#   * Invoice/Bill-driven (not journal-driven). Faster, simpler, matches how
#     the dashboard route already works. For GAAP-grade numbers, the P&L
#     report in reports.py still reads TransactionLine.
#   * Uses the ORM enum members (InvoiceStatus.PAID, BillStatus.UNPAID, ...)
#     to stay consistent with the rest of the codebase and to avoid the
#     "unknown enum value" trap.
#   * Bill uses `.total` and `.balance_due` — there is no `.amount` column
#     on the Bill model. (Yes, that was a bug in the first cut. Fixed.)
# ============================================================================

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.models.bills import Bill, BillLine, BillStatus
from app.models.contacts import Customer, Vendor
from app.models.invoices import Invoice, InvoiceStatus


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month_start(d: date) -> date:
    """First day of the month after `d`."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _prev_month_start(d: date) -> date:
    """First day of the month before `d`."""
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


class AnalyticsEngine:
    """Real-time business intelligence over invoices / bills / accounts."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Revenue
    # ------------------------------------------------------------------

    def revenue_by_customer(self, start_date: date = None, end_date: date = None):
        """Paid revenue per customer, for the given date window.

        Defaults to month-to-date.
        """
        if start_date is None:
            start_date = _month_start(date.today())
        if end_date is None:
            end_date = date.today()

        rows = (
            self.db.query(
                Customer.name,
                func.coalesce(func.sum(Invoice.total), 0).label("revenue"),
            )
            .join(Invoice, Invoice.customer_id == Customer.id)
            .filter(Invoice.date >= start_date, Invoice.date <= end_date)
            .filter(Invoice.status == InvoiceStatus.PAID)
            .group_by(Customer.id, Customer.name)
            .all()
        )
        return {name: float(revenue or 0) for name, revenue in rows}

    def revenue_trend(self, months: int = 12):
        """Monthly paid-revenue for the last N months (oldest → newest).

        Single query: fetches `(date, total)` pairs for paid invoices in the
        window and aggregates by calendar month in Python. One query instead
        of N (the old per-month loop was a classic N+1). Result is dense —
        months with no revenue still appear with 0.0.
        """
        today = date.today()
        end_cursor = _next_month_start(today)
        start_cursor = _month_start(today)
        for _ in range(months - 1):
            start_cursor = _prev_month_start(start_cursor)

        rows = (
            self.db.query(Invoice.date, Invoice.total)
            .filter(Invoice.date >= start_cursor, Invoice.date < end_cursor)
            .filter(Invoice.status == InvoiceStatus.PAID)
            .all()
        )

        result = {}
        cursor = start_cursor
        for _ in range(months):
            result[cursor.strftime("%Y-%m")] = 0.0
            cursor = _next_month_start(cursor)

        for inv_date, total in rows:
            key = inv_date.strftime("%Y-%m")
            if key in result:
                result[key] += float(total or 0)

        return result

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------

    def expenses_by_category(self, start_date: date = None, end_date: date = None):
        """Paid-bill expenses grouped by expense account number.

        Defaults to month-to-date. Includes a human-readable label per row.
        """
        if start_date is None:
            start_date = _month_start(date.today())
        if end_date is None:
            end_date = date.today()

        rows = (
            self.db.query(
                Account.account_number,
                Account.name,
                func.coalesce(func.sum(BillLine.amount), 0).label("amount"),
            )
            .join(BillLine, BillLine.account_id == Account.id)
            .join(Bill, BillLine.bill_id == Bill.id)
            .filter(Bill.date >= start_date, Bill.date <= end_date)
            .filter(Bill.status == BillStatus.PAID)
            .group_by(Account.id, Account.account_number, Account.name)
            .all()
        )
        return {
            (account_number or name or "Uncategorized"): float(amount or 0)
            for account_number, name, amount in rows
        }

    # ------------------------------------------------------------------
    # Aging
    # ------------------------------------------------------------------

    def ar_aging(self):
        """A/R aging by customer — buckets keyed current / 30 / 60 / 90.

        Uses Invoice.balance_due (the canonical open-balance column) so we
        don't double-count partial payments. Single joined query returning
        only `(customer_name, date, balance_due)` tuples — avoids the N+1
        relationship load that `inv.customer.name` would have triggered.
        """
        today = date.today()
        rows = (
            self.db.query(Customer.name, Invoice.date, Invoice.balance_due)
            .join(Customer, Invoice.customer_id == Customer.id)
            .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
            .filter(Invoice.balance_due > 0)
            .all()
        )

        aging = {
            "current": defaultdict(float),
            "30": defaultdict(float),
            "60": defaultdict(float),
            "90": defaultdict(float),
        }

        for customer_name, inv_date, balance in rows:
            days = (today - inv_date).days
            if days <= 30:
                bucket = "current"
            elif days <= 60:
                bucket = "30"
            elif days <= 90:
                bucket = "60"
            else:
                bucket = "90"
            aging[bucket][customer_name or "Unknown"] += float(balance or 0)

        return {k: dict(v) for k, v in aging.items()}

    def ap_aging(self):
        """A/P aging by vendor — buckets keyed current / 30 / 60 / 90.

        Single joined query; same N+1-avoidance story as `ar_aging`.
        """
        today = date.today()
        rows = (
            self.db.query(Vendor.name, Bill.date, Bill.balance_due)
            .join(Vendor, Bill.vendor_id == Vendor.id)
            .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
            .filter(Bill.balance_due > 0)
            .all()
        )

        aging = {
            "current": defaultdict(float),
            "30": defaultdict(float),
            "60": defaultdict(float),
            "90": defaultdict(float),
        }

        for vendor_name, bill_date, balance in rows:
            days = (today - bill_date).days
            if days <= 30:
                bucket = "current"
            elif days <= 60:
                bucket = "30"
            elif days <= 90:
                bucket = "60"
            else:
                bucket = "90"
            aging[bucket][vendor_name or "Unknown"] += float(balance or 0)

        return {k: dict(v) for k, v in aging.items()}

    # ------------------------------------------------------------------
    # Cash metrics
    # ------------------------------------------------------------------

    def dso(self):
        """Days Sales Outstanding = (open A/R / last-30d paid revenue) * 30.

        Returns 0 when there's no recent revenue (no meaningful DSO).
        """
        thirty_days_ago = date.today() - _days(30)

        ar_balance = (
            self.db.query(func.coalesce(func.sum(Invoice.balance_due), 0))
            .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
            .scalar()
        ) or Decimal(0)

        recent_revenue = (
            self.db.query(func.coalesce(func.sum(Invoice.total), 0))
            .filter(Invoice.date >= thirty_days_ago)
            .filter(Invoice.status == InvoiceStatus.PAID)
            .scalar()
        ) or Decimal(0)

        if recent_revenue == 0:
            return 0.0
        return float((ar_balance / recent_revenue) * 30)

    def cash_forecast(self, days: int = 90):
        """Weekly buckets of expected inflow (A/R due) vs outflow (A/P due).

        Cumulative: every bucket shows the total due on-or-before that date.
        Always includes day 0 and day `days` so a 90-day forecast ends at 90.

        Two queries (open A/R, open A/P), then a single sorted walk that
        accumulates running sums at each weekly cutoff. Was 28 queries.
        """
        today = date.today()

        ar_rows = (
            self.db.query(Invoice.due_date, Invoice.balance_due)
            .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
            .filter(Invoice.due_date.isnot(None))
            .all()
        )
        ap_rows = (
            self.db.query(Bill.due_date, Bill.balance_due)
            .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
            .filter(Bill.due_date.isnot(None))
            .all()
        )

        ar_sorted = sorted((d, float(b or 0)) for d, b in ar_rows)
        ap_sorted = sorted((d, float(b or 0)) for d, b in ap_rows)

        offsets = list(range(0, days, 7))
        if not offsets or offsets[-1] != days:
            offsets.append(days)

        forecast = []
        ar_idx = ap_idx = 0
        ar_sum = ap_sum = 0.0
        for offset in offsets:
            cutoff = today + _days(offset)
            while ar_idx < len(ar_sorted) and ar_sorted[ar_idx][0] <= cutoff:
                ar_sum += ar_sorted[ar_idx][1]
                ar_idx += 1
            while ap_idx < len(ap_sorted) and ap_sorted[ap_idx][0] <= cutoff:
                ap_sum += ap_sorted[ap_idx][1]
                ap_idx += 1
            forecast.append({
                "date": cutoff.isoformat(),
                "collections": ar_sum,
                "payments": ap_sum,
                "net": ar_sum - ap_sum,
            })

        return forecast

    # ------------------------------------------------------------------
    # Profitability
    # ------------------------------------------------------------------

    def customer_profit(self):
        """Lifetime paid revenue per customer (first pass at profitability).

        Real COGS attribution would require per-customer cost tagging, which
        SlowBooks doesn't model yet — so for now this is revenue-only.
        """
        rows = (
            self.db.query(
                Customer.name,
                func.coalesce(func.sum(Invoice.total), 0).label("revenue"),
            )
            .outerjoin(Invoice, (Invoice.customer_id == Customer.id) &
                                (Invoice.status == InvoiceStatus.PAID))
            .group_by(Customer.id, Customer.name)
            .all()
        )
        return {name: {"revenue": float(revenue or 0)} for name, revenue in rows}

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def get_dashboard(self, start_date: date = None, end_date: date = None):
        """All metrics in one shot — what the frontend hits on page load.

        `start_date` / `end_date` apply to the windowed metrics
        (`revenue_by_customer`, `expenses_by_category`). The others have
        their own time semantics: `revenue_trend` is always the last
        12 months, aging is as-of-today, `cash_forecast` is the next 90
        days.
        """
        return {
            "revenue_by_customer": self.revenue_by_customer(start_date, end_date),
            "revenue_trend": self.revenue_trend(),
            "expenses_by_category": self.expenses_by_category(start_date, end_date),
            "ar_aging": self.ar_aging(),
            "ap_aging": self.ap_aging(),
            "dso": self.dso(),
            "cash_forecast": self.cash_forecast(),
            "customer_profit": self.customer_profit(),
        }


def _days(n: int):
    """Tiny helper so we don't have to import timedelta everywhere."""
    from datetime import timedelta
    return timedelta(days=n)
