"""Regressions for the four private security reports fixed in 2.9.2.

GHSA-rh68-48w8-pj8r  bookkeeper could mint any employee's portal token
GHSA-rh75-6834-f66j  bookkeeper could rewrite direct-deposit accounts + export NACHA
GHSA-pwj7-6qq3-h4fj  readonly could download pay stubs, W-2s, I-9 documents
GHSA-rm5h-555g-vpjj  batch / bill payments took no row lock on the document

The first three share a root: the role gate in app.main treated every path
outside six admin prefixes as daily books. The contract here walks the real
route table so a new HR router cannot land outside the rule.
"""

import pytest

from app.main import (
    _ADMIN_ONLY_EMPLOYEE_RE,
    _ADMIN_ONLY_PREFIXES,
    _is_hr_sensitive,
    app,
)
from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY
from tests.test_api_tokens import _bearer, _mint
from tests.test_payroll import _create_employee
from tests.test_rbac_users import _login_as, _mk_user
from tests.test_sensitive_route_auth_contract import _fill_params, _iter_app_routes

ADMIN_PW = "test-password-123"


def _all_routes():
    for route in _iter_app_routes(app.routes):
        methods = getattr(route, "methods", None) or set()
        for m in methods - {"HEAD", "OPTIONS"}:
            yield m, route.path


def _hr_routes():
    return sorted({(m, p) for m, p in _all_routes() if _is_hr_sensitive(p)})


def test_the_rule_covers_the_hr_surface():
    """The prefixes the reports named, and every employee sub-resource that is
    a credential or a bank account, are in the rule."""
    for p in (
        "/api/payroll",
        "/api/tax-forms",
        "/api/benefits",
        "/api/deductions",
        "/api/onboarding",
    ):
        assert p in _ADMIN_ONLY_PREFIXES
    for sub in (
        "portal-token",
        "portal-access",
        "everify",
        "bank-accounts",
        "documents",
        "ytd",
    ):
        assert _ADMIN_ONLY_EMPLOYEE_RE.match(f"/api/employees/7/{sub}")
        assert _ADMIN_ONLY_EMPLOYEE_RE.match(f"/api/employees/7/{sub}/3")
    # the directory entry itself is not in the rule (redacted instead)
    assert not _is_hr_sensitive("/api/employees")
    assert not _is_hr_sensitive("/api/employees/7")
    # daily books stay daily books
    for p in (
        "/api/time-entries",
        "/api/pto/requests",
        "/api/customers",
        "/api/tax/rates",
    ):
        assert not _is_hr_sensitive(p)
    assert len(_hr_routes()) >= 40, _hr_routes()


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_BOOKKEEPER])
def test_every_hr_route_is_refused_below_admin(client, db_session, role):
    """Route-table contract: every method of every HR/payroll route answers
    403 to a readonly and a bookkeeper session — before any handler runs, so
    404s for missing rows never leak whether a row exists."""
    _mk_user(db_session, f"u-{role}", "role-password-123", role)
    _login_as(client, f"u-{role}", "role-password-123")
    wrong = []
    for method, path in _hr_routes():
        r = client.request(method, _fill_params(path), json={})
        if r.status_code != 403:
            wrong.append((method, path, r.status_code))
    assert not wrong, wrong


def test_bookkeeper_cannot_touch_a_staff_record_or_its_credentials(client, db_session):
    emp = _create_employee(client, ssn_last_four="1234", pay_rate=31.5)
    _mk_user(db_session, "keeper", "keeper-password-1", ROLE_BOOKKEEPER)
    _login_as(client, "keeper", "keeper-password-1")
    eid = emp["id"]
    assert client.get(f"/api/employees/{eid}/portal-token").status_code == 403
    assert client.post(f"/api/employees/{eid}/portal-token", json={}).status_code == 403
    assert client.get(f"/api/employees/{eid}/bank-accounts").status_code == 403
    assert (
        client.post(
            f"/api/employees/{eid}/bank-accounts",
            json={
                "routing_number": "021000021",
                "account_number": "1",
                "deposit_type": "checking",
            },
        ).status_code
        == 403
    )
    assert client.get(f"/api/employees/{eid}/documents").status_code == 403
    assert client.post("/api/payroll", json={}).status_code == 403
    assert client.post("/api/payroll/1/nacha", json={}).status_code == 403
    # staff records are an admin write
    assert client.post("/api/employees", json={"first_name": "x"}).status_code == 403
    assert client.put(f"/api/employees/{eid}", json={"pay_rate": 1}).status_code == 403
    # daily books still work
    assert client.get("/api/time-entries").status_code == 200


def test_readonly_cannot_read_pay_stubs_tax_forms_or_documents(client, db_session):
    emp = _create_employee(client)
    _mk_user(db_session, "viewer", "viewer-password-1", ROLE_READONLY)
    _login_as(client, "viewer", "viewer-password-1")
    assert client.get("/api/payroll/1/paystub/1").status_code == 403
    assert client.get("/api/payroll").status_code == 403
    assert client.get("/api/tax-forms/w2").status_code == 403
    assert client.get(f"/api/tax-forms/w2/{emp['id']}/pdf").status_code == 403
    assert client.get(f"/api/employees/{emp['id']}/documents").status_code == 403
    assert client.get(f"/api/employees/{emp['id']}/ytd").status_code == 403
    assert client.get("/api/benefits/enrollments").status_code == 403
    # reports and lookups, as documented
    assert client.get("/api/reports/profit-loss").status_code == 200
    assert client.get("/api/employees").status_code == 200


