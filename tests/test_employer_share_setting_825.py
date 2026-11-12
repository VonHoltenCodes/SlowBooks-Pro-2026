"""The paid-leave employer share as the agency determined it (#825).

Washington PFML's employer share applies from 50 employees working in
Washington and Paid Leave Oregon's from 25 employees; a pay run counts the
active employees when it is figured. ESD and OED size an employer over
their own period and say so once a year, so a setting per state (Settings
→ Payroll, "Employer share applies (as the agency determined)") says yes,
no, or follow the headcount (the default, today's behaviour). The stub and
the run say which applied and why; the run warns when the headcount and
the setting disagree."""

from decimal import Decimal

import pytest

from app.models.payroll import Employee

D = Decimal

# $25 x 80 hours = $2,000 gross on every stub here.
WA_PFML_EMPLOYER = (D("2000") * D("0.0113") * D("0.2857")).quantize(D("0.01"))
# the placeholder L&I rate's employer part, (0.20 - 0.08) x 80 hours
WA_LNI_EMPLOYER = (D("0.20") - D("0.08")) * 80
OR_PAID_LEAVE_EMPLOYER = D("2000") * D("0.004")

SETTING = {
    "WA": "payroll_wa_pfml_employer_share",
    "OR": "payroll_or_paid_leave_employer_share",
}
# headcounts on either side of each state's threshold
COUNTS = {"WA": (49, 50), "OR": (24, 25)}


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


def _fill(db_session, state, n):
    """n more active employees in ``state``, for the headcount."""
    for i in range(n):
        db_session.add(
            Employee(
                first_name="Count",
                last_name=f"{state}{i}",
                work_state=state,
                is_active=True,
            )
        )
    db_session.commit()


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


def _set(client, state, value):
    r = client.put("/api/settings", json={SETTING[state]: value})
    assert r.status_code == 200, r.text
    assert r.json()[SETTING[state]] == value


def _employer_share(state, stub):
    other = D(str(stub["state_other_employer"]))
    return other - WA_LNI_EMPLOYER if state == "WA" else other


def _company(client, db_session, state, count):
    """A company of ``count`` active employees in ``state``; the one paid."""
    emp = _employee(client, "Paid", work_state=state)
    _fill(db_session, state, count - 1)
    return emp


CHARGED = {"WA": WA_PFML_EMPLOYER, "OR": OR_PAID_LEAVE_EMPLOYER}


@pytest.mark.parametrize("state", ["WA", "OR"])
@pytest.mark.parametrize("value", ["yes", "no", "headcount"])
@pytest.mark.parametrize("side", ["below", "at_or_above"])
def test_the_setting_decides_the_employer_share_on_each_side_of_the_threshold(
    client, db_session, state, value, side
):
    below, above = COUNTS[state]
    count = below if side == "below" else above
    emp = _company(client, db_session, state, count)
    _set(client, state, value)
    run = _run(client, emp["id"])
    [stub] = run["stubs"]
    by_count = side == "at_or_above"
    applies = {"yes": True, "no": False, "headcount": by_count}[value]
    assert _employer_share(state, stub) == (CHARGED[state] if applies else D("0"))
    # the employee's share is withheld whatever the setting says
    assert stub["state_other_employee"] > 0
    share_warnings = [w for w in run["warnings"] if "employer share" in w]
    if value == "headcount":
        # following the headcount says nothing new, as before the setting
        assert run["notes"] == [] and stub["notes"] == []
        assert share_warnings == []
    else:
        [note] = run["notes"]
        assert note in stub["notes"]
        assert ("says yes" if applies else "says no") in note
        assert len(share_warnings) == (0 if applies == by_count else 1)


def test_the_stub_and_the_run_say_which_applied_and_why(client, db_session):
    emp = _company(client, db_session, "WA", 12)
    _set(client, "WA", "yes")
    run = _run(client, emp["id"])
    expected = (
        "Washington PFML employer share charged: Settings → Payroll → "
        "Employer share applies (as the agency determined) says yes, as ESD "
        "determined (12 employees working in Washington at this run)."
    )
    assert run["notes"] == [expected]
    assert run["stubs"][0]["notes"] == [expected]
    # and wherever the run is read again
    got = client.get(f"/api/payroll/{run['id']}").json()
    assert got["notes"] == [expected]
    assert got["stubs"][0]["notes"] == [expected]
    listed = {r["id"]: r for r in client.get("/api/payroll").json()}
    assert listed[run["id"]]["notes"] == [expected]


def test_oregon_says_not_charged_and_counts_every_employee(client, db_session):
    emp = _company(client, db_session, "OR", 30)
    # Oregon sizes by every employee, in or out of state
    _fill(db_session, "TX", 3)
    _set(client, "OR", "no")
    run = _run(client, emp["id"])
    expected = (
        "Paid Leave Oregon employer share not charged: Settings → Payroll → "
        "Employer share applies (as the agency determined) says no, as OED "
        "determined (33 employees at this run)."
    )
    assert run["notes"] == [expected]


