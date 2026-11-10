"""An API token's "Can run payroll" and "Can import" permissions (#755).

A token is never an administrator, and payroll, HR and the migration imports
were an administrator's. An administrator can now let one bookkeeper token
run payroll (pay runs, tax forms, benefits, deductions, and employees added
and edited with their pay fields) or run the imports, the way "Can make
backups" works (#434). Never a delete under /api/employees, never a portal
login, bank account, document, E-Verify or onboarding, and never on any
role but bookkeeper.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.audit import AuditLog

EMPLOYEE = {
    "first_name": "Dale",
    "last_name": "Whitaker",
    "pay_type": "salary",
    "pay_rate": 68000,
    "pay_frequency": "biweekly",
    "filing_status": "married",
    "work_state": "FL",
    "ssn_last_four": "4821",
}


def _mint(client, label, role="bookkeeper", **extra):
    r = client.post("/api/tokens", json={"label": label, "role": role, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(secret):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {secret}"
    return c


@pytest.fixture
def payroll_token(client):
    created = _mint(client, "books-agent", allow_payroll=True)
    assert created["allow_payroll"] is True and created["allow_imports"] is False
    return _bearer(created["token"])


def test_a_payroll_token_adds_an_employee_and_runs_payroll(
    client, seed_accounts, payroll_token, db_session
):
    api = payroll_token
    r = api.post("/api/employees", json=EMPLOYEE)
    assert r.status_code == 201, r.text
    emp = r.json()
    # it sees the pay fields other non-admin roles see redacted
    got = api.get(f"/api/employees/{emp['id']}").json()
    assert float(got["pay_rate"]) == 68000 and got["filing_status"] == "married"
    r = api.put(f"/api/employees/{emp['id']}", json={"pay_rate": 70000})
    assert r.status_code == 200, r.text
    assert api.get(f"/api/employees/{emp['id']}/ytd").status_code == 200

    run = api.post(
        "/api/payroll",
        json={
            "period_start": "2026-07-01",
            "period_end": "2026-07-14",
            "pay_date": "2026-07-17",
            "stubs": [{"employee_id": emp["id"]}],
        },
    )
    assert run.status_code in (200, 201), run.text
    p = api.post(f"/api/payroll/{run.json()['id']}/process")
    assert p.status_code == 200, p.text
    assert api.get("/api/payroll").status_code == 200
    assert api.get("/api/tax-forms/w2?year=2026").status_code == 200
    assert api.get("/api/benefits/enrollments").status_code == 200

    # the audit log names the program
    names = {
        r.username
        for r in db_session.query(AuditLog).filter(AuditLog.table_name == "employees")
    }
    assert names == {"token:books-agent"}


def test_a_payroll_token_never_deletes_or_touches_credentials(
    client, seed_accounts, payroll_token
):
    emp = client.post("/api/employees", json=EMPLOYEE).json()  # the administrator's
    api = payroll_token
    eid = emp["id"]
    refused = [
        api.delete(f"/api/employees/{eid}"),
        api.delete(f"/api/employees/{eid}/opening-ytd"),
        api.get(f"/api/employees/{eid}/portal-token"),
        api.post(f"/api/employees/{eid}/portal-token"),
        api.get(f"/api/employees/{eid}/portal-access"),
        api.get(f"/api/employees/{eid}/bank-accounts"),
        api.post(f"/api/employees/{eid}/bank-accounts", json={}),
        api.get(f"/api/employees/{eid}/documents"),
        api.get(f"/api/employees/{eid}/everify"),
        api.get("/api/onboarding/tasks"),
    ]
    assert [r.status_code for r in refused] == [403] * len(refused)
    # and still none of an administrator's own
    assert api.put("/api/settings", json={"invoice_prefix": "X-"}).status_code == 403
    assert api.get("/api/tokens").status_code == 403
    assert api.post("/api/migration/qbo/dry-run", json={}).status_code == 403


def test_without_the_permission_a_bookkeeper_token_still_gets_no_payroll(
    client, seed_accounts
):
    emp = client.post("/api/employees", json=EMPLOYEE).json()
    api = _bearer(_mint(client, "plain-books")["token"])
    assert api.get("/api/payroll").status_code == 403
    assert api.post("/api/employees", json=EMPLOYEE).status_code == 403
    assert api.get(f"/api/employees/{emp['id']}/ytd").status_code == 403
    # the directory still lists, redacted
    listed = api.get("/api/employees").json()
    assert [e["id"] for e in listed] == [emp["id"]]
    assert float(listed[0]["pay_rate"]) == 0


def test_only_a_bookkeeper_token_gets_payroll_or_imports(client):
    for role in ("accountant", "readonly"):
        for grant in ("allow_payroll", "allow_imports"):
            r = client.post(
                "/api/tokens",
                json={"label": f"{role}-{grant}", "role": role, grant: True},
            )
            assert r.status_code == 400, (role, grant, r.text)
            assert "Only a bookkeeper token" in r.json()["detail"]
    reader = _mint(client, "reader")
    reader_ro = _mint(client, "reader-ro", "readonly")
    r = client.put(f"/api/tokens/{reader_ro['id']}", json={"allow_payroll": True})
    assert r.status_code == 400
    r = client.put(f"/api/tokens/{reader['id']}", json={"allow_payroll": True})
    assert r.status_code == 200 and r.json()["allow_payroll"] is True
    # turning it off again takes effect on the next request
    api = _bearer(reader["token"])
    assert api.get("/api/payroll").status_code == 200
    client.put(f"/api/tokens/{reader['id']}", json={"allow_payroll": False})
    assert api.get("/api/payroll").status_code == 403


def test_an_import_token_reaches_the_imports_and_not_payroll(client):
    api = _bearer(_mint(client, "importer", allow_imports=True)["token"])
    assert api.get("/api/migration/sources").status_code == 200
    assert api.get("/api/payroll").status_code == 403
    assert api.put("/api/settings", json={"invoice_prefix": "X-"}).status_code == 403


def test_a_signed_in_bookkeeper_is_unchanged(client, db_session):
    from app.models.users import ROLE_BOOKKEEPER, User
    from app.services import auth as auth_service

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
    assert person.get("/api/payroll").status_code == 403
    assert person.post("/api/employees", json=EMPLOYEE).status_code == 403


def test_a_grant_stored_on_another_role_gives_nothing(client, db_session):
    """Settings refuses the grants on any role but bookkeeper; a row that
    carries one anyway (edited in the database, an older build) gets nothing
    from it at the gate."""
    from app.models.api_tokens import ApiToken

    for role in ("readonly", "accountant"):
        made = _mint(client, f"forced-{role}", role)
        row = db_session.get(ApiToken, made["id"])
        row.allow_payroll = True
        row.allow_imports = True
        db_session.commit()
        api = _bearer(made["token"])
        assert api.get("/api/payroll").status_code == 403, role
        assert api.post("/api/employees", json=EMPLOYEE).status_code == 403, role
        r = api.post("/api/migration/qbo/dry-run", json={})
        assert r.status_code == 403, (role, r.text)


def test_the_pay_run_is_audited_as_the_token(
    client, seed_accounts, payroll_token, db_session
):
    emp = payroll_token.post("/api/employees", json=EMPLOYEE).json()
    run = payroll_token.post(
        "/api/payroll",
        json={
            "period_start": "2026-07-01",
            "period_end": "2026-07-14",
            "pay_date": "2026-07-17",
            "stubs": [{"employee_id": emp["id"]}],
        },
    ).json()
    assert payroll_token.post(f"/api/payroll/{run['id']}/process").status_code == 200
    by_table = {}
    for r in db_session.query(AuditLog).filter(
        AuditLog.table_name.in_(("pay_runs", "pay_stubs", "transactions"))
    ):
        by_table.setdefault(r.table_name, set()).add(r.username)
    assert by_table and all(
        names == {"token:books-agent"} for names in by_table.values()
    ), by_table
