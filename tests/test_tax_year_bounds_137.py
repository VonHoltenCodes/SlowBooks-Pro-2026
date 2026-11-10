"""A tax year out of range is refused, not a 500, on every route (2.31.12 gate,
W-137 / NEW-220).

PR #756 bounded the /api/tax-forms routes to 1900–9999 (#762). Two places
were missed: the quarterly liability's fourth quarter of 9999 put its due date
in the year 10000 and raised, and the official forms under
/api/payroll/forms took the year as a plain int, so 99999 reached date()
there too (a payroll token reaches those routes now).
"""

import pytest

from tests.test_api_token_payroll_grant_755 import EMPLOYEE


def _employee(client) -> int:
    r = client.post("/api/employees", json=EMPLOYEE)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.parametrize("year", [99999, 10000, 1899])
def test_the_payroll_forms_refuse_a_year_out_of_range(client, seed_accounts, year):
    emp = _employee(client)
    forms = "/api/payroll/forms"
    requests = [
        ("POST", f"{forms}/w2/{emp}?year={year}"),
        ("GET", f"{forms}/w2/{emp}/pdf?year={year}"),
        ("POST", f"{forms}/w2/{emp}/pdf?year={year}"),
        ("POST", f"{forms}/w3/{year}"),
        ("GET", f"{forms}/w3/{year}/pdf"),
        ("POST", f"{forms}/w3/{year}/pdf"),
        ("POST", f"{forms}/940/{year}"),
        ("GET", f"{forms}/940/{year}/pdf"),
        ("POST", f"{forms}/940/{year}/pdf"),
        ("POST", f"{forms}/941/{year}/1"),
        ("GET", f"{forms}/941/{year}/1/pdf"),
        ("POST", f"{forms}/941/{year}/1/pdf"),
    ]
    for method, path in requests:
        r = client.request(method, path)
        assert r.status_code == 422, (method, path, r.status_code, r.text[:200])


def test_the_liabilitys_last_quarter_of_the_calendar_has_a_due_date(
    client, seed_accounts
):
    """Q4 of 9999 is due on the calendar's last day, not in a year that does
    not exist; the quarter before it is due as ever."""
    r = client.get("/api/tax-forms/liability?year=9999&quarter=4")
    assert r.status_code == 200, r.text
    assert "9999-12-31" in r.text
    r = client.get("/api/tax-forms/liability?year=9999&quarter=3")
    assert r.status_code == 200 and "9999-10-31" in r.text
    assert (
        client.get("/api/tax-forms/liability?year=10000&quarter=1").status_code == 422
    )
