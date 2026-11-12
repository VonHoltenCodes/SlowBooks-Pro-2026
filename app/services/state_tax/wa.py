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
#   2025 (a run dated 2025, #818): Paid Leave, "2025 Premium Rates Employer
#   Mailer" — 0.92% of gross wages up to the 2025 Social Security cap
#   ($176,100), employees 71.52% / employers 28.48%
#   (https://paidleave.wa.gov/app/uploads/2024/10/2025-Premium-Rates-Employer-Mailer.pdf); WA Cares
#   0.58% (unchanged); ESD taxable wage base 2025 $72,800; L&I "2025 Hourly
#   Rates by Risk Classification Code and Fund".
#
# L&I for a salaried employee with no hours on the run (#826): L&I's
# reporting rule for salaried workers whose hours are not tracked is 160
# hours a month, prorated to the pay period (salaried_lni_hours); the pay
# run passes those hours and the stub says so. Hours entered win.
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
# them each September from the previous four quarters, counting the
# employees working in Washington). The pay run passes the company's active
# Washington employees as state_employee_count; a caller that passes none
# is charged the employer share (the 50+ employee case). The company's
# setting (employer_share_applies, #825) outranks the count when it is set.
PFML_EMPLOYER_MIN_EMPLOYEES = 50

# --- WA Cares Fund (long-term care) -----------------------------------------
WA_CARES_RATE = Decimal("0.0058")  # employee-only, fraction of gross, no cap

# Each year's figures (#818). The module constants above are 2026's.
WA_YEARS = {
    2025: {
        "pfml_total_rate": Decimal("0.0092"),
        "pfml_employee_share": Decimal("0.7152"),
        "pfml_employer_share": Decimal("0.2848"),
        "pfml_wage_cap": Decimal("176100"),
        "wa_cares_rate": Decimal("0.0058"),
        "suta_wage_base": Decimal("72800"),
    },
    2026: {
        "pfml_total_rate": PFML_TOTAL_RATE,
        "pfml_employee_share": PFML_EMPLOYEE_SHARE,
        "pfml_employer_share": PFML_EMPLOYER_SHARE,
        "pfml_wage_cap": PFML_WAGE_CAP,
        "wa_cares_rate": WA_CARES_RATE,
        "suta_wage_base": Decimal("78200"),
    },
}

# --- L&I reporting basis for salaried workers (#826) ------------------------
# 160 hours a month: 1,920 a year, divided by the pay periods.
SALARIED_LNI_HOURS_PER_MONTH = Decimal("160")


def salaried_lni_hours(pay_periods: int) -> Decimal:
    """The L&I hours a salaried pay period reports when no hours were
    entered: 160 a month prorated to the period (80 semimonthly, 73.85
    biweekly, 36.92 weekly), to the hundredth."""
    return _q(SALARIED_LNI_HOURS_PER_MONTH * 12 / Decimal(pay_periods))


class WAEngine(StateEngine):
    state_code: str = "WA"
    # ESD taxable wage base, 2026: $78,200.
    suta_wage_base: Decimal = Decimal("78200")

    def __init__(self, year: int = 2026) -> None:
        self.year = year
        self.t = WA_YEARS[year]
        self.suta_wage_base = self.t["suta_wage_base"]

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
        t = self.t
        ytd = Decimal(str(ytd_gross))
        pfml_wages = max(Decimal("0"), min(gross, t["pfml_wage_cap"] - ytd))
        pfml_total = pfml_wages * t["pfml_total_rate"]
        pfml_employee = _q(pfml_total * t["pfml_employee_share"])
        count = _extra.get("state_employee_count")
        # the company's setting, as ESD determined it (#825), outranks the
        # run's headcount; None follows the headcount
        applies = _extra.get("employer_share_applies")
        if applies is None:
            applies = count is None or count >= PFML_EMPLOYER_MIN_EMPLOYEES
        pfml_employer = (
            _q(pfml_total * t["pfml_employer_share"]) if applies else Decimal("0.00")
        )

        # WA Cares — employee-only long-term-care premium.
        wa_cares = _q(gross * t["wa_cares_rate"])

        # L&I workers' comp — per-hour rates by risk classification.
        rate = get_lni_rate(wc_class_code, self.year)
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
