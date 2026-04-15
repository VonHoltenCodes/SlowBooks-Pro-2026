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

        Walks months by decrementing month/year so two iterations can never
        collapse into the same bucket (the naive `-30*i` approach would).
        """
        result = {}
        cursor = _month_start(date.today())
        for _ in range(months):
            next_start = _next_month_start(cursor)
            total = (
                self.db.query(func.coalesce(func.sum(Invoice.total), 0))
                .filter(Invoice.date >= cursor, Invoice.date < next_start)
                .filter(Invoice.status == InvoiceStatus.PAID)
                .scalar()
            ) or Decimal(0)
            result[cursor.strftime("%Y-%m")] = float(total)
            cursor = _prev_month_start(cursor)
        return dict(sorted(result.items()))

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
        don't double-count partial payments.
        """
        today = date.today()
        invoices = (
            self.db.query(Invoice)
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

        for inv in invoices:
            days = (today - inv.date).days
            customer = inv.customer.name if inv.customer else "Unknown"
            balance = float(inv.balance_due or 0)

            if days <= 30:
                bucket = "current"
            elif days <= 60:
                bucket = "30"
            elif days <= 90:
                bucket = "60"
            else:
                bucket = "90"

            aging[bucket][customer] += balance

        return {k: dict(v) for k, v in aging.items()}

    def ap_aging(self):
        """A/P aging by vendor — buckets keyed current / 30 / 60 / 90."""
        today = date.today()
        bills = (
            self.db.query(Bill)
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

        for bill in bills:
            days = (today - bill.date).days
            vendor = bill.vendor.name if bill.vendor else "Unknown"
            balance = float(bill.balance_due or 0)

            if days <= 30:
                bucket = "current"
            elif days <= 60:
                bucket = "30"
            elif days <= 90:
                bucket = "60"
            else:
                bucket = "90"

            aging[bucket][vendor] += balance

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
        """
        today = date.today()
        forecast = []

        offsets = list(range(0, days, 7))
        if not offsets or offsets[-1] != days:
            offsets.append(days)

        for offset in offsets:
            cutoff = today + _days(offset)

            collections = (
                self.db.query(func.coalesce(func.sum(Invoice.balance_due), 0))
                .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]))
                .filter(Invoice.due_date.isnot(None))
                .filter(Invoice.due_date <= cutoff)
                .scalar()
            ) or Decimal(0)

            payments = (
                self.db.query(func.coalesce(func.sum(Bill.balance_due), 0))
                .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
                .filter(Bill.due_date.isnot(None))
                .filter(Bill.due_date <= cutoff)
                .scalar()
            ) or Decimal(0)

            forecast.append({
                "date": cutoff.isoformat(),
                "collections": float(collections),
                "payments": float(payments),
                "net": float(collections - payments),
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

    def get_dashboard(self):
        """All metrics in one shot — what the frontend hits on page load."""
        return {
            "revenue_by_customer": self.revenue_by_customer(),
            "revenue_trend": self.revenue_trend(),
            "expenses_by_category": self.expenses_by_category(),
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
