# ============================================================================
# Slowbooks Pro 2026 — Analytics API
# Built 2026-04-14; integrated 2026-04-15; enhanced 2026-04-15 (Phase 1).
#
# Read-only endpoints powered by AnalyticsEngine. Every endpoint accepts a
# period window via either:
#   * `?period=month|quarter|year` (MTD / QTD / YTD)
#   * explicit `?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
# Explicit dates override `period`. Defaults to MTD.
#
# Plus /export.csv which dumps the full snapshot as a flat CSV for
# spreadsheet-loving accountants.
# ============================================================================

import csv
import io
from datetime import date
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analytics import AnalyticsEngine

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _resolve_period(
    period: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
) -> Tuple[date, date, str]:
    """Resolve period name or explicit dates to `(start, end, label)`.

    Explicit start/end dates take precedence. If only one is provided the
    other defaults to a sensible bound. If neither is provided the named
    period (month/quarter/year, case-insensitive, mtd/qtd/ytd also OK) is
    resolved. Default is month-to-date.
    """
    today = date.today()

    if start_date or end_date:
        s = start_date or date(today.year, 1, 1)
        e = end_date or today
        return s, e, "custom"

    p = (period or "month").strip().lower()
    if p in ("month", "mtd"):
        return today.replace(day=1), today, "month"
    if p in ("quarter", "qtd"):
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_start_month, day=1), today, "quarter"
    if p in ("year", "ytd"):
        return today.replace(month=1, day=1), today, "year"
    # Unrecognised: fall back to MTD, report the label we actually used.
    return today.replace(day=1), today, "month"


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def get_dashboard(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Complete analytics snapshot — the page-load payload.

    The `period` window applies to revenue_by_customer and
    expenses_by_category. All other metrics are time-windowed by their own
    semantics (trend = last 12 months, aging = open balances as of today,
    cash_forecast = next 90 days).
    """
    s, e, label = _resolve_period(period, start_date, end_date)
    payload = AnalyticsEngine(db).get_dashboard(start_date=s, end_date=e)
    payload["period"] = {
        "name": label,
        "start": s.isoformat(),
        "end": e.isoformat(),
    }
    return payload


@router.get("/revenue")
def get_revenue(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Revenue by customer (windowed) + 12-month trend."""
    s, e, label = _resolve_period(period, start_date, end_date)
    engine = AnalyticsEngine(db)
    return {
        "period": {"name": label, "start": s.isoformat(), "end": e.isoformat()},
        "by_customer": engine.revenue_by_customer(s, e),
        "trend": engine.revenue_trend(),
    }


@router.get("/expenses")
def get_expenses(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Expense breakdown by account number (windowed)."""
    s, e, label = _resolve_period(period, start_date, end_date)
    return {
        "period": {"name": label, "start": s.isoformat(), "end": e.isoformat()},
        "by_category": AnalyticsEngine(db).expenses_by_category(s, e),
    }


@router.get("/cash-flow")
def get_cash_flow(
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Cash forecast + DSO + A/R and A/P aging."""
    engine = AnalyticsEngine(db)
    return {
        "forecast": engine.cash_forecast(days),
        "dso": engine.dso(),
        "ar_aging": engine.ar_aging(),
        "ap_aging": engine.ap_aging(),
    }


@router.get("/profitability")
def get_profitability(db: Session = Depends(get_db)):
    """Customer profitability (lifetime paid revenue for now)."""
    return AnalyticsEngine(db).customer_profit()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


@router.get("/export.csv")
def export_csv(
    period: Optional[str] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Dump the full analytics snapshot as a flat CSV.

    One row per (section, key, subkey, value) tuple. Loads into Excel,
    Google Sheets, or any BI tool without ceremony.
    """
    s, e, label = _resolve_period(period, start_date, end_date)
    engine = AnalyticsEngine(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "subkey", "value"])

    writer.writerow(["period", "name", "", label])
    writer.writerow(["period", "start", "", s.isoformat()])
    writer.writerow(["period", "end", "", e.isoformat()])

    for customer, revenue in engine.revenue_by_customer(s, e).items():
        writer.writerow(["revenue_by_customer", customer, "", f"{revenue:.2f}"])

    for month, total in engine.revenue_trend().items():
        writer.writerow(["revenue_trend", month, "", f"{total:.2f}"])

    for category, amount in engine.expenses_by_category(s, e).items():
        writer.writerow(["expenses_by_category", category, "", f"{amount:.2f}"])

    for bucket, by_customer in engine.ar_aging().items():
        for customer, amount in by_customer.items():
            writer.writerow(["ar_aging", bucket, customer, f"{amount:.2f}"])

    for bucket, by_vendor in engine.ap_aging().items():
        for vendor, amount in by_vendor.items():
            writer.writerow(["ap_aging", bucket, vendor, f"{amount:.2f}"])

    writer.writerow(["dso", "days", "", f"{engine.dso():.2f}"])

    for entry in engine.cash_forecast():
        writer.writerow(["cash_forecast", entry["date"], "collections",
                         f"{entry['collections']:.2f}"])
        writer.writerow(["cash_forecast", entry["date"], "payments",
                         f"{entry['payments']:.2f}"])
        writer.writerow(["cash_forecast", entry["date"], "net",
                         f"{entry['net']:.2f}"])

    for customer, info in engine.customer_profit().items():
        writer.writerow(["customer_profit", customer, "",
                         f"{info['revenue']:.2f}"])

    filename = f"slowbooks-analytics-{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
