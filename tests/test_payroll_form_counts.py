"""Payroll forms count the employees who were paid wages (2.17.3 exploratory
test, skytech W-M11).

A $0.00 stub (the time-tracking bug made one for an hourly employee) made the
W-3 say "Number of W-2s transmitted: 2" and the 940 and 941 say 2 employees
received wages, when only one had. The W-3 also printed `2400.00` where the
W-2 prints `$2,400.00`.
"""

from decimal import Decimal

import pytest

from app.models.payroll import Employee, PayStub


@pytest.fixture
def one_paid_one_zero(client, db_session, seed_accounts):
    paid = client.post(
        "/api/employees",
        json={
            "first_name": "Sam",
            "last_name": "Ortiz",
            "pay_type": "salary",
            "pay_rate": 62400,
            "work_state": "IL",
        },
    ).json()
    run = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-03-01",
            "period_end": "2026-03-14",
            "pay_date": "2026-03-20",
            "stubs": [{"employee_id": paid["id"]}],
        },
    ).json()
    assert run["stubs"][0]["gross_pay"] == 2400.0
    # A $0.00 stub as runs made before 2.17.4 could hold one.
    hana = Employee(first_name="Hana", last_name="Lee", pay_type="hourly", pay_rate=22)
    db_session.add(hana)
    db_session.flush()
    db_session.add(
        PayStub(
            pay_run_id=run["id"],
            employee_id=hana.id,
            hours=Decimal("0"),
            gross_pay=Decimal("0"),
            net_pay=Decimal("0"),
        )
    )
    db_session.commit()
    r = client.post(f"/api/payroll/{run['id']}/process")
    assert r.status_code == 200, r.text
    return paid, hana


def test_the_w3_transmits_one_w2_per_employee_with_wages(client, one_paid_one_zero):
    w3 = client.post("/api/payroll/forms/w3/2026").json()
    assert w3["number_of_w2s"] == "1"
    assert w3["box_1"] == "2400.00"
    w2s = client.get("/api/tax-forms/w2?year=2026").json()["w2"]
    assert [w["employee"]["name"] for w in w2s] == ["Sam Ortiz"]


def test_940_and_941_count_the_employees_who_received_wages(client, one_paid_one_zero):
    assert client.get("/api/tax-forms/940?year=2026").json()["num_employees"] == 1
    q1 = client.get("/api/tax-forms/941?year=2026&quarter=1").json()
    assert q1["num_employees"] == 1
    sui = client.get("/api/tax-forms/sui?year=2026&quarter=1").json()
    assert sui["num_employees"] == 1
    assert [e["name"] for e in sui["employees"]] == ["Sam Ortiz"]


def test_the_w3_prints_amounts_like_the_w2(client, one_paid_one_zero, monkeypatch):
    from app.services.tax_forms import w2_w3

    monkeypatch.setattr(w2_w3, "render_pdf", lambda html, **kw: html.encode())
    w3 = client.post("/api/payroll/forms/w3/2026/pdf").text
    assert "$2,400.00" in w3
    assert ">2400.00<" not in w3
    assert "Number of W-2s transmitted:</strong> 1" in w3
    w2 = client.post(
        f"/api/payroll/forms/w2/{one_paid_one_zero[0]['id']}/pdf?year=2026"
    ).text
    assert "$2,400.00" in w2
