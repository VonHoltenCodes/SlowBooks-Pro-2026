# ============================================================================
# New York State payroll engine.
# ----------------------------------------------------------------------------
# New York withholds a progressive state income tax plus two employee-side
# insurance premiums:
#   * NY State Disability Insurance (SDI) — 0.5% of gross, capped at $0.60/week
#   * NY Paid Family Leave (PFL) — % of gross, with an annual maximum
#
# Income tax follows NYS-50-T-NYS Method II (Exact Calculation Method):
#   Step 1  wages less the deduction allowance (Table B: $7,400 single /
#           $7,950 married) and $1,000 per exemption (Table C) = net wages
#   Steps 2-5  the Annual Tax Rate Schedule for the marital status: net wages
#           less column 3, times column 4, plus column 5. Net wages of
#           $1,077,550 (single) / $2,155,350 (married) and over use Method III
#           (Top Income Tax Rates Method): annualized net wages times one rate.
# New York has only single and married schedules; head of household uses
# the single schedule (the publication's "Single or Head of Household").
#
# Sources (verified 2026-10-31):
#   * NYS Department of Taxation and Finance, NYS-50-T-NYS (1/26), New York
#     State Withholding Tax Tables and Methods, effective January 1, 2026:
#     Tables A-C (pages 14-15), Method II annual schedules (pages 17 and 19),
#     Method III (page 22).
#     https://www.tax.ny.gov/pdf/publications/withholding/nys50_t_nys.pdf
#   * NY Paid Family Leave, "2026 Paid Family Leave": "employees will
#     contribute 0.432% of their gross wages per pay period, with a maximum
#     annual contribution of $411.91." https://paidfamilyleave.ny.gov/2026
#     (2027: 0.452%, $451.81 maximum, per the DFS decision of 2026-08-31.)
#   * NY DOL, "NYS-45 Quarterly Reporting", UI wage base: "2026 - $17,600"
#     (from 2026 the base is 18% of the state average annual wage).
#     https://dol.ny.gov/nys-45-quarterly-reporting
#   * NY Workers' Compensation Law §209: DBL employee contribution of 0.5% of
#     wages, not more than 60 cents a week (statutory, unchanged).
# ============================================================================

from decimal import Decimal

from app.services.accounting import _q
from app.services.state_tax.base import StateEngine, StateTaxResult

WEEKS_PER_YEAR = Decimal("52")

# --- NY State Disability Insurance ------------------------------------------
SDI_RATE = Decimal("0.005")  # 0.5% of gross, employee (WCL §209)
SDI_WEEKLY_CAP = Decimal("0.60")  # statutory weekly maximum

# --- NY Paid Family Leave (2026) --------------------------------------------
PFL_RATE = Decimal("0.00432")  # 0.432% of gross wages
PFL_ANNUAL_MAX = Decimal("411.91")  # maximum annual employee contribution

# --- NY income tax, NYS-50-T-NYS (1/26) Method II ---------------------------
# Table B — deduction allowance, annual. Head of household uses single.
DEDUCTION_ALLOWANCE = {
    "single": Decimal("7400"),
    "head_of_household": Decimal("7400"),
    "married": Decimal("7950"),
}
# Table C — exemption allowance, annual, per exemption claimed on the IT-2104.
EXEMPTION_ALLOWANCE = Decimal("1000")

