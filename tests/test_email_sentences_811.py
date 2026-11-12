"""A failed send's sentence reads the SMTP reply code first (#811), and an
invoice's last_emailed_at carries the offset the email log carries.

A 550 for the recipient read "The server could not be reached" because the
sentence matched words in the error text ("refused"). The exception's type
and code decide now, from the exception itself or from the text stored on
the log row; the words are the fallback for an error with no code.
"""

import smtplib
import socket

import pytest

from app.models.email_log import EmailLog
from app.routes.invoices import documents
from app.services.email_errors import failure_sentence
from app.services.settings_service import set_setting

REFUSED = "The address was refused by the mail server: ap@probe.test. Check it and send again."
LATER = "The mail server asked to try again later."
UNREACHABLE = (
    "The server could not be reached — check the host, port, and the "
    "Use TLS setting."
)
CREDENTIALS = "The server rejected the username or password — check both."
FALLBACK = "The email could not be sent."

CASES = [
    (
        "a refused recipient",
        smtplib.SMTPRecipientsRefused(
            {"ap@probe.test": (550, b"5.1.1 Recipient address rejected: refused")}
        ),
        REFUSED,
    ),
    (
        "a recipient told to try later",
        smtplib.SMTPRecipientsRefused(
            {"ap@probe.test": (450, b"4.2.0 Mailbox busy, connection refused")}
        ),
        LATER,
    ),
    (
        "a refused sender",
        smtplib.SMTPSenderRefused(553, b"5.7.1 Sender refused", "books@probe.test"),
        "The mail server refused the sender address: books@probe.test. Check "
        "the From address under Settings → Email (SMTP).",
    ),
    (
        "a 4xx data reply",
        smtplib.SMTPDataError(451, b"4.3.0 Temporary failure, timed out upstream"),
        LATER,
    ),
    (
        "a 5xx data reply",
        smtplib.SMTPDataError(554, b"5.7.1 Message refused as spam"),
        FALLBACK,
    ),
    (
        "a rejected password",
        smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"),
        CREDENTIALS,
    ),
    (
        "a server that will not take the connection",
        smtplib.SMTPConnectError(421, b"Service not available"),
        UNREACHABLE,
    ),
    (
        "a connection refused",
        ConnectionRefusedError(111, "Connection refused"),
        UNREACHABLE,
    ),
    ("a timeout", socket.timeout("timed out"), UNREACHABLE),
    (
        "an unknown host",
        socket.gaierror(-2, "Name or service not known"),
        UNREACHABLE,
    ),
    (
        "a dropped connection",
        smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
        UNREACHABLE,
    ),
]


@pytest.mark.parametrize("what,error,sentence", CASES, ids=[c[0] for c in CASES])
def test_each_exception_gives_its_sentence(what, error, sentence):
    assert failure_sentence(error) == sentence


@pytest.mark.parametrize("what,error,sentence", CASES, ids=[c[0] for c in CASES])
def test_the_stored_text_of_each_exception_gives_the_same_sentence(
    what, error, sentence
):
    # the email log keeps str(e); what is shown is read back from it.
    # SMTPConnectError's text is a bare (421, ...) pair, which reads as a 4xx.
    if isinstance(error, smtplib.SMTPConnectError):
        sentence = LATER
    assert failure_sentence(str(error)) == sentence


def test_a_refused_recipient_never_reads_as_an_unreachable_server():
    error = smtplib.SMTPRecipientsRefused(
        {"ap@probe.test": (550, b"Relay access denied; connection refused")}
    )
    assert "could not be reached" not in failure_sentence(error)
    assert "could not be reached" not in failure_sentence(str(error))


def test_a_gmail_password_rejection_names_the_app_password():
    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username not accepted")
    assert "app password" in failure_sentence(error, "smtp.gmail.com")
    assert "app password" in failure_sentence(str(error), "smtp.gmail.com")


def test_several_refused_recipients_are_all_named():
    error = smtplib.SMTPRecipientsRefused(
        {"a@probe.test": (550, b"no"), "b@probe.test": (553, b"no")}
    )
    assert failure_sentence(error) == (
        "The address was refused by the mail server: a@probe.test, b@probe.test. "
        "Check it and send again."
    )


def test_an_error_without_a_code_falls_back_to_the_words():
    assert failure_sentence("[Errno 111] Connection refused") == UNREACHABLE
    assert failure_sentence("SMTP not configured").startswith("SMTP is not configured")
    assert failure_sentence("something odd") == FALLBACK
    assert failure_sentence("") == FALLBACK
    assert failure_sentence(None) == FALLBACK


@pytest.fixture
def smtp(monkeypatch, db_session):
    """SMTP configured and faked; `smtp.refuse = True` has the server answer
    550 for the recipient."""

    class State:
        refuse = False

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, _from, to, message):
            if State.refuse:
                raise smtplib.SMTPRecipientsRefused(
                    {to[0]: (550, b"5.1.1 mx.example.net: recipient refused")}
                )

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")
    set_setting(db_session, "smtp_host", "mail.example.com")
    set_setting(db_session, "smtp_from_email", "books@example.com")
    db_session.commit()
    return State


def _invoice(client):
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "ap@probe.test"}
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


def _email(client, inv):
    return client.post(
        f"/api/invoices/{inv['id']}/email",
        json={"recipient": "ap@probe.test", "subject": "Your invoice", "message": ""},
    )


