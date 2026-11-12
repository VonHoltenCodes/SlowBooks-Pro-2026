# ============================================================================
# Slowbooks Pro 2026 — Analytics Engine
# Built 2026-04-14; integrated 2026-04-15.
#
# Net-new for 2026 — QuickBooks 2003's "Company Snapshot" was a handful of
# canned reports stitched together; this is real-time SQL aggregates.
#
# Design notes:
#   * Revenue by customer, aging, DSO and the cash forecast are document-
#     driven (invoices and bills: they need a customer, a due date, a
#     balance). Expenses by category and the revenue trend read the ledger
#     (TransactionLine, v2.22.3, #281): a company that books through a bank
#     feed and journal entries rather than Bills had an empty doughnut and an
#     empty AI brief. The ledger is accrual, as the P&L is: a bill counts
#     when it is dated, paid or not, beside expenses, register entries, card
#     charges, payroll and journal entries; a voided posting and its reversal
#     net to zero.
#   * Uses the ORM enum members (InvoiceStatus.PAID, BillStatus.UNPAID, ...)
#     to stay consistent with the rest of the codebase and to avoid the
#     "unknown enum value" trap.
#   * Bill uses `.total` and `.balance_due` — there is no `.amount` column
#     on the Bill model. (Yes, that was a bug in the first cut. Fixed.)
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.bills import Bill, BillStatus
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.transactions import Transaction, TransactionLine


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


# How the Profit & Loss names an account on screen: "6100 - Auto & Truck
# Expense" (the page, the PDF, the CSV, the AI brief and the AI tool all use
# it, so a category reads the same everywhere — 2.22.3 gate, NEW-74: it was
# the number alone). Lives in account_label.py since #567; imported here so
# `from app.services.analytics import account_label` still works.
from app.services.account_label import account_label  # noqa: E402


