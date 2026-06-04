# ============================================================================
# Generic State Engine — configurable flat-rate fallback.
# ----------------------------------------------------------------------------
# Used by get_engine() for any state without a dedicated engine. Applies a flat
# percentage to the period taxable wages as state income tax; no other items.
#
# The default instance is built with flat_rate 0 so an unknown state simply
# contributes no state income tax rather than a wrong guess. The rate stays
# configurable for callers that want a known flat-tax state.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.services.state_tax.base import StateEngine, StateTaxResult

CENT = Decimal("0.01")


class GenericStateEngine(StateEngine):
    state_code: str = "??"
    suta_wage_base: Decimal = Decimal("9000")

    def __init__(self, flat_rate: Decimal = Decimal("0")) -> None:
        self.flat_rate = Decimal(str(flat_rate))

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

        income_tax = (taxable * self.flat_rate).quantize(CENT, rounding=ROUND_HALF_UP)
        return StateTaxResult(
            income_tax=income_tax,
            employee_other=Decimal("0.00"),
            employer_other=Decimal("0.00"),
            detail={"State income tax (generic)": income_tax},
        )