def test_a_refused_recipient_reads_as_refused_in_the_email_log(
    client, seed_accounts, smtp
):
    inv = _invoice(client)
    smtp.refuse = True
    assert _email(client, inv).status_code == 502
    row = next(
        r
        for r in client.get("/api/email-log").json()["rows"]
        if r["status"] == "failed"
    )
    assert row["failure"] == REFUSED
    # the administrator still reads the raw text
    assert "550" in row["error_message"]


def test_last_emailed_at_carries_the_offset_and_matches_the_log(
    client, db_session, seed_accounts, smtp
):
    inv = _invoice(client)
    assert _email(client, inv).status_code == 200
    got = client.get(f"/api/invoices/{inv['id']}").json()
    listed = next(i for i in client.get("/api/invoices").json() if i["id"] == inv["id"])
    log = next(
        r
        for r in client.get("/api/email-log").json()["rows"]
        if r["entity_type"] == "invoice" and r["entity_id"] == inv["id"]
    )
    assert got["last_emailed_at"].endswith("+00:00")
    assert got["last_emailed_at"] == log["sent_at"]
    assert listed["last_emailed_at"] == log["sent_at"]
    row = db_session.query(EmailLog).filter(EmailLog.entity_id == inv["id"]).one()
    assert log["sent_at"].startswith(row.sent_at.isoformat()[:19])


def test_the_invoice_email_failure_says_the_address_was_refused(
    client, seed_accounts, smtp
):
    inv = _invoice(client)
    smtp.refuse = True
    r = _email(client, inv)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Email could not be sent. " + REFUSED)
    assert "Settings → Email (SMTP) → Recent sends" in detail
    # the raw reply stays in the log, not in the answer
    assert "550" not in detail and "mx.example.net" not in detail
    assert "Check the SMTP settings" not in detail


def test_a_failure_with_nothing_to_say_still_points_at_the_settings(
    client, seed_accounts, smtp, monkeypatch
):
    inv = _invoice(client)

    def odd(*a, **k):
        raise smtplib.SMTPException("something odd")

    monkeypatch.setattr(smtplib.SMTP, "sendmail", odd, raising=False)
    r = _email(client, inv)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail.startswith(
        "Email could not be sent. Check the SMTP settings under Settings → Email (SMTP)."
    )


def test_the_estimate_email_failure_says_the_address_was_refused(
    client, seed_accounts, smtp, monkeypatch
):
    import app.routes.estimates as estimates_routes

    monkeypatch.setattr(
        estimates_routes, "generate_estimate_pdf", lambda *a, **k: b"%PDF-"
    )
    cust = client.post(
        "/api/customers", json={"name": "Probe Co", "email": "ap@probe.test"}
    ).json()
    est = client.post(
        "/api/estimates",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "lines": [{"description": "work", "quantity": 1, "rate": 100}],
        },
    )
    assert est.status_code == 201, est.text
    smtp.refuse = True
    r = client.post(f"/api/estimates/{est.json()['id']}/email", json={})
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Email could not be sent. " + REFUSED)
    assert "550" not in detail


def test_a_batch_statement_failure_says_the_address_was_refused(
    client, seed_accounts, smtp, monkeypatch
):
    import app.routes.reports.receivables as receivables

    monkeypatch.setattr(receivables, "generate_statement_pdf", lambda *a, **k: b"%PDF-")
    inv = _invoice(client)
    assert client.post(f"/api/invoices/{inv['id']}/send").status_code == 200
    smtp.refuse = True
    r = client.post("/api/reports/batch-email-statements")
    assert r.status_code == 200, r.text
    (error,) = r.json()["errors"]
    assert error == "Probe Co: the statement could not be sent. " + REFUSED


def test_the_donor_acknowledgment_failure_says_the_address_was_refused(
    client, seed_accounts, seed_customer, smtp, monkeypatch
):
    from app.services import pdf_service
    from tests.test_donor_documents import _nonprofit, _receipt

    # The letter is rendered before it is sent; the render is stubbed so the
    # send's sentence is what this checks on every box (see the giving
    # statement test below).
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    _nonprofit(client)
    client.put(f"/api/customers/{seed_customer.id}", json={"email": "ap@probe.test"})
    inv = _receipt(client, seed_customer.id).json()["invoice"]
    smtp.refuse = True
    r = client.post(
        f"/api/donors/gifts/invoice/{inv['id']}/acknowledgment/email", json={}
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail.startswith("Email could not be sent. " + REFUSED)
    assert "550" not in detail and "check SMTP settings" not in detail


def test_a_giving_statement_failure_says_the_address_was_refused(
    client, seed_accounts, seed_customer, smtp, monkeypatch
):
    from app.services import pdf_service
    from tests.test_donor_documents import _giving_year

    # The statement is rendered before it is sent; on a box without
    # WeasyPrint's native stack the render is what fails, and the route's
    # answer is about that, not the address. The sentence under test is
    # the send's, so the render is stubbed as test_giving_statement_message
    # does (the PDF's own tests render for real).
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: html.encode())
    _giving_year(client, seed_accounts, seed_customer)
    smtp.refuse = True
    r = client.post("/api/donors/giving-statements/batch-email", json={"year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == [
        "Test Customer: the giving statement could not be sent. "
        + REFUSED.replace("ap@probe.test", "d@example.org")
    ]
