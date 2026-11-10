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
    plain = _bearer(_mint(client, "no-imports")["token"])
    # any bookkeeper reads the sources; the grant shows on a write, where an
    # import token gets past the gate to the route's own 422
    assert api.get("/api/migration/sources").status_code == 200
    assert plain.post("/api/migration/qbo/dry-run", json={}).status_code == 403
    assert api.post("/api/migration/qbo/dry-run", json={}).status_code == 422
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


def test_a_payroll_token_never_gets_the_nacha_file(
    client, seed_accounts, payroll_token
):
    """The NACHA file decrypts every employee's full routing and account
    number and takes the originating account from the request."""
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
    for r in (
        payroll_token.get(f"/api/payroll/{run['id']}/nacha"),
        payroll_token.post(
            f"/api/payroll/{run['id']}/nacha", json={"company_name": "EVIL CO"}
        ),
        payroll_token.get(f"/api/payroll/{run['id']}/nacha/"),
    ):
        assert r.status_code == 403, r.text
        assert "EVIL" not in r.text


def test_the_list_shows_only_a_grant_the_gate_honours(client, db_session):
    from app.models.api_tokens import ApiToken

    made = _mint(client, "forced-list", "readonly")
    row = db_session.get(ApiToken, made["id"])
    row.allow_payroll = True
    row.allow_imports = True
    db_session.commit()
    listed = next(t for t in client.get("/api/tokens").json() if t["id"] == made["id"])
    assert listed["allow_payroll"] is False and listed["allow_imports"] is False


def test_only_an_employee_id_is_a_payroll_route(client, payroll_token):
    from app.main import _is_token_payroll_route

    assert _is_token_payroll_route("GET", "/api/employees/12")
    assert _is_token_payroll_route("PUT", "/api/employees/12/ytd")
    assert not _is_token_payroll_route("GET", "/api/employees/export")
    assert not _is_token_payroll_route("DELETE", "/api/employees/12")


def test_the_create_form_locks_the_grants_to_a_bookkeeper():
    from pathlib import Path

    js = Path("app/static/js/settings.js").read_text(encoding="utf-8")
    assert 'id="token-new-payroll" name="token_new_allow_payroll" disabled' in js
    assert 'id="token-new-imports" name="token_new_allow_imports" disabled' in js
    assert 'onchange="SettingsPage.tokenGrantsFollowRole()"' in js


def test_a_prefix_grants_only_its_own_segments():
    from app.main import _is_token_payroll_route

    assert _is_token_payroll_route("GET", "/api/payroll")
    assert _is_token_payroll_route("GET", "/api/payroll/3")
    assert not _is_token_payroll_route("GET", "/api/payrollx")
    assert not _is_token_payroll_route("GET", "/api/payroll-export")
    assert not _is_token_payroll_route("GET", "/api/benefits-admin")


