"""First month of the fiscal year (#444).

One setting, ``fiscal_year_start_month`` ("1"-"12"), moves every report's
default period, the dashboard's profit card and nonprofit FY-to-date off the
calendar year. A January start (the default) is the calendar year and changes
nothing. Payroll forms (W-2, 941) stay on the calendar year by law and never
read this.

The month-only functions are pure so JS can mirror them
(ReportsPage._fiscalYearStart); the ``db`` functions read the setting once.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting_raw


def start_month(db: Session) -> int:
    """First month of the fiscal year, 1-12 (1 = calendar year, the default)."""
    raw = get_setting_raw(db, "fiscal_year_start_month")
    try:
        month = int(raw)
    except (TypeError, ValueError):
        return 1
    return month if 1 <= month <= 12 else 1


def _fy_start(month: int, on: date) -> date:
    """Most recent first-of-start-month on or before ``on`` (pure)."""
    year = on.year if on.month >= month else on.year - 1
    return date(year, month, 1)


def _fy_bounds(month: int, on: date) -> tuple[date, date]:
    """Start and last day of the fiscal year containing ``on`` (pure)."""
    start = _fy_start(month, on)
    nxt = date(start.year + 1, start.month, 1)
    return start, nxt - timedelta(days=1)


def fiscal_year_start(db: Session, on: date) -> date:
    return _fy_start(start_month(db), on)


def fiscal_year_bounds(db: Session, on: date) -> tuple[date, date]:
    return _fy_bounds(start_month(db), on)


def fiscal_year_to_date(db: Session, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return fiscal_year_start(db, today), today


def fiscal_year_label(start: date, end: date) -> str:
    """ "2026" for a calendar year, "FY2027" when it spans two (labelled by the
    year it ends in, as accountants name a fiscal year)."""
    return str(end.year) if start.year == end.year else f"FY{end.year}"
