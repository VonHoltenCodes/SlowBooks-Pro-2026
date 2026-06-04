# ============================================================================
# California State payroll engine.
# ----------------------------------------------------------------------------
# California withholds a progressive state income tax plus the State Disability
# Insurance (SDI) premium. As of 2024 California removed the SDI taxable-wage
# cap, so SDI now applies to the full gross with no ceiling.
#
# Income-tax brackets and the standard deduction are 2026-approximate,
# simplified figures modelled on the published CA FTB schedule structure.
# Verify against the current FTB withholding tables before relying on these
# for actual tax filing.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.services.state_tax.base import StateEngine, StateTaxResult

CENT = Decimal("0.01")

# --- CA State Disability Insurance ------------------------------------------
SDI_RATE = Decimal("0.011")  # 1.1% of gross, employee only, NO wage cap

# --- CA income tax (2026-approximate, simplified) ---------------------------
# Standard deduction by filing status.
STD_DEDUCTION = {
    "single": Decimal("5500"),
    "head_of_household": Decimal("11000"),
    "married": Decimal("11000"),
}

# Annual progressive brackets as ascending (lower_bound, marginal_rate) pairs.
# Single and head_of_household share one schedule; married is roughly doubled.
_BRACKETS = {
    "single": [
        (Decimal("0"), Decimal("0.011")),
        (Decimal("10800"), Decimal("0.022")),
        (Decimal("25600"), Decimal("0.044")),
        (Decimal("40400"), Decimal("0.066")),
        (Decimal("56100"), Decimal("0.088")),
        (Decimal("70900"), Decimal("0.1023")),
        (Decimal("362000"), Decimal("0.1133")),
        (Decimal("434000"), Decimal("0.1243")),
        (Decimal("724000"), Decimal("0.1353")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.011")),
        (Decimal("21600"), Decimal("0.022")),
        (Decimal("51200"), Decimal("0.044")),
        (Decimal("80800"), Decimal("0.066")),
        (Decimal("112200"), Decimal("0.088")),
        (Decimal("141800"), Decimal("0.1023")),
        (Decimal("724000"), Decimal("0.1133")),
        (Decimal("868000"), Decimal("0.1243")),
        (Decimal("1448000"), Decimal("0.1353")),
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


class CAEngine(StateEngine):
    state_code: str = "CA"
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

        # SDI — 1.1% of full gross, employee only, no wage cap.
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
