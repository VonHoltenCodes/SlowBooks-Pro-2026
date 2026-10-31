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


def test_wa_pfml_employer_share_only_from_fifty_washington_employees():
    # 50,000 x 1.13% = 565.00; employer 28.57% = 161.42, employee 403.58.
    # ESD counts the employees working in Washington: 48 in Texas and one
    # in Washington is a one-employee Washington employer.
    big = _calc("WA", employee_count=50, state_employee_count=50)
    small = _calc("WA", employee_count=15, state_employee_count=15)
    mostly_texas = _calc("WA", employee_count=49, state_employee_count=1)
    none = _calc("WA")
    assert mostly_texas.detail["WA PFML (employer)"] == Decimal("0.00")
    assert big.detail["WA PFML (employer)"] == Decimal("161.42")
    assert none.detail["WA PFML (employer)"] == Decimal("161.42")
    assert small.detail["WA PFML (employer)"] == Decimal("0.00")
    # the employee's share is the same either way
    assert small.detail["WA PFML (employee)"] == big.detail["WA PFML (employee)"]
    assert small.employer_other < big.employer_other


def test_paid_leave_oregon_employer_share_only_from_twenty_five_employees():
    # 50,000 x 0.4% = 200.00; Oregon counts every employee, in or out of state
    big = _calc(
        "OR",
        employee_count=25,
        state_employee_count=1,
        federal_withholding=Decimal("3820"),
    )
    small = _calc(
        "OR",
        employee_count=6,
        state_employee_count=6,
        federal_withholding=Decimal("3820"),
    )
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


def test_the_prior_year_note_is_on_the_run_wherever_it_is_read(
    client, db_session, seed_accounts
):
    # the note came back only on the create response (2.31.3 gate, note A);
    # the run's own GET and the list carry it too, and a 2026 run has none
    r = client.post(
        "/api/employees",
        json={
            "first_name": "Pat",
            "last_name": "Worker",
            "pay_type": "salary",
            "pay_rate": 78000,
            "pay_frequency": "biweekly",
            "filing_status": "single",
            "work_state": "TX",
        },
    )
    assert r.status_code == 201, r.text
    emp = r.json()["id"]

    def run(start, end, pay):
        r = client.post(
            "/api/payroll",
            json={
                "period_start": start,
                "period_end": end,
                "pay_date": pay,
                "stubs": [{"employee_id": emp}],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    old = run("2025-12-01", "2025-12-14", "2025-12-15")
    assert any("dated 2025" in w for w in old["warnings"])
    got = client.get(f"/api/payroll/{old['id']}").json()
    assert any("dated 2025" in w for w in got["warnings"])
    listed = {r["id"]: r for r in client.get("/api/payroll").json()}
    assert any("dated 2025" in w for w in listed[old["id"]]["warnings"])

    new = run("2026-01-01", "2026-01-14", "2026-01-15")
    assert not any("dated" in w for w in new["warnings"])
    assert client.get(f"/api/payroll/{new['id']}").json()["warnings"] == []
