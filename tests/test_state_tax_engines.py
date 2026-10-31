# ============================================================================
# State payroll-withholding engine coverage.
# ----------------------------------------------------------------------------
# These are pure-unit tests over app/services/state_tax/*. Every expected
# figure is hand-derived from the published bracket/rate tables in the engine
# modules so the test fails loudly if a rate is edited without intent — the
# whole point of a tax engine is that the numbers don't drift silently.
#
# All wage/period figures below assume a $2,000 biweekly check (26 periods,
# 80 hours) unless a test says otherwise.
# ============================================================================

from decimal import Decimal

import pytest

from app.services.state_tax import (
    get_engine,
    GenericStateEngine,
    WAEngine,
    CAEngine,
    NYEngine,
    OregonEngine,
)
from app.services.state_tax.base import StateEngine, StateTaxResult
from app.services.state_tax.reciprocity import (
    has_reciprocity,
    withholding_state,
)


def _calc(engine, **overrides):
    """Run an engine with sensible defaults; override per-test."""
    kwargs = dict(
        gross=Decimal("2000"),
        taxable=Decimal("2000"),
        ytd_gross=Decimal("0"),
        pay_periods=26,
        hours=Decimal("80"),
        filing_status="single",
        wc_class_code="5206",
    )
    kwargs.update(overrides)
    return engine.calculate(**kwargs)


# ---------------------------------------------------------------------------
# Abstract base contract
# ---------------------------------------------------------------------------
def test_base_engine_is_a_zero_noop():
    # The un-subclassed StateEngine returns an empty result and exposes the
    # default SUTA base — concrete engines must override calculate().
    r = _calc(StateEngine())
    assert r == StateTaxResult()
    assert StateEngine.suta_wage_base == Decimal("9000")


def test_high_income_walks_into_top_open_ended_bracket():
    # A very large married check exercises the final open-ended bracket
    # (upper is None) in the progressive walk for every income-tax state.
    for state in ("CA", "NY", "OR"):
        r = _calc(
            get_engine(state),
            gross=Decimal("100000"),
            taxable=Decimal("100000"),
            filing_status="married",
        )
        assert r.income_tax > Decimal("0")


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code,cls",
    [
        ("WA", WAEngine),
        ("wa", WAEngine),
        (" Ca ", CAEngine),
        ("NY", NYEngine),
        ("or", OregonEngine),
    ],
)
def test_get_engine_resolves_known_states_case_insensitively(code, cls):
    assert isinstance(get_engine(code), cls)


@pytest.mark.parametrize("code", [None, "", "ZZ", "XX", "  "])
def test_get_engine_falls_back_to_generic(code):
    assert isinstance(get_engine(code), GenericStateEngine)


def test_engine_singletons_are_reused():
    # Registry holds module-level singletons — same object every lookup.
    assert get_engine("CA") is get_engine("ca")


# ---------------------------------------------------------------------------
# Zero / non-positive wages short-circuit everywhere
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["WA", "CA", "NY", "OR", "ZZ"])
@pytest.mark.parametrize("gross,taxable", [("0", "0"), ("0", "2000"), ("2000", "0")])
def test_nonpositive_wages_produce_empty_result(state, gross, taxable):
    r = _calc(get_engine(state), gross=Decimal(gross), taxable=Decimal(taxable))
    assert r.income_tax == Decimal("0")
    assert r.employee_other == Decimal("0")
    assert r.employer_other == Decimal("0")
    assert r.detail == {}


# ---------------------------------------------------------------------------
# Washington — no income tax, three wage/hour assessments
# ---------------------------------------------------------------------------
def test_wa_has_no_income_tax():
    assert _calc(get_engine("WA")).income_tax == Decimal("0.00")


def test_wa_pfml_split_71_43_28_57():
    # 2026 premium 1.13% (ESD release 2025-10-29): 2000 * 0.0113 = 22.60;
    # employee 71.43% = 16.143 -> 16.14; employer 28.57% = 6.457 -> 6.46.
    r = _calc(get_engine("WA"))
    assert r.detail["WA PFML (employee)"] == Decimal("16.14")
    assert r.detail["WA PFML (employer)"] == Decimal("6.46")


