"""Every batch send reports the reason a single send gives (W-211).

Collections ▸ Send statements answered "<customer>: the statement could not
be sent (the email log has the reason)" for a mail server whose certificate
failed, while the month's row and a single send carried the sentence. Each
loop that sends mail is run here against the stub SMTP server of
test_smtp_tls_verify, presenting a self-signed certificate, and each answer
must carry the same certificate sentence, per recipient. No real network,
no stored password (one made up per test).
"""

import secrets
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.services import collections as svc
from tests.test_smtp_tls_verify import (  # noqa: F401  (pytest fixtures)
    HOST,
    StubSMTP,
    _configure,
    pki,
    trust_ca,
)

SENTENCE = (
    "SlowBooks Pro could not verify the mail server's security certificate, "
    "so nothing was sent and your password was not given to it. Check the "
    "server name under Settings → Email (SMTP), or ask your mail provider. "
    f"({HOST}: certificate is self-signed)"
)


@pytest.fixture
def stub(db_session, pki, trust_ca, monkeypatch):  # noqa: F811
    """A mail server with a self-signed certificate, set up in Settings; the
    PDFs are stubbed (the send is under test, not the render)."""
    import app.routes.reports.receivables as receivables
    from app.services import pdf_service

    for target, name in (
        (svc, "generate_statement_pdf"),
        (svc, "generate_invoice_pdf"),
        (receivables, "generate_statement_pdf"),
        (receivables, "generate_collection_letter_pdf"),
    ):
        monkeypatch.setattr(target, name, lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(pdf_service, "render_pdf", lambda html, **kw: b"%PDF-")
    server = StubSMTP(*pki["self_signed"])
    _configure(db_session, server.port, secrets.token_urlsafe(16))
    yield server
    server.close()
    assert "AUTH" not in [c.split(" ", 1)[0].upper() for c in server.commands]


def _overdue(db, name="Mail Probe", email="probe@example.test", **kw):
    c = Customer(name=name, email=email, is_active=True, **kw)
    db.add(c)
    db.commit()
    db.add(
        Invoice(
            invoice_number=f"W211-{c.id}",
            customer_id=c.id,
            status=InvoiceStatus.SENT,
            date=date.today() - timedelta(days=90),
            due_date=date.today() - timedelta(days=45),
            total=Decimal(100),
            balance_due=Decimal(100),
        )
    )
    db.commit()
    return c


def test_collections_send_statements(client, db_session, stub):
    _overdue(db_session, monthly_statement=True)
    r = client.post("/api/collections/statements/run", json={})
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == [
        "Mail Probe: The statement could not be sent. " + SENTENCE
    ]


def test_collections_reminders(client, db_session, stub):
    svc.seed_default_rules(db_session)
    _overdue(db_session)
    r = client.post("/api/collections/reminders/run", json={})
    assert r.status_code == 200, r.text
    (error,) = r.json()["errors"]
    assert error.startswith("Mail Probe, ")
    assert error.endswith(": The email could not be sent. " + SENTENCE)


def test_reports_batch_statements(client, db_session, stub):
    _overdue(db_session)
    r = client.post("/api/reports/batch-email-statements")
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == [
        "Mail Probe: the statement could not be sent. " + SENTENCE
    ]


def test_collection_letters(client, db_session, stub):
    _overdue(db_session)
    r = client.post(
        "/api/reports/collection-letters",
        json={"letter_type": "30", "send_email": True},
    )
    assert r.status_code == 200, r.text
    # the same sentence as the other four paths (W-218)
    assert r.json()["errors"] == [
        "Mail Probe: the letter could not be sent. " + SENTENCE
    ]


def test_giving_statements(client, db_session, seed_accounts, seed_customer, stub):
    from tests.test_donor_documents import _giving_year

    _giving_year(client, seed_accounts, seed_customer)
    r = client.post("/api/donors/giving-statements/batch-email", json={"year": 2026})
    assert r.status_code == 200, r.text
    # the whole answer on a failure: it once differed in one full run only
    assert r.json()["errors"] == [
        "Test Customer: the giving statement could not be sent. " + SENTENCE
    ], r.json()
