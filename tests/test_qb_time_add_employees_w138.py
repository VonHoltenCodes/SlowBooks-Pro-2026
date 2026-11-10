"""Adding people to the Employees list from a QuickBooks Desktop time report
is a person's choice (W-138, 2.31.12).

Time by Name by Job adds an employee for each person it does not know, and
employee writes are an administrator's for every other role: a bookkeeper
token with no grants added the report's people through it. ``add_employees``
is the choice: left out of the request, an administrator (or a token let run
payroll) adds them as before, and anyone else's import leaves their entries
out and names them (an entry is always someone's); ``true`` from anyone else
is refused and nothing is imported; ``false`` leaves them out for anyone.
"""

import io

from fastapi.testclient import TestClient

from app.main import app
from app.models.audit import AuditLog
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry
from tests.test_qb_desktop_time import BY_NAME, _imported


def _bearer(client, label, **grants):
    r = client.post(
        "/api/tokens", json={"label": label, "role": "bookkeeper", **grants}
    )
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _post(api, **data):
    return api.post(
        "/api/csv/import/qb-report",
        files={
            "file": (BY_NAME.name, io.BytesIO(BY_NAME.read_bytes()), "application/x")
        },
        data=data,
    )


def _two_known(db):
    db.add(Employee(first_name="M", last_name="Alvarez"))
    db.add(Employee(first_name="R", last_name="Chen"))
    db.commit()


def test_an_administrator_adds_unknown_people_as_before(
    client, db_session, seed_accounts
):
    _two_known(db_session)
    r = _post(client).json()
    assert r["time_entries"] == 26 and r["entries_left_out"] == 0, r
    assert r["people_left_out"] == []
    assert db_session.query(Employee).count() == 3
    assert any("1 person(s)" in w and "'Okafor, T'" in w for w in r["warnings"])


def test_a_no_grant_token_leaves_unknown_people_out_and_names_them(
    client, db_session, seed_accounts
):
    _two_known(db_session)
    api = _bearer(client, "plain-books")
    r = _post(api)
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["errors"] == [], r
    assert db_session.query(Employee).count() == 2  # nobody added
    entries = _imported(db_session)
    assert {e.employee.last_name for e in entries} == {"Alvarez", "Chen"}
    left = 26 - len(entries)
    assert left > 0 and r["time_entries"] == len(entries)
    assert r["people_left_out"] == ["Okafor, T"]
    assert r["entries_left_out"] == left
    said = next(w for w in r["warnings"] if "not on the Employees list" in w)
    assert said == (
        f"1 person(s) in the report are not on the Employees list, so their "
        f"{left} entries were left out: 'Okafor, T' ({left}). An administrator "
        "adds them on the Employees page, or a token that may run payroll "
        "imports this report; once they are on the list, importing it again "
        "brings their time in."
    )
    assert not any("were added from their names" in w for w in r["warnings"])
    # the same answer for add_employees=false
    again = _post(api, add_employees="false").json()
    assert again["entries_left_out"] == left and again["time_entries"] == 0
    assert again["duplicates_skipped"] == len(entries)


def test_add_employees_true_from_a_no_grant_token_is_refused_whole(
    client, db_session, seed_accounts
):
    api = _bearer(client, "plain-books")
    r = _post(api, add_employees="true")
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == (
        "Adding people to the Employees list needs an administrator, or a token "
        "that may also run payroll. Leave add_employees out, or send it false, "
        "to import the time of the people already on the list. Nothing was "
        "imported."
    )
    assert db_session.query(TimeEntry).count() == 0
    assert db_session.query(Employee).count() == 0


def test_a_payroll_token_adds_them_audited_as_the_token(
    client, db_session, seed_accounts
):
    api = _bearer(client, "books-payroll", allow_payroll=True)
    r = _post(api, add_employees="true").json()
    assert r["time_entries"] == 26 and r["entries_left_out"] == 0, r
    assert db_session.query(Employee).count() == 3
    names = {
        a.username
        for a in db_session.query(AuditLog).filter(AuditLog.table_name == "employees")
    }
    assert names == {"token:books-payroll"}


def test_an_administrator_can_choose_to_leave_them_out(
    client, db_session, seed_accounts
):
    _two_known(db_session)
    r = _post(client, add_employees="false").json()
    assert db_session.query(Employee).count() == 2
    left = r["entries_left_out"]
    assert left > 0 and r["time_entries"] == 26 - left
    said = next(w for w in r["warnings"] if "not on the Employees list" in w)
    assert f"so their {left} entries were left out, as asked: 'Okafor, T'" in said
    assert "Add them on the Employees page, then import the report again" in said
    # added afterwards, the report again brings only their time in
    db_session.add(Employee(first_name="T", last_name="Okafor"))
    db_session.commit()
    again = _post(client, add_employees="false").json()
    assert again["time_entries"] == left and again["entries_left_out"] == 0
    assert again["duplicates_skipped"] == 26 - left


def test_a_signed_in_bookkeeper_is_like_the_no_grant_token(
    client, db_session, seed_accounts
):
    from app.models.users import ROLE_BOOKKEEPER, User
    from app.services import auth as auth_service

    _two_known(db_session)
    db_session.add(
        User(
            username="pat",
            password_hash=auth_service.hash_password("pat-password-1"),
            role=ROLE_BOOKKEEPER,
        )
    )
    db_session.commit()
    person = TestClient(app)
    r = person.post(
        "/api/auth/login", json={"username": "pat", "password": "pat-password-1"}
    )
    assert r.status_code == 200, r.text
    assert _post(person, add_employees="true").status_code == 403
    assert db_session.query(TimeEntry).count() == 0
    r = _post(person).json()
    assert r["people_left_out"] == ["Okafor, T"] and r["entries_left_out"] > 0
    assert db_session.query(Employee).count() == 2