def test_the_migration_adds_and_removes_the_two_columns(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    db = tmp_path / "tokens.db"
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()

    def columns():
        with sqlite3.connect(db) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(api_tokens)")}

    command.upgrade(cfg, "f3b9d2e6a1c7")
    assert {"allow_payroll", "allow_imports"} <= columns()
    command.downgrade(cfg, "e2b6c4d8f0a3")
    assert not {"allow_payroll", "allow_imports"} & columns()
    command.upgrade(cfg, "head")
    assert {"allow_payroll", "allow_imports"} <= columns()


def test_tax_forms_refuse_a_year_out_of_range_and_an_unknown_employee(
    client, seed_accounts, payroll_token
):
    """Both answered on main before the grant reached them: year 99999 was a
    500 from date(), and a W-2 for an employee not on file was a $0 form."""
    emp = payroll_token.post("/api/employees", json=EMPLOYEE).json()
    r = payroll_token.get(f"/api/tax-forms/w2/{emp['id']}/pdf?year=99999")
    assert r.status_code == 422, r.text
    for path in (
        "/api/tax-forms/w2/999999?year=2026",
        "/api/tax-forms/w2/999999/pdf?year=2026",
    ):
        r = payroll_token.get(path)
        assert r.status_code == 404, (path, r.text)
        assert r.json()["detail"] == "Employee not found"
    assert (
        payroll_token.get(f"/api/tax-forms/w2/{emp['id']}?year=2026").status_code == 200
    )


def test_an_import_token_without_payroll_adds_no_employees(
    client, db_session, seed_accounts
):
    """The QuickBooks Online export's Employee Contact List adds employees,
    which is HR: a token let import but not run payroll imports the books
    without it and says so, its dry run says the same beforehand (NEW-222),
    and asking for the list is refused; one let do both brings them in."""
    from app.models.accounts import Account
    from app.models.payroll import Employee
    from tests.test_qbo_export_migration import _controls_export

    files = _controls_export(aging=False)
    importer = _bearer(_mint(client, "books-import", allow_imports=True)["token"])

    # the dry run says what the import will do: the list is found, and left out
    dry = importer.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["ok"] is True, dry["errors"]
    assert dry["employees"] == 0
    listed = dry["employee_list"]
    assert listed["employees"] == 3 and listed["included"] is False
    assert listed["may_add"] is False
    assert any(
        "Employee Contact List will be left out" in w
        and "an administrator, or a token that may also run payroll" in w
        for w in dry["warnings"]
    ), dry["warnings"]

    # asking for it is refused, saying why, and nothing is imported
    accounts = db_session.query(Account).count()
    for path in ("dry-run", "import"):
        r = importer.post(
            f"/api/migration/qbo_export/{path}",
            files=files(),
            data={"include_employees": "true"},
        )
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail.startswith("The Employee Contact List cannot come in:")
        assert "a token that may also run payroll" in detail
        assert ("Nothing was imported." in detail) == (path == "import")
    db_session.expire_all()
    assert db_session.query(Account).count() == accounts
    assert db_session.query(Employee).count() == 0

    # left out of the request: the import matches its dry run
    data = importer.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_employees"] == dry["employees"] == 0
    assert db_session.query(Employee).count() == 0
    assert any("Employee Contact List was left out" in w for w in data["warnings"])

    both = _bearer(
        _mint(client, "books-both", allow_imports=True, allow_payroll=True)["token"]
    )
    dry = both.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["employee_list"]["included"] is True and dry["employees"] == 3
    assert "come in with the import" in dry["employee_list"]["note"]
    assert not any("Employee Contact List" in w for w in dry["warnings"])
    again = both.post(
        "/api/migration/qbo_export/import",
        files=files(),
        data={"include_employees": "true"},
    ).json()
    assert again["ok"] is True, again["errors"]
    assert again["imported_employees"] == dry["employees"] == 3
    assert db_session.query(Employee).count() == 3
    names = {
        r.username
        for r in db_session.query(AuditLog).filter(AuditLog.table_name == "employees")
    }
    assert names == {"token:books-both"}


def test_include_employees_false_leaves_the_list_out_for_an_administrator(
    client, db_session, seed_accounts
):
    from app.models.payroll import Employee
    from tests.test_qbo_export_migration import _controls_export

    files = _controls_export(aging=False)
    dry = client.post(
        "/api/migration/qbo_export/dry-run",
        files=files(),
        data={"include_employees": "false"},
    ).json()
    assert dry["ok"] is True, dry["errors"]
    assert dry["employees"] == 0
    assert dry["employee_list"] == {
        "employees": 3,
        "included": False,
        "may_add": True,
        "note": "The Employee Contact List (3 employees) will be left out, as asked.",
    }
    data = client.post(
        "/api/migration/qbo_export/import",
        files=files(),
        data={"include_employees": "false"},
    ).json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_employees"] == 0
    assert db_session.query(Employee).count() == 0
    assert (
        "The Employee Contact List (3 employees) was left out, as asked."
        in data["warnings"]
    )
    # and left out of the request, an administrator's import brings it in
    dry = client.post("/api/migration/qbo_export/dry-run", files=files()).json()
    assert dry["employee_list"]["included"] is True and dry["employees"] == 3
    data = client.post("/api/migration/qbo_export/import", files=files()).json()
    assert data["imported_employees"] == 3
    assert db_session.query(Employee).count() == 3


def test_an_export_without_the_list_says_nothing_about_it(
    client, db_session, seed_accounts
):
    from tests.test_qbo_export_migration import _controls_export

    files = _controls_export(aging=False)
    kept = [f for f in files() if "Employee" not in f[1][0]]
    assert len(kept) < len(files())
    dry = client.post("/api/migration/qbo_export/dry-run", files=kept).json()
    assert "employee_list" not in dry
    importer = _bearer(_mint(client, "books-import-2", allow_imports=True)["token"])
    dry = importer.post("/api/migration/qbo_export/dry-run", files=kept).json()
    assert "employee_list" not in dry
    assert not any("Employee Contact List" in w for w in dry["warnings"])
