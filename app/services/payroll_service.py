# ============================================================================
# Payroll Service — federal withholding (IRS Pub 15-T) + FICA + employer taxes
# ----------------------------------------------------------------------------
# Federal income tax withholding follows Pub 15-T Worksheet 1A (Percentage
# Method for Automated Payroll Systems) using the 2020+ redesigned Form W-4 —
# there are no "allowances" anymore. Supplemental wages use Worksheet 4 (flat).
#
# Sources for the figures below (verified against the publications 2026-10-31):
#   * IRS Publication 15-T (2026), Federal Income Tax Withholding Methods,
#     Dec. 2025 (Cat. No. 32112B) — Worksheet 1A and the "2026 Percentage
#     Method Tables for Automated Payroll Systems", pages 11-12.
#     https://www.irs.gov/pub/irs-pdf/p15t.pdf
#   * IRS Publication 15 (2026), (Circular E) Employer's Tax Guide, Dec. 15,
#     2025 (Cat. No. 10000W) — Social Security wage base, supplemental-wage
#     rates (section 7), FUTA rate and credit (section 14).
#     https://www.irs.gov/pub/irs-pdf/p15.pdf
#   * SSA, "Contribution and Benefit Base" (2027 base announced 2026-10-14).
#     https://www.ssa.gov/oact/cola/cbb.html
# ============================================================================

from decimal import Decimal

from app.models.payroll import periods_per_year
from app.services.accounting import _q
from app.services.state_tax import get_engine

# --- FICA -------------------------------------------------------------------
SS_RATE = Decimal("0.062")  # Social Security 6.2% (employee & employer each)
# The Social Security wage base changes every year (SSA announces the next
# year's in October). A pay run, an opening balance and a form use the base
# for their own year: ss_wage_base(year).
# 2026: Pub 15 (2026) "What's New" — "The social security wage base limit is
# $184,500." 2027: SSA Contribution and Benefit Base page, $192,300
# (https://www.ssa.gov/oact/cola/cbb.html, announced with the 2027 COLA on
# 2026-10-14).
SS_WAGE_BASES = {
    2023: Decimal("160200"),
    2024: Decimal("168600"),
    2025: Decimal("176100"),
    2026: Decimal("184500"),
    2027: Decimal("192300"),
}
# The current year's base: the fallback when a caller gives no year, and the
# single base every form used before per-year bases (2.29.0), which the
# Form history "legacy" check still measures against. Next year's base
# lives only in the table above until the year turns.
SS_WAGE_BASE = SS_WAGE_BASES[2026]
MEDICARE_RATE = Decimal("0.0145")  # Medicare 1.45% (employee & employer each)
MEDICARE_ADDITIONAL_RATE = Decimal("0.009")  # extra 0.9% (employee only)
MEDICARE_ADDITIONAL_THRESHOLD = Decimal("200000")  # withhold the extra above this

# --- Federal unemployment (FUTA) -------------------------------------------
# $7,000 by statute (26 U.S.C. 3306(b)(1)) since 1983: the same every year.
# Pub 15 (2026) section 14: "For 2026, the FUTA tax rate is 6.0%" and the
# credit for state unemployment tax "may be as much as 5.4%", so 0.6% net.
# A credit-reduction state's extra FUTA is figured on the annual Form 940
# (tax_forms/form_940.py), not on the paycheck.
FUTA_WAGE_BASE = Decimal("7000")
FUTA_EFFECTIVE_RATE = Decimal("0.006")  # 6.0% gross less the standard 5.4% state credit

# --- Pub 15-T Worksheet 1A -------------------------------------------------
# Line 1g standard-deduction add-back (skipped when the Step 2 box is checked).
# Pub 15-T (2026) Worksheet 1A line 1g: "enter $12,900 if the taxpayer is
# married filing jointly or $8,600 otherwise" — unchanged for 2026.
STD_DEDUCTION_ADDBACK = {
    "married": Decimal("12900"),
    "single": Decimal("8600"),
    "head_of_household": Decimal("8600"),
}