def test_the_run_names_the_setting_when_the_headcount_disagrees(client, db_session):
    wa = _company(client, db_session, "WA", 52)
    _set(client, "WA", "no")
    run = _run(client, wa["id"])
    [warning] = [w for w in run["warnings"] if "employer share" in w]
    assert warning == (
        "Washington PFML: the employer share was not charged because "
        "Settings → Payroll → Employer share applies (as the agency "
        "determined) says no, though this run counts 52 employees working in "
        "Washington, 50 or more. If ESD's latest letter says otherwise, "
        "change the setting and run the stubs again."
    )


def test_one_warning_per_state_however_many_stubs(client, db_session):
    first = _employee(client, "One", work_state="OR")
    second = _employee(client, "Two", work_state="OR")
    _set(client, "OR", "yes")
    run = _run(client, first["id"], second["id"])
    share_warnings = [w for w in run["warnings"] if "employer share" in w]
    assert share_warnings == [
        "Paid Leave Oregon: the employer share was charged because Settings "
        "→ Payroll → Employer share applies (as the agency determined) says "
        "yes, though this run counts 2 employees, fewer than the 25 the share "
        "starts at. If OED's latest letter says otherwise, change the setting "
        "and run the stubs again."
    ]
    assert len(run["notes"]) == 1
    assert all(len(s["notes"]) == 1 for s in run["stubs"])


def test_a_setting_reaches_only_its_own_state(client, db_session):
    wa = _employee(client, "Wa", work_state="WA")
    tx = _employee(client, "Tx", work_state="TX")
    _set(client, "OR", "yes")
    run = _run(client, wa["id"], tx["id"])
    assert run["notes"] == []
    by_name = {s["employee_name"]: s for s in run["stubs"]}
    # one Washington employee: no PFML employer share, by the headcount
    assert _employer_share("WA", by_name["Test Wa"]) == D("0")


def test_settings_answer_headcount_until_set_and_refuse_anything_else(client):
    got = client.get("/api/settings").json()
    assert got[SETTING["WA"]] == "headcount"
    assert got[SETTING["OR"]] == "headcount"
    r = client.put(
        "/api/settings", json={SETTING["WA"]: "maybe", "company_name": "Changed"}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == (
        "Washington PFML, Employer share applies (as the agency determined), "
        "must be yes (ESD determined the employer share applies), no (it "
        "determined it does not) or headcount (follow the headcount at each "
        "pay run: 50 or more employees working in Washington). Nothing was "
        "saved."
    )
    r = client.put("/api/settings", json={SETTING["OR"]: ""})
    assert r.status_code == 422
    assert "Paid Leave Oregon" in r.json()["detail"]
    assert "25 or more employees" in r.json()["detail"]
    got = client.get("/api/settings").json()
    assert got[SETTING["WA"]] == "headcount"
    assert got["company_name"] != "Changed"
    _set(client, "WA", "yes")
    _set(client, "OR", "no")
    _set(client, "WA", "headcount")


def test_an_unset_company_runs_exactly_as_before(client, db_session):
    """No setting saved: the same amounts, notes and warnings as a company
    set to follow the headcount, and the engines are asked exactly what
    they were asked before the setting (no override)."""
    from app.services.state_tax import get_engine

    wa = _company(client, db_session, "WA", 50)
    ore = _employee(client, "Or", work_state="OR")
    unset = _run(client, wa["id"], ore["id"])
    _set(client, "WA", "headcount")
    _set(client, "OR", "headcount")
    followed = _run(client, wa["id"], ore["id"])

    def strip(run):
        return [
            {k: v for k, v in s.items() if k not in ("id", "pay_run_id")}
            for s in run["stubs"]
        ]

    assert strip(unset) == strip(followed)
    assert unset["warnings"] == followed["warnings"] == []
    assert unset["notes"] == followed["notes"] == []
    by_name = {s["employee_name"]: s for s in unset["stubs"]}
    # 50 in Washington: the PFML share; 52 employees in all: Oregon's too
    assert _employer_share("WA", by_name["Test Paid"]) == WA_PFML_EMPLOYER
    assert _employer_share("OR", by_name["Test Or"]) == OR_PAID_LEAVE_EMPLOYER

    # the engines with no override are the engines before #825
    base = dict(
        gross=D("2000"),
        taxable=D("2000"),
        ytd_gross=D("0"),
        pay_periods=26,
        hours=D("0"),
        filing_status="single",
        wc_class_code=None,
    )
    for code, kw in (
        ("WA", {"state_employee_count": 49}),
        ("OR", {"employee_count": 24}),
    ):
        before = get_engine(code).calculate(**base, **kw)
        none = get_engine(code).calculate(**base, **kw, employer_share_applies=None)
        assert before == none
        forced = get_engine(code).calculate(**base, **kw, employer_share_applies=True)
        assert forced.employer_other > before.employer_other
        assert forced.employee_other == before.employee_other
