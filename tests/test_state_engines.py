"""State withholding: every state + DC resolves to a real engine.

Structure checks for all 51 (no-tax states withhold nothing, flat states
withhold their rate, bracket states are monotonic and honour deductions
and allowances), exact spot checks against hand-computed figures for a
few, the "other" items (SDI / TDI / PFML / UI), the local-rate and
rate-election inputs, and the employee fields flowing through a pay run.
"""

from decimal import Decimal

import pytest

from app.services.state_tax import get_engine, is_supported, list_states
from app.services.state_tax.tables import DEDICATED, STATES

ALL = sorted(set(STATES) | set(DEDICATED))


def calc(code, gross=2000, taxable=None, status="single", periods=26, ytd=0, **kw):
    return get_engine(code).calculate(
        gross=Decimal(str(gross)),
        taxable=Decimal(str(taxable if taxable is not None else gross)),
        ytd_gross=Decimal(str(ytd)),
        pay_periods=periods,
        hours=Decimal("80"),
        filing_status=status,
        wc_class_code=None,
        **kw,
    )


def test_every_state_and_dc_is_supported():
    assert len(ALL) == 51
    for code in ALL:
        assert is_supported(code), code
        assert get_engine(code).state_code == code, code
        assert get_engine(code).suta_wage_base > 0, code
    assert not is_supported("ZZ") and not is_supported("PR")
    cat = {s["code"]: s for s in list_states()}
    assert len(cat) == 51 and cat["IL"]["summary"].startswith("Flat 4.95")


@pytest.mark.parametrize("code", [c for c, s in STATES.items() if s.method == "none"])
def test_no_income_tax_states_withhold_nothing(code):
    r = calc(code, gross=3000)
    assert r.income_tax == 0
    assert r.employer_other == 0


@pytest.mark.parametrize(
    "code", [c for c, s in STATES.items() if s.method == "flat" and s.flat_rate]
)
def test_flat_states_never_exceed_their_rate_and_grow_with_wages(code):
    spec = STATES[code]
    low, high = calc(code, gross=1500), calc(code, gross=6000)
    assert low.income_tax <= Decimal("1500") * spec.flat_rate + Decimal("0.01")
    assert high.income_tax <= Decimal("6000") * spec.flat_rate + Decimal("0.01")
    assert high.income_tax >= low.income_tax
    # no deductions at all → exactly the rate
    if not spec.std_deduction and not spec.base_exemption:
        assert high.income_tax == (Decimal("6000") * spec.flat_rate).quantize(
            Decimal("0.01")
        )


@pytest.mark.parametrize(
    "code", [c for c, s in STATES.items() if s.method == "brackets"]
)
def test_bracket_states_are_monotonic_and_honour_status_and_allowances(code):
    spec = STATES[code]
    a, b, c = (calc(code, gross=g) for g in (1500, 4000, 12000))
    assert 0 <= a.income_tax <= b.income_tax <= c.income_tax, code
    assert c.income_tax > 0, code  # $312k a year is taxed everywhere with an income tax
    top = spec.brackets["single"][-1][1]
    assert c.income_tax <= Decimal("12000") * top + Decimal("0.01"), code
    married = calc(code, gross=4000, status="married")
    assert married.income_tax <= b.income_tax, code
    if spec.exemption:
        assert (
            calc(code, gross=4000, state_allowances=3).income_tax < b.income_tax
        ), code


# --- exact spot checks (annualized percentage method, biweekly) --------------
def test_illinois_flat_with_allowances():
    # 2000 × 26 = 52,000 − 2 × 2,850 = 46,300 × 4.95% = 2,291.85 / 26 = 88.15
    r = calc("IL", gross=2000, state_allowances=2)
    assert r.income_tax == Decimal("88.15")
    assert calc("IL", gross=2000).income_tax == Decimal("99.00")
    assert calc("IL", gross=2000, state_extra_withholding=25).income_tax == Decimal(
        "124.00"
    )


def test_pennsylvania_flat_no_deductions_plus_employee_ui():
    r = calc("PA", gross=2000)
    assert r.income_tax == Decimal("61.40")
    assert r.employee_other == Decimal("1.40")  # 0.07% employee UI
    assert "PA employee unemployment contribution" in r.detail