# Percentage Method annual schedules as (lower_bound, marginal_rate) pairs,
# ascending. Cumulative tax is derived in _tax_from_brackets so the schedules
# stay internally consistent regardless of transcription; the derived
# cumulative amounts equal column C of the published tables to the cent.
#
# "2026 Percentage Method Tables for Automated Payroll Systems and
# Withholding on Periodic Payments of Pensions and Annuities" — STANDARD
# Withholding Rate Schedules (Form W-4 from 2019 or earlier, or 2020+ with
# the Step 2 box NOT checked), Pub 15-T (2026) page 12.
_STANDARD = {
    "single": [  # Single or Married Filing Separately
        (Decimal("0"), Decimal("0")),
        (Decimal("7500"), Decimal("0.10")),
        (Decimal("19900"), Decimal("0.12")),
        (Decimal("57900"), Decimal("0.22")),
        (Decimal("113200"), Decimal("0.24")),
        (Decimal("209275"), Decimal("0.32")),
        (Decimal("263725"), Decimal("0.35")),
        (Decimal("648100"), Decimal("0.37")),
    ],
    "married": [  # Married Filing Jointly
        (Decimal("0"), Decimal("0")),
        (Decimal("19300"), Decimal("0.10")),
        (Decimal("44100"), Decimal("0.12")),
        (Decimal("120100"), Decimal("0.22")),
        (Decimal("230700"), Decimal("0.24")),
        (Decimal("422850"), Decimal("0.32")),
        (Decimal("531750"), Decimal("0.35")),
        (Decimal("788000"), Decimal("0.37")),
    ],
    "head_of_household": [
        (Decimal("0"), Decimal("0")),
        (Decimal("15550"), Decimal("0.10")),
        (Decimal("33250"), Decimal("0.12")),
        (Decimal("83000"), Decimal("0.22")),
        (Decimal("121250"), Decimal("0.24")),
        (Decimal("217300"), Decimal("0.32")),
        (Decimal("271750"), Decimal("0.35")),
        (Decimal("656150"), Decimal("0.37")),
    ],
}

# "Form W-4, Step 2, Checkbox, Withholding Rate Schedules" — used when the
# employee checked the multiple-jobs box (Pub 15-T (2026) page 12, right
# half). Roughly the standard schedule with the brackets halved.
_CHECKBOX = {
    "single": [  # Single or Married Filing Separately
        (Decimal("0"), Decimal("0")),
        (Decimal("8050"), Decimal("0.10")),
        (Decimal("14250"), Decimal("0.12")),
        (Decimal("33250"), Decimal("0.22")),
        (Decimal("60900"), Decimal("0.24")),
        (Decimal("108938"), Decimal("0.32")),
        (Decimal("136163"), Decimal("0.35")),
        (Decimal("328350"), Decimal("0.37")),
    ],
    "married": [  # Married Filing Jointly
        (Decimal("0"), Decimal("0")),
        (Decimal("16100"), Decimal("0.10")),
        (Decimal("28500"), Decimal("0.12")),
        (Decimal("66500"), Decimal("0.22")),
        (Decimal("121800"), Decimal("0.24")),
        (Decimal("217875"), Decimal("0.32")),
        (Decimal("272325"), Decimal("0.35")),
        (Decimal("400450"), Decimal("0.37")),
    ],
    "head_of_household": [
        (Decimal("0"), Decimal("0")),
        (Decimal("12075"), Decimal("0.10")),
        (Decimal("20925"), Decimal("0.12")),
        (Decimal("45800"), Decimal("0.22")),
        (Decimal("64925"), Decimal("0.24")),
        (Decimal("112950"), Decimal("0.32")),
        (Decimal("140175"), Decimal("0.35")),
        (Decimal("332375"), Decimal("0.37")),
    ],
}

# Supplemental wage withholding (Pub 15 (2026) section 7 / Pub 15-T
# Worksheet 4): "The withholding rate on supplemental wages remains 22% (37%
# if supplemental wages paid to an employee during the calendar year exceed
# $1 million)".
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


def has_wage_base_table(year: int | None) -> bool:
    """Whether SlowBooks has the year's Social Security wage base."""
    return year in SS_WAGE_BASES


def ss_wage_base(year: int | None = None) -> Decimal:
    """The Social Security wage base for `year`. A year SlowBooks has no
    base for uses the nearest one it has (a pay run in January, before the
    release with the new year's base, still runs); wage_base_note says so."""
    if year is None or year in SS_WAGE_BASES:
        return SS_WAGE_BASES.get(year, SS_WAGE_BASE)
    nearest = max(SS_WAGE_BASES) if year > max(SS_WAGE_BASES) else min(SS_WAGE_BASES)
    return SS_WAGE_BASES[nearest]