def test_wa_pfml_stops_at_the_social_security_wage_base():
    # Premiums apply to gross "up to the 2026 Social Security cap ($184,500)".
    # 1,000 of room left: 1000 * 0.0113 = 11.30 -> employee 8.07, employer 3.23;
    # none left once ytd gross reaches the cap. WA Cares has no cap.
    r = _calc(get_engine("WA"), ytd_gross=Decimal("183500"))
    assert r.detail["WA PFML (employee)"] == Decimal("8.07")  # 11.30*0.7143
    assert r.detail["WA PFML (employer)"] == Decimal("3.23")  # 11.30*0.2857
    capped = _calc(get_engine("WA"), ytd_gross=Decimal("184500"))
    assert capped.detail["WA PFML (employee)"] == Decimal("0.00")
    assert capped.detail["WA PFML (employer)"] == Decimal("0.00")
    assert capped.detail["WA Cares"] == Decimal("11.60")


def test_wa_cares_is_058_percent_of_gross_employee_only():
    r = _calc(get_engine("WA"))
    assert r.detail["WA Cares"] == Decimal("11.60")  # 2000 * 0.0058


def test_wa_lni_scales_with_hours_not_wages():
    # L&I is assessed per hour, so doubling hours doubles the L&I line while
    # PFML/Cares (wage-based) stay put. Class 5206 (2026 base rates): worker
    # deduction 0.21480/hr -> 80 h = 17.184 -> 17.18, 160 h = 34.368 -> 34.37;
    # employer share (0.9828 - 0.2148) = 0.7680/hr -> 61.44 and 122.88.
    base = _calc(get_engine("WA"), hours=Decimal("80"))
    dbl = _calc(get_engine("WA"), hours=Decimal("160"))
    assert base.detail["WA L&I (employee)"] == Decimal("17.18")
    assert dbl.detail["WA L&I (employee)"] == Decimal("34.37")
    assert base.detail["WA L&I (employer)"] == Decimal("61.44")
    assert dbl.detail["WA L&I (employer)"] == Decimal("122.88")
    assert dbl.detail["WA Cares"] == base.detail["WA Cares"]


def test_wa_lni_2026_base_rates_by_class():
    # L&I "2026 Hourly Rates by Risk Classification Code and Fund", composite
    # rate / payroll deduction at experience factor 1.0: 4904 clerical office
    # 0.2223 / 0.10070; 0510 wood frame building 4.6938 / 0.78965.
    from app.seed.wa_lni_rates import get_lni_rate

    assert get_lni_rate("4904") == {
        "total": Decimal("0.2223"),
        "employee": Decimal("0.10070"),
    }
    assert get_lni_rate("0510") == {
        "total": Decimal("4.6938"),
        "employee": Decimal("0.78965"),
    }
    # 0540 is a per-square-foot wallboard class, not an hourly one: gone.
    assert get_lni_rate("0540") == get_lni_rate(None)


def test_wa_employee_other_is_sum_of_employee_lines():
    r = _calc(get_engine("WA"))
    expected = (
        r.detail["WA PFML (employee)"]
        + r.detail["WA Cares"]
        + r.detail["WA L&I (employee)"]
    )
    assert r.employee_other == expected


def test_wa_suta_wage_base():
    assert WAEngine.suta_wage_base == Decimal("78200")


# ---------------------------------------------------------------------------
# California — progressive income tax + uncapped SDI
# ---------------------------------------------------------------------------
def test_ca_income_tax_single_progressive():
    # EDD Method B 2026, single, 0 allowances: annual = 52000 (over the
    # 18,896 low income exemption), less 5,706 std ded (Table 3) = 46,294.
    # Table 5:
    #   0–11079      @1.10%  = 121.869
    #   11079–26264  @2.20%  = 334.070
    #   26264–41452  @4.40%  = 668.272
    #   41452–46294  @6.60%  = 319.572
    #   total = 1443.783, less 0 x 168.30 credit; / 26 = 55.53
    assert _calc(get_engine("CA")).detail["CA income tax"] == Decimal("55.53")


def test_ca_exemption_allowance_credit_per_allowance():
    # Table 4: $168.30 a year per allowance comes off the computed tax.
    # 1443.783 - 168.30 = 1275.483 / 26 = 49.06
    r = _calc(get_engine("CA"), state_allowances=1)
    assert r.detail["CA income tax"] == Decimal("49.06")


