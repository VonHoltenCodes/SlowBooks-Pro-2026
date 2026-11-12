"""A blank work state is no state (#818).

An employee with no work state was counted as Washington for the PFML
headcount and charged Washington's employer share. A blank state counts
toward no state's headcount and is charged nothing; the run's warnings
and the stub say "work state missing" for that employee."""

from decimal import Decimal

D = Decimal


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


def _run(client, *emp_ids):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-06-01",
            "period_end": "2026-06-12",
            "pay_date": "2026-06-12",
            "stubs": [{"employee_id": e, "hours": 80} for e in emp_ids],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_blank_work_state_is_charged_no_state_tax_and_the_run_says_so(client):
    blank = _employee(client, "Blank")
    run = _run(client, blank["id"])
    [stub] = run["stubs"]
    assert stub["work_state"] is None
    assert stub["state_tax"] == 0
    assert stub["state_other_employee"] == 0
    assert stub["state_other_employer"] == 0
    assert stub["suta_tax"] == 0
    # federal taxes are still figured
    assert stub["federal_tax"] > 0 and stub["ss_tax"] > 0
    assert any("Test Blank: work state missing" in w for w in run["warnings"])
    assert any("work state missing" in n for n in stub["notes"])
    # and the stub keeps saying so wherever it is read
    got = client.get(f"/api/payroll/{run['id']}").json()
    assert any("work state missing" in n for n in got["stubs"][0]["notes"])


def test_a_blank_work_state_does_not_count_toward_washingtons_headcount(client):
    # 49 employees in Washington and one with no work state: a 49-employee
    # Washington employer, which owes no PFML employer share
    wa = [_employee(client, f"Wa{i}", work_state="WA") for i in range(49)]
    _employee(client, "Blank")
    [stub] = _run(client, wa[0]["id"])["stubs"]
    # the employer's other Washington items are L&I alone: the placeholder
    # rate's employer part, (0.20 - 0.08) x 80 hours
    lni_employer = (D("0.20") - D("0.08")) * 80
    assert D(str(stub["state_other_employer"])) == lni_employer
    # a fiftieth Washington employee makes the share owed
    _employee(client, "Wa49", work_state="WA")
    [stub] = _run(client, wa[1]["id"])["stubs"]
    pfml_employer = (D("2000") * D("0.0113") * D("0.2857")).quantize(D("0.01"))
    assert D(str(stub["state_other_employer"])) == lni_employer + pfml_employer


def test_an_employee_with_a_work_state_has_no_missing_state_note(client):
    emp = _employee(client, "Placed", work_state="WA")
    run = _run(client, emp["id"])
    assert not any("work state missing" in w for w in run["warnings"])
    assert run["stubs"][0]["notes"] == []
