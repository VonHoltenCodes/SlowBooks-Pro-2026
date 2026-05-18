# ============================================================================
# Payroll Service — federal withholding (IRS Pub 15-T) + FICA + employer taxes
# ----------------------------------------------------------------------------
# Federal income tax withholding follows Pub 15-T Worksheet 1A (Percentage
# Method for Automated Payroll Systems) using the 2020+ redesigned Form W-4 —
# there are no "allowances" anymore. Supplemental wages use Worksheet 4 (flat).
#
# DISCLAIMER: Bracket figures are 2026-approximate, modelled on the published
# Pub 15-T schedule structure. Verify against the current IRS Pub 15-T before
# relying on these for actual tax filing.
# ============================================================================

from decimal import Decimal, ROUND_HALF_UP

from app.models.payroll import periods_per_year
from app.services.state_tax import get_engine

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- FICA -------------------------------------------------------------------
SS_RATE = Decimal("0.062")  # Social Security 6.2% (employee & employer each)
SS_WAGE_BASE = Decimal("184500")  # 2026 SS wage base (approximate)
MEDICARE_RATE = Decimal("0.0145")  # Medicare 1.45% (employee & employer each)
MEDICARE_ADDITIONAL_RATE = Decimal("0.009")  # extra 0.9% (employee only)
MEDICARE_ADDITIONAL_THRESHOLD = Decimal("200000")  # withhold the extra above this

# --- Federal unemployment (FUTA) -------------------------------------------
FUTA_WAGE_BASE = Decimal("7000")
FUTA_EFFECTIVE_RATE = Decimal("0.006")  # 6.0% gross less the standard 5.4% state credit

# --- Pub 15-T Worksheet 1A -------------------------------------------------
# Line 1g standard-deduction add-back (skipped when the Step 2 box is checked).
STD_DEDUCTION_ADDBACK = {
    "married": Decimal("12900"),
    "single": Decimal("8600"),
    "head_of_household": Decimal("8600"),
}

# Percentage Method annual schedules as (lower_bound, marginal_rate) pairs,
# ascending. Cumulative tax is derived in _tax_from_brackets so the schedules
# stay internally consistent regardless of transcription.
_STANDARD = {
    "single": [
        (Decimal("0"), Decimal("0")),
        (Decimal("6000"), Decimal("0.10")),
        (Decimal("17600"), Decimal("0.12")),
        (Decimal("53150"), Decimal("0.22")),
        (Decimal("106525"), Decimal("0.24")),
        (Decimal("197950"), Decimal("0.32")),
        (Decimal("249725"), Decimal("0.35")),
        (Decimal("615350"), Decimal("0.37")),
    ],
    "married": [
        (Decimal("0"), Decimal("0")),
        (Decimal("17100"), Decimal("0.10")),
        (Decimal("40300"), Decimal("0.12")),
        (Decimal("111400"), Decimal("0.22")),
        (Decimal("218150"), Decimal("0.24")),
        (Decimal("400000"), Decimal("0.32")),
        (Decimal("503550"), Decimal("0.35")),
        (Decimal("747200"), Decimal("0.37")),
    ],
    "head_of_household": [
        (Decimal("0"), Decimal("0")),
        (Decimal("13300"), Decimal("0.10")),
        (Decimal("29850"), Decimal("0.12")),
        (Decimal("76400"), Decimal("0.22")),
        (Decimal("113800"), Decimal("0.24")),
        (Decimal("205250"), Decimal("0.32")),
        (Decimal("257000"), Decimal("0.35")),
        (Decimal("622650"), Decimal("0.37")),
    ],
}

# "Form W-4, Step 2, Checkbox" schedules — used when the employee checked the
# multiple-jobs box. Roughly the standard schedule with the brackets halved.
_CHECKBOX = {
    "single": [
        (Decimal("0"), Decimal("0")),
        (Decimal("7300"), Decimal("0.10")),
        (Decimal("13100"), Decimal("0.12")),
        (Decimal("30875"), Decimal("0.22")),
        (Decimal("57563"), Decimal("0.24")),
        (Decimal("103275"), Decimal("0.32")),
        (Decimal("129163"), Decimal("0.35")),
        (Decimal("311975"), Decimal("0.37")),
    ],
    "married": [
        (Decimal("0"), Decimal("0")),
        (Decimal("14600"), Decimal("0.10")),
        (Decimal("26200"), Decimal("0.12")),
        (Decimal("61750"), Decimal("0.22")),
        (Decimal("115125"), Decimal("0.24")),
        (Decimal("206550"), Decimal("0.32")),
        (Decimal("258325"), Decimal("0.35")),
        (Decimal("380200"), Decimal("0.37")),
    ],
    "head_of_household": [
        (Decimal("0"), Decimal("0")),
        (Decimal("10800"), Decimal("0.10")),
        (Decimal("19075"), Decimal("0.12")),
        (Decimal("42350"), Decimal("0.22")),
        (Decimal("61050"), Decimal("0.24")),
        (Decimal("106775"), Decimal("0.32")),
        (Decimal("132650"), Decimal("0.35")),
        (Decimal("224100"), Decimal("0.37")),
    ],
}

