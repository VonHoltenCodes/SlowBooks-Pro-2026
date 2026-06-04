# ============================================================================
# Quarterly payroll tax liability report — what is owed, and when.
# ----------------------------------------------------------------------------
# Aggregates a quarter's processed pay stubs into a schedule of tax deposits /
# returns with their statutory due dates (Form 941, Form 940 FUTA, state
# unemployment, state income-tax withholding).
# ============================================================================

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.models.payroll import PayRun, PayStub, PayRunStatus

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _quarter_bounds(year: int, quarter: int) -> tuple:
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    start = date(year, start_month, 1)
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def _filing_due_date(year: int, quarter: int) -> date:
    """Quarterly returns are due the last day of the month following quarter end."""
    if quarter == 4:
        due_year, due_month = year + 1, 1
    else:
        due_year, due_month = year, quarter * 3 + 1
    return date(due_year, due_month, calendar.monthrange(due_year, due_month)[1])


def compute_tax_liability(db, year: int, quarter: int) -> dict:
    """Return the payroll tax liability schedule for one calendar quarter."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be 1-4")

    start, end = _quarter_bounds(year, quarter)
    due = _filing_due_date(year, quarter)

    stubs = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .filter(
            PayRun.status == PayRunStatus.PROCESSED,
            PayRun.pay_date >= start,
            PayRun.pay_date <= end,
        )
        .all()
    )

    federal_income = sum((_q(s.federal_tax) for s in stubs), Decimal("0"))
    ss = sum((_q(s.ss_tax) + _q(s.employer_ss_tax) for s in stubs), Decimal("0"))
    medicare = sum(
        (_q(s.medicare_tax) + _q(s.employer_medicare_tax) for s in stubs), Decimal("0")
    )
    futa = sum((_q(s.futa_tax) for s in stubs), Decimal("0"))
    suta = sum((_q(s.suta_tax) for s in stubs), Decimal("0"))
    state_income = sum((_q(s.state_tax) for s in stubs), Decimal("0"))
    state_other = sum(
        (_q(s.state_other_employee) + _q(s.state_other_employer) for s in stubs),
        Decimal("0"),
    )

    federal_941 = _q(federal_income + ss + medicare)

    liabilities = [
        {
            "form": "941",
            "agency": "IRS",
            "description": "Federal employment tax (income tax withheld + Social Security + Medicare)",
            "amount": float(federal_941),
            "due_date": due.isoformat(),
        },
        {
            "form": "940",
            "agency": "IRS",
            "description": "Federal unemployment tax (FUTA)",
            "amount": float(_q(futa)),
            "due_date": due.isoformat(),
        },
        {
            "form": "State SUI",
            "agency": "State",
            "description": "State unemployment insurance",
            "amount": float(_q(suta)),
            "due_date": due.isoformat(),
        },
        {
            "form": "State WH",
            "agency": "State",
            "description": "State income tax withheld",
            "amount": float(_q(state_income)),
            "due_date": due.isoformat(),
        },
        {
            "form": "State Other",
            "agency": "State",
            "description": "State disability / paid-leave / L&I premiums",
            "amount": float(_q(state_other)),
            "due_date": due.isoformat(),
        },
    ]
    total_due = _q(federal_941 + futa + suta + state_income + state_other)

    return {
        "year": year,
        "quarter": quarter,
        "quarter_start": start.isoformat(),
        "quarter_end": end.isoformat(),
        "num_stubs": len(stubs),
        "liabilities": liabilities,
        "total_due": float(total_due),
    }
