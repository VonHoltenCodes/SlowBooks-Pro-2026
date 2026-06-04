# ============================================================================
# Washington State payroll engine.
# ----------------------------------------------------------------------------
# Washington has NO state income tax. Its payroll burden comes from three
# wage/hour assessments:
#   * WA Paid Family & Medical Leave (PFML) — % of gross, split employee/employer
#   * WA Cares Fund — long-term-care premium, % of gross, employee only
#   * WA L&I workers' compensation — assessed PER HOUR worked, by risk class
#
# Rates are 2026-approximate. Verify against the WA ESD / L&I rate notices
# before relying on these for actual tax filing.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.seed.wa_lni_rates import get_lni_rate
from app.services.state_tax.base import StateEngine, StateTaxResult

CENT = Decimal("0.01")

# --- WA Paid Family & Medical Leave -----------------------------------------
PFML_TOTAL_RATE = Decimal("0.0074")  # total premium as a fraction of gross
PFML_EMPLOYEE_SHARE = Decimal("0.7143")  # employee pays 71.43% of the premium
PFML_EMPLOYER_SHARE = Decimal("0.2857")  # employer pays 28.57% of the premium

# --- WA Cares Fund (long-term care) -----------------------------------------
WA_CARES_RATE = Decimal("0.0058")  # employee-only, fraction of gross


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class WAEngine(StateEngine):
    state_code: str = "WA"
    suta_wage_base: Decimal = Decimal("72800")

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

        # PFML — total premium split between employee and employer.
        pfml_total = gross * PFML_TOTAL_RATE
        pfml_employee = _q(pfml_total * PFML_EMPLOYEE_SHARE)
        pfml_employer = _q(pfml_total * PFML_EMPLOYER_SHARE)

        # WA Cares — employee-only long-term-care premium.
        wa_cares = _q(gross * WA_CARES_RATE)

        # L&I workers' comp — per-hour rates by risk classification.
        rate = get_lni_rate(wc_class_code)
        lni_employee = _q(hours * rate["employee"])
        lni_employer = _q(hours * (rate["total"] - rate["employee"]))

        employee_other = pfml_employee + wa_cares + lni_employee
        employer_other = pfml_employer + lni_employer

        return StateTaxResult(
            income_tax=Decimal("0.00"),
            employee_other=employee_other,
            employer_other=employer_other,
            detail={
                "WA PFML (employee)": pfml_employee,
                "WA Cares": wa_cares,
                "WA L&I (employee)": lni_employee,
                "WA PFML (employer)": pfml_employer,
                "WA L&I (employer)": lni_employer,
            },
        )
