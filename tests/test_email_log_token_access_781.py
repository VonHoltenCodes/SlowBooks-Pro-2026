"""API tokens and non-admin sign-ins read the email send log (#781).

An administrator sees every row whole, the raw SMTP error included. A
bookkeeper or read-only role, signed in or through an API token, gets the
same rows with `error_message` replaced by the bookkeeper's sentence for the
failure: the raw text carries mail-server hostnames and banners.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.email_log import EmailLog
from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
from app.services import auth as auth_service

RAW_ERROR = (
    "(535, b'5.7.8 Authentication failed on relay7.mailhost-internal.example "
    "ESMTP Postfix (Debian/GNU)')"
)
PASSWORD = "a-password-123"


def _seed(db):
    base = datetime(2026, 10, 1, 12, 0, 0)
    rows = [
        ("invoice", "ap@probe.test", "Invoice 1", "sent", None, 0),
        ("statement", "owner@probe.test", "Statement", "failed", RAW_ERROR, 10),
        ("invoice", "ap@probe.test", "Invoice 2", "sent", None, 20),
    ]
    for et, to, subj, status, err, minutes in rows:
        db.add(
            EmailLog(
                entity_type=et,
                entity_id=1,
                recipient=to,
                cc="copy@probe.test",
                subject=subj,
                status=status,
                error_message=err,
                sent_at=base + timedelta(minutes=minutes),
            )
        )
    db.commit()


def _token(client, role):
    r = client.post("/api/tokens", json={"label": f"{role}-bot", "role": role})
    assert r.status_code in (200, 201), r.text
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return api


def _signed_in(db, role):
    name = f"user-{role}"
    db.add(
        User(
            username=name,
            display_name=name,
            password_hash=auth_service.hash_password(PASSWORD),
            role=role,
            is_active=True,
        )
    )
    db.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _who(client, db, how):
    if how == "admin":
        return client
    kind, role = how.split("-")
    role = {"bookkeeper": ROLE_BOOKKEEPER, "readonly": ROLE_READONLY}[role]
    return _token(client, role) if kind == "token" else _signed_in(db, role)


NON_ADMIN = [
    "token-bookkeeper",
    "token-readonly",
    "session-bookkeeper",
    "session-readonly",
]


def test_an_administrator_sees_the_whole_row_with_the_raw_error(client, db_session):
    _seed(db_session)
    body = client.get("/api/email-log").json()
    failed = next(r for r in body["rows"] if r["status"] == "failed")
    assert failed["error_message"] == RAW_ERROR
    assert "username or password" in failed["failure"]
    assert set(failed) == {
        "id",
        "sent_at",
        "recipient",
        "cc",
        "bcc",
        "subject",
        "entity_type",
        "entity_id",
        "status",
        "error_message",
        "failure",
    }


@pytest.mark.parametrize("how", NON_ADMIN)
def test_a_non_admin_role_reads_the_rows_with_a_sentence_for_the_error(
    client, db_session, how
):
    _seed(db_session)
    admin_rows = client.get("/api/email-log").json()["rows"]
    r = _who(client, db_session, how).get("/api/email-log")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert [x["subject"] for x in body["rows"]] == [
        "Invoice 2",
        "Statement",
        "Invoice 1",
    ]
    for mine, theirs in zip(body["rows"], admin_rows):
        assert set(mine) == set(theirs)
        # everything but the raw error is the administrator's row
        same = {k: v for k, v in theirs.items() if k != "error_message"}
        assert {k: mine[k] for k in same} == same
    failed = body["rows"][1]
    assert failed["recipient"] == "owner@probe.test"
    assert failed["error_message"] == failed["failure"]
    assert "username or password" in failed["error_message"]
    assert body["rows"][0]["error_message"] is None


@pytest.mark.parametrize("how", NON_ADMIN)
def test_the_raw_smtp_error_never_reaches_a_non_admin(client, db_session, how):
    _seed(db_session)
    who = _who(client, db_session, how)
    for query in ("", "?status=failed", "?status=sent", "?limit=1&offset=1"):
        text = who.get("/api/email-log" + query).text
        assert "mailhost-internal" not in text, query
        assert "Postfix" not in text, query
        assert "5.7.8" not in text, query


@pytest.mark.parametrize("how", ["token-bookkeeper", "token-readonly"])
def test_status_filter_and_paging_work_for_a_token(client, db_session, how):
    _seed(db_session)
    who = _who(client, db_session, how)
    failed = who.get("/api/email-log?status=failed").json()
    assert failed["total"] == 1
    assert [r["subject"] for r in failed["rows"]] == ["Statement"]
    sent = who.get("/api/email-log?status=sent").json()
    assert sent["total"] == 2
    page = who.get("/api/email-log?limit=1&offset=1").json()
    assert page["total"] == 3
    assert [r["subject"] for r in page["rows"]] == ["Statement"]
    assert who.get("/api/email-log?limit=9999").status_code == 422


def test_an_unauthenticated_call_is_still_refused(client, db_session):
    _seed(db_session)
    r = TestClient(app).get("/api/email-log")
    assert r.status_code == 401
    assert "relay7" not in r.text


@pytest.mark.parametrize("how", ["admin", "token-bookkeeper"])
def test_a_row_for_an_emailed_invoice_carries_that_invoice_id(
    client, db_session, seed_accounts, monkeypatch, how
):
    import smtplib

    from app.routes.invoices import documents
    from app.services.settings_service import set_setting

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, *a):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "ap@probe.test"}
    ).json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "due_date": "2026-06-30",
            "lines": [
                {"description": "work", "quantity": 1, "rate": 100, "line_order": 0}
            ],
        },
    ).json()
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "ap@probe.test", "subject": "Invoice", "message": ""},
    )
    assert r.status_code == 200, r.text
    rows = _who(client, db_session, how).get("/api/email-log").json()["rows"]
    (row,) = rows
    assert (row["entity_type"], row["entity_id"]) == ("invoice", inv["id"])
    assert row["status"] == "sent"
