# ============================================================================
# Washington State payroll engine.
# ----------------------------------------------------------------------------
# Washington has NO state income tax. Its payroll burden comes from three
# wage/hour assessments:
#   * WA Paid Family & Medical Leave (PFML) — % of gross up to the Social
#     Security wage base, split employee/employer
#   * WA Cares Fund — long-term-care premium, % of gross, employee only
#   * WA L&I workers' compensation — assessed PER HOUR worked, by risk class
#
# Sources (verified 2026-10-31):
#   * WA ESD news release 2025-10-29, "Paid Family & Medical Leave premium
#     rate increases to 1.13% in 2026": "Employers will pay 28.57% of the
#     total premium and employees will pay 71.43%"; "Businesses classified
#     ... as having fewer than 50 employees are not required to pay the
#     employer portion". https://esd.wa.gov/node/1109
#   * paidleave.wa.gov, "Employer roles & responsibilities": "In 2026, the
#     premium rate is 1.13% of each employee's gross wages ... up to the 2026
#     Social Security cap ($184,500)"; "The WA Cares premium rate is 0.58
#     percent of each employee's gross wages — there is no Social Security
#     cap." https://paidleave.wa.gov/employer-roles-responsibilities/
#     (2027: 1.2%, employer 29.3% / employee 70.7%.)
#   * WA ESD, "Taxable wage base": 2026 $78,200 (2027 $82,000).
#     https://esd.wa.gov/employer-requirements/unemployment-taxes/how-we-determine-tax-rates/taxable-wage-base
#   * WA L&I, "2026 Hourly Rates by Risk Classification Code and Fund" —
#     see app/seed/wa_lni_rates.py.
# ============================================================================

from decimal import Decimal

from app.seed.wa_lni_rates import get_lni_rate
from app.services.accounting import _q
from app.services.state_tax.base import StateEngine, StateTaxResult

# --- WA Paid Family & Medical Leave (2026) ----------------------------------
PFML_TOTAL_RATE = Decimal("0.0113")  # total premium as a fraction of gross
PFML_EMPLOYEE_SHARE = Decimal("0.7143")  # employee pays 71.43% of the premium
PFML_EMPLOYER_SHARE = Decimal("0.2857")  # employer pays 28.57% of the premium
PFML_WAGE_CAP = Decimal("184500")  # the 2026 Social Security wage base
# Employers with fewer than 50 employees owe no employer share (ESD sizes
# them each September from the previous four quarters). The pay run passes
# the company's active-employee count as employee_count; a caller that
# passes none is charged the employer share (the 50+ employee case).
PFML_EMPLOYER_MIN_EMPLOYEES = 50

# --- WA Cares Fund (long-term care) -----------------------------------------
WA_CARES_RATE = Decimal("0.0058")  # employee-only, fraction of gross, no cap


class WAEngine(StateEngine):
    state_code: str = "WA"
    # ESD taxable wage base, 2026: $78,200.
    suta_wage_base: Decimal = Decimal("78200")

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

        # PFML — total premium on gross up to the Social Security cap, split
        # between employee and employer.
        ytd = Decimal(str(ytd_gross))
        pfml_wages = max(Decimal("0"), min(gross, PFML_WAGE_CAP - ytd))
        pfml_total = pfml_wages * PFML_TOTAL_RATE
        pfml_employee = _q(pfml_total * PFML_EMPLOYEE_SHARE)
        count = _extra.get("employee_count")
        pfml_employer = (
            _q(pfml_total * PFML_EMPLOYER_SHARE)
            if count is None or count >= PFML_EMPLOYER_MIN_EMPLOYEES
            else Decimal("0.00")
        )

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