def test_virginia_brackets_and_deductions():
    # 52,000 − 8,500 std − 930 × 1 = 42,570 → 60 + 60 + 600 + (42,570 − 17,000) × 5.75% = 2,190.28 / 26
    r = calc("VA", gross=2000, state_allowances=1)
    assert r.income_tax == Decimal("84.24")


def test_maryland_needs_a_county_rate():
    base = calc("MD", gross=2000)
    withlocal = calc("MD", gross=2000, local_tax_rate=3.2)
    assert withlocal.income_tax == base.income_tax
    assert withlocal.employee_other - base.employee_other == Decimal("64.00")
    assert "MD local income tax" in withlocal.detail


def test_arizona_uses_the_elected_rate():
    assert calc("AZ", gross=2000).income_tax == Decimal("40.00")  # 2.0% default
    assert calc("AZ", gross=2000, state_rate_override=3.5).income_tax == Decimal(
        "70.00"
    )


def test_new_jersey_other_items_cap_at_the_wage_base():
    r = calc("NJ", gross=2000)
    # UI 0.3825% + DI 0.23% + FLI 0.33% = 0.9425% of 2,000
    assert r.employee_other == Decimal("18.85")
    capped = calc("NJ", gross=2000, ytd=43000)  # only $300 left under the base
    assert capped.employee_other == Decimal("2.83")
    assert r.income_tax > 0


def test_pfml_states_split_employee_and_employer():
    for code, ee, er in (
        ("MA", "0.0046", "0.0042"),
        ("CO", "0.0045", "0.0045"),
        ("MN", "0.0044", "0.0044"),
    ):
        r = calc(code, gross=2000)
        assert r.employee_other >= (Decimal("2000") * Decimal(ee)).quantize(
            Decimal("0.01")
        ), code
        assert r.employer_other == (Decimal("2000") * Decimal(er)).quantize(
            Decimal("0.01")
        ), code
    assert calc("DC", gross=2000).employer_other == Decimal(
        "15.00"
    )  # 0.75% employer PFL
    assert calc("CT", gross=2000).employee_other == Decimal(
        "10.00"
    )  # 0.5% CT Paid Leave


def test_mississippi_first_10k_exempt():
    # 1,000 × 26 = 26,000 − 2,300 − 6,000 = 17,700 → 4.4% on the part over 10,000 = 338.80 / 26
    assert calc("MS", gross=1000).income_tax == Decimal("13.03")


def test_unknown_code_is_still_a_zero_generic_engine():
    r = calc("ZZ", gross=2000)
    assert r.income_tax == 0 and r.employee_other == 0


# --- through a pay run --------------------------------------------------------
def test_employee_state_fields_flow_into_the_stub(client, seed_accounts):
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Ida",
            "last_name": "Prairie",
            "pay_type": "hourly",
            "pay_rate": 25,
            "pay_frequency": "biweekly",
            "work_state": "IN",
            "residence_state": "IN",
            "state_allowances": 2,
            "local_tax_rate": 1.5,
            "state_extra_withholding": 10,
        },
    )
    assert r.status_code in (200, 201), r.text
    emp = r.json()
    assert emp["state_allowances"] == 2 and emp["local_tax_rate"] == 1.5
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-07-01",
            "period_end": "2026-07-14",
            "pay_date": "2026-07-17",
            "stubs": [{"employee_id": emp["id"], "hours": 80}],
        },
    )
    assert run.status_code == 201, run.text
    stub = run.json()["stubs"][0]
    # 2,000 × 26 = 52,000 − 2 × 1,000 = 50,000 × 3% = 1,500 / 26 = 57.69 + 10 extra
    assert stub["state_tax"] == 67.69
    assert stub["state_other_employee"] == 30.0  # 1.5% county on 2,000
    assert stub["work_state"] == "IN"


def test_reciprocity_withholds_for_the_residence_state(client, seed_accounts):
    """Lives in Illinois, works in Iowa: reciprocity says withhold Illinois."""
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Quad",
            "last_name": "Cities",
            "pay_type": "hourly",
            "pay_rate": 25,
            "pay_frequency": "biweekly",
            "work_state": "IA",
            "residence_state": "IL",
        },
    )
    emp = r.json()
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-07-01",
            "period_end": "2026-07-14",
            "pay_date": "2026-07-17",
            "stubs": [{"employee_id": emp["id"], "hours": 80}],
        },
    )
    stub = run.json()["stubs"][0]
    assert stub["state_tax"] == 99.0  # IL 4.95% of 2,000, no allowances
