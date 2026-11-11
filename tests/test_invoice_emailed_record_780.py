"""An emailed invoice records that it was emailed (#780).

GET /api/invoices/{id} (and the list) carry last_emailed_at and
last_emailed_to, read from the newest email_log row for the invoice that
went out. A failed send leaves them as they were; the status is not touched.
The app's own Email button posts to the same route, so it is covered here.
"""

import smtplib

import pytest

from app.models.email_log import EmailLog
from app.routes.invoices import documents
from app.services.settings_service import set_setting


@pytest.fixture
def smtp(monkeypatch, db_session):
    """SMTP configured and faked. Set `smtp.fail = True` to have the server
    refuse the next sends."""

    class State:
        fail = False
        sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            if State.fail:
                raise smtplib.SMTPRecipientsRefused(
                    {to[0]: (550, b"mx.example.net rejected")}
                )
            State.sent.append(list(to))

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()
    return State


def _invoice(client, name="Probe Co"):
    cust = client.post(
        "/api/customers", json={"name": name, "email": "billing@probe.test"}
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


def _email(client, inv, to):
    return client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": to, "subject": "Your invoice", "message": ""},
    )


def _get(client, inv):
    r = client.get(f"/api/invoices/{inv['id']}")
    assert r.status_code == 200, r.text
    return r.json()


def test_an_invoice_never_emailed_has_no_emailed_record(client, seed_accounts):
    inv = _invoice(client)
    got = _get(client, inv)
    assert got["last_emailed_at"] is None
    assert got["last_emailed_to"] is None
    listed = next(i for i in client.get("/api/invoices").json() if i["id"] == inv["id"])
    assert listed["last_emailed_at"] is None and listed["last_emailed_to"] is None


def test_a_sent_email_records_when_and_to_whom(client, db_session, seed_accounts, smtp):
    inv = _invoice(client)
    r = _email(client, inv, "ap@probe.test")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "sent"}
    got = _get(client, inv)
    assert got["last_emailed_to"] == "ap@probe.test"
    row = (
        db_session.query(EmailLog)
        .filter(EmailLog.entity_type == "invoice", EmailLog.status == "sent")
        .one()
    )
    assert got["last_emailed_at"] is not None
    assert got["last_emailed_at"].startswith(row.sent_at.isoformat()[:19])
    # the list says the same
    listed = next(i for i in client.get("/api/invoices").json() if i["id"] == inv["id"])
    assert listed["last_emailed_to"] == "ap@probe.test"
    assert listed["last_emailed_at"] == got["last_emailed_at"]


def test_an_email_does_not_change_the_invoice_status(client, seed_accounts, smtp):
    inv = _invoice(client)
    before = _get(client, inv)["status"]
    assert _email(client, inv, "ap@probe.test").status_code == 200
    assert _get(client, inv)["status"] == before


def test_a_failed_send_leaves_the_record_as_it_was(client, seed_accounts, smtp):
    inv = _invoice(client)
    smtp.fail = True
    assert _email(client, inv, "ap@probe.test").status_code == 502
    got = _get(client, inv)
    assert got["last_emailed_at"] is None and got["last_emailed_to"] is None
    # after a send that went out, a later failure does not move it
    smtp.fail = False
    assert _email(client, inv, "ap@probe.test").status_code == 200
    first = _get(client, inv)
    smtp.fail = True
    assert _email(client, inv, "other@probe.test").status_code == 502
    after = _get(client, inv)
    assert after["last_emailed_to"] == "ap@probe.test"
    assert after["last_emailed_at"] == first["last_emailed_at"]


def test_a_second_send_updates_the_record(client, db_session, seed_accounts, smtp):
    inv = _invoice(client)
    assert _email(client, inv, "ap@probe.test").status_code == 200
    assert _email(client, inv, "owner@probe.test").status_code == 200
    got = _get(client, inv)
    assert got["last_emailed_to"] == "owner@probe.test"
    newest = (
        db_session.query(EmailLog)
        .filter(EmailLog.entity_type == "invoice", EmailLog.status == "sent")
        .order_by(EmailLog.id.desc())
        .first()
    )
    assert got["last_emailed_at"].startswith(newest.sent_at.isoformat()[:19])


def test_the_app_email_button_with_the_customer_address_is_recorded(
    client, seed_accounts, smtp
):
    # The Email button posts the dialog to the same route; with the To box
    # left empty the customer's address on file is used.
    inv = _invoice(client)
    r = client.post(
        f"/api/invoices/{inv['id']}/email",
        json={
            "recipient": "",
            "cc": None,
            "bcc": None,
            "subject": "Invoice",
            "message": "Thanks",
        },
    )
    assert r.status_code == 200, r.text
    assert _get(client, inv)["last_emailed_to"] == "billing@probe.test"


def test_another_document_send_is_not_this_invoice_record(
    client, db_session, seed_accounts, smtp
):
    one, two = _invoice(client), _invoice(client, "Second Probe Co")
    assert _email(client, one, "ap@probe.test").status_code == 200
    assert _get(client, two)["last_emailed_at"] is None
    # an estimate or statement sent with the same id is not the invoice's
    for kind in ("estimate", "statement"):
        db_session.add(
            EmailLog(
                entity_type=kind,
                entity_id=two["id"],
                recipient="someone@probe.test",
                subject="Not an invoice",
                status="sent",
            )
        )
    db_session.commit()
    assert _get(client, two)["last_emailed_to"] is None
