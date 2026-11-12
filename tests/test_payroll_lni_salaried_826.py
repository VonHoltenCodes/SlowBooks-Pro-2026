"""Washington L&I for a salaried employee with no hours entered (#826).

L&I premiums are per hour, so a salaried stub with no hours carried no L&I
at all. A salaried Washington stub with no hours on the run is figured on
L&I's reporting basis for salaried workers, 160 hours a month prorated to
the pay period, and says so; hours entered on the run win.

Class 4904 (clerical), L&I 2026: composite 0.2223 an hour, worker
deduction 0.10070."""

import json
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.models.payroll import PayStub
from app.services.state_tax.wa import salaried_lni_hours

D = Decimal
TOTAL, WORKER = D("0.2223"), D("0.10070")
NOTE = "L&I on 160 hours (salaried, no hours entered)"


def _c(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _salaried(client, frequency, **kw):
    body = {
        "first_name": "Test",
        "last_name": "Salaried",
        "pay_type": "salary",
        "pay_rate": 52000,
        "pay_frequency": frequency,
        "filing_status": "single",
        "work_state": "WA",
        "wc_class_code": "4904",
    }
    body.update(kw)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, emp_id, **stub):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
            "pay_date": "2026-06-30",
            "stubs": [{"employee_id": emp_id, **stub}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["stubs"][0]


def _detail(db_session, stub_id):
    stub = db_session.query(PayStub).filter(PayStub.id == stub_id).one()
    return json.loads(stub.detail_json)


@pytest.mark.parametrize(
    "frequency, hours",
    [
        ("monthly", "160"),
        ("semi_monthly", "80"),
        ("biweekly", "73.85"),
        ("weekly", "36.92"),
    ],
)
def test_the_salaried_basis_is_160_hours_a_month_prorated_to_the_period(
    frequency, hours
):
    periods = {"monthly": 12, "semi_monthly": 24, "biweekly": 26, "weekly": 52}
    assert salaried_lni_hours(periods[frequency]) == D(hours)


@pytest.mark.parametrize(
    "frequency, hours",
    [("monthly", "160"), ("semi_monthly", "80"), ("biweekly", "73.85")],
)
def test_a_salaried_stub_with_no_hours_carries_l_and_i_on_the_salaried_basis(
    client, db_session, frequency, hours
):
    emp = _salaried(client, frequency)
    stub = _run(client, emp["id"])
    detail = _detail(db_session, stub["id"])
    h = D(hours)
    assert D(detail["WA L&I (employee)"]) == _c(h * WORKER)
    assert D(detail["WA L&I (employer)"]) == _c(h * (TOTAL - WORKER))
    assert any(NOTE in n for n in stub["notes"])
    # the pay is the salary's and the stub's hours are still none entered
    assert stub["hours"] == 0


def test_hours_entered_on_a_salaried_stub_win(client, db_session):
    emp = _salaried(client, "biweekly")
    stub = _run(client, emp["id"], hours=70)
    detail = _detail(db_session, stub["id"])
    assert D(detail["WA L&I (employee)"]) == _c(D("70") * WORKER)
    assert D(detail["WA L&I (employer)"]) == _c(D("70") * (TOTAL - WORKER))
    assert not any(NOTE in n for n in stub["notes"])
    # entered hours do not change a salary's pay
    assert D(str(stub["gross_pay"])) == _c(D("52000") / 26)
    assert stub["hours"] == 70


def test_a_salaried_stub_outside_washington_gets_no_l_and_i_hours(client):
    emp = _salaried(client, "biweekly", work_state="OR")
    stub = _run(client, emp["id"])
    assert not any(NOTE in n for n in stub["notes"])
