# ============================================================================
# California State payroll engine.
# ----------------------------------------------------------------------------
# California withholds a progressive state income tax plus the State Disability
# Insurance (SDI) premium. Since 2024 (SB 951) SDI has no taxable-wage cap, so
# it applies to the full gross with no ceiling.
#
# Income tax follows EDD Method B (Exact Calculation Method) for 2026:
#   Step 1  wages at or under the Low Income Exemption (Table 1) → no tax
#   Step 2  additional allowances for estimated deductions (Table 2) — NOT
#           modelled: SlowBooks keeps one allowance count per employee (the
#           regular allowances) and has no field for the DE 4 "estimated
#           deductions" allowances, so none are subtracted.
#   Step 3  less the standard deduction (Table 3)
#   Step 4  the annual rate table for the filing status (Tables 5, 6, 7)
#   Step 5  less the exemption allowance credit (Table 4) per allowance
#
# Sources (verified 2026-10-31):
#   * EDD, "California Withholding Schedules for 2026 — Method B, Exact
#     Calculation Method", Tables 1, 3, 4 (page 5) and the Annual Payroll
#     Period Tables 5-7 (page 6).
#     https://edd.ca.gov/siteassets/files/pdf_pub_ctr/26methb.pdf
#   * EDD, DE 44 Rev. 52 (4-26), California Employer's Guide, "2026 Payroll
#     Tax Rates, Taxable Wage Limits, and Maximum Benefit Amounts": SDI 1.3%
#     with no taxable wage limit; UI taxable wage limit $7,000.
#     https://edd.ca.gov/siteassets/files/pdf_pub_ctr/de44.pdf
#   The EDD rates page (edd.ca.gov/en/payroll_taxes/rates_and_withholding/)
#   already lists the 2027 SDI rate as 1.4%.
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q
from app.services.state_tax.base import StateEngine, StateTaxResult

# --- CA State Disability Insurance ------------------------------------------
# DE 44 Rev. 52: "The 2026 SDI withholding rate is 1.3 percent (.013)";
# "There is no SDI taxable wage limit beginning January 1, 2024."
SDI_RATE = Decimal("0.013")  # employee only, NO wage cap

# --- CA income tax, Method B 2026 ------------------------------------------
# Table 3 — Standard Deduction, annual. $5,706 for Single (also "Dual Income
# Married or Married with Multiple Employers") and for Married claiming 0 or
# 1 allowances; $11,412 for Married claiming 2 or more and for Unmarried Head
# of Household.
STD_DEDUCTION_LOW = Decimal("5706")
STD_DEDUCTION_HIGH = Decimal("11412")

# Table 1 — Low Income Exemption, annual: no tax is withheld when wages are
# at or under $18,896 (Single; Married 0-1 allowances) / $37,791 (Married 2+
# allowances; Head of Household).
LOW_INCOME_EXEMPTION_LOW = Decimal("18896")
LOW_INCOME_EXEMPTION_HIGH = Decimal("37791")

# Table 4 — Exemption Allowance credit, annual, per allowance claimed on the
# DE 4 or Form W-4: $168.30 (0 allowances → $0.00).
EXEMPTION_CREDIT = Decimal("168.30")

