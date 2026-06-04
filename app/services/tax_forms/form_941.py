# ============================================================================
# Form 941 — Employer's Quarterly Federal Tax Return
# ----------------------------------------------------------------------------
# Aggregates PROCESSED pay stubs whose PayRun.pay_date falls inside a calendar
# quarter into the wage/withholding totals reported on IRS Form 941.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import joinedload

from app.models.payroll import PayRun, PayStub, PayRunStatus
from app.services.pdf_service import _jinja_env, _safe_url_fetcher
from weasyprint import HTML

CENT = Decimal("0.01")

# 941 combines the employee + employer FICA share into a single line and
# expresses it as a rate applied to wages: 12.4% Social Security, 2.9% Medicare.
SS_COMBINED_RATE = Decimal("0.124")
MEDICARE_COMBINED_RATE = Decimal("0.029")


def _q(value) -> Decimal:
    """Coerce to Decimal and quantize to cents."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value or 0))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    """Return (first_day, last_day) for a calendar quarter."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter!r}")
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 4:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def _quarter_stubs(db, year: int, quarter: int) -> list[PayStub]:
    """All PROCESSED pay stubs with a pay_date inside the given quarter."""
    start, end = _quarter_bounds(year, quarter)
    return (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(joinedload(PayStub.pay_run), joinedload(PayStub.employee))
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= start)
        .filter(PayRun.pay_date <= end)
        .all()
    )


def compute_941(db, year: int, quarter: int) -> dict:
    """Aggregate quarterly Form 941 totals.

    Returns wage, withholding and tax-liability totals for the quarter along
    with the count of distinct employees paid.
    """
    stubs = _quarter_stubs(db, year, quarter)

    employee_ids: set[int] = set()
    total_wages = Decimal("0")
    federal_withheld = Decimal("0")
    ss_employee = Decimal("0")
    ss_employer = Decimal("0")
    medicare_employee = Decimal("0")
    medicare_employer = Decimal("0")

    for s in stubs:
        if s.employee_id is not None:
            employee_ids.add(s.employee_id)
        total_wages += Decimal(str(s.gross_pay or 0))
        federal_withheld += Decimal(str(s.federal_tax or 0))
        ss_employee += Decimal(str(s.ss_tax or 0))
        ss_employer += Decimal(str(s.employer_ss_tax or 0))
        medicare_employee += Decimal(str(s.medicare_tax or 0))
        medicare_employer += Decimal(str(s.employer_medicare_tax or 0))

    ss_tax = ss_employee + ss_employer
    medicare_tax = medicare_employee + medicare_employer
    # Lines 2 + 3 + 5e net to the total tax after adjustments. With no
    # fractions-of-cents / sick-pay adjustments this is the quarter liability.
    total_tax_liability = federal_withheld + ss_tax + medicare_tax

    return {
        "year": year,
        "quarter": quarter,
        "num_employees": len(employee_ids),
        "num_stubs": len(stubs),
        # Line 2 — wages, tips and other compensation
        "total_wages": _q(total_wages),
        # Line 3 — federal income tax withheld
        "federal_income_tax_withheld": _q(federal_withheld),
        # Line 5a — taxable Social Security wages and combined tax
        "social_security_wages": _q(total_wages),
        "social_security_tax": _q(ss_tax),
        "social_security_tax_employee": _q(ss_employee),
        "social_security_tax_employer": _q(ss_employer),
        # Line 5c — taxable Medicare wages and combined tax
        "medicare_wages": _q(total_wages),
        "medicare_tax": _q(medicare_tax),
        "medicare_tax_employee": _q(medicare_employee),
        "medicare_tax_employer": _q(medicare_employer),
        # Line 5e — total Social Security + Medicare tax
        "total_fica_tax": _q(ss_tax + medicare_tax),
        # Line 12 — total taxes after adjustments
        "total_tax_liability": _q(total_tax_liability),
    }


def generate_941_pdf(
    db, year: int, quarter: int, company: dict, audit: dict | None = None
) -> bytes:
    """Render Form 941 to a PDF for the given quarter."""
    data = compute_941(db, year, quarter)
    template = _jinja_env.get_template("form_941.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return HTML(string=html_str, url_fetcher=_safe_url_fetcher).write_pdf()
