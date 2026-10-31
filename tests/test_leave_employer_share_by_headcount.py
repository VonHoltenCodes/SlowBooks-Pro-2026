"""WA PFML's employer share applies to employers with 50 or more employees
and Paid Leave Oregon's to 25 or more (2.31.3 gate, W-93). The pay run
passes the company's active-employee count; an engine called without one
charges the share, as the large-employer case."""

from decimal import Decimal

from app.services.state_tax import get_engine


def _calc(code, **kw):
    base = dict(
        gross=Decimal("50000"),
        taxable=Decimal("50000"),
        ytd_gross=Decimal("0"),
        pay_periods=1,
        hours=Decimal("0"),
        filing_status="single",
        wc_class_code=None,
    )
    base.update(kw)
    return get_engine(code).calculate(**base)


def test_wa_pfml_employer_share_only_from_fifty_employees():
    # 50,000 x 1.13% = 565.00; employer 28.57% = 161.42, employee 403.58
    big = _calc("WA", employee_count=50)
    small = _calc("WA", employee_count=15)
    none = _calc("WA")
    assert big.detail["WA PFML (employer)"] == Decimal("161.42")
    assert none.detail["WA PFML (employer)"] == Decimal("161.42")
    assert small.detail["WA PFML (employer)"] == Decimal("0.00")
    # the employee's share is the same either way
    assert small.detail["WA PFML (employee)"] == big.detail["WA PFML (employee)"]
    assert small.employer_other < big.employer_other


def test_paid_leave_oregon_employer_share_only_from_twenty_five_employees():
    # 50,000 x 0.4% = 200.00
    big = _calc("OR", employee_count=25, federal_withholding=Decimal("3820"))
    small = _calc("OR", employee_count=6, federal_withholding=Decimal("3820"))
    assert big.detail["OR Paid Leave (employer)"] == Decimal("200.00")
    assert small.detail["OR Paid Leave (employer)"] == Decimal("0.00")
    assert small.detail["OR Paid Leave (employee)"] == Decimal("300.00")
    assert small.detail["OR income tax"] == big.detail["OR income tax"]


def test_a_pay_run_dated_in_another_year_says_which_tables_it_used():
    from app.services.payroll_service import TABLES_YEAR, tables_year_note

    assert tables_year_note(TABLES_YEAR) is None
    assert tables_year_note(None) is None
    note = tables_year_note(2025)
    assert note and "dated 2025" in note and str(TABLES_YEAR) in note
