"""The Accountant role (#340): an outside accountant's sign-in reads
everything, the audit log included, and writes journal entries only — it
posts them (as adjusting entries, #341) and voids its own. Every other
write is refused with a sentence that says what the role is for, and HR
and payroll stay the administrator's, reads included."""

import io
from pathlib import Path

from app.main import ACCOUNTANT_REFUSAL
from app.models.audit import AuditLog
from app.models.users import ROLE_ACCOUNTANT, VALID_ROLES, User
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]
CPA_PW = "cpa-password-123"
ADMIN_PW = "test-password-123"


def _mk_user(db, username, password, role):
    u = User(
        username=username,
        display_name=username.title(),
        password_hash=auth_service.hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    return u


def _login_as(client, username, password):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return r


def _entry(client, accounts, amount="120.00", text="Accrue insurance", **extra):
    return client.post(
        "/api/journal",
        json={
            "date": "2026-03-31",
            "description": text,
            "lines": [
                {"account_id": accounts["6300"].id, "debit": amount, "credit": "0"},
                {"account_id": accounts["2000"].id, "debit": "0", "credit": amount},
            ],
            **extra,
        },
    )


def _as_cpa(client, db_session):
    _mk_user(db_session, "cpa", CPA_PW, ROLE_ACCOUNTANT)
    _login_as(client, "cpa", CPA_PW)


def test_the_role_exists_and_is_offered(client, db_session):
    assert "accountant" in VALID_ROLES
    r = client.post(
        "/api/users",
        json={"username": "cpa", "password": CPA_PW, "role": "accountant"},
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["role"] == "accountant"
    settings_js = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert (
        settings_js.count('<option value="accountant">') == 3
    )  # users, tokens, the invitation
    assert "['admin', 'bookkeeper', 'accountant', 'readonly']" in settings_js


def test_reads_everything_the_audit_log_included(client, db_session, seed_accounts):
    _as_cpa(client, db_session)
    for path in (
        "/api/customers",
        "/api/vendors",
        "/api/accounts",
        "/api/reports/profit-loss",
        "/api/reports/trial-balance",
        "/api/reports/general-ledger",
        "/api/settings",
        "/api/audit",  # refused to read-only; the accountant's job is to see what moved
        "/api/journal",
    ):
        assert client.get(path).status_code == 200, path


def test_posts_journal_entries_as_adjusting_in_its_own_name(
    client, db_session, seed_accounts
):
    _as_cpa(client, db_session)
    r = _entry(client, seed_accounts)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_adjusting"] is True  # the accountant's entries are adjustments
    assert body["posted_by"] == "cpa"
    row = (
        db_session.query(AuditLog)
        .filter(AuditLog.table_name == "transactions", AuditLog.action == "INSERT")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None and row.username == "cpa"
    # the box can be cleared: an ordinary entry, still in its name
    r = _entry(client, seed_accounts, text="Reclass", is_adjusting=False)
    assert r.status_code == 201 and r.json()["is_adjusting"] is False
    assert r.json()["posted_by"] == "cpa"


def test_every_other_write_is_refused_with_the_sentence(
    client, db_session, seed_accounts
):
    _as_cpa(client, db_session)
    refused = [
        ("post", "/api/customers", {"name": "Not Yours Inc"}),
        ("post", "/api/vendors", {"name": "Not Yours Supply"}),
        ("post", "/api/accounts", {"name": "Side account", "account_type": "expense"}),
        (
            "post",
            "/api/banking/transactions",
            {
                "account_id": seed_accounts["1000"].id,
                "date": "2026-03-01",
                "amount": "-10.00",
                "category_account_id": seed_accounts["6300"].id,
            },
        ),
        # the accountant posts and voids journal entries but does not edit
        # them: the PUT is refused by the middleware (#388)
        (
            "put",
            "/api/journal/1",
            {"date": "2026-03-01", "description": "x", "lines": []},
        ),
        ("put", "/api/settings", {"invoice_prefix": "X-"}),
        ("post", "/api/backups", {}),
        (
            "post",
            "/api/users",
            {"username": "x", "password": "long-enough-pw", "role": "admin"},
        ),
        ("post", "/api/closing-date", {"closing_date": "2026-01-31"}),
    ]
    for method, path, payload in refused:
        r = getattr(client, method)(path, json=payload)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json()["detail"] == ACCOUNTANT_REFUSAL, path
    # HR and payroll: the administrator's, reads included
    for path in ("/api/payroll/runs", "/api/benefits/plans", "/api/tax-forms"):
        assert client.get(path).status_code == 403, path
    assert client.get("/api/users").status_code == 403  # route-level admin check


def test_voids_its_own_entries_and_no_others(client, db_session, seed_accounts):
    # the administrator's entry first
    admin_entry = _entry(client, seed_accounts, text="Owner's entry").json()
    assert admin_entry["posted_by"] == "admin" and admin_entry["is_adjusting"] is False
    _as_cpa(client, db_session)
    own = _entry(client, seed_accounts, text="Year-end accrual").json()

    r = client.post(f"/api/journal/{admin_entry['id']}/void")
    assert r.status_code == 403
    assert "posted by admin" in r.json()["detail"]

    r = client.post(f"/api/journal/{own['id']}/void")
    assert r.status_code == 200, r.text
    reversal = r.json()
    assert reversal["description"] == "VOID adjusting entry: Year-end accrual"
    assert reversal["is_adjusting"] is True  # the reversal of an adjustment is one
    assert reversal["posted_by"] == "cpa"
    assert client.get(f"/api/journal/{own['id']}").json()["voided"] is True


def test_attaches_the_support_behind_an_entry(client, db_session, seed_accounts):
    _as_cpa(client, db_session)
    own = _entry(client, seed_accounts).json()
    r = client.post(
        f"/api/attachments/journal_entry/{own['id']}",
        files={"file": ("workpaper.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    # but not on a document
    r = client.post(
        "/api/attachments/invoice/1",
        files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
    )
    assert r.status_code == 403 and r.json()["detail"] == ACCOUNTANT_REFUSAL


def test_an_api_token_can_wear_the_role(client, db_session, seed_accounts):
    r = client.post("/api/tokens", json={"label": "cpa-agent", "role": "accountant"})
    assert r.status_code == 201, r.text
    secret = r.json()["token"]
    client.post("/api/auth/logout")
    headers = {"Authorization": f"Bearer {secret}"}
    assert client.get("/api/audit", headers=headers).status_code == 200
    r = client.post("/api/customers", json={"name": "Nope"}, headers=headers)
    assert r.status_code == 403 and r.json()["detail"] == ACCOUNTANT_REFUSAL
    r = (
        _entry(client, seed_accounts, headers=headers)
        if False
        else client.post(
            "/api/journal",
            json={
                "date": "2026-03-31",
                "description": "Agent's adjustment",
                "lines": [
                    {
                        "account_id": seed_accounts["6300"].id,
                        "debit": "5.00",
                        "credit": "0",
                    },
                    {
                        "account_id": seed_accounts["2000"].id,
                        "debit": "0",
                        "credit": "5.00",
                    },
                ],
            },
            headers=headers,
        )
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_adjusting"] is True
    assert r.json()["posted_by"] == "token:cpa-agent"


def test_the_page_treats_the_accountant_as_locked_but_for_the_journal():
    app_js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    assert (
        "isAccountant() { return App.role === 'accountant' || App.inCopy; }" in app_js
    )
    assert "isLocked() { return App.isReadOnly() || App.isAccountant(); }" in app_js
    assert "ACCOUNTANT_MESSAGE:" in app_js and "ACCOUNTANT_ACTIONS: new Set([" in app_js
    # the locks run for the accountant too, and skip what it keeps
    assert "if (!root || !App.isLocked()) return;" in app_js
    assert "':not([data-accountant-ok])'" in app_js
    assert "el.closest('[data-accountant-ok]')" in app_js
    assert "App.ACCOUNTANT_ACTIONS.has(call[1])) return;" in app_js
    # the audit log stays in its sidebar (read-only's is hidden)
    assert "if (App.isReadOnly()) {\n            // the Audit Log" in app_js
    assert "App.setRole(a.user.role, a.user.username);" in app_js
    journal_js = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    assert 'data-accountant-ok onclick="JournalPage.showForm()"' in journal_js
    assert "data-accountant-ok data-skip-to" in journal_js  # the entry form
    assert 'data-accountant-ok style="margin-top:12px;' in journal_js  # Attach
    assert "e.posted_by === App.username" in journal_js  # Void on its own only


def test_the_guide_exists_and_the_server_doc_names_the_role():
    guide = (ROOT / "docs/working-with-your-accountant.md").read_text(encoding="utf-8")
    assert "QBX" in guide and "Accountant" in guide and "Closing Date" in guide
    server = (ROOT / "docs/server-edition.md").read_text(encoding="utf-8")
    assert "accountant (reads everything" in server
    assert "working-with-your-accountant.md" in server


def test_the_accountant_sees_edit_on_a_bank_entry_but_not_a_journal_entry(
    client, db_session, seed_accounts
):
    """#388 review: the role gate only takes Edit off a manual journal row;
    a bank entry keeps #408's behaviour (editable, hidden client-side)."""
    je = client.post(
        "/api/journal",
        json={
            "date": "2026-03-01",
            "description": "Owner draw",
            "lines": [
                {"account_id": seed_accounts["1000"].id, "debit": 0, "credit": 100},
                {"account_id": seed_accounts["6900"].id, "debit": 100, "credit": 0},
            ],
        },
    ).json()
    be = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-03-02",
            "amount": "-25.00",
            "category_account_id": seed_accounts["6900"].id,
        },
    ).json()
    _as_cpa(client, db_session)
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    by_id = {e["transaction_id"]: e for e in reg["entries"]}
    assert by_id[je["id"]]["editable"] is False  # a manual JE: not the accountant's
    assert by_id[be["id"]]["editable"] is True  # a bank entry: #408, hidden client-side
    # the journal GET agrees the accountant does not edit this entry
    assert client.get(f"/api/journal/{je['id']}").json()["editable"] is False