# The year the withholding schedules and state tables in this build are for.
# Only the Social Security wage base is kept per year (SS_WAGE_BASES); a pay
# run dated in another year is figured with these and says so.
TABLES_YEAR = 2026


def tables_year_note(year: int | None) -> str | None:
    """The sentence a pay run carries when its pay date is not in the year
    the federal schedules and state tables are for; None when it is."""
    if year is None or year == TABLES_YEAR:
        return None
    # true of a run made by an earlier version too: each version keeps one
    # year's tables and figures every run with them, whatever its pay date
    return (
        f"This run is dated {year}. SlowBooks keeps one year's federal "
        f"schedules, state tables and leave rates ({TABLES_YEAR}'s in this "
        f"version) and figures every run with them, whatever its pay date; "
        f"only the Social Security wage base is {year}'s own. Check the "
        f"figures against {year}'s publications before relying on them."
    )


def wage_base_note(year: int | None) -> str | None:
    """The sentence a pay run or an opening balance carries when its year
    has no Social Security wage base here; None when it has one."""
    if year is None or has_wage_base_table(year):
        return None
    nearest = max(SS_WAGE_BASES) if year > max(SS_WAGE_BASES) else min(SS_WAGE_BASES)
    return (
        f"SlowBooks has the Social Security wage base for {min(SS_WAGE_BASES)} "
        f"through {max(SS_WAGE_BASES)}, not for {year}: {year} was measured "
        f"against {nearest}'s (${SS_WAGE_BASES[nearest]:,}). Update SlowBooks "
        f"before filing {year}'s forms."
    )


def stub_taxable_wages(stub) -> dict:
    """A pay stub's wages as each tax measured them, from what the stub
    recorded: its gross less the pre-tax amounts that reduce that tax.

    "federal" (income-tax wages, W-2 box 1) and "state" (state income-tax
    wages, box 16) drop every pre-tax benefit code that reduces them plus
    the ad-hoc pre-tax amount typed on the run (which reduces both).
    "fica" (Social Security, Medicare, FUTA and SUTA wages) drops only the
    codes that reduce FICA (Section 125, HSA): a 401(k) does not. This is
    the same arithmetic calculate_withholdings did when the stub was
    figured, so the caps, the paychecks and the forms agree."""
    gross = Decimal(str(stub.gross_pay or 0))
    pretax = Decimal(str(stub.pretax_deductions or 0))
    codes = [
        b
        for b in (getattr(stub, "benefits", None) or [])
        if b.category == "pretax" and b.employee_amount
    ]
    coded = sum((Decimal(str(b.employee_amount)) for b in codes), Decimal("0"))
    adhoc = max(Decimal("0"), pretax - coded)

    def less(flag: str, extra: Decimal) -> Decimal:
        reduced = sum(
            (Decimal(str(b.employee_amount)) for b in codes if getattr(b, flag)),
            Decimal("0"),
        )
        return max(Decimal("0"), gross - reduced - extra)

    return {
        "gross": gross,
        "federal": less("reduces_federal", adhoc),
        "state": less("reduces_state", adhoc),
        "fica": less("reduces_fica", Decimal("0")),
    }


def _capped_wages(gross: Decimal, ytd_gross: Decimal, wage_base: Decimal) -> Decimal:
    """Portion of `gross` that is still below an annual wage-base cap."""
    if ytd_gross >= wage_base:
        return Decimal("0")
    if ytd_gross + gross > wage_base:
        return wage_base - ytd_gross
    return gross