def test_ca_matches_edd_method_b_examples():
    # Annual figures against the published tables (annual payroll period):
    # single $50,000, 1 allowance: 50,000 - 5,706 = 44,294;
    #   121.869 + 334.070 + 668.272 + (44,294 - 41,452) x 6.6% = 187.572
    #   = 1,311.783 - 168.30 = 1,143.48
    ca = get_engine("CA")
    single = _calc(
        ca,
        gross=Decimal("50000"),
        taxable=Decimal("50000"),
        pay_periods=1,
        state_allowances=1,
    )
    assert single.detail["CA income tax"] == Decimal("1143.48")
    # married $100,000, 2 allowances: 2+ allowances -> 11,412 std ded,
    # 88,588 taxable on Table 6: 22,158 x 1.1% = 243.738; 30,370 x 2.2% =
    # 668.14; 30,376 x 4.4% = 1,336.544; (88,588 - 82,904) x 6.6% = 375.144
    # = 2,623.566 - 336.60 = 2,286.97
    married = _calc(
        ca,
        gross=Decimal("100000"),
        taxable=Decimal("100000"),
        pay_periods=1,
        filing_status="married",
        state_allowances=2,
    )
    assert married.detail["CA income tax"] == Decimal("2286.97")


def test_ca_head_of_household_has_its_own_schedule():
    # HOH takes the 11,412 std ded and Table 7, so owes less than single.
    hoh = _calc(get_engine("CA"), filing_status="head_of_household")
    single = _calc(get_engine("CA"), filing_status="single")
    # 52,000 - 11,412 = 40,588: 22,173 x 1.1% = 243.903; 18,415 x 2.2% =
    # 405.13 = 649.033 / 26 = 24.96
    assert hoh.detail["CA income tax"] == Decimal("24.96")
    assert hoh.income_tax < single.income_tax


def test_ca_top_bracket_is_14_63_percent_over_one_million():
    # Table 5 last row: 14.63% over $1,000,000 (annual pay period). Tax on
    # 1,100,000 - 5,706 = 1,094,294: 114,220.27 on the first million (the
    # table's "Plus" figure) + 94,294 x 14.63% = 13,795.21 = 128,015.48.
    r = _calc(
        get_engine("CA"),
        gross=Decimal("1100000"),
        taxable=Decimal("1100000"),
        pay_periods=1,
    )
    assert r.detail["CA income tax"] == Decimal("128015.48")


def test_ca_low_income_exemption_withholds_nothing():
    # Table 1: annual wages at or under $18,896 (single) -> no income tax,
    # even though 18,896 is above the 5,706 standard deduction.
    r = _calc(
        get_engine("CA"), gross=Decimal("1500"), taxable=Decimal("1500"), pay_periods=12
    )
    assert r.detail["CA income tax"] == Decimal("0.00")
    over = _calc(
        get_engine("CA"), gross=Decimal("1600"), taxable=Decimal("1600"), pay_periods=12
    )
    assert over.detail["CA income tax"] > Decimal("0")


def test_ca_sdi_is_uncapped_1_3_percent():
    # DE 44 Rev. 52: 2026 SDI rate 1.3%, no taxable wage limit since 2024.
    r = _calc(get_engine("CA"), gross=Decimal("2000"))
    assert r.detail["CA SDI"] == Decimal("26.00")
    big = _calc(get_engine("CA"), gross=Decimal("50000"), taxable=Decimal("50000"))
    assert big.detail["CA SDI"] == Decimal("650.00")  # still 1.3%, no cap


def test_ca_low_wage_under_deduction_yields_zero_income_tax():
    # taxable*26 below the standard deduction → no income tax, SDI still applies.
    r = _calc(get_engine("CA"), gross=Decimal("100"), taxable=Decimal("100"))
    assert r.detail["CA income tax"] == Decimal("0.00")
    assert r.detail["CA SDI"] == Decimal("1.30")


def test_ca_married_bracket_is_wider_than_single():
    single = _calc(get_engine("CA"), filing_status="single")
    married = _calc(get_engine("CA"), filing_status="married")
    # Same wages, wider married brackets → married owes no more than single.
    assert married.income_tax <= single.income_tax


def test_ca_unknown_filing_status_defaults_to_single():
    single = _calc(get_engine("CA"), filing_status="single")
    bogus = _calc(get_engine("CA"), filing_status="martian")
    assert bogus.income_tax == single.income_tax


# ---------------------------------------------------------------------------
# New York — income tax + capped SDI + PFL with annual max
# ---------------------------------------------------------------------------
def test_ny_sdi_hits_weekly_cap():
    # 0.5% of 2000 = 10.00, but the 0.60/wk cap → 0.60*52/26 = 1.20 per period.
    assert _calc(get_engine("NY")).detail["NY SDI"] == Decimal("1.20")