@pytest.mark.parametrize("role", [ROLE_READONLY, ROLE_BOOKKEEPER])
def test_employee_directory_is_redacted_below_admin(client, db_session, role):
    emp = _create_employee(
        client,
        ssn_last_four="1234",
        pay_rate=31.5,
        address1="1 Main St",
        city="Shorewood",
        zip="60404",
    )
    full = client.get(f"/api/employees/{emp['id']}").json()
    assert (
        full["ssn_last_four"] == "1234"
        and full["pay_rate"] == 31.5
        and full["address1"] == "1 Main St"
    )

    _mk_user(db_session, f"d-{role}", "role-password-123", role)
    _login_as(client, f"d-{role}", "role-password-123")
    for row in (
        client.get(f"/api/employees/{emp['id']}").json(),
        client.get("/api/employees").json()[0],
    ):
        assert row["id"] == emp["id"]
        assert row["first_name"] == "Pat" and row["last_name"] == "Worker"
        assert row["is_active"] is True and row["work_state"] == "WA"
        assert row["ssn_last_four"] is None
        assert row["pay_rate"] == 0 and row["cost_rate"] is None
        assert row["address1"] is None and row["city"] is None and row["zip"] is None
        assert row["filing_status"] == "" and row["extra_withholding"] == 0


def test_admin_keeps_the_hr_surface(client):
    emp = _create_employee(client)
    r = client.get(f"/api/employees/{emp['id']}/portal-token")
    assert r.status_code == 200 and r.json().get("portal_token")
    assert client.get(f"/api/employees/{emp['id']}/bank-accounts").status_code == 200
    assert client.get("/api/payroll").status_code == 200
    assert client.get("/api/tax-forms/w2?year=2026").status_code == 200


def test_api_tokens_wear_the_same_rule(client):
    """A bookkeeper-scoped token is the reporters' second vector."""
    emp = _create_employee(client)
    keeper = _bearer(_mint(client, "agent-bk", role="bookkeeper")["token"])
    assert keeper.get(f"/api/employees/{emp['id']}/portal-token").status_code == 403
    assert keeper.post("/api/payroll/1/nacha", json={}).status_code == 403
    assert keeper.get("/api/employees").status_code == 200
    assert keeper.get("/api/employees").json()[0]["pay_rate"] == 0
    admin = _bearer(_mint(client, "agent-admin", role="admin")["token"])
    assert admin.get(f"/api/employees/{emp['id']}/portal-token").status_code == 200
    assert admin.get("/api/employees").json()[0]["pay_rate"] == 25


# ---------------------------------------------------------------------------
# GHSA-rm5h-555g-vpjj — the row lock. SQLite cannot show the race; what this
# pins is that the create paths ask for the lock, exactly like create_payment.
# The Postgres proof (two concurrent batches, one refused) runs in the Linux
# Docker gate of the testing repo.
# ---------------------------------------------------------------------------


@pytest.fixture
def lock_log(monkeypatch):
    from sqlalchemy.orm import Query

    seen = []
    orig = Query.with_for_update

    def spy(self, *a, **kw):
        ent = (
            self.column_descriptions[0].get("entity")
            if self.column_descriptions
            else None
        )
        if ent is not None:
            seen.append(ent.__name__)
        return orig(self, *a, **kw)

    monkeypatch.setattr(Query, "with_for_update", spy)
    return seen


def test_batch_payment_locks_the_invoice_row(
    client, seed_accounts, seed_customer, lock_log
):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "tax_rate": 0,
            "lines": [
                {"description": "x", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    lock_log.clear()
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-03-02",
            "method": "check",
            "allocations": [
                {
                    "customer_id": seed_customer.id,
                    "invoice_id": inv["id"],
                    "amount": "100",
                }
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    assert "Invoice" in lock_log, lock_log
    inv2 = client.get(f"/api/invoices/{inv['id']}").json()
    assert float(inv2["balance_due"]) == 0 and inv2["status"] == "paid"
    # the second batch against the same invoice is refused up front
    r = client.post(
        "/api/batch-payments",
        json={
            "date": "2026-03-03",
            "method": "check",
            "allocations": [
                {
                    "customer_id": seed_customer.id,
                    "invoice_id": inv["id"],
                    "amount": "100",
                }
            ],
        },
    )
    assert r.status_code == 400, r.text


def test_bill_payment_create_locks_the_bill_row(
    client, db_session, seed_accounts, lock_log
):
    from app.models.contacts import Vendor

    v = Vendor(name="Lock Vendor", is_active=True)
    db_session.add(v)
    db_session.commit()
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": v.id,
            "date": "2026-03-01",
            "bill_number": "B-LOCK",
            "lines": [
                {
                    "description": "x",
                    "quantity": 1,
                    "rate": 75,
                    "account_id": seed_accounts["6000"].id,
                    "line_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    lock_log.clear()
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.id,
            "date": "2026-03-02",
            "amount": 75.0,
            "method": "check",
            "allocations": [{"bill_id": bill["id"], "amount": 75.0}],
        },
    )
    assert r.status_code in (200, 201), r.text
    assert "Bill" in lock_log, lock_log
