# ============================================================================
# New York State payroll engine.
# ----------------------------------------------------------------------------
# New York withholds a progressive state income tax plus two employee-side
# insurance premiums:
#   * NY State Disability Insurance (SDI) — 0.5% of gross, capped at $0.60/week
#   * NY Paid Family Leave (PFL) — % of gross, with an annual maximum
#
# Income-tax brackets, the standard deduction, and the PFL rate/cap are
# 2026-approximate, simplified figures modelled on the published NY DTF
# schedule structure. Verify against the current NY withholding tables before
# relying on these for actual tax filing.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.services.state_tax.base import StateEngine, StateTaxResult

CENT = Decimal("0.01")
WEEKS_PER_YEAR = Decimal("52")

# --- NY State Disability Insurance ------------------------------------------
SDI_RATE = Decimal("0.005")  # 0.5% of gross, employee
SDI_WEEKLY_CAP = Decimal("0.60")  # statutory weekly maximum

# --- NY Paid Family Leave ---------------------------------------------------
PFL_RATE = Decimal("0.00388")  # 2026-approximate fraction of gross
PFL_ANNUAL_MAX = Decimal("354.00")  # 2026-approximate annual premium cap

# --- NY income tax (2026-approximate, simplified) ---------------------------
STD_DEDUCTION = {
    "single": Decimal("8000"),
    "head_of_household": Decimal("11200"),
    "married": Decimal("16050"),
}

# Annual progressive brackets as ascending (lower_bound, marginal_rate) pairs.
_BRACKETS = {
    "single": [
        (Decimal("0"), Decimal("0.04")),
        (Decimal("8500"), Decimal("0.045")),
        (Decimal("11700"), Decimal("0.0525")),
        (Decimal("13900"), Decimal("0.055")),
        (Decimal("80650"), Decimal("0.06")),
        (Decimal("215400"), Decimal("0.0685")),
        (Decimal("1077550"), Decimal("0.0965")),
        (Decimal("5000000"), Decimal("0.103")),
        (Decimal("25000000"), Decimal("0.109")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.04")),
        (Decimal("17150"), Decimal("0.045")),
        (Decimal("23600"), Decimal("0.0525")),
        (Decimal("27900"), Decimal("0.055")),
        (Decimal("161550"), Decimal("0.06")),
        (Decimal("323200"), Decimal("0.0685")),
        (Decimal("2155350"), Decimal("0.0965")),
        (Decimal("5000000"), Decimal("0.103")),
        (Decimal("25000000"), Decimal("0.109")),
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


class NYEngine(StateEngine):
    state_code: str = "NY"
    suta_wage_base: Decimal = Decimal("12800")

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

        # SDI — 0.5% of gross, capped at $0.60/week converted to a period cap.
        period_cap = SDI_WEEKLY_CAP * WEEKS_PER_YEAR / Decimal(pay_periods)
        sdi = gross * SDI_RATE
        if sdi > period_cap:
            sdi = period_cap
        sdi = _q(sdi)

        # PFL — flat rate of gross, bounded by the annual premium maximum.
        pfl = gross * PFL_RATE
        ytd_pfl = Decimal(str(ytd_gross)) * PFL_RATE
        remaining = PFL_ANNUAL_MAX - ytd_pfl
        if remaining < 0:
            remaining = Decimal("0")
        if pfl > remaining:
            pfl = remaining
        pfl = _q(pfl)

        return StateTaxResult(
            income_tax=income_tax,
            employee_other=sdi + pfl,
            employer_other=Decimal("0.00"),
            detail={
                "NY income tax": income_tax,
                "NY SDI": sdi,
                "NY PFL": pfl,
            },
        )
