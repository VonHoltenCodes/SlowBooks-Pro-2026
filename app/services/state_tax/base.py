# ============================================================================
# State Tax Engine — base types and the StateEngine abstract contract.
# ----------------------------------------------------------------------------
# Every per-state withholding engine returns a StateTaxResult: the state income
# tax withheld plus aggregate "other" employee/employer items (PFML, SDI, PFL,
# WA Cares, L&I, transit tax, ...) with a fully itemized ``detail`` map for
# pay-stub rendering.
#
# Subclasses set ``state_code`` / ``suta_wage_base`` and override calculate().
# All monetary values are decimal.Decimal, quantized to cents by the engine.
# ============================================================================

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class StateTaxResult:
    income_tax: Decimal = Decimal("0")  # state income tax withheld (employee)
    employee_other: Decimal = Decimal(
        "0"
    )  # sum of all OTHER employee items (PFML, WA Cares, SDI, PFL, transit tax...)
    employer_other: Decimal = Decimal(
        "0"
    )  # sum of all OTHER employer items (PFML employer share, WA L&I employer share...)
    detail: dict = field(
        default_factory=dict
    )  # itemized {human_label: Decimal} for every line above


class StateEngine:
    """Base class. Subclasses set state_code / suta_wage_base and override calculate()."""

    state_code: str = "??"
    suta_wage_base: Decimal = Decimal(
        "9000"
    )  # employer SUTA taxable wage base for this state

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
        return StateTaxResult()
