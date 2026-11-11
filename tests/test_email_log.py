"""Recent sends (#447): the /api/email-log route, who may read it, and the
test-email failure that now says the reason and a hint instead of pointing at
a log with no UI."""

from datetime import datetime, timedelta

import smtplib

import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.models.email_log import EmailLog
from app.services.email_errors import explain_send_failure


def _seed(db, rows):
    """rows: list of (entity_type, recipient, subject, status, error, minutes).
    `minutes` offsets sent_at so order is deterministic."""
    base = datetime(2026, 10, 1, 12, 0, 0)
    for et, to, subj, status, err, minutes in rows:
        db.add(
            EmailLog(
                entity_type=et,
                entity_id=0,
                recipient=to,
                subject=subj,
                status=status,
                error_message=err,
                sent_at=base + timedelta(minutes=minutes),
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def test_admin_gets_rows_newest_first(client, db_session):
    _seed(
        db_session,
        [
            ("invoice", "a@x.com", "Invoice #1", "sent", None, 0),
            ("collection", "b@x.com", "Notice", "sent", None, 10),
            ("settings_test", "c@x.com", "Test", "failed", "boom", 20),
        ],
    )
    body = client.get("/api/email-log").json()
    assert body["total"] == 3
    subjects = [r["subject"] for r in body["rows"]]
    assert subjects == ["Test", "Notice", "Invoice #1"]  # newest first


def test_paging_is_bounded(client, db_session):
    _seed(
        db_session,
        [("invoice", f"{i}@x.com", f"S{i}", "sent", None, i) for i in range(5)],
    )
    page1 = client.get("/api/email-log?limit=2&offset=0").json()
    assert page1["total"] == 5
    assert [r["subject"] for r in page1["rows"]] == ["S4", "S3"]
    page2 = client.get("/api/email-log?limit=2&offset=2").json()
    assert [r["subject"] for r in page2["rows"]] == ["S2", "S1"]
    # the max is enforced
    assert client.get("/api/email-log?limit=9999").status_code == 422


def test_status_filter(client, db_session):
    _seed(
        db_session,
        [
            ("invoice", "a@x.com", "Sent one", "sent", None, 0),
            ("invoice", "b@x.com", "Failed one", "failed", "535 nope", 10),
        ],
    )
    failed = client.get("/api/email-log?status=failed").json()
    assert [r["subject"] for r in failed["rows"]] == ["Failed one"]
    assert failed["total"] == 1
    # an unknown status is ignored, not an error
    both = client.get("/api/email-log?status=banana").json()
    assert both["total"] == 2


def test_non_admin_sessions_read_the_list_without_the_raw_error(client, db_session):
    # #781: every role that reads the books reads the sends; only an
    # administrator sees the raw SMTP text (tests/test_email_log_token_access_781.py)
    from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
    from app.services import auth as auth_service

    _seed(db_session, [("invoice", "b@x.com", "Inv", "failed", "535 nope", 0)])
    for name, role in [("keeper", ROLE_BOOKKEEPER), ("viewer", ROLE_READONLY)]:
        db_session.add(
            User(
                username=name,
                display_name=name,
                password_hash=auth_service.hash_password("a-password-123"),
                role=role,
                is_active=True,
            )
        )
    db_session.commit()
    for name in ("keeper", "viewer"):
        client.post("/api/auth/logout")
        client.post(
            "/api/auth/login", json={"username": name, "password": "a-password-123"}
        )
        r = client.get("/api/email-log")
        assert r.status_code == 200
        assert "535 nope" not in r.text


def test_a_bookkeeper_token_reads_the_list(client):
    created = client.post(
        "/api/tokens", json={"label": "keeper-bot", "role": "bookkeeper"}
    ).json()
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {created['token']}"
    assert api.get("/api/email-log").status_code == 200


# ---------------------------------------------------------------------------
# The hint mapping (small, one function)
# ---------------------------------------------------------------------------


def test_hint_maps_the_common_cases():
    reason, hint = explain_send_failure(
        "(535, b'5.7.8 Username and Password not accepted')", "smtp.gmail.com"
    )
    assert reason == "535 5.7.8 Username and Password not accepted"
    assert "app password" in hint.lower()

    reason, hint = explain_send_failure("(535, b'bad creds')", "mail.acme.com")
    assert "username or password" in hint.lower()
    assert "app password" not in hint.lower()

    _, hint = explain_send_failure("[Errno 61] Connection refused", "mail.acme.com")
    assert "host, port" in hint.lower()

    _, hint = explain_send_failure("timed out", "mail.acme.com")
    assert "host, port" in hint.lower()

    reason, hint = explain_send_failure("something odd happened", "mail.acme.com")
    assert reason == "something odd happened"
    assert hint == ""


# ---------------------------------------------------------------------------
# The test-email failure says the reason itself
# ---------------------------------------------------------------------------


class _FakeSMTP:
    def __init__(self, *a, **k):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted"
        )

    def sendmail(self, *a, **k):  # pragma: no cover - never reached
        pass

    def quit(self):
        pass


def test_test_email_failure_states_reason_and_hint(client, monkeypatch):
    client.put(
        "/api/settings",
        json={
            "smtp_host": "smtp.gmail.com",
            "smtp_port": "587",
            "smtp_user": "me@gmail.com",
            "smtp_password": "not-an-app-password",
            "smtp_from_email": "me@gmail.com",
            "smtp_use_tls": "true",
        },
    )
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    r = client.post("/api/settings/test-email")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "535" in detail
    assert "app password" in detail.lower()
    assert "Recent sends" in detail
    # and the failure is in the list the message points at
    rows = client.get("/api/email-log?status=failed").json()["rows"]
    assert rows and rows[0]["entity_type"] == "settings_test"


# ---------------------------------------------------------------------------
# One wording of a failure, and a pointer each role can follow
# ---------------------------------------------------------------------------


def test_failure_sentence_is_the_one_mapping():
    from app.services.email_errors import failure_sentence

    assert "app password" in failure_sentence(
        "(535, b'x https://support.google.com/mail')"
    )
    assert "username or password" in failure_sentence("(535, b'bad creds')")
    assert "host, port" in failure_sentence("[Errno 61] Connection refused")
    assert "not configured" in failure_sentence("SMTP not configured")
    assert failure_sentence("weird thing") == "The email could not be sent."


def test_the_list_carries_the_failure_sentence(client, db_session):
    _seed(
        db_session,
        [
            ("invoice", "a@x.com", "Inv", "sent", None, 0),
            ("invoice", "b@x.com", "Inv", "failed", "(535, b'bad creds')", 1),
        ],
    )
    rows = client.get("/api/email-log").json()["rows"]
    failed, sent = rows
    assert "username or password" in failed["failure"]
    assert sent["failure"] is None


def _bookkeeper_login(client, db_session):
    from app.models.users import ROLE_BOOKKEEPER, User
    from app.services import auth as auth_service

    db_session.add(
        User(
            username="keeper2",
            display_name="keeper2",
            password_hash=auth_service.hash_password("a-password-123"),
            role=ROLE_BOOKKEEPER,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "keeper2", "password": "a-password-123"}
    )
    assert r.status_code == 200, r.text


def _invoice(client):
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "a@b.com"}
    ).json()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _failed_invoice_email(client, monkeypatch, inv):
    import app.routes.invoices.documents as documents

    # no PDF stack needed: the claim is the failure message, not the PDF
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "a@b.com", "subject": "Invoice", "message": ""},
    )
    assert r.status_code == 502, r.text
    return r.json()["detail"]