def test_ny_pfl_within_annual_max():
    # 2026: 0.432% of gross — 2000 * 0.00432 = 8.64, under the 411.91 max.
    assert _calc(get_engine("NY")).detail["NY PFL"] == Decimal("8.64")


def test_ny_pfl_caps_at_annual_max():
    # With ytd_gross already past the PFL max wage, the remaining premium is 0.
    # PFL_ANNUAL_MAX / PFL_RATE = 411.91 / 0.00432 ≈ 95,350 of wages exhausts it.
    r = _calc(get_engine("NY"), ytd_gross=Decimal("200000"))
    assert r.detail["NY PFL"] == Decimal("0.00")


def test_ny_income_tax_single_progressive():
    # NYS-50-T-NYS (1/26) Method II, single, 0 exemptions: annual = 52000,
    # less the 7,400 deduction allowance (Table B) = 44,600 net wages.
    # Annual schedule line 4 (13,900 – 80,650): (44,600 - 13,900) x 0.0540
    # = 1,657.80 + 586.00 = 2,243.80 / 26 = 86.30
    assert _calc(get_engine("NY")).detail["NY income tax"] == Decimal("86.30")


def test_ny_exemptions_are_1000_each():
    # Table C: $1,000 a year per exemption. 1 exemption: net 43,600 ->
    # (43,600 - 13,900) x 0.054 = 1,603.80 + 586 = 2,189.80 / 26 = 84.22
    r = _calc(get_engine("NY"), state_allowances=1)
    assert r.detail["NY income tax"] == Decimal("84.22")


def test_ny_married_schedule_and_recapture_rows():
    # Married $100,000, 2 exemptions, annual: 100,000 - 7,950 - 2,000 =
    # 90,050 net; married line 5 (80,650 – 96,800): (90,050 - 80,650) x
    # 0.0590 = 554.60 + 4,190 = 4,744.60
    r = _calc(
        get_engine("NY"),
        gross=Decimal("100000"),
        taxable=Decimal("100000"),
        pay_periods=1,
        filing_status="married",
        state_allowances=2,
    )
    assert r.detail["NY income tax"] == Decimal("4744.60")
    # Single $230,000 lands in the 11.44% recapture row (215,400 – 265,400):
    # net 222,600; (222,600 - 215,400) x 0.1144 = 823.68 + 13,369 = 14,192.68
    hi = _calc(
        get_engine("NY"),
        gross=Decimal("230000"),
        taxable=Decimal("230000"),
        pay_periods=1,
    )
    assert hi.detail["NY income tax"] == Decimal("14192.68")


def test_ny_method_iii_flat_rate_over_the_top_threshold():
    # Net wages of $1,077,550 and over (single) use Method III: annualized
    # net wages x 0.1045. Gross 1,207,400 - 7,400 = 1,200,000 x 0.1045 =
    # 125,400.00
    r = _calc(
        get_engine("NY"),
        gross=Decimal("1207400"),
        taxable=Decimal("1207400"),
        pay_periods=1,
    )
    assert r.detail["NY income tax"] == Decimal("125400.00")


def test_ny_suta_wage_base_2026():
    assert NYEngine.suta_wage_base == Decimal("17600")


# ---------------------------------------------------------------------------
# Oregon — income tax + flat statewide transit tax
# ---------------------------------------------------------------------------
def test_or_transit_tax_flat_0_1_percent():
    assert _calc(get_engine("OR")).detail["OR statewide transit tax"] == Decimal(
        "2.00"
    )  # 2000 * 0.001


def test_or_paid_leave_split_60_40_to_the_social_security_base():
    # Paid Leave Oregon 2026: 1% of gross up to $184,500; employees 60%
    # (2000 x 0.006 = 12.00), employer 40% (2000 x 0.004 = 8.00).
    r = _calc(get_engine("OR"))
    assert r.detail["OR Paid Leave (employee)"] == Decimal("12.00")
    assert r.detail["OR Paid Leave (employer)"] == Decimal("8.00")
    assert r.employee_other == Decimal("12.00") + Decimal("2.00")  # + transit
    assert r.employer_other == Decimal("8.00")
    # 500 of room left under the cap: 500 x 0.006 = 3.00 / 500 x 0.004 = 2.00
    near = _calc(get_engine("OR"), ytd_gross=Decimal("184000"))
    assert near.detail["OR Paid Leave (employee)"] == Decimal("3.00")
    assert near.detail["OR Paid Leave (employer)"] == Decimal("2.00")
    capped = _calc(get_engine("OR"), ytd_gross=Decimal("184500"))
    assert capped.detail["OR Paid Leave (employee)"] == Decimal("0.00")
    assert capped.detail["OR Paid Leave (employer)"] == Decimal("0.00")
    assert capped.detail["OR statewide transit tax"] == Decimal("2.00")  # no cap


