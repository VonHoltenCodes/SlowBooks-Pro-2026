"""Every payroll table is the pay date's year's (#818).

A run dated in 2025 used 2025's Social Security wage base but 2026's
federal withholding schedules, state tables, Washington PFML (1.13%) and
California SDI (1.3%). Each year's tables are kept, 2025's and 2026's, and
every table is picked by the run's pay date; a pay date in a year with no
tables is refused with a sentence naming the year.

Every expected figure is derived here from the publication the tables
cite: IRS Pub 15-T (2025) / (2026), EDD Method B 2025 / 2026, Paid Leave
Washington's 2025 and 2026 rates, L&I's 2025 and 2026 hourly rates, Oregon
150-206-436 (2025) / (2026)."""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.services.payroll_service import (
    PayrollYearError,
    calculate_withholdings,
    federal_income_tax,
    ss_wage_base,
    tables_year_refusal,
)
from app.services.state_tax import get_engine

D = Decimal
CENT = D("0.01")


def _c(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def _state(code, year, **kw):
    base = dict(
        gross=D("50000"),
        taxable=D("50000"),
        ytd_gross=D("0"),
        pay_periods=1,
        hours=D("0"),
        filing_status="single",
        wc_class_code=None,
    )
    base.update(kw)
    return get_engine(code, year).calculate(**base)


# --- federal ----------------------------------------------------------------
def test_a_2025_paycheck_uses_pub_15_t_2025s_schedule_and_2026_its_own():
    # $2,000 biweekly, single, Step 2 unchecked: annual 52,000 less the
    # $8,600 line 1g add-back = 43,400, in the 12% row of each year
    y25 = (D("1192.50") + (D("43400") - D("18325")) * D("0.12")) / 26
    y26 = (
        (D("19900") - D("7500")) * D("0.10") + (D("43400") - D("19900")) * D("0.12")
    ) / 26
    assert federal_income_tax(D("2000"), 26, year=2025) == _c(y25)
    assert federal_income_tax(D("2000"), 26, year=2026) == _c(y26)
    assert _c(y25) != _c(y26)


def test_the_2025_step_2_checkbox_schedule_is_2025s():
    # annual 52,000, no add-back: Pub 15-T (2025) checkbox single, 22% row
    y25 = (D("2789.25") + (D("52000") - D("31738")) * D("0.22")) / 26
    assert federal_income_tax(D("2000"), 26, multiple_jobs=True, year=2025) == _c(y25)


def test_the_social_security_base_is_the_pay_dates_years():
    assert ss_wage_base(2025) == D("176100")
    assert ss_wage_base(2026) == D("184500")


# --- Washington -------------------------------------------------------------
def test_washington_pfml_is_092_percent_in_2025_and_113_percent_in_2026():
    y25 = _state("WA", 2025, state_employee_count=50)
    y26 = _state("WA", 2026, state_employee_count=50)
    assert y25.detail["WA PFML (employee)"] == _c(
        D("50000") * D("0.0092") * D("0.7152")
    )
    assert y25.detail["WA PFML (employer)"] == _c(
        D("50000") * D("0.0092") * D("0.2848")
    )
    assert y26.detail["WA PFML (employee)"] == _c(
        D("50000") * D("0.0113") * D("0.7143")
    )
    assert y26.detail["WA PFML (employer)"] == _c(
        D("50000") * D("0.0113") * D("0.2857")
    )
    assert (
        y25.detail["WA Cares"] == y26.detail["WA Cares"] == _c(D("50000") * D("0.0058"))
    )


def test_washington_pfml_stops_at_each_years_social_security_base():
    # $176,000 already paid: 2025 has $100 left of $176,100, 2026 all $5,000
    y25 = _state("WA", 2025, gross=D("5000"), taxable=D("5000"), ytd_gross=D("176000"))
    y26 = _state("WA", 2026, gross=D("5000"), taxable=D("5000"), ytd_gross=D("176000"))
    assert y25.detail["WA PFML (employee)"] == _c(D("100") * D("0.0092") * D("0.7152"))
    assert y26.detail["WA PFML (employee)"] == _c(D("5000") * D("0.0113") * D("0.7143"))


def test_washington_l_and_i_is_each_years_hourly_rate():
    # class 4904 clerical, 10 hours: L&I 2025 0.2051 / 0.09315, 2026 0.2223 / 0.10070
    y25 = _state("WA", 2025, hours=D("10"), wc_class_code="4904")
    y26 = _state("WA", 2026, hours=D("10"), wc_class_code="4904")
    assert y25.detail["WA L&I (employee)"] == _c(D("10") * D("0.09315"))
    assert y25.detail["WA L&I (employer)"] == _c(D("10") * (D("0.2051") - D("0.09315")))
    assert y26.detail["WA L&I (employee)"] == _c(D("10") * D("0.10070"))
    assert y26.detail["WA L&I (employer)"] == _c(D("10") * (D("0.2223") - D("0.10070")))


def test_each_years_state_unemployment_wage_base():
    assert get_engine("WA", 2025).suta_wage_base == D("72800")
    assert get_engine("WA", 2026).suta_wage_base == D("78200")
    assert get_engine("OR", 2025).suta_wage_base == D("54300")
    assert get_engine("OR", 2026).suta_wage_base == D("56700")


# --- California -------------------------------------------------------------
def test_california_sdi_is_12_percent_in_2025_and_13_percent_in_2026():
    assert _state("CA", 2025, gross=D("1000"), taxable=D("1000")).detail["CA SDI"] == D(
        "12.00"
    )
    assert _state("CA", 2026, gross=D("1000"), taxable=D("1000")).detail["CA SDI"] == D(
        "13.00"
    )


def test_california_income_tax_uses_each_years_method_b_tables():
    # single, no allowances, $60,000 a year: less the standard deduction,
    # then the annual table up to the 6.6% row
    t25 = D("60000") - D("5540")
    y25 = (
        D("10756") * D("0.011")
        + (D("25499") - D("10756")) * D("0.022")
        + (D("40245") - D("25499")) * D("0.044")
        + (t25 - D("40245")) * D("0.066")
    )
    t26 = D("60000") - D("5706")
    y26 = (
        D("11079") * D("0.011")
        + (D("26264") - D("11079")) * D("0.022")
        + (D("41452") - D("26264")) * D("0.044")
        + (t26 - D("41452")) * D("0.066")
    )
    kw = dict(gross=D("60000"), taxable=D("60000"))
    assert _state("CA", 2025, **kw).income_tax == _c(y25)
    assert _state("CA", 2026, **kw).income_tax == _c(y26)
    # $18,500 a year is over 2025's low income exemption ($18,368) and
    # under 2026's ($18,896)
    low = dict(gross=D("18500"), taxable=D("18500"))
    low25 = D("10756") * D("0.011") + (D("18500") - D("5540") - D("10756")) * D("0.022")
    assert _state("CA", 2025, **low).income_tax == _c(low25)
    assert _state("CA", 2026, **low).income_tax == D("0.00")
    # one allowance: 2025's credit is $163.90
    one = _state("CA", 2025, state_allowances=1, **kw).income_tax
    assert one == _c(y25 - D("163.90"))


# --- Oregon -----------------------------------------------------------------
def test_oregon_uses_each_years_withholding_formula():
    # single, $40,000 a year, no federal subtraction: 2025's formula is
    # 917 + (BASE - 11,100) x 0.0875 on BASE = 40,000 - 2,835
    kw = dict(gross=D("40000"), taxable=D("40000"))
    y25 = D("917") + (D("40000") - D("2835") - D("11100")) * D("0.0875")
    assert _state("OR", 2025, **kw).income_tax == _c(y25)
    # 2026: 941 + (BASE - 11,400) x 0.0875 on BASE = 40,000 - 2,910
    y26 = D("941") + (D("40000") - D("2910") - D("11400")) * D("0.0875")
    assert _state("OR", 2026, **kw).income_tax == _c(y26)


def test_paid_leave_oregon_stops_at_each_years_social_security_base():
    kw = dict(gross=D("5000"), taxable=D("5000"), ytd_gross=D("176000"))
    assert _state("OR", 2025, **kw).detail["OR Paid Leave (employee)"] == _c(
        D("100") * D("0.006")
    )
    assert _state("OR", 2026, **kw).detail["OR Paid Leave (employee)"] == _c(
        D("5000") * D("0.006")
    )


# --- a year with no tables --------------------------------------------------
def test_a_year_with_no_tables_is_a_sentence_naming_the_year():
    msg = tables_year_refusal(2024)
    assert "not for 2024" in msg and "2025 and 2026" in msg
    assert msg.endswith(".")
    with pytest.raises(PayrollYearError) as e:
        calculate_withholdings(D("2000"), year=2024)
    assert str(e.value) == msg
    with pytest.raises(PayrollYearError):
        federal_income_tax(D("2000"), 26, year=2027)


# --- through the pay run ----------------------------------------------------
def _employee(client, **kw):
    body = {
        "first_name": "Test",
        "last_name": "Worker",
        "pay_type": "hourly",
        "pay_rate": 25,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _post_run(client, emp_id, pay_date, **stub):
    return client.post(
        "/api/payroll",
        json={
            "period_start": pay_date,
            "period_end": pay_date,
            "pay_date": pay_date,
            "stubs": [{"employee_id": emp_id, "hours": 80, **stub}],
        },
    )


def test_a_2025_dated_run_uses_every_2025_figure(client):
    emp = _employee(client)
    r = _post_run(client, emp["id"], "2025-06-13")
    assert r.status_code == 201, r.text
    run = r.json()
    [stub] = run["stubs"]
    gross = D("2000")
    assert D(str(stub["gross_pay"])) == gross
    fed = (D("1192.50") + (D("43400") - D("18325")) * D("0.12")) / 26
    assert D(str(stub["federal_tax"])) == _c(fed)
    # 2025 PFML employee share + WA Cares + L&I at the placeholder rate
    other = (
        _c(gross * D("0.0092") * D("0.7152"))
        + _c(gross * D("0.0058"))
        + _c(D("80") * D("0.08"))
    )
    assert D(str(stub["state_other_employee"])) == other
    # nothing figured with another year's Washington tables, so no note
    assert not [w for w in run["warnings"] if "dated 2025" in w]


def test_a_2026_dated_run_uses_every_2026_figure(client):
    emp = _employee(client)
    r = _post_run(client, emp["id"], "2026-06-12")
    assert r.status_code == 201, r.text
    [stub] = r.json()["stubs"]
    gross = D("2000")
    fed = (
        (D("19900") - D("7500")) * D("0.10") + (D("43400") - D("19900")) * D("0.12")
    ) / 26
    assert D(str(stub["federal_tax"])) == _c(fed)
    other = (
        _c(gross * D("0.0113") * D("0.7143"))
        + _c(gross * D("0.0058"))
        + _c(D("80") * D("0.08"))
    )
    assert D(str(stub["state_other_employee"])) == other


def test_a_2025_california_run_withholds_2025s_sdi(client):
    emp = _employee(client, work_state="CA")
    r = _post_run(client, emp["id"], "2025-06-13")
    assert r.status_code == 201, r.text
    [stub] = r.json()["stubs"]
    # SDI is the only other California item: 1.2% of $2,000
    assert D(str(stub["state_other_employee"])) == D("24.00")


def test_a_2025_run_in_a_state_with_only_2026_tables_says_so(client):
    emp = _employee(client, work_state="NY")
    r = _post_run(client, emp["id"], "2025-06-13")
    assert r.status_code == 201, r.text
    run = r.json()
    [note] = [w for w in run["warnings"] if "dated 2025" in w]
    assert "2026's tables for New York" in note
    got = client.get(f"/api/payroll/{run['id']}").json()
    assert note in got["warnings"]


def test_a_run_dated_in_a_year_with_no_tables_is_refused_and_writes_nothing(client):
    emp = _employee(client)
    r = _post_run(client, emp["id"], "2024-06-14")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == tables_year_refusal(2024)
    assert client.get("/api/payroll").json() == []
