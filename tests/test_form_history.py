"""Form history under Tax Forms (issue #263).

The document_audits rows the W-2, W-3, 940 and 941 write had no page:
the PDF footer was the only place a person met the hash. The Tax Forms
page lists them — what, when, by whom — and Check re-figures a form from
today's books and says, in a sentence, whether it still matches.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.document_audit import DocumentAudit
from app.models.payroll import PayRun, PayRunStatus

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

# fixture sign-ins: module constants, never a literal beside a username
FIXTURE_PW = "test-password-123"


def _html(monkeypatch):
    from app.services.tax_forms import form_940, form_941, w2_w3

    for mod in (w2_w3, form_940, form_941):
        monkeypatch.setattr(mod, "render_pdf", lambda html, **kw: html.encode())


def _employee(client, **over):
    body = {
        "first_name": "Jonah",
        "last_name": "Pike",
        "pay_type": "salary",
        "pay_rate": 52000,
        "work_state": "TX",
        **over,
    }
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _paid(client, emp_id, pay_date, gross):
    run = client.post(
        "/api/payroll",
        json={
            "period_start": pay_date,
            "period_end": pay_date,
            "pay_date": pay_date,
            "stubs": [{"employee_id": emp_id, "gross_override": gross}],
        },
    )
    assert run.status_code == 201, run.text
    r = client.post(f"/api/payroll/{run.json()['id']}/process")
    assert r.status_code == 200, r.text
    return run.json()


def _generate(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    return r


def _history(client):
    r = client.get("/api/document-audits")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# The list: what, when, by whom
# ---------------------------------------------------------------------------
def test_the_history_names_each_form_and_who_generated_it(
    client, db_session, seed_accounts, monkeypatch
):
    from app.models.users import User
    from app.services import auth as auth_service

    _html(monkeypatch)
    emp = _employee(client)
    _paid(client, emp["id"], "2026-03-20", 2000)
    # a named sign-in, so the row has someone to name
    db_session.add(
        User(
            username="dana",
            display_name="Dana",
            password_hash=auth_service.hash_password(FIXTURE_PW),
            role="admin",
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "dana", "password": FIXTURE_PW}
    )
    assert r.status_code == 200, r.text

    _generate(client, f"/api/payroll/forms/w2/{emp['id']}/pdf?year=2026")
    _generate(client, "/api/payroll/forms/941/2026/1/pdf")
    _generate(client, "/api/payroll/forms/w3/2026/pdf")
    _generate(client, "/api/payroll/forms/940/2026/pdf")

    rows = _history(client)
    assert [(r["form"], r["subject"]) for r in rows] == [
        ("Form 940", "2026"),
        ("W-3", "2026"),
        ("Form 941", "2026 Q1"),
        ("W-2", "Jonah Pike, 2026"),
    ]
    assert all(r["username"] == "dana" for r in rows), rows
    assert all(len(r["content_hash"]) == 64 and r["created_at"] for r in rows)
    # stored in UTC and said so, so a page shows the reader's own time (NEW-58)
    assert all(r["created_at"].endswith(("+00:00", "Z")) for r in rows), rows[0][
        "created_at"
    ]


# ---------------------------------------------------------------------------
# Check: the books as they were, or changed
# ---------------------------------------------------------------------------
def test_check_says_a_form_still_matches_until_the_books_change(
    client, db_session, seed_accounts, monkeypatch
):
    _html(monkeypatch)
    emp = _employee(client)
    _paid(client, emp["id"], "2026-03-20", 2000)
    _generate(client, "/api/payroll/forms/941/2026/1/pdf")
    [row] = _history(client)

    r = client.get(f"/api/document-audits/{row['id']}/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matches"] is True
    assert body["current_hash"] == body["generated_hash"] == row["content_hash"]
    assert body["voided_runs"] == 0
    assert body["message"].startswith(
        "Matches the books: re-figured today, the Form 941 for 2026 Q1 comes out the same"
    )

    # another pay run in the quarter: the 941 would come out differently now
    _paid(client, emp["id"], "2026-03-27", 2500)
    body = client.get(f"/api/document-audits/{row['id']}/check").json()
    assert body["matches"] is False
    assert body["current_hash"] != body["generated_hash"]
    assert body["message"].startswith(
        "No longer matches: re-figured today, the Form 941 for 2026 Q1 comes out differently"
    )
    assert "the pay runs behind it changed" in body["message"]

    # a pay run in another quarter is not this form's business
    _generate(client, "/api/payroll/forms/941/2026/1/pdf")
    fresh = _history(client)[0]
    _paid(client, emp["id"], "2026-05-15", 1000)
    assert client.get(f"/api/document-audits/{fresh['id']}/check").json()["matches"]


def test_check_says_the_company_changed_not_the_pay_runs(
    client, db_session, seed_accounts, monkeypatch
):
    """#678: the employer block is part of the hash. A form generated before
    the company was renamed, or its EIN corrected, differs although no pay
    run moved; Check says which part differs."""
    _html(monkeypatch)
    r = client.put(
        "/api/settings", json={"company_name": "Pike Signs", "company_tax_id": ""}
    )
    assert r.status_code == 200, r.text
    emp = _employee(client)
    _paid(client, emp["id"], "2026-03-20", 2000)
    _generate(client, "/api/payroll/forms/941/2026/1/pdf")
    _generate(client, f"/api/payroll/forms/w2/{emp['id']}/pdf?year=2026")
    w2, f941 = _history(client)

    r = client.put("/api/settings", json={"company_name": "Pike Signs & Co"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/document-audits/{f941['id']}/check").json()
    assert body["matches"] is False and body["employer_changed"] is True
    assert body["message"].startswith(
        "No longer matches: the company name on the Form 941 for 2026 Q1 "
        "changed after it was generated"
    )
    assert body["message"].endswith("the figures are as they were.")
    assert "pay runs" not in body["message"]

    # the EIN as well, on the W-2: both are named
    r = client.put("/api/settings", json={"company_tax_id": "12-3456789"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/document-audits/{w2['id']}/check").json()
    assert body["employer_changed"] is True
    assert "the company name and EIN on the W-2 for Jonah Pike, 2026" in (
        body["message"]
    )
    assert "pay runs" not in body["message"]

    # a form generated after the rename still matches
    _generate(client, "/api/payroll/forms/941/2026/1/pdf")
    fresh = _history(client)[0]
    body = client.get(f"/api/document-audits/{fresh['id']}/check").json()
    assert body["matches"] is True and body["employer_changed"] is False

    # and when the figures moved too, the pay runs are what it says
    _paid(client, emp["id"], "2026-03-27", 2500)
    body = client.get(f"/api/document-audits/{f941['id']}/check").json()
    assert body["matches"] is False and body["employer_changed"] is False
    assert "the pay runs behind it changed" in body["message"]


def test_check_says_when_a_pay_run_in_the_period_was_voided(
    client, db_session, seed_accounts, monkeypatch
):
    _html(monkeypatch)
    emp = _employee(client)
    run = _paid(client, emp["id"], "2026-03-20", 2000)
    _generate(client, f"/api/payroll/forms/w2/{emp['id']}/pdf?year=2026")
    [row] = _history(client)

    db_session.query(PayRun).filter(PayRun.id == run["id"]).update(
        {"status": PayRunStatus.VOID}
    )
    db_session.commit()
    body = client.get(f"/api/document-audits/{row['id']}/check").json()
    assert body["matches"] is False and body["voided_runs"] == 1
    assert body["message"].endswith("1 pay run in its period was voided.")


def test_check_of_a_w2_whose_employee_is_gone_says_so(
    client, db_session, seed_accounts, monkeypatch
):
    from app.models.payroll import Employee

    _html(monkeypatch)
    emp = _employee(client, first_name="Lena", last_name="Ortiz")
    _generate(client, f"/api/payroll/forms/w2/{emp['id']}/pdf?year=2026")
    [row] = _history(client)
    assert row["subject"] == "Lena Ortiz, 2026"

    db_session.query(Employee).filter(Employee.id == emp["id"]).delete()
    db_session.commit()
    [row] = _history(client)  # still listed
    assert row["subject"] == f"employee #{emp['id']} (no longer on file), 2026"
    body = client.get(f"/api/document-audits/{row['id']}/check").json()
    assert body["matches"] is None and body["current_hash"] is None
    assert "no longer on file" in body["message"]

    assert client.get("/api/document-audits/99999/check").status_code == 404


# ---------------------------------------------------------------------------
# The hash from a footer, looked up; who may look
# ---------------------------------------------------------------------------
def test_a_footer_hash_finds_its_form(client, db_session, seed_accounts, monkeypatch):
    _html(monkeypatch)
    emp = _employee(client)
    _paid(client, emp["id"], "2026-03-20", 2000)
    _generate(client, "/api/payroll/forms/w3/2026/pdf")
    [row] = _history(client)
    found = client.get(f"/api/document-audits/verify/{row['content_hash']}").json()
    assert [(f["id"], f["form"], f["subject"]) for f in found] == [
        (row["id"], "W-3", "2026")
    ]
    # the 16 characters a form's footer prints find it too (W-3)
    short = client.get(f"/api/document-audits/verify/{row['content_hash'][:16]}").json()
    assert [f["id"] for f in short] == [row["id"]]
    assert client.get(f"/api/document-audits/verify/{'0' * 64}").json() == []
    assert client.get(f"/api/document-audits/verify/{'0' * 16}").json() == []
    for bad in ("abc", row["content_hash"][:15], "g" * 16):
        assert client.get(f"/api/document-audits/verify/{bad}").status_code == 400, bad


def test_the_history_is_admin_only_like_the_rest_of_payroll(client, db_session):
    from app.models.users import User
    from app.services import auth as auth_service

    db_session.add(
        User(
            username="books",
            display_name="Books",
            password_hash=auth_service.hash_password(FIXTURE_PW),
            role="bookkeeper",
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "books", "password": FIXTURE_PW}
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/document-audits").status_code == 403
    assert client.get("/api/document-audits/1/check").status_code == 403
    assert client.get(f"/api/document-audits/verify/{'0' * 64}").status_code == 403


# ---------------------------------------------------------------------------
# The page and the migration
# ---------------------------------------------------------------------------
def test_the_page_lists_checks_and_finds():
    js = (JS / "tax_forms.js").read_text(encoding="utf-8")
    assert "API.get('/document-audits?limit=100')" in js
    assert "API.get(`/document-audits/${id}/check`)" in js
    assert "API.get(`/document-audits/verify/${hash}`)" in js
    # the footer's 16 characters, or the audit ID, are enough to find a form (W-3)
    assert (
        "/^[0-9a-f]{16,64}$/.test(hash)" in js
        and "API.get(`/document-audits/${byId[1]}`)" in js
    )
    # the answer lands in a live region, as a sentence
    # the whole answer is read on every Check, not only the part that changed
    # (gate round 4, NEW-72: a second Check was announced as the time alone)
    assert 'id="audit-status-${a.id}" role="status" aria-atomic="true"' in js
    assert "${escapeHtml(r.message)}" in js  # the sentence, with its mark (NEW-67)
    assert 'id="taxforms-h-form-history">Form history</h3>' in js
    # Find clears the last highlight before any answer (NEW-64)
    find = js[js.index("async findByHash()") :]
    assert find.index("forEach(tr => tr.removeAttribute('data-found'))") < find.index(
        "/^[0-9a-f]{16,64}$/.test(hash)"
    )
    # Check answers with a mark in the success / danger colour and the time (NEW-67)
    check = js[js.index("async checkForm(id)") : js.index("async findByHash()")]
    assert "cell.className = `audit-status audit-status--${kind}`;" in check
    assert "(checked at ${escapeHtml(at)})" in check
    # with seconds, so two Checks within a minute read differently (W-8 / NEW-71)
    assert "{ hour: 'numeric', minute: '2-digit', second: '2-digit' }" in check
    # the dark theme's cell rule is outranked by the cell's own class (NEW-69 / NEW-70)
    dark = (ROOT / "app/static/css/dark.css").read_text(encoding="utf-8")
    assert (
        '[data-theme="dark"] tbody td.audit-status--ok { color: var(--text-success); }'
        in dark
    )
    assert (
        '[data-theme="dark"] tbody td.audit-status--changed { color: #ff8a8a; }' in dark
    )
    assert (
        '[data-theme="dark"] tbody td.audit-status .audit-checked-at { color: var(--text-secondary); }'
        in dark
    )
    # the hidden Check header sits inside its cell
    assert (
        '<th scope="col" style="position:relative"><span class="visually-hidden">Check</span></th>'
        in js
    )
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".audit-status--ok { color: var(--text-success); }" in css
    assert (
        ".visually-hidden { position: absolute;" in css
    )  # the Check header stays out of sight


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_desktop_probe_still_passes_with_the_history_card():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "tax_forms_shim_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr


def test_the_migration_adds_who_and_keeps_one_head(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    db = tmp_path / "fresh.db"
    url = "sqlite:///" + db.as_posix()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = url
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    cols = {c["name"] for c in sa.inspect(engine).get_columns("document_audits")}
    assert "username" in cols
    command.downgrade(cfg, "b4d6f8a0c2e4")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("document_audits")}
    assert "username" not in cols


def test_rows_from_before_carry_no_name_and_still_list(client, db_session):
    db_session.add(
        DocumentAudit(doc_type="941", doc_key="yr2025-q4", content_hash="a" * 64)
    )
    db_session.commit()
    [row] = _history(client)
    assert row["username"] is None  # a stamp happens only on generation
    assert (row["form"], row["subject"]) == ("Form 941", "2025 Q4")
