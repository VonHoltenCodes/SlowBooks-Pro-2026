# ============================================================================
# Oregon State payroll engine.
# ----------------------------------------------------------------------------
# Module named "oregon" rather than "or" — `or` is a Python keyword and cannot
# be imported under its 2-letter code.
#
# Oregon withholds a progressive state income tax plus the statewide transit
# tax (a flat 0.1% of gross used to fund public transportation).
#
# Income-tax brackets and the standard deduction are 2026-approximate,
# simplified figures modelled on the published Oregon DOR schedule structure.
# Verify against the current Oregon withholding tables before relying on these
# for actual tax filing.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.services.state_tax.base import StateEngine, StateTaxResult

CENT = Decimal("0.01")

# --- Oregon statewide transit tax -------------------------------------------
TRANSIT_TAX_RATE = Decimal("0.001")  # 0.1% of gross, employee

# --- Oregon income tax (2026-approximate, simplified) -----------------------
STD_DEDUCTION = {
    "single": Decimal("2745"),
    "head_of_household": Decimal("4420"),
    "married": Decimal("5495"),
}

# Annual progressive brackets as ascending (lower_bound, marginal_rate) pairs.
_BRACKETS = {
    "single": [
        (Decimal("0"), Decimal("0.0475")),
        (Decimal("4300"), Decimal("0.0675")),
        (Decimal("10750"), Decimal("0.0875")),
        (Decimal("125000"), Decimal("0.099")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.0475")),
        (Decimal("8600"), Decimal("0.0675")),
        (Decimal("21500"), Decimal("0.0875")),
        (Decimal("250000"), Decimal("0.099")),
    ],
}


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


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


class OregonEngine(StateEngine):
    state_code: str = "OR"
    suta_wage_base: Decimal = Decimal("54300")

    def calculate(
        self,
        *,
        gross: Decimal,
        taxable: Decimal,
        ytd_gross: Decimal,
        pay_periods: int,
        hours: Decimal,
        filing_status: str,
        wc_class_code: str | None
    ) -> StateTaxResult:
        if gross <= 0 or taxable <= 0:
            return StateTaxResult()

        fs = filing_status if filing_status in _BRACKETS else "single"

        # Income tax — annualize, apply brackets net of the standard deduction,
        # then divide back down to the period amount.
        annual = taxable * pay_periods
        annual_taxable = annual - STD_DEDUCTION[fs]
        if annual_taxable < 0:
            annual_taxable = Decimal("0")
        annual_tax = _tax_from_brackets(annual_taxable, _BRACKETS[fs])
        income_tax = _q(annual_tax / pay_periods)

        # Statewide transit tax — flat 0.1% of gross, employee.
        transit_tax = _q(gross * TRANSIT_TAX_RATE)

        return StateTaxResult(
            income_tax=income_tax,
            employee_other=transit_tax,
            employer_other=Decimal("0.00"),
            detail={
                "OR income tax": income_tax,
                "OR statewide transit tax": transit_tax,
            },
        )
