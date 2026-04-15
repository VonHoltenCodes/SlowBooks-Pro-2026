# ============================================================================
# Slowbooks Pro 2026 — Analytics API
# Built 2026-04-14; integrated 2026-04-15.
#
# Five read-only endpoints powered by AnalyticsEngine. Everything is a GET
# so dashboards and wget-loving accountants alike can curl it.
# ============================================================================

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analytics import AnalyticsEngine

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    """Complete analytics snapshot — the page-load payload."""
    return AnalyticsEngine(db).get_dashboard()


@router.get("/revenue")
def get_revenue(db: Session = Depends(get_db)):
    """Revenue by customer + 12-month trend."""
    engine = AnalyticsEngine(db)
    return {
        "by_customer": engine.revenue_by_customer(),
        "trend": engine.revenue_trend(),
    }


@router.get("/expenses")
def get_expenses(db: Session = Depends(get_db)):
    """Expense breakdown by account number."""
    return AnalyticsEngine(db).expenses_by_category()


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
