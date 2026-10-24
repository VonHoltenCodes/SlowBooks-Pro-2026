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
    ss_wage_base,
    stub_taxable_wages,
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
    return stub_taxable_wages(stub)["fica"]


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


def compute_941(db, year: int, quarter: int, legacy: bool = False) -> dict:
    """Aggregate quarterly Form 941 totals.

    Returns wage, withholding and tax-liability totals for the quarter along
    with the count of distinct employees paid.

    Column 2 of lines 5a, 5c and 5d is the form's own arithmetic: the rate
    times the quarter's taxable wages. The tax actually withheld and matched
    was rounded paycheck by paycheck, so it can differ by cents; the form
    carries that difference on line 7 (fractions of cents), and line 12 is
    what was withheld and matched. Printing the per-paycheck sums on 5a/5c
    showed 12.4% of $3,726.67 as $462.10 (2.17.3, macbase1 F23).

    `legacy` figures it as SlowBooks did before 2.29.0 (each earlier stub's
    gross toward the wage base and the threshold, the 2026 Social Security
    base for every year): Form history's Check uses it to tell a 941
    generated then from books that changed (document_check).
    """
    start, end = _quarter_bounds(year, quarter)
    wage_base = SS_WAGE_BASE if legacy else ss_wage_base(year)

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
    # What already counted toward each employee's Social Security wage base
    # and Additional Medicare threshold: the measure the paycheck used
    # (ytd.wage_base_ytd): a stub's FICA wages, an opening's own taxable
    # wages.
    ss_before: dict[int, Decimal] = {}
    med_before: dict[int, Decimal] = {}

    # Opening balances (#433), quarter by quarter: the old system filed the
    # 941 for every quarter it finished, so this quarter's 941 carries only
    # this quarter's opening figures (its taxable wages and taxes as the old
    # system figured them); earlier quarters fill the wage base. An opening
    # counts from its as_of, so a stub dated on or before it measures
    # without it, as the paycheck did.
    from app.services.payroll_opening import openings_for_year

    events = []
    for emp_id, o in openings_for_year(db, year).items():
        for row in o.quarters:
            if row.quarter <= quarter:
                events.append((o.as_of, 0, row.quarter, emp_id, row))
    for s in _stubs_through_quarter(db, year, quarter):
        events.append((s.pay_run.pay_date, 1, s.id, s.employee_id, s))
    events.sort(key=lambda e: e[:3])

    for _when, kind, _order, emp_id, item in events:
        ss_prior = ss_before.get(emp_id, Decimal("0"))
        med_prior = med_before.get(emp_id, Decimal("0"))
        if kind == 0:
            row = item
            row_ss = Decimal(str(row.ss_wages or 0))
            row_med = Decimal(str(row.medicare_wages or 0))
            ss_before[emp_id] = ss_prior + row_ss
            med_before[emp_id] = med_prior + row_med
            if row.quarter != quarter:
                continue
            gross = Decimal(str(row.gross or 0))
            if gross > 0:
                employee_ids.add(emp_id)
            total_wages += gross
            federal_withheld += Decimal(str(row.federal_tax or 0))
            ss_employee += Decimal(str(row.ss_tax or 0))
            ss_employer += Decimal(str(row.employer_ss_tax or 0))
            medicare_employee += Decimal(str(row.medicare_tax or 0))
            medicare_employer += Decimal(str(row.employer_medicare_tax or 0))
            ss_wages += row_ss
            medicare_wages += row_med
            additional_wages += _additional_medicare_wages(row_med, med_prior)
            continue
        s = item
        gross = Decimal(str(s.gross_pay or 0))
        fica = _fica_wages(s)
        # what the paycheck counted toward the base: its FICA wages
        counted = gross if legacy else fica
        ss_before[emp_id] = ss_prior + counted
        med_before[emp_id] = med_prior + counted
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
        ss_wages += _capped_wages(fica, ss_prior, wage_base)
        medicare_wages += fica
        additional_wages += _additional_medicare_wages(fica, med_prior)

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