# Annual progressive brackets as ascending (lower_bound, marginal_rate) pairs
# — Tables 5 (Single), 6 (Married) and 7 (Unmarried/Head of Household).
# Cumulative tax is derived in _tax_from_brackets; the derived amounts match
# the tables' "Plus" column to the cent.
_BRACKETS = {
    "single": [
        (Decimal("0"), Decimal("0.011")),
        (Decimal("11079"), Decimal("0.022")),
        (Decimal("26264"), Decimal("0.044")),
        (Decimal("41452"), Decimal("0.066")),
        (Decimal("57542"), Decimal("0.088")),
        (Decimal("72724"), Decimal("0.1023")),
        (Decimal("371479"), Decimal("0.1133")),
        (Decimal("445771"), Decimal("0.1243")),
        (Decimal("742953"), Decimal("0.1353")),
        (Decimal("1000000"), Decimal("0.1463")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.011")),
        (Decimal("22158"), Decimal("0.022")),
        (Decimal("52528"), Decimal("0.044")),
        (Decimal("82904"), Decimal("0.066")),
        (Decimal("115084"), Decimal("0.088")),
        (Decimal("145448"), Decimal("0.1023")),
        (Decimal("742958"), Decimal("0.1133")),
        (Decimal("891542"), Decimal("0.1243")),
        (Decimal("1000000"), Decimal("0.1353")),
        (Decimal("1485906"), Decimal("0.1463")),
    ],
    "head_of_household": [
        (Decimal("0"), Decimal("0.011")),
        (Decimal("22173"), Decimal("0.022")),
        (Decimal("52530"), Decimal("0.044")),
        (Decimal("67716"), Decimal("0.066")),
        (Decimal("83805"), Decimal("0.088")),
        (Decimal("98990"), Decimal("0.1023")),
        (Decimal("505208"), Decimal("0.1133")),
        (Decimal("606251"), Decimal("0.1243")),
        (Decimal("1000000"), Decimal("0.1353")),
        (Decimal("1010417"), Decimal("0.1463")),
    ],
}


def _tax_from_brackets(wage: Decimal, brackets) -> Decimal:
    """Progressive tax on `wage` given ascending (lower_bound, rate) brackets."""
    if wage <= 0:
        return Decimal("0")
    tax = Decimal("0")
    for i, (lower, rate) in enumerate(brackets):
        if wage <= lower:
            break
        upper = brackets[i + 1][0] if i + 1 < len(brackets) else None
        top = wage if upper is None else min(wage, upper)
        tax += (top - lower) * rate
        if upper is None or wage <= upper:
            break
    return tax


class CAEngine(StateEngine):
    state_code: str = "CA"
    # DE 44 Rev. 52: "The 2026 taxable wage limit is $7,000 per employee."
    suta_wage_base: Decimal = Decimal("7000")

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

        fs = filing_status if filing_status in _BRACKETS else "single"
        # The employee's "State W-4 allowances" are the DE 4 regular
        # allowances; the pay run passes them as state_allowances.
        allowances = max(0, int(_extra.get("state_allowances") or 0))
        extra = Decimal(str(_extra.get("state_extra_withholding") or 0))

        # Married with 2+ allowances and Head of Household use the higher
        # standard deduction and low-income exemption (Tables 1 and 3).
        high = fs == "head_of_household" or (fs == "married" and allowances >= 2)
        std_deduction = STD_DEDUCTION_HIGH if high else STD_DEDUCTION_LOW
        low_income = LOW_INCOME_EXEMPTION_HIGH if high else LOW_INCOME_EXEMPTION_LOW

        # Income tax — annualize, apply Method B, divide back to the period.
        annual = taxable * pay_periods
        if annual <= low_income:
            annual_tax = Decimal("0")  # Step 1: low income exemption
        else:
            annual_taxable = max(Decimal("0"), annual - std_deduction)  # Step 3
            annual_tax = _tax_from_brackets(annual_taxable, _BRACKETS[fs])  # Step 4
            annual_tax -= EXEMPTION_CREDIT * allowances  # Step 5
            if annual_tax < 0:
                annual_tax = Decimal("0")
        income_tax = _q(max(Decimal("0"), _q(annual_tax / pay_periods) + extra))

        # SDI — 1.3% of full gross, employee only, no wage cap.
        sdi = _q(gross * SDI_RATE)

        return StateTaxResult(
            income_tax=income_tax,
            employee_other=sdi,
            employer_other=Decimal("0.00"),
            detail={
                "CA income tax": income_tax,
                "CA SDI": sdi,
            },
        )