# Annual Tax Rate Schedule rows as (column 1 "at least", column 4 rate,
# column 5 add-on). The published column 5 figures are rounded to the
# dollar, so they are carried as printed rather than re-derived.
_SCHEDULE = {
    "single": [
        (Decimal("0"), Decimal("0.0390"), Decimal("0")),
        (Decimal("8500"), Decimal("0.0440"), Decimal("332")),
        (Decimal("11700"), Decimal("0.0515"), Decimal("472")),
        (Decimal("13900"), Decimal("0.0540"), Decimal("586")),
        (Decimal("80650"), Decimal("0.0590"), Decimal("4190")),
        (Decimal("96800"), Decimal("0.0703"), Decimal("5143")),
        (Decimal("107650"), Decimal("0.0753"), Decimal("5906")),
        (Decimal("157650"), Decimal("0.0640"), Decimal("9673")),
        (Decimal("215400"), Decimal("0.1144"), Decimal("13369")),
        (Decimal("265400"), Decimal("0.0735"), Decimal("19091")),
    ],
    "married": [
        (Decimal("0"), Decimal("0.0390"), Decimal("0")),
        (Decimal("8500"), Decimal("0.0440"), Decimal("332")),
        (Decimal("11700"), Decimal("0.0515"), Decimal("472")),
        (Decimal("13900"), Decimal("0.0540"), Decimal("586")),
        (Decimal("80650"), Decimal("0.0590"), Decimal("4190")),
        (Decimal("96800"), Decimal("0.0657"), Decimal("5143")),
        (Decimal("107650"), Decimal("0.0707"), Decimal("5855")),
        (Decimal("157650"), Decimal("0.0801"), Decimal("9388")),
        (Decimal("211550"), Decimal("0.0640"), Decimal("13708")),
        (Decimal("323200"), Decimal("0.1349"), Decimal("20854")),
        (Decimal("373200"), Decimal("0.0735"), Decimal("27600")),
        (Decimal("1077550"), Decimal("0.0765"), Decimal("79369")),
    ],
}
_SCHEDULE["head_of_household"] = _SCHEDULE["single"]

# Method III — Top Income Tax Rates Method: annualized net wages at or over
# the threshold are multiplied by one rate (column 3), no add-on.
_METHOD_III_THRESHOLD = {
    "single": Decimal("1077550"),
    "head_of_household": Decimal("1077550"),
    "married": Decimal("2155350"),
}
_METHOD_III = [  # (at least, rate) — the same for single and married
    (Decimal("25000000"), Decimal("0.1170")),
    (Decimal("5000000"), Decimal("0.1110")),
    (Decimal("0"), Decimal("0.1045")),
]


def _annual_tax(net: Decimal, fs: str) -> Decimal:
    """NYS-50-T-NYS Method II / III annualized tax on annual net wages."""
    if net <= 0:
        return Decimal("0")
    if net >= _METHOD_III_THRESHOLD[fs]:
        for floor, rate in _METHOD_III:
            if net >= floor:
                return net * rate
    row = _SCHEDULE[fs][0]
    for candidate in _SCHEDULE[fs]:
        if net >= candidate[0]:
            row = candidate
    lower, rate, add = row
    return (net - lower) * rate + add


class NYEngine(StateEngine):
    state_code: str = "NY"
    # NY DOL: UI wage base "2026 - $17,600" (2025 was $12,800).
    suta_wage_base: Decimal = Decimal("17600")

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

        fs = filing_status if filing_status in _SCHEDULE else "single"
        # IT-2104 exemptions arrive as the employee's state allowances.
        exemptions = max(0, int(_extra.get("state_allowances") or 0))
        extra = Decimal(str(_extra.get("state_extra_withholding") or 0))

        # Income tax — annualize, subtract the deduction and exemption
        # allowances (Step 1), apply the annual schedule, divide back down.
        annual = taxable * pay_periods
        net = annual - DEDUCTION_ALLOWANCE[fs] - EXEMPTION_ALLOWANCE * exemptions
        annual_tax = _annual_tax(net, fs)
        income_tax = _q(max(Decimal("0"), _q(annual_tax / pay_periods) + extra))

        # SDI — 0.5% of gross, capped at $0.60/week converted to a period cap.
        period_cap = SDI_WEEKLY_CAP * WEEKS_PER_YEAR / Decimal(pay_periods)
        sdi = gross * SDI_RATE
        if sdi > period_cap:
            sdi = period_cap
        sdi = _q(sdi)

        # PFL — flat rate of gross, bounded by the annual contribution maximum.
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
