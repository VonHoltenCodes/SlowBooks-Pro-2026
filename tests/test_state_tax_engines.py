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
    # Total premium 2000 * 0.0074 = 14.80; employee 71.43% / employer 28.57%.
    r = _calc(get_engine("WA"))
    assert r.detail["WA PFML (employee)"] == Decimal("10.57")  # 14.80*0.7143
    assert r.detail["WA PFML (employer)"] == Decimal("4.23")  # 14.80*0.2857


def test_wa_cares_is_058_percent_of_gross_employee_only():
    r = _calc(get_engine("WA"))
    assert r.detail["WA Cares"] == Decimal("11.60")  # 2000 * 0.0058


def test_wa_lni_scales_with_hours_not_wages():
    # L&I is assessed per hour, so doubling hours doubles the L&I line while
    # PFML/Cares (wage-based) stay put.
    base = _calc(get_engine("WA"), hours=Decimal("80"))
    dbl = _calc(get_engine("WA"), hours=Decimal("160"))
    assert dbl.detail["WA L&I (employee)"] == base.detail["WA L&I (employee)"] * 2
    assert dbl.detail["WA Cares"] == base.detail["WA Cares"]


def test_wa_employee_other_is_sum_of_employee_lines():
    r = _calc(get_engine("WA"))
    expected = (
        r.detail["WA PFML (employee)"]
        + r.detail["WA Cares"]
        + r.detail["WA L&I (employee)"]
    )
    assert r.employee_other == expected


def test_wa_suta_wage_base():
    assert WAEngine.suta_wage_base == Decimal("72800")


# ---------------------------------------------------------------------------
# California — progressive income tax + uncapped SDI
# ---------------------------------------------------------------------------
def test_ca_income_tax_single_progressive():
    # annual = 52000, less 5500 std ded = 46500 taxable.
    #   0–10800   @1.10%  = 118.80
    #   10800–25600@2.20% = 325.60
    #   25600–40400@4.40% = 651.20
    #   40400–46500@6.60% = 402.60
    #   total = 1498.20 / 26 = 57.62
    assert _calc(get_engine("CA")).detail["CA income tax"] == Decimal("57.62")


def test_ca_sdi_is_uncapped_1_1_percent():
    # No wage ceiling since 2024 — straight 1.1% of gross.
    r = _calc(get_engine("CA"), gross=Decimal("2000"))
    assert r.detail["CA SDI"] == Decimal("22.00")
    big = _calc(get_engine("CA"), gross=Decimal("50000"), taxable=Decimal("50000"))
    assert big.detail["CA SDI"] == Decimal("550.00")  # still 1.1%, no cap


def test_ca_low_wage_under_deduction_yields_zero_income_tax():
    # taxable*26 below the standard deduction → no income tax, SDI still applies.
    r = _calc(get_engine("CA"), gross=Decimal("100"), taxable=Decimal("100"))
    assert r.detail["CA income tax"] == Decimal("0.00")
    assert r.detail["CA SDI"] == Decimal("1.10")


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
    # 2000 * 0.00388 = 7.76, well under the 354 annual cap at ytd 0.
    assert _calc(get_engine("NY")).detail["NY PFL"] == Decimal("7.76")


def test_ny_pfl_caps_at_annual_max():
    # With ytd_gross already past the PFL max wage, the remaining premium is 0.
    # PFL_ANNUAL_MAX / PFL_RATE = 354 / 0.00388 ≈ 91,237 of wages exhausts it.
    r = _calc(get_engine("NY"), ytd_gross=Decimal("200000"))
    assert r.detail["NY PFL"] == Decimal("0.00")


def test_ny_income_tax_single_progressive():
    # annual = 52000, less 8000 std ded = 44000 taxable.
    #   0–8500    @4.00%  = 340.00
    #   8500–11700@4.50%  = 144.00
    #   11700–13900@5.25% = 115.50
    #   13900–44000@5.50% = 1655.50
    #   total = 2255.00 / 26 = 86.73
    assert _calc(get_engine("NY")).detail["NY income tax"] == Decimal("86.73")


# ---------------------------------------------------------------------------
# Oregon — income tax + flat statewide transit tax
# ---------------------------------------------------------------------------
def test_or_transit_tax_flat_0_1_percent():
    assert _calc(get_engine("OR")).detail["OR statewide transit tax"] == Decimal(
        "2.00"
    )  # 2000 * 0.001


def test_ny_and_or_low_wage_under_deduction_yield_zero_income_tax():
    # taxable*26 below the standard deduction → income tax floors at 0 while
    # the wage-based premiums (NY SDI/PFL, OR transit) still apply.
    ny = _calc(get_engine("NY"), gross=Decimal("100"), taxable=Decimal("100"))
    assert ny.detail["NY income tax"] == Decimal("0.00")
    assert ny.detail["NY PFL"] > Decimal("0")
    orr = _calc(get_engine("OR"), gross=Decimal("100"), taxable=Decimal("100"))
    assert orr.detail["OR income tax"] == Decimal("0.00")
    assert orr.detail["OR statewide transit tax"] == Decimal("0.10")


def test_or_income_tax_single_progressive():
    # annual = 52000, less 2745 std ded = 49255 taxable.
    #   0–4300     @4.75% = 204.25
    #   4300–10750 @6.75% = 435.375
    #   10750–49255@8.75% = 3369.1875
    #   total = 4008.8125 / 26 = 154.185... → 154.19
    assert _calc(get_engine("OR")).detail["OR income tax"] == Decimal("154.19")


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