def test_an_admin_is_pointed_at_recent_sends(client, seed_accounts, monkeypatch):
    detail = _failed_invoice_email(client, monkeypatch, _invoice(client))
    assert "Settings → Email (SMTP) → Recent sends" in detail
    assert "administrator" not in detail


def test_a_bookkeeper_is_told_an_administrator_can_see_why(
    client, db_session, seed_accounts, monkeypatch
):
    # Recent sends is an administrator's section of Settings, so a
    # bookkeeper is not sent there (the API answers them without the raw
    # error since #781).
    inv = _invoice(client)
    _bookkeeper_login(client, db_session)
    detail = _failed_invoice_email(client, monkeypatch, inv)
    assert "An administrator can see why under Settings → Recent sends." in detail
    assert "Check the SMTP settings" not in detail


def _failed_letters(client, monkeypatch):
    import app.routes.reports.receivables as receivables

    monkeypatch.setattr(
        receivables, "generate_collection_letter_pdf", lambda *a, **k: b"%PDF-"
    )
    r = client.post(
        "/api/reports/collection-letters",
        json={"letter_type": "30", "send_email": True},
    )
    assert r.status_code == 200, r.text
    (error,) = r.json()["errors"]
    return error


@pytest.mark.parametrize("role", ["admin", "bookkeeper"])
def test_a_failed_collection_letter_says_who_can_see_why(
    client, db_session, seed_accounts, monkeypatch, role
):
    from app.models.invoices import Invoice, InvoiceStatus

    inv = _invoice(client)
    row = db_session.get(Invoice, inv["id"])
    row.status = InvoiceStatus.SENT
    db_session.commit()
    if role == "bookkeeper":
        _bookkeeper_login(client, db_session)
    error = _failed_letters(client, monkeypatch)
    assert error.startswith("Probe Co: the letter could not be emailed.")
    if role == "admin":
        assert "Settings → Email (SMTP) → Recent sends" in error
    else:
        assert "An administrator can see why under Settings → Recent sends." in error


_TOAST_PROBE = r"""
const fs = require('fs'), vm = require('vm');
const timers = [];
function node() {
  return { className: '', textContent: '', style: {}, children: [],
    appendChild(c) { this.children.push(c); }, remove() {},
    addEventListener() {}, matches: () => false, contains: () => false };
}
const shown = [];
const container = { appendChild: (el) => shown.push(el) };
const ctx = { console, window: {},
  document: { addEventListener: () => {}, createElement: () => node(), activeElement: null,
              contains: () => false,
              querySelector: (s) => (s === '#toast-container' ? container : null) },
  setTimeout: (fn, ms) => { timers.push(ms); return timers.length; },
  clearTimeout: () => {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/js/utils.js', 'utf8')
  + '\nthis.toastAction = toastAction;', ctx);
ctx.toastAction('Email could not be sent.', 'Recent sends', () => {}, 2000, null, { type: 'error' });
ctx.toastAction('Saved', 'Show', () => {});
console.log(JSON.stringify({ classes: shown.map(e => e.className), ms: timers }));
"""


def test_a_failure_toast_with_an_action_is_an_error_toast():
    import json
    import shutil
    import subprocess
    from pathlib import Path

    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    out = subprocess.run(
        ["node", "-e", _TOAST_PROBE],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["classes"] == ["toast toast-error", "toast toast-success"]
    # an error stays at least toastMs's 6 s even when asked for less
    assert got["ms"][0] >= 6000 and got["ms"][1] == 8000
