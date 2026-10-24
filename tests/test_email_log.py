"""Recent sends (#447): the /api/email-log route, who may read it, and the
test-email failure that now says the reason and a hint instead of pointing at
a log with no UI."""

from datetime import datetime, timedelta

import smtplib

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


def test_non_admin_sessions_refused(client, db_session):
    from app.models.users import ROLE_BOOKKEEPER, ROLE_READONLY, User
    from app.services import auth as auth_service

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
        assert client.get("/api/email-log").status_code == 403


def test_bookkeeper_token_refused(client):
    created = client.post(
        "/api/tokens", json={"label": "keeper-bot", "role": "bookkeeper"}
    ).json()
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {created['token']}"
    assert api.get("/api/email-log").status_code == 403


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