# Supplemental wage withholding (Pub 15 / Worksheet 4): flat 22%, or 37% on
# cumulative supplemental wages above $1M for the year.
SUPPLEMENTAL_RATE = Decimal("0.22")
SUPPLEMENTAL_HIGH_RATE = Decimal("0.37")
SUPPLEMENTAL_HIGH_THRESHOLD = Decimal("1000000")


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


def federal_income_tax(
    taxable_wages: Decimal,
    pay_periods: int,
    filing_status: str = "single",
    multiple_jobs: bool = False,
    dependents_amount: Decimal = Decimal("0"),
    other_income_annual: Decimal = Decimal("0"),
    deductions_annual: Decimal = Decimal("0"),
    extra_withholding: Decimal = Decimal("0"),
) -> Decimal:
    """Federal income tax to withhold for one pay period — Pub 15-T Worksheet 1A.

    `taxable_wages` is gross for the period already net of any pre-tax
    deductions that reduce federal taxable wages.
    """
    taxable_wages = Decimal(str(taxable_wages))
    if taxable_wages <= 0:
        return Decimal("0")
    fs = filing_status if filing_status in _STANDARD else "single"

    # Step 1 — adjusted annual wage amount
    annual_wage = taxable_wages * pay_periods
    line_1e = annual_wage + Decimal(str(other_income_annual))
    addback = Decimal("0") if multiple_jobs else STD_DEDUCTION_ADDBACK[fs]
    line_1h = Decimal(str(deductions_annual)) + addback
    adjusted_annual = line_1e - line_1h
    if adjusted_annual < 0:
        adjusted_annual = Decimal("0")

    # Step 2 — tentative withholding from the percentage-method schedule
    table = _CHECKBOX[fs] if multiple_jobs else _STANDARD[fs]
    tentative_annual = _tax_from_brackets(adjusted_annual, table)
    tentative_period = tentative_annual / pay_periods

    # Step 3 — apply the Step 3 dependent/credit amount
    credit_period = Decimal(str(dependents_amount)) / pay_periods
    withholding = tentative_period - credit_period
    if withholding < 0:
        withholding = Decimal("0")

    # Step 4 — add any extra per-period withholding from Step 4(c)
    withholding += Decimal(str(extra_withholding))
    return _q(withholding)


def supplemental_federal_tax(
    amount: Decimal, ytd_supplemental: Decimal = Decimal("0")
) -> Decimal:
    """Flat-rate federal withholding on supplemental wages (bonuses, etc.)."""
    amount = Decimal(str(amount))
    if amount <= 0:
        return Decimal("0")
    ytd = Decimal(str(ytd_supplemental))
    tax = Decimal("0")
    if ytd >= SUPPLEMENTAL_HIGH_THRESHOLD:
        tax = amount * SUPPLEMENTAL_HIGH_RATE
    elif ytd + amount > SUPPLEMENTAL_HIGH_THRESHOLD:
        over = ytd + amount - SUPPLEMENTAL_HIGH_THRESHOLD
        tax = (amount - over) * SUPPLEMENTAL_RATE + over * SUPPLEMENTAL_HIGH_RATE
    else:
        tax = amount * SUPPLEMENTAL_RATE
    return _q(tax)


