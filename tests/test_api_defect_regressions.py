"""Regressions for defects found in a full-surface sweep of the 357-operation API.

Each test corresponds to a specific observed failure, recorded here so the
behaviour cannot silently return.
"""

from fastapi.testclient import TestClient

from app.main import app


def _bearer(token):
    """Fresh cookie-less client so the Bearer path (not the session) auths."""
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _mint(client, label="sweep", role="admin"):
    r = client.post("/api/tokens", json={"label": label, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ---------------------------------------------------------------------------
# Employee pay_type / role were bare strings while the columns are enums, so
# an invalid value was written and then made the row permanently unreadable —
# taking GET /api/employees down for the whole company.
# ---------------------------------------------------------------------------


def _make_employee(client, **over):
    body = {"first_name": "Grace", "last_name": "Hopper"}
    body.update(over)
    return client.post("/api/employees", json=body)


def test_invalid_pay_type_is_rejected_not_persisted(client):
    r = _make_employee(client, pay_type="not-a-pay-type")
    assert r.status_code == 422, r.text


def test_invalid_role_is_rejected(client):
    r = _make_employee(client, role="sweep-role")
    assert r.status_code == 422, r.text


def test_valid_pay_types_and_roles_still_accepted(client):
    for pt in ("hourly", "salary"):
        assert _make_employee(client, pay_type=pt).status_code in (200, 201)
    for role in ("employee", "manager", "admin"):
        assert _make_employee(client, role=role).status_code in (200, 201)


def test_bad_update_cannot_brick_the_employee_roster(client):
    """The original failure: one bad PUT made the record and the whole list
    unreadable. The write must be refused and every read stay healthy."""
    created = _make_employee(client)
    assert created.status_code in (200, 201), created.text
    emp_id = created.json()["id"]

    bad = client.put(f"/api/employees/{emp_id}", json={"pay_type": "not-a-pay-type"})
    assert bad.status_code == 422, bad.text

    assert client.get(f"/api/employees/{emp_id}").status_code == 200
    assert client.get("/api/employees").status_code == 200


# ---------------------------------------------------------------------------
# Account constraint violations escaped as 500s instead of business errors.
# ---------------------------------------------------------------------------


def _mk_account(client, name, number=None, atype="asset"):
    body = {"name": name, "account_type": atype}
    if number:
        body["account_number"] = number
    return client.post("/api/accounts", json=body)


def test_duplicate_account_number_on_create_is_409(client):
    assert _mk_account(client, "First", "7001").status_code == 201
    dup = _mk_account(client, "Second", "7001")
    assert dup.status_code == 409, dup.text
    assert "7001" in dup.json()["detail"]
    assert "First" in dup.json()["detail"]


def test_duplicate_account_number_on_update_is_409(client):
    _mk_account(client, "Alpha", "7101")
    b = _mk_account(client, "Beta", "7102").json()
    r = client.put(f"/api/accounts/{b['id']}", json={"account_number": "7101"})
    assert r.status_code == 409, r.text
    assert "Alpha" in r.json()["detail"]


def test_account_cannot_be_its_own_parent(client):
    a = _mk_account(client, "Selfref", "7201").json()
    r = client.put(f"/api/accounts/{a['id']}", json={"parent_id": a["id"]})
    assert r.status_code == 400, r.text


def test_delete_account_with_postings_is_409_not_500(client):
    debit = _mk_account(client, "Sweep Debit", "7301").json()
    credit = _mk_account(client, "Sweep Credit", "7302", atype="income").json()
    je = client.post(
        "/api/journal",
        json={
            "date": "2026-08-01",
            "description": "regression posting",
            "lines": [
                {"account_id": debit["id"], "debit": "50.00", "credit": "0"},
                {"account_id": credit["id"], "debit": "0", "credit": "50.00"},
            ],
        },
    )
    assert je.status_code in (200, 201), je.text

    r = client.delete(f"/api/accounts/{debit['id']}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "Deactivate" in detail or "deactivate" in detail


def test_delete_unused_account_still_works(client):
    a = _mk_account(client, "Disposable", "7401").json()
    assert client.delete(f"/api/accounts/{a['id']}").status_code == 200


# ---------------------------------------------------------------------------
# The closing-date lock could be switched off by the principal it constrains.
# ---------------------------------------------------------------------------


def test_token_cannot_clear_or_roll_back_closing_date(client):
    assert (
        client.put("/api/settings", json={"closing_date": "2026-06-30"}).status_code
        == 200
    )
    tc = _bearer(_mint(client))

    cleared = tc.put("/api/settings", json={"closing_date": ""})
    assert cleared.status_code == 403, cleared.text

    rolled = tc.put("/api/settings", json={"closing_date": "2026-01-01"})
    assert rolled.status_code == 403, rolled.text

    assert client.get("/api/settings").json()["closing_date"] == "2026-06-30"


def test_token_may_still_tighten_the_closing_date(client):
    client.put("/api/settings", json={"closing_date": "2026-06-30"})
    tc = _bearer(_mint(client))
    r = tc.put("/api/settings", json={"closing_date": "2026-07-31"})
    assert r.status_code == 200, r.text


def test_token_cannot_set_closing_date_password(client):
    tc = _bearer(_mint(client))
    r = tc.put("/api/settings", json={"closing_date_password": "agent-set-this"})
    assert r.status_code == 403, r.text


def test_session_user_may_still_clear_closing_date(client):
    client.put("/api/settings", json={"closing_date": "2026-06-30"})
    r = client.put("/api/settings", json={"closing_date": ""})
    assert r.status_code == 200, r.text


def test_token_settings_writes_are_otherwise_unaffected(client):
    """The guard must not block unrelated settings updates once a lock is set."""
    client.put("/api/settings", json={"closing_date": "2026-06-30"})
    tc = _bearer(_mint(client))
    r = tc.put("/api/settings", json={"company_name": "Still Editable LLC"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# send_email() was called with a signature it no longer had, so emailing an
# invoice raised TypeError -> 500 regardless of SMTP configuration.
# ---------------------------------------------------------------------------


def test_invoice_email_does_not_raise_typeerror(client, seed_customer):
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-08-01",
            "lines": [{"description": "x", "quantity": "1", "rate": "10.00"}],
        },
    )
    assert inv.status_code in (200, 201), inv.text
    r = client.post(
        f"/api/invoices/{inv.json()['id']}/email",
        json={"recipient": "someone@example.com"},
    )
    # SMTP is unconfigured under test, so a clean 502 is the expected outcome.
    # What must never come back is the old TypeError surfacing as a 500.
    assert r.status_code != 500, r.text
    assert "unexpected keyword argument" not in r.text


# ---------------------------------------------------------------------------
# Rows corrupted before the validation fix cannot be repaired through the API
# (they cannot be read, updated or deleted), so scripts/repair_employee_enums.py
# works in raw SQL. This proves the whole cycle: corrupt -> roster breaks ->
# repair -> roster healthy.
# ---------------------------------------------------------------------------


def test_repair_script_recovers_a_bricked_roster(client, db_session):
    from sqlalchemy import text

    from scripts.repair_employee_enums import repair, scan

    created = _make_employee(client, first_name="Ada", last_name="Lovelace")
    assert created.status_code in (200, 201), created.text
    emp_id = created.json()["id"]
    assert client.get("/api/employees").status_code == 200

    # Reproduce the pre-fix damage directly, since the API now refuses it.
    db_session.execute(
        text("UPDATE employees SET pay_type = :v WHERE id = :id"),
        {"v": "sweep-pay_type", "id": emp_id},
    )
    db_session.commit()

    found = scan(db_session)
    assert any(f["id"] == emp_id for f in found), found
    assert found[0]["invalid"]["pay_type"] == "sweep-pay_type"

    repair(db_session, found)

    assert scan(db_session) == []
    row = db_session.execute(
        text("SELECT pay_type FROM employees WHERE id = :id"), {"id": emp_id}
    ).scalar()
    assert row == "HOURLY"  # SQLAlchemy Enum persists the member NAME

    # The roster reads again, which is the whole point.
    assert client.get("/api/employees").status_code == 200
    assert client.get(f"/api/employees/{emp_id}").status_code == 200


def test_repair_script_is_a_noop_on_healthy_data(client, db_session):
    from scripts.repair_employee_enums import scan

    _make_employee(client, pay_type="salary", role="manager")
    assert scan(db_session) == []
