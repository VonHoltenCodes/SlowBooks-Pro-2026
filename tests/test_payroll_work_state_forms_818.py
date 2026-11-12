"""A blank work state is no state on the forms either (#818).

A stub figured with no work state was read as Washington by the W-2, the
state SUI report and the opening balance's wage bases. Now such a stub is
on no state's SUI report, the W-2 leaves the state boxes empty, and the
forms say "work state missing" naming the employee, so nothing is filed
under a state nobody chose."""

from decimal import Decimal

import pytest

from app.routes.payroll.ytd import wage_base_ytd
from app.services.payroll_opening import opening_for, wage_bases
from app.services.tax_forms.state_sui import compute_sui
from app.services.tax_forms.w2_w3 import compute_w2, w2_state_lines

D = Decimal


@pytest.fixture(autouse=True)
def _chart(seed_accounts):
    return seed_accounts


def _employee(client, last, **kw):
    body = {
        "first_name": "Test",
        "last_name": last,
        "pay_type": "hourly",
        "pay_rate": 25,
        "pay_frequency": "biweekly",
        "filing_status": "single",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _paid(client, *emp_ids):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-10",
            "pay_date": "2026-04-10",
            "stubs": [{"employee_id": e, "hours": 80} for e in emp_ids],
        },
    )
    assert r.status_code == 201, r.text
    p = client.post(f"/api/payroll/{r.json()['id']}/process")
    assert p.status_code == 200, p.text
    return r.json()


def test_the_w2_leaves_the_state_boxes_empty_and_says_why(client, db_session):
    blank = _employee(client, "Blank")
    _paid(client, blank["id"])
    w2 = compute_w2(db_session, 2026, blank["id"])
    assert w2["box1_federal_wages"] == D("2000.00")
    assert w2["box16_state_wages"] == D("0.00")
    assert "states" not in w2
    assert w2_state_lines(db_session, 2026, blank["id"]) == []
    r = client.post(f"/api/payroll/forms/w2/{blank['id']}?year=2026")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["states"] == []
    [warning] = data["warnings"]
    assert warning.startswith("Test Blank: work state missing on 1 paycheck in 2026")


def test_a_blank_state_stub_is_on_no_states_sui_report(client, db_session):
    blank = _employee(client, "Blank")
    placed = _employee(client, "Placed", work_state="WA")
    _paid(client, blank["id"], placed["id"])
    wa = compute_sui(db_session, 2026, 2, "WA")
    assert [e["name"] for e in wa["employees"]] == ["Test Placed"]
    assert wa["total_wages"] == D("2000.00")
    every = compute_sui(db_session, 2026, 2)
    assert [e["name"] for e in every["employees"]] == ["Test Placed"]
    assert any("Test Blank: work state missing" in w for w in every["warnings"])
    # through the API too, and the Tax Forms page's list
    got = client.get("/api/tax-forms/sui?year=2026&quarter=2&state=WA").json()
    assert [e["name"] for e in got["employees"]] == ["Test Placed"]
    page = client.get("/api/tax-forms/work-state-missing?year=2026").json()
    assert len(page["warnings"]) == 1 and "Test Blank" in page["warnings"][0]


def test_a_placed_employee_brings_no_warning(client, db_session):
    placed = _employee(client, "Placed", work_state="WA")
    _paid(client, placed["id"])
    assert "warnings" not in compute_sui(db_session, 2026, 2)
    page = client.get("/api/tax-forms/work-state-missing?year=2026").json()
    assert page["warnings"] == []
    assert [ln["state"] for ln in w2_state_lines(db_session, 2026, placed["id"])] == [
        "WA"
    ]


def test_an_opening_balance_with_no_state_counts_toward_no_state(client, db_session):
    blank = _employee(client, "Blank")
    body = {
        "year": 2026,
        "as_of": "2026-03-31",
        "quarters": [
            {
                "quarter": 1,
                "gross": 10000,
                "ss_wages": 10000,
                "medicare_wages": 10000,
                "futa_wages": 7000,
                "net": 7000,
            }
        ],
    }
    r = client.put(f"/api/employees/{blank['id']}/opening-ytd", json=body)
    assert r.status_code == 200, r.text
    opening = opening_for(db_session, blank["id"], 2026)
    # it was Washington's: the SUTA and state wages of a state nobody chose
    assert wage_bases(opening, "WA")["suta"] == D("0")
    assert wage_bases(opening, "WA")["state"] == D("0")
    assert wage_bases(opening, "")["suta"] == D("0")
    bases = wage_base_ytd(db_session, blank["id"], 2026, "WA")
    assert bases["suta"] == D("0") and bases["ss"] == D("10000")
    # a blank stub fills no state's base either
    _paid(client, blank["id"])
    later = wage_base_ytd(db_session, blank["id"], 2026, "")
    assert later["suta"] == D("0") and later["state"] == D("0")
    assert w2_state_lines(db_session, 2026, blank["id"]) == []
    page = client.get("/api/tax-forms/work-state-missing?year=2026").json()
    [warning] = page["warnings"]
    assert "1 paycheck and the opening balance in 2026" in warning
