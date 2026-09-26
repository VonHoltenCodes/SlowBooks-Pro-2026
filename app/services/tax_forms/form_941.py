# ============================================================================
# Form 941 — Employer's Quarterly Federal Tax Return
# ----------------------------------------------------------------------------
# Aggregates PROCESSED pay stubs whose PayRun.pay_date falls inside a calendar
# quarter into the wage/withholding totals reported on IRS Form 941.
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import joinedload, selectinload

from app.models.payroll import PayRun, PayStub, PayRunStatus
from app.services.accounting import _q
from app.services.payroll_service import (
    MEDICARE_ADDITIONAL_RATE,
    MEDICARE_ADDITIONAL_THRESHOLD,
    SS_WAGE_BASE,
    _capped_wages,
)
from app.services.pdf_service import _jinja_env, render_pdf

# 941 combines the employee + employer FICA share into a single line and
# expresses it as a rate applied to wages: 12.4% Social Security, 2.9% Medicare,
# plus the employee-only 0.9% Additional Medicare Tax on line 5d.
SS_COMBINED_RATE = Decimal("0.124")
MEDICARE_COMBINED_RATE = Decimal("0.029")


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


def _stubs_through_quarter(db, year: int, quarter: int) -> list[PayStub]:
    """PROCESSED stubs from January 1 to the quarter's end, in pay order —
    the earlier quarters are what the Social Security wage base and the
    Additional Medicare threshold are measured against."""
    _, end = _quarter_bounds(year, quarter)
    stubs = (
        db.query(PayStub)
        .join(PayRun, PayStub.pay_run_id == PayRun.id)
        .options(
            joinedload(PayStub.pay_run),
            joinedload(PayStub.employee),
            selectinload(PayStub.benefits),
        )
        .filter(PayRun.status == PayRunStatus.PROCESSED)
        .filter(PayRun.pay_date >= date(year, 1, 1))
        .filter(PayRun.pay_date <= end)
        .all()
    )
    return sorted(stubs, key=lambda s: (s.pay_run.pay_date, s.id))


def _fica_wages(stub: PayStub) -> Decimal:
    """Gross less the pre-tax benefit amounts that reduce FICA wages
    (Section 125 cafeteria plans, HSA): the base the paycheck's Social
    Security and Medicare were figured on."""
    reduced = sum(
        (
            Decimal(str(b.employee_amount or 0))
            for b in stub.benefits
            if b.category == "pretax" and b.reduces_fica
        ),
        Decimal("0"),
    )
    return max(Decimal("0"), Decimal(str(stub.gross_pay or 0)) - reduced)


def _additional_medicare_wages(fica_wages: Decimal, ytd_gross: Decimal) -> Decimal:
    """The part of this paycheck's Medicare wages above the $200,000 an
    employee is paid in the year before Additional Medicare is withheld —
    the same measure the paycheck used (payroll_service.medicare)."""
    annual = ytd_gross + fica_wages
    if annual <= MEDICARE_ADDITIONAL_THRESHOLD:
        return Decimal("0")
    if ytd_gross >= MEDICARE_ADDITIONAL_THRESHOLD:
        return fica_wages
    return annual - MEDICARE_ADDITIONAL_THRESHOLD


