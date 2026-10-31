# ============================================================================
# Oregon State payroll engine.
# ----------------------------------------------------------------------------
# Module named "oregon" rather than "or" — `or` is a Python keyword and cannot
# be imported under its 2-letter code.
#
# Oregon withholds a progressive state income tax, the Paid Leave Oregon
# contribution (employee and employer shares) and the statewide transit tax
# (a flat 0.1% of wages, employee).
#
# Income tax follows the Oregon DOR computer formula (150-206-436):
#   BASE = wages − federal tax withheld (capped, phased out for high wages)
#          − standard deduction
#   WH   = the rate-table amount on BASE
#          + $263 when annual wages are under $50,000
#          − $263 × allowances (allowances = 0 when single wages > $100,000
#            or married wages > $200,000), not below zero
# "Single with 3 or more allowances" uses the married standard deduction and
# rate table; head of household has no Oregon schedule and uses single.
# The federal tax withheld is the period's federal withholding, which the
# pay run passes as federal_withholding; with none passed the subtraction is
# zero (the DOR formula's conservative case).
#
# Sources (verified 2026-10-31):
#   * Oregon DOR, "Oregon Withholding Tax Formulas, Effective January 1,
#     2026", 150-206-436 (Rev. 12-31-25), pages 6-7 (annual wages formulas,
#     standard deductions $2,910 / $5,820, federal tax subtraction limit
#     $8,750 with phase-outs, $263 personal exemption credit).
#     https://www.oregon.gov/dor/forms/FormsPubs/withholding-tax-formulas_206-436_2026.pdf
#   * Oregon Employment Department release 2025-11-18, "Unemployment
#     Insurance tax and Paid Leave Oregon contribution rates hold steady for
#     2026": UI taxable wage base $56,700; Paid Leave Oregon rate 1%.
#     https://www.oregon.gov/employ/NewsAndMedia/Documents/2025-11-18_Tax_Rate_2026_Press_Release.pdf
#   * Paid Leave Oregon, Employers: "The total contribution rate for 2026 is
#     1% of gross wages up to $184,500"; employees pay 60%, large employers
#     40%; "Employers with fewer than 25 employees on average don't pay the
#     employer share." https://paidleave.oregon.gov/employers/
#   * Oregon DOR, Statewide Transit Tax: "one-tenth of 1 percent or .001";
#     Measure 120 (the HB 3991 increase) failed on 2026-05-19.
#     https://www.oregon.gov/dor/programs/businesses/Pages/Statewide-Transit-Tax.aspx
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q
from app.services.state_tax.base import StateEngine, StateTaxResult

# --- Oregon statewide transit tax -------------------------------------------
TRANSIT_TAX_RATE = Decimal("0.001")  # 0.1% of gross, employee

# --- Paid Leave Oregon (2026) -----------------------------------------------
# 1% of gross wages up to the Social Security wage base ($184,500 in 2026),
# 60% employee / 40% employer. SIMPLIFICATION: employers averaging fewer
# than 25 employees owe no employer share; SlowBooks has no headcount in the
# engine, so the employer share is always charged (the large-employer case).
PAID_LEAVE_EMPLOYEE_RATE = Decimal("0.006")
PAID_LEAVE_EMPLOYER_RATE = Decimal("0.004")
PAID_LEAVE_WAGE_CAP = Decimal("184500")

# --- Oregon income tax, 150-206-436 (2026) ----------------------------------
STD_DEDUCTION = {
    "single": Decimal("2910"),  # single with fewer than 3 allowances
    "married": Decimal("5820"),  # married, or single with 3 or more allowances
}
FEDERAL_TAX_SUBTRACTION_CAP = Decimal("8750")
# Phase-out of the federal tax subtraction by annual wages: (wages at least,
# cap). Single (also "single with 3 or more allowances") and married.
_FED_PHASE_OUT = {
    "single": [
        (Decimal("145000"), Decimal("0")),
        (Decimal("140000"), Decimal("1750")),
        (Decimal("135000"), Decimal("3500")),
        (Decimal("130000"), Decimal("5250")),
        (Decimal("125000"), Decimal("7000")),
    ],
    "married": [
        (Decimal("290000"), Decimal("0")),
        (Decimal("280000"), Decimal("1750")),
        (Decimal("270000"), Decimal("3500")),
        (Decimal("260000"), Decimal("5250")),
        (Decimal("250000"), Decimal("7000")),
    ],
}
EXEMPTION_CREDIT = Decimal("263")  # personal exemption credit per allowance
LOW_WAGE_LIMIT = Decimal("50000")  # the +$263 applies under this
# Allowances count as zero above these annual wages.
_ALLOWANCE_WAGE_LIMIT = {"single": Decimal("100000"), "married": Decimal("200000")}