def supplemental_aggregate_tax(
    supplemental: Decimal,
    regular_wages: Decimal,
    pay_periods: int,
    filing_status: str = "single",
    multiple_jobs: bool = False,
    dependents_amount: Decimal = Decimal("0"),
    other_income_annual: Decimal = Decimal("0"),
    deductions_annual: Decimal = Decimal("0"),
) -> Decimal:
    """Aggregate-method federal withholding on supplemental wages.

    Withhold the difference between the tax on (regular + supplemental) wages
    and the tax on the regular wages alone — the alternative to the flat 22%.
    """
    supplemental = Decimal(str(supplemental))
    regular_wages = Decimal(str(regular_wages))
    if supplemental <= 0:
        return Decimal("0")
    combined = federal_income_tax(
        regular_wages + supplemental,
        pay_periods,
        filing_status,
        multiple_jobs,
        dependents_amount,
        other_income_annual,
        deductions_annual,
    )
    base = federal_income_tax(
        regular_wages,
        pay_periods,
        filing_status,
        multiple_jobs,
        dependents_amount,
        other_income_annual,
        deductions_annual,
    )
    return _q(max(Decimal("0"), combined - base))


def _capped_wages(gross: Decimal, ytd_gross: Decimal, wage_base: Decimal) -> Decimal:
    """Portion of `gross` that is still below an annual wage-base cap."""
    if ytd_gross >= wage_base:
        return Decimal("0")
    if ytd_gross + gross > wage_base:
        return wage_base - ytd_gross
    return gross


def social_security(gross: Decimal, ytd_gross: Decimal = Decimal("0")) -> tuple:
    """Return (employee_ss, employer_ss) honouring the annual wage-base cap."""
    taxable = _capped_wages(Decimal(str(gross)), Decimal(str(ytd_gross)), SS_WAGE_BASE)
    amt = _q(taxable * SS_RATE)
    return amt, amt


def medicare(gross: Decimal, ytd_gross: Decimal = Decimal("0")) -> tuple:
    """Return (employee_medicare, employer_medicare).

    Employee Medicare includes the Additional Medicare Tax (0.9%) on wages
    above $200,000; the employer share never includes the additional tax.
    """
    gross = Decimal(str(gross))
    ytd_gross = Decimal(str(ytd_gross))
    base = _q(gross * MEDICARE_RATE)
    employee = base
    annual = ytd_gross + gross
    if annual > MEDICARE_ADDITIONAL_THRESHOLD:
        if ytd_gross >= MEDICARE_ADDITIONAL_THRESHOLD:
            extra_wages = gross
        else:
            extra_wages = annual - MEDICARE_ADDITIONAL_THRESHOLD
        employee += _q(extra_wages * MEDICARE_ADDITIONAL_RATE)
    return employee, base


def futa(gross: Decimal, ytd_gross: Decimal = Decimal("0")) -> Decimal:
    """Employer FUTA tax (effective 0.6% on the first $7,000 of wages)."""
    taxable = _capped_wages(
        Decimal(str(gross)), Decimal(str(ytd_gross)), FUTA_WAGE_BASE
    )
    return _q(taxable * FUTA_EFFECTIVE_RATE)


def suta(
    gross: Decimal, ytd_gross: Decimal, rate: Decimal, wage_base: Decimal
) -> Decimal:
    """Employer state unemployment tax on wages below the state wage base."""
    taxable = _capped_wages(
        Decimal(str(gross)), Decimal(str(ytd_gross)), Decimal(str(wage_base))
    )
    return _q(taxable * Decimal(str(rate)))