def test_ny_and_or_low_wage_under_deduction_yield_zero_income_tax():
    # taxable*26 below the standard deduction → income tax floors at 0 while
    # the wage-based premiums (NY SDI/PFL, OR transit) still apply.
    ny = _calc(get_engine("NY"), gross=Decimal("100"), taxable=Decimal("100"))
    assert ny.detail["NY income tax"] == Decimal("0.00")
    assert ny.detail["NY PFL"] > Decimal("0")
    # Oregon's under-$50,000 formula is 263 + BASE x 4.75% - 263 x allowances,
    # so with one allowance a wage below the deduction withholds nothing.
    orr = _calc(
        get_engine("OR"),
        gross=Decimal("100"),
        taxable=Decimal("100"),
        state_allowances=1,
    )
    assert orr.detail["OR income tax"] == Decimal("0.00")
    assert orr.detail["OR statewide transit tax"] == Decimal("0.10")


def test_or_zero_allowances_under_50k_carry_the_263():
    # 150-206-436 page 6: WH = 263 + [BASE x 0.0475] - (263 x allowances);
    # with 0 allowances a $100 check (2,600/yr, BASE below 0) withholds
    # 263 / 26 = 10.12 a period.
    orr = _calc(get_engine("OR"), gross=Decimal("100"), taxable=Decimal("100"))
    assert orr.detail["OR income tax"] == Decimal("10.12")


def test_or_income_tax_single_progressive():
    # 150-206-436 (Rev. 12-31-25), single, 0 allowances, no federal
    # withholding passed: wages 52,000 (>= 50,000, so no +263);
    # BASE = 52,000 - 0 - 2,910 = 49,090; row 11,400 – 125,000:
    #   678 + (49,090 - 11,400) x 0.0875 = 678 + 3,297.875 = 3,975.875
    #   / 26 = 152.92
    assert _calc(get_engine("OR")).detail["OR income tax"] == Decimal("152.92")


def test_or_subtracts_federal_withholding_from_wages():
    # The pay run passes the period's federal withholding; Oregon subtracts
    # the annualized amount (up to 8,750). Federal 156.15 x 26 = 4,059.90:
    # BASE = 52,000 - 4,059.90 - 2,910 = 45,030.10;
    #   678 + (45,030.10 - 11,400) x 0.0875 = 678 + 2,942.63375 = 3,620.63375
    #   / 26 = 139.26
    r = _calc(get_engine("OR"), federal_withholding=Decimal("156.15"))
    assert r.detail["OR income tax"] == Decimal("139.26")
    # The DOR's own example shape: single $50,000, 1 allowance, federal
    # withheld $3,820 for the year -> BASE 50,000 - 3,820 - 2,910 = 43,270;
    #   678 + (43,270 - 11,400) x 0.0875 = 3,466.625 - 263 = 3,203.625 -> 3,203.63
    annual = _calc(
        get_engine("OR"),
        gross=Decimal("50000"),
        taxable=Decimal("50000"),
        pay_periods=1,
        state_allowances=1,
        federal_withholding=Decimal("3820"),
    )
    assert annual.detail["OR income tax"] == Decimal("3203.63")


def test_or_federal_subtraction_is_capped_and_phased_out():
    # Capped at 8,750 under $125,000 of wages: single $100,000 with 20,000
    # of federal withholding -> BASE 100,000 - 8,750 - 2,910 = 88,340;
    #   678 + (88,340 - 11,400) x 0.0875 = 678 + 6,732.25 = 7,410.25
    capped = _calc(
        get_engine("OR"),
        gross=Decimal("100000"),
        taxable=Decimal("100000"),
        pay_periods=1,
        federal_withholding=Decimal("20000"),
    )
    assert capped.detail["OR income tax"] == Decimal("7410.25")
    # $132,000 of wages, single, 4 allowances (the DOR's Example 3 shape):
    # the cap steps down to 5,250 (130,000 – 135,000) and allowances count
    # as 0 over $100,000, but "single with 3 or more allowances" keeps the
    # married deduction and rates: BASE 132,000 - 5,250 - 5,820 = 120,930;
    # 1,357 + (120,930 - 22,800) x 0.0875 = 1,357 + 8,586.375 = 9,943.38
    phased = _calc(
        get_engine("OR"),
        gross=Decimal("132000"),
        taxable=Decimal("132000"),
        pay_periods=1,
        state_allowances=4,
        federal_withholding=Decimal("21098"),
    )
    assert phased.detail["OR income tax"] == Decimal("9943.38")