def social_security(
    gross: Decimal, ytd_gross: Decimal = Decimal("0"), year: int | None = None
) -> tuple:
    """Return (employee_ss, employer_ss) honouring the annual wage-base cap
    for `year` (the newest SlowBooks has when None)."""
    taxable = _capped_wages(
        Decimal(str(gross)), Decimal(str(ytd_gross)), ss_wage_base(year)
    )
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
    pretax_state=None,
    supplemental: bool = False,
    supplemental_method: str = "flat",
    regular_wages=Decimal("0"),
    ytd_supplemental=Decimal("0"),
    suta_rate: Decimal = None,
    state_allowances: int = 0,
    state_extra_withholding=Decimal("0"),
    state_rate_override=None,
    local_tax_rate=None,
    ytd_bases: dict | None = None,
    year: int | None = None,
    employee_count: int | None = None,
    state_employee_count: int | None = None,
) -> dict:
    """Compute a full set of payroll taxes for one employee for one pay period.

    ``ytd_bases`` is what already counts toward each annual wage base
    (routes.payroll.ytd.wage_base_ytd): "ss", "medicare" (the Additional
    Medicare threshold), "futa", "suta" (the work state's own) and "state"
    (the work state's capped taxes). Any key left out is measured by
    ``ytd_gross``, as before. ``year`` is the pay date's: the Social
    Security wage base is that year's (ss_wage_base).

    ``pretax_deductions`` is the total of pre-tax deductions that reduce
    federal income-tax wages; ``pretax_fica`` is the subset of those that
    ALSO reduce FICA wages (Section 125 cafeteria plans, HSA) — a
    traditional 401(k) reduces income tax but not FICA, so it belongs only
    in pretax_deductions. ``pretax_state`` is the amount that reduces STATE
    income-tax wages (None = same as federal); the benefits engine tracks
    the three bases separately because a code can reduce any combination.

    Returns employee-side withholding, employer-side taxes, the per-state
    results, and an itemized ``detail`` map for pay-stub / form rendering.
    """
    from app.config import SUTA_RATE

    gross = _q(gross_pay)
    ytd = Decimal(str(ytd_gross))
    pretax = Decimal(str(pretax_deductions))
    pretax_fica_amt = Decimal(str(pretax_fica))
    pretax_state_amt = pretax if pretax_state is None else Decimal(str(pretax_state))
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
    state_taxable = max(Decimal("0"), gross - pretax_state_amt)
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
            # YTD supplemental wages drive the $1M/37% mandatory-flat-rate
            # tier — without them the high-earner rate can never fire.
            federal = supplemental_federal_tax(fed_taxable, ytd_supplemental)
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

    bases = {k: ytd for k in ("ss", "medicare", "futa", "suta", "state")}
    for k, v in (ytd_bases or {}).items():
        if k in bases and v is not None:
            bases[k] = Decimal(str(v))

    # --- FICA (on FICA wages — Section 125 / HSA reduce these) ---
    ss_emp, ss_empr = social_security(fica_wages, bases["ss"], year)
    med_emp, med_empr = medicare(fica_wages, bases["medicare"])

    # --- Employer unemployment taxes ---
    futa_tax = futa(fica_wages, bases["futa"])

    # --- State engine ---
    # The work-state engine drives SUTA situs and state disability/leave
    # premiums; income tax may instead follow the residence state under a
    # reciprocity agreement (see state_tax.reciprocity).
    state_kw = dict(
        state_allowances=state_allowances,
        state_extra_withholding=state_extra_withholding,
        state_rate_override=state_rate_override,
        local_tax_rate=local_tax_rate,
        # Oregon subtracts the period's federal withholding from wages
        # before its rates (150-206-436); the other engines ignore it.
        federal_withholding=federal,
        # headcounts for the leave programs' employer share: Paid Leave
        # Oregon sizes an employer by every employee (25+), WA PFML by the
        # employees working in Washington (50+)
        employee_count=employee_count,
        state_employee_count=state_employee_count,
    )
    engine = get_engine(work_state)
    state = engine.calculate(
        gross=gross,
        taxable=state_taxable,
        ytd_gross=bases["state"],
        pay_periods=pay_periods,
        hours=Decimal(str(hours)),
        filing_status=filing_status,
        wc_class_code=wc_class_code,
        **state_kw,
    )

    state_income = state.income_tax
    if (
        withholding_state
        and (work_state or "").strip().upper() != withholding_state.strip().upper()
    ):
        wh = get_engine(withholding_state).calculate(
            gross=gross,
            taxable=state_taxable,
            ytd_gross=bases["state"],
            pay_periods=pay_periods,
            hours=Decimal(str(hours)),
            filing_status=filing_status,
            wc_class_code=wc_class_code,
            **state_kw,
        )
        state_income = wh.income_tax
        # the reciprocity state's income tax replaces the work state's, and
        # its detail line should say so on the stub
        for k in list(state.detail):
            if k.endswith("income tax") and not k.endswith("local income tax"):
                state.detail.pop(k)
        for k, v in wh.detail.items():
            if k.endswith("income tax") and not k.endswith("local income tax"):
                state.detail[k] = v

    rate = Decimal(str(suta_rate)) if suta_rate is not None else Decimal(str(SUTA_RATE))
    suta_tax = suta(fica_wages, bases["suta"], rate, engine.suta_wage_base)

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