def compute_941(db, year: int, quarter: int) -> dict:
    """Aggregate quarterly Form 941 totals.

    Returns wage, withholding and tax-liability totals for the quarter along
    with the count of distinct employees paid.

    Column 2 of lines 5a, 5c and 5d is the form's own arithmetic: the rate
    times the quarter's taxable wages. The tax actually withheld and matched
    was rounded paycheck by paycheck, so it can differ by cents; the form
    carries that difference on line 7 (fractions of cents), and line 12 is
    what was withheld and matched. Printing the per-paycheck sums on 5a/5c
    showed 12.4% of $3,726.67 as $462.10 (2.17.3, macbase1 F23).
    """
    start, _ = _quarter_bounds(year, quarter)

    employee_ids: set[int] = set()
    stub_count = 0
    total_wages = Decimal("0")
    federal_withheld = Decimal("0")
    ss_employee = Decimal("0")
    ss_employer = Decimal("0")
    medicare_employee = Decimal("0")
    medicare_employer = Decimal("0")
    ss_wages = Decimal("0")
    medicare_wages = Decimal("0")
    additional_wages = Decimal("0")
    # Gross paid to each employee earlier in the year: the basis the
    # paycheck used for the wage base and the Additional Medicare threshold.
    ytd_gross: dict[int, Decimal] = {}

    for s in _stubs_through_quarter(db, year, quarter):
        gross = Decimal(str(s.gross_pay or 0))
        before = ytd_gross.get(s.employee_id, Decimal("0"))
        ytd_gross[s.employee_id] = before + gross
        if s.pay_run.pay_date < start:
            continue
        stub_count += 1
        # Line 1 counts employees who received wages; a $0.00 stub pays no one.
        if s.employee_id is not None and gross > 0:
            employee_ids.add(s.employee_id)
        total_wages += gross
        federal_withheld += Decimal(str(s.federal_tax or 0))
        ss_employee += Decimal(str(s.ss_tax or 0))
        ss_employer += Decimal(str(s.employer_ss_tax or 0))
        medicare_employee += Decimal(str(s.medicare_tax or 0))
        medicare_employer += Decimal(str(s.employer_medicare_tax or 0))
        fica = _fica_wages(s)
        ss_wages += _capped_wages(fica, before, SS_WAGE_BASE)
        medicare_wages += fica
        additional_wages += _additional_medicare_wages(fica, before)

    ss_tax = _q(ss_wages * SS_COMBINED_RATE)  # line 5a, column 2
    medicare_tax = _q(medicare_wages * MEDICARE_COMBINED_RATE)  # line 5c
    additional_tax = _q(additional_wages * MEDICARE_ADDITIONAL_RATE)  # line 5d
    total_fica = ss_tax + medicare_tax + additional_tax  # line 5e
    total_before = federal_withheld + total_fica  # line 6
    withheld_and_matched = (
        ss_employee + ss_employer + medicare_employee + medicare_employer
    )
    fractions_of_cents = withheld_and_matched - total_fica  # line 7
    total_after = total_before + fractions_of_cents  # lines 10 and 12

    return {
        "year": year,
        "quarter": quarter,
        "num_employees": len(employee_ids),
        "num_stubs": stub_count,
        # Line 2 — wages, tips and other compensation
        "total_wages": _q(total_wages),
        # Line 3 — federal income tax withheld
        "federal_income_tax_withheld": _q(federal_withheld),
        # Line 5a — taxable Social Security wages x 12.4%
        "social_security_wages": _q(ss_wages),
        "social_security_tax": ss_tax,
        "social_security_tax_employee": _q(ss_employee),
        "social_security_tax_employer": _q(ss_employer),
        # Line 5c — taxable Medicare wages x 2.9%
        "medicare_wages": _q(medicare_wages),
        "medicare_tax": medicare_tax,
        "medicare_tax_employee": _q(medicare_employee),
        "medicare_tax_employer": _q(medicare_employer),
        # Line 5d — wages subject to Additional Medicare Tax x 0.9%
        "additional_medicare_wages": _q(additional_wages),
        "additional_medicare_tax": additional_tax,
        # Line 5e — total Social Security + Medicare tax
        "total_fica_tax": _q(total_fica),
        # Line 6 — total taxes before adjustments
        "total_tax_before_adjustments": _q(total_before),
        # Line 7 — current quarter's adjustment for fractions of cents
        "fractions_of_cents": _q(fractions_of_cents),
        # Lines 10 and 12 — total taxes after adjustments (no credits)
        "total_tax_liability": _q(total_after),
    }


def generate_941_pdf(
    db, year: int, quarter: int, company: dict, audit: dict | None = None
) -> bytes:
    """Render Form 941 to a PDF for the given quarter."""
    data = compute_941(db, year, quarter)
    template = _jinja_env.get_template("form_941.html")
    html_str = template.render(data=data, company=company or {}, audit=audit or {})
    return render_pdf(html_str)