# Rate table rows as (BASE at least, rate, add-on): the published formula
# constants less the $263 that the under-$50,000 formulas carry. The add-ons
# are the DOR's rounded dollar figures, carried as printed.
_RATES = {
    "single": [
        (Decimal("0"), Decimal("0.0475"), Decimal("0")),
        (Decimal("4550"), Decimal("0.0675"), Decimal("216")),
        (Decimal("11400"), Decimal("0.0875"), Decimal("678")),
        (Decimal("125000"), Decimal("0.099"), Decimal("10618")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.0475"), Decimal("0")),
        (Decimal("9100"), Decimal("0.0675"), Decimal("432")),
        (Decimal("22800"), Decimal("0.0875"), Decimal("1357")),
        (Decimal("250000"), Decimal("0.099"), Decimal("21237")),
    ],
}


def _federal_subtraction(
    federal_annual: Decimal, wages: Decimal, table: str
) -> Decimal:
    cap = FEDERAL_TAX_SUBTRACTION_CAP
    for floor, limit in _FED_PHASE_OUT[table]:
        if wages >= floor:
            cap = limit
            break
    return min(max(Decimal("0"), federal_annual), cap)


def _rate_table_tax(base: Decimal, table: str) -> Decimal:
    if base <= 0:
        return Decimal("0")
    row = _RATES[table][0]
    for candidate in _RATES[table]:
        if base >= candidate[0]:
            row = candidate
    lower, rate, add = row
    return (base - lower) * rate + add


class OregonEngine(StateEngine):
    state_code: str = "OR"
    # OED 2025-11-18: 2026 taxable wage base $56,700 (Tax Schedule 3).
    suta_wage_base: Decimal = Decimal("56700")

    def calculate(
        self,
        *,
        gross: Decimal,
        taxable: Decimal,
        ytd_gross: Decimal,
        pay_periods: int,
        hours: Decimal,
        filing_status: str,
        wc_class_code: str | None,
        **_extra,
    ) -> StateTaxResult:
        if gross <= 0 or taxable <= 0:
            return StateTaxResult()

        allowances = max(0, int(_extra.get("state_allowances") or 0))
        extra = Decimal(str(_extra.get("state_extra_withholding") or 0))
        federal_period = Decimal(str(_extra.get("federal_withholding") or 0))

        # Which formula: married, or single with 3 or more allowances, use
        # the married deduction and rates; everyone else single.
        married = filing_status == "married"
        table = "married" if (married or allowances >= 3) else "single"
        phase_out = "married" if married else "single"

        # Income tax — annualize, build BASE, apply the formula, divide back.
        wages = taxable * pay_periods
        federal_annual = _federal_subtraction(
            federal_period * pay_periods, wages, phase_out
        )
        base = wages - federal_annual - STD_DEDUCTION[table]
        annual_tax = _rate_table_tax(base, table)
        if wages < LOW_WAGE_LIMIT:
            annual_tax += EXEMPTION_CREDIT
        if wages > _ALLOWANCE_WAGE_LIMIT[phase_out]:
            allowances = 0
        annual_tax -= EXEMPTION_CREDIT * allowances
        if annual_tax < 0:
            annual_tax = Decimal("0")
        income_tax = _q(max(Decimal("0"), _q(annual_tax / pay_periods) + extra))

        # Paid Leave Oregon — on gross wages up to the Social Security base.
        ytd = Decimal(str(ytd_gross))
        pl_wages = max(Decimal("0"), min(gross, PAID_LEAVE_WAGE_CAP - ytd))
        paid_leave_employee = _q(pl_wages * PAID_LEAVE_EMPLOYEE_RATE)
        paid_leave_employer = _q(pl_wages * PAID_LEAVE_EMPLOYER_RATE)

        # Statewide transit tax — flat 0.1% of gross, employee.
        transit_tax = _q(gross * TRANSIT_TAX_RATE)

        return StateTaxResult(
            income_tax=income_tax,
            employee_other=transit_tax + paid_leave_employee,
            employer_other=paid_leave_employer,
            detail={
                "OR income tax": income_tax,
                "OR Paid Leave (employee)": paid_leave_employee,
                "OR statewide transit tax": transit_tax,
                "OR Paid Leave (employer)": paid_leave_employer,
            },
        )