def test_or_married_and_single_three_allowances_share_the_married_formula():
    # Married $80,000, 2 allowances, no federal: BASE 80,000 - 5,820 =
    # 74,180; row 22,800 – 250,000: 1,357 + (74,180 - 22,800) x 0.0875 =
    # 1,357 + 4,495.75 = 5,852.75 - 526 = 5,326.75
    married = _calc(
        get_engine("OR"),
        gross=Decimal("80000"),
        taxable=Decimal("80000"),
        pay_periods=1,
        filing_status="married",
        state_allowances=2,
    )
    assert married.detail["OR income tax"] == Decimal("5326.75")
    # Single with 3 allowances uses the same deduction and rates:
    # 5,852.75 - 789 = 5,063.75
    single3 = _calc(
        get_engine("OR"),
        gross=Decimal("80000"),
        taxable=Decimal("80000"),
        pay_periods=1,
        state_allowances=3,
    )
    assert single3.detail["OR income tax"] == Decimal("5063.75")


# ---------------------------------------------------------------------------
# Generic flat-rate fallback
# ---------------------------------------------------------------------------
def test_generic_default_is_zero_rate():
    r = _calc(GenericStateEngine())
    assert r.income_tax == Decimal("0.00")
    assert r.detail["State income tax (generic)"] == Decimal("0.00")


def test_generic_configurable_flat_rate():
    # A 5% flat-tax state: 2000 * 0.05 = 100.00 on the period taxable wage.
    eng = GenericStateEngine(flat_rate=Decimal("0.05"))
    assert _calc(eng).income_tax == Decimal("100.00")


def test_generic_accepts_float_or_string_rate():
    # Constructor coerces via str() so a float rate doesn't poison precision.
    assert GenericStateEngine(flat_rate=0.05).flat_rate == Decimal("0.05")


# ---------------------------------------------------------------------------
# Reciprocity
# ---------------------------------------------------------------------------
def test_has_reciprocity_known_pair():
    assert has_reciprocity("OH", "KY") is True
    assert has_reciprocity("IN", "MI") is True


def test_has_reciprocity_is_case_and_space_insensitive():
    assert has_reciprocity(" oh ", "ky") is True


def test_has_reciprocity_not_symmetric_when_only_one_side_listed():
    # NJ⇄PA is the reciprocal pair; an unrelated pair must be False.
    assert has_reciprocity("NJ", "PA") is True
    assert has_reciprocity("CA", "NY") is False


@pytest.mark.parametrize("ws,rs", [(None, "KY"), ("OH", None), (None, None)])
def test_has_reciprocity_handles_missing_inputs(ws, rs):
    assert has_reciprocity(ws, rs) is False


def test_withholding_state_uses_residence_when_reciprocal():
    # Work OH, live KY, agreement exists → withhold for KY (residence).
    assert withholding_state("OH", "KY") == "KY"


def test_withholding_state_uses_work_when_no_agreement():
    assert withholding_state("CA", "NY") == "CA"


def test_withholding_state_defaults_to_work_when_no_residence():
    assert withholding_state("WA", None) == "WA"


# ---------------------------------------------------------------------------
# Shared Social Security wage base
# ---------------------------------------------------------------------------
def test_tables_ss_base_is_the_2026_social_security_wage_base():
    # tables.py can't import payroll_service (circular), so it carries the
    # figure as a literal; the two must agree.
    from app.services.payroll_service import SS_WAGE_BASES
    from app.services.state_tax.tables import SS_BASE

    assert SS_BASE == SS_WAGE_BASES[2026] == Decimal("184500")
    assert SS_WAGE_BASES[2027] == Decimal("192300")


def test_dedicated_catalog_suta_bases_match_the_engines():
    from app.services.state_tax.tables import DEDICATED

    for code, (_name, base, *_rest) in DEDICATED.items():
        assert get_engine(code).suta_wage_base == base, code