def calculate_withholdings(
    gross_pay,
    *,
    pay_frequency="biweekly",
    filing_status: str = "single",
    multiple_jobs: bool = False,
    dependents_amount=Decimal("0"),
    other_income_annual=Decimal("0"),
    deductions_annual=Decimal("0"),
    extra_withholding=Decimal("0"),
    ytd_gross=Decimal("0"),
    work_state: str = "WA",
    withholding_state: str = None,
    wc_class_code: str = None,
    hours=Decimal("0"),
    pretax_deductions=Decimal("0"),
    pretax_fica=Decimal("0"),
    supplemental: bool = False,
    supplemental_method: str = "flat",
    regular_wages=Decimal("0"),
    suta_rate: Decimal = None,
) -> dict:
    """Compute a full set of payroll taxes for one employee for one pay period.

    ``pretax_deductions`` is the total of pre-tax deductions that reduce
    income-tax wages; ``pretax_fica`` is the subset of those that ALSO reduce
    FICA wages (Section 125 cafeteria plans, HSA) — a traditional 401(k)
    reduces income tax but not FICA, so it belongs only in pretax_deductions.

    Returns employee-side withholding, employer-side taxes, the per-state
    results, and an itemized ``detail`` map for pay-stub / form rendering.
    """
    from app.config import SUTA_RATE

    gross = _q(gross_pay)
    ytd = Decimal(str(ytd_gross))
    pretax = Decimal(str(pretax_deductions))
    pretax_fica_amt = min(Decimal(str(pretax_fica)), pretax)
    pay_periods = periods_per_year(pay_frequency)

    if gross <= 0:
        zero = Decimal("0")
        return {
            "gross": zero,
            "federal": zero,
            "ss": zero,
            "medicare": zero,
            "state_income": zero,
            "state_other_employee": zero,
            "employer_ss": zero,
            "employer_medicare": zero,
            "futa": zero,
            "suta": zero,
            "state_other_employer": zero,
            "total_employee_tax": zero,
            "total_employer_tax": zero,
            "net": zero,
            "detail": {},
        }

    # Income-tax wages drop the full pre-tax total; FICA wages drop only the
    # cafeteria-plan / HSA subset.
    fed_taxable = max(Decimal("0"), gross - pretax)
    fica_wages = max(Decimal("0"), gross - pretax_fica_amt)

    # --- Federal income tax ---
    if supplemental:
        if supplemental_method == "aggregate":
            federal = supplemental_aggregate_tax(
                fed_taxable,
                regular_wages,
                pay_periods,
                filing_status,
                multiple_jobs,
                dependents_amount,
                other_income_annual,
                deductions_annual,
            )
        else:
            federal = supplemental_federal_tax(fed_taxable)
    else:
        federal = federal_income_tax(
            fed_taxable,
            pay_periods,
            filing_status,
            multiple_jobs,
            dependents_amount,
            other_income_annual,
            deductions_annual,
            extra_withholding,
        )

    # --- FICA (on FICA wages — Section 125 / HSA reduce these) ---
    ss_emp, ss_empr = social_security(fica_wages, ytd)
    med_emp, med_empr = medicare(fica_wages, ytd)

    # --- Employer unemployment taxes ---
    futa_tax = futa(fica_wages, ytd)

    # --- State engine ---
    # The work-state engine drives SUTA situs and state disability/leave
    # premiums; income tax may instead follow the residence state under a
    # reciprocity agreement (see state_tax.reciprocity).
    engine = get_engine(work_state)
    state = engine.calculate(
        gross=gross,
        taxable=fed_taxable,
        ytd_gross=ytd,
        pay_periods=pay_periods,
        hours=Decimal(str(hours)),
        filing_status=filing_status,
        wc_class_code=wc_class_code,
    )

    state_income = state.income_tax
    if (
        withholding_state
        and (work_state or "").strip().upper() != withholding_state.strip().upper()
    ):
        wh = get_engine(withholding_state).calculate(
            gross=gross,
            taxable=fed_taxable,
            ytd_gross=ytd,
            pay_periods=pay_periods,
            hours=Decimal(str(hours)),
            filing_status=filing_status,
            wc_class_code=wc_class_code,
        )
        state_income = wh.income_tax

    rate = Decimal(str(suta_rate)) if suta_rate is not None else Decimal(str(SUTA_RATE))
    suta_tax = suta(fica_wages, ytd, rate, engine.suta_wage_base)

    total_employee = federal + state_income + state.employee_other + ss_emp + med_emp
    total_employer = ss_empr + med_empr + futa_tax + suta_tax + state.employer_other
    net = gross - total_employee - pretax

    detail = {
        "federal_income_tax": federal,
        "social_security_employee": ss_emp,
        "medicare_employee": med_emp,
        "state_income_tax": state_income,
        "pretax_deductions": _q(pretax),
        "employer_social_security": ss_empr,
        "employer_medicare": med_empr,
        "futa": futa_tax,
        "suta": suta_tax,
    }
    detail.update(state.detail)
    detail["state_income_tax"] = state_income

    return {
        "gross": gross,
        "federal": federal,
        "ss": ss_emp,
        "medicare": med_emp,
        "state_income": state_income,
        "state_other_employee": state.employee_other,
        "employer_ss": ss_empr,
        "employer_medicare": med_empr,
        "futa": futa_tax,
        "suta": suta_tax,
        "state_other_employer": state.employer_other,
        "total_employee_tax": _q(total_employee),
        "total_employer_tax": _q(total_employer),
        "net": _q(net),
        "detail": detail,
    }