class AnalyticsEngine:
    """Real-time business intelligence over invoices / bills / accounts."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Revenue
    # ------------------------------------------------------------------

    def revenue_by_customer(self, start_date: date = None, end_date: date = None):
        """Paid revenue per customer, for the given date window: invoices and
        sales receipts dated in it and paid in full, **before sales tax** and
        in home currency — what the ledger booked to income for each of them
        (the tax went to a liability; 2.22.3 gate, NEW-79: this summed
        `Invoice.total`, so a $1,000 invoice at 10% tax read $1,100 beside a
        ledger revenue of $1,000).

        Defaults to month-to-date.
        """
        from app.services.contact_balances import home_amount

        if start_date is None:
            start_date = _month_start(date.today())
        if end_date is None:
            end_date = date.today()

        rows = (
            self.db.query(Customer.name, Invoice.subtotal, Invoice.exchange_rate)
            .join(Invoice, Invoice.customer_id == Customer.id)
            .filter(Invoice.date >= start_date, Invoice.date <= end_date)
            .filter(Invoice.status == InvoiceStatus.PAID)
            .all()
        )
        out: dict = {}
        for name, subtotal, rate in rows:
            out[name] = out.get(name, Decimal(0)) + home_amount(subtotal, rate)
        return {name: float(amount) for name, amount in out.items()}

    def revenue_trend(self, months: int = 12):
        """Monthly income for the last N months (oldest → newest), from the
        ledger: what was credited to income accounts, less what was debited
        back, by the posting's date (v2.22.3, #281 — it was paid invoices by
        invoice date, so a company that books income through deposits or
        journal entries had a flat line).

        One grouped query over the window, aggregated by calendar month in
        Python. Result is dense — months with no income still appear with
        0.0.

        The WIP schedule's as-of entry, its next-day reversal and the voids
        of both are left out, as on the dashboard's Monthly Revenue card
        (#805): the trend is what was billed; the P&L carries the WIP
        adjustments.
        """
        from app.services.jobs_service import not_wip_entry

        today = date.today()
        end_cursor = _next_month_start(today)
        start_cursor = _month_start(today)
        for _ in range(months - 1):
            start_cursor = _prev_month_start(start_cursor)

        rows = (
            self.db.query(
                Transaction.date,
                func.coalesce(
                    func.sum(TransactionLine.credit - TransactionLine.debit), 0
                ),
            )
            .join(TransactionLine, TransactionLine.transaction_id == Transaction.id)
            .join(Account, TransactionLine.account_id == Account.id)
            .filter(Account.account_type == AccountType.INCOME)
            .filter(Transaction.date >= start_cursor, Transaction.date < end_cursor)
            .filter(not_wip_entry())
            .group_by(Transaction.date)
            .all()
        )

        result = {}
        cursor = start_cursor
        for _ in range(months):
            result[cursor.strftime("%Y-%m")] = 0.0
            cursor = _next_month_start(cursor)

        for when, total in rows:
            key = when.strftime("%Y-%m")
            if key in result:
                result[key] += float(total or 0)

        return {k: round(v, 2) for k, v in result.items()}

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------

    def expenses_by_category(self, start_date: date = None, end_date: date = None):
        """Expenses grouped by account number, from the ledger: everything
        posted to an expense or cost-of-goods account in the period — bills
        when dated (paid or not), expenses, register entries, card charges,
        payroll and journal entries — as the Profit & Loss counts them
        (v2.22.3, #281: it was paid bills only, so a company that books
        through a bank feed and journal entries saw nothing here, and the AI
        brief saw nothing either). A voided posting and its reversal net to
        zero and drop out.

        Defaults to month-to-date. Keyed by the account's label, "6100 -
        Auto & Truck Expense", as the Profit & Loss names it.
        """
        if start_date is None:
            start_date = _month_start(date.today())
        if end_date is None:
            end_date = date.today()
        from app.services.settings_service import use_account_numbers

        numbered = use_account_numbers(self.db)
        return {
            account_label(account_number, name, numbered): amount
            for account_number, name, amount in self.expenses_by_account(
                start_date, end_date
            )
        }

    def expenses_by_account(self, start_date: date = None, end_date: date = None):
        """The ledger's expense postings summed per account: `(number, name,
        amount)` rows for every expense or cost-of-goods account with a
        non-zero net in the window (either bound may be None: no bound).
        Shared by the Analytics page and the AI tool, so the two agree."""
        q = (
            self.db.query(
                Account.account_number,
                Account.name,
                func.coalesce(
                    func.sum(TransactionLine.debit - TransactionLine.credit), 0
                ).label("amount"),
            )
            .join(TransactionLine, TransactionLine.account_id == Account.id)
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .filter(Account.account_type.in_([AccountType.EXPENSE, AccountType.COGS]))
        )
        if start_date is not None:
            q = q.filter(Transaction.date >= start_date)
        if end_date is not None:
            q = q.filter(Transaction.date <= end_date)
        rows = q.group_by(Account.id, Account.account_number, Account.name).all()
        out = []
        for number, name, amount in rows:
            amount = round(float(amount or 0), 2)
            if amount != 0:
                out.append((number, name, amount))
        return out

    def income_total(self, start_date: date = None, end_date: date = None):
        """The ledger's income for the window: what was credited to income
        accounts less what was debited back (accrual — the Profit & Loss's
        total income for the same dates). Either bound may be None."""
        q = (
            self.db.query(
                func.coalesce(
                    func.sum(TransactionLine.credit - TransactionLine.debit), 0
                )
            )
            .join(Transaction, TransactionLine.transaction_id == Transaction.id)
            .join(Account, TransactionLine.account_id == Account.id)
            .filter(Account.account_type == AccountType.INCOME)
        )
        if start_date is not None:
            q = q.filter(Transaction.date >= start_date)
        if end_date is not None:
            q = q.filter(Transaction.date <= end_date)
        return round(float(q.scalar() or 0), 2)

    def totals(self, start_date: date = None, end_date: date = None):
        """Revenue, expenses, net income and margin on one basis — the
        ledger's, as the Profit & Loss counts them — for the KPI cards, the
        PDF and the AI brief (2.22.3 gate, NEW-73 / W-2: the margin divided
        paid-invoice revenue into ledger expenses, so a company with
        journal-entry income and no invoices read "Margin 0.0%" and a loss in
        a profitable month). `margin_pct` is None when there is no revenue
        to divide by. Defaults to month-to-date."""
        if start_date is None:
            start_date = _month_start(date.today())
        if end_date is None:
            end_date = date.today()
        revenue = self.income_total(start_date, end_date)
        expenses = round(
            sum(
                amount
                for _, _, amount in self.expenses_by_account(start_date, end_date)
            ),
            2,
        )
        net_income = round(revenue - expenses, 2)
        margin_pct = round(net_income / revenue * 100, 1) if revenue > 0 else None
        return {
            "basis": "ledger",
            "revenue": revenue,
            "expenses": expenses,
            "net_income": net_income,
            "margin_pct": margin_pct,
        }

    # ------------------------------------------------------------------
    # Aging
    # ------------------------------------------------------------------

    def ar_aging(self):
        """A/R aging by customer — buckets keyed current / 30 / 60 / 90 —
        read from the A/R Aging report itself, so the chart and the report
        agree: balances in home currency (a EUR 850 invoice booked at 1.10
        counts $935, not 850), and the credits a customer holds (unapplied
        payments, credit memos) netted into Current, so the buckets add up
        to what account 1100 carries. This chart summed raw invoice
        balances instead (found integrating the 2.17.3 exploratory fixes).
        A customer whose credits exceed what is current shows a negative
        Current, as the report's own Current column does.

        Bucketed by days past DUE, as the report is."""
        from app.routes.reports.receivables import ar_aging_report

        report = ar_aging_report(self.db, date.today())
        aging = {"current": {}, "30": {}, "60": {}, "90": {}}
        for row in report["items"]:
            name = row["customer_name"] or "Unknown"
            for bucket, column in (
                ("current", "current"),
                ("30", "over_30"),
                ("60", "over_60"),
                ("90", "over_90"),
            ):
                if row[column]:
                    aging[bucket][name] = aging[bucket].get(name, 0.0) + row[column]
        return aging

    def ap_aging(self):
        """A/P aging by vendor — buckets keyed current / 30 / 60 / 90 — read
        from the A/P Aging report, so the chart and the report agree: home
        currency, credits netted into Current, bucketed by days past due
        (this chart summed raw bill balances by bill date; found
        integrating the 2.17.3 exploratory fixes)."""
        from app.routes.reports.payables_tax import ap_aging_report

        report = ap_aging_report(self.db, date.today())
        aging = {"current": {}, "30": {}, "60": {}, "90": {}}
        for row in report["items"]:
            name = row["vendor_name"] or "Unknown"
            for bucket, column in (
                ("current", "current"),
                ("30", "over_30"),
                ("60", "over_60"),
                ("90", "over_90"),
            ):
                if row[column]:
                    aging[bucket][name] = aging[bucket].get(name, 0.0) + row[column]
        return aging

    # ------------------------------------------------------------------
    # Cash metrics
    # ------------------------------------------------------------------

    def dso(self):
        """Days Sales Outstanding = (open A/R / last-30d paid revenue) * 30.

        The denominator is the invoices and sales receipts DATED in the last
        30 days that are paid in full (a payment received today on an older
        invoice is not in it — the words on the card and in the brief say
        so; 2.22.3 gate, NEW-82). None when that is nothing: there is no
        collection period to measure, and 0.0 read as instant collection
        beside $125k of open invoices (NEW-80 / W-4). The page, the PDF and
        the brief show it as absent.
        """
        thirty_days_ago = date.today() - _days(30)

        # What customers owe, as account 1100 carries it: home currency and
        # net of the credits they hold (the A/R Aging report's total).
        from app.routes.reports.receivables import ar_aging_report
        from app.services.contact_balances import home_amount

        ar_balance = Decimal(
            str(ar_aging_report(self.db, date.today())["totals"]["total"])
        )
        recent_revenue = sum(
            (
                home_amount(total, rate)
                for total, rate in self.db.query(Invoice.total, Invoice.exchange_rate)
                .filter(Invoice.date >= thirty_days_ago)
                .filter(Invoice.status == InvoiceStatus.PAID)
            ),
            Decimal(0),
        )

        if recent_revenue <= 0:
            return None
        return float((ar_balance / recent_revenue) * 30)

    def cash_forecast(self, days: int = 90):
        """Weekly buckets of expected inflow (A/R due) vs outflow (A/P due).

        Cumulative: every bucket shows the total due on-or-before that date.
        Always includes day 0 and day `days` so a 90-day forecast ends at 90.

        Two queries (open A/R, open A/P), then a single sorted walk that
        accumulates running sums at each weekly cutoff. Was 28 queries.
        """
        today = date.today()

        from app.services.contact_balances import home_amount

        # home currency: a EUR invoice brings in what it is booked at
        ar_rows = (
            self.db.query(Invoice.due_date, Invoice.balance_due, Invoice.exchange_rate)
            .filter(
                Invoice.status.in_(
                    [InvoiceStatus.DRAFT, InvoiceStatus.SENT, InvoiceStatus.PARTIAL]
                )
            )
            .filter(Invoice.due_date.isnot(None))
            .all()
        )
        ap_rows = (
            self.db.query(Bill.due_date, Bill.balance_due, Bill.exchange_rate)
            .filter(Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL]))
            .filter(Bill.due_date.isnot(None))
            .all()
        )

        ar_sorted = sorted((d, float(home_amount(b or 0, r))) for d, b, r in ar_rows)
        ap_sorted = sorted((d, float(home_amount(b or 0, r))) for d, b, r in ap_rows)

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
            forecast.append(
                {
                    "date": cutoff.isoformat(),
                    "collections": ar_sum,
                    "payments": ap_sum,
                    "net": ar_sum - ap_sum,
                }
            )

        return forecast

    # ------------------------------------------------------------------
    # Profitability
    # ------------------------------------------------------------------

    def customer_profit(self):
        """Lifetime paid revenue per customer (first pass at profitability),
        before sales tax and in home currency, as `revenue_by_customer`.

        Real COGS attribution would require per-customer cost tagging, which
        SlowBooks doesn't model yet — so for now this is revenue-only.
        """
        from app.services.contact_balances import home_amount

        rows = (
            self.db.query(Customer.name, Invoice.subtotal, Invoice.exchange_rate)
            .outerjoin(
                Invoice,
                (Invoice.customer_id == Customer.id)
                & (Invoice.status == InvoiceStatus.PAID),
            )
            .all()
        )
        out: dict = {}
        for name, subtotal, rate in rows:
            out[name] = out.get(name, Decimal(0)) + (
                home_amount(subtotal, rate) if subtotal is not None else Decimal(0)
            )
        return {name: {"revenue": float(amount)} for name, amount in out.items()}

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def get_dashboard(self, start_date: date = None, end_date: date = None):
        """All metrics in one shot — what the frontend hits on page load.

        `start_date` / `end_date` apply to the windowed metrics
        (`totals`, `revenue_by_customer`, `expenses_by_category`). The
        others have their own time semantics: `revenue_trend` is always the
        last 12 months, aging is as-of-today, `cash_forecast` is the next 90
        days.
        """
        return {
            "totals": self.totals(start_date, end_date),
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
    """Tiny helper — wraps timedelta(days=n) for brevity."""
    return timedelta(days=n)
