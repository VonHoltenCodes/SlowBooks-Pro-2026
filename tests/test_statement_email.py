"""A/R Aging → Email All Overdue reports what actually went out.

Explore 2.17.3 (skytech W-H7): with no mail server configured, "Email All
Overdue" answered "Sent 2 statements". batch_email_statements counted every
call to send_email() as a send, but send_email() returns False (it does not
raise) when SMTP is missing or refuses; and it treated never-sent DRAFT
invoices as overdue. Collection letters had the same counting.
"""

from pathlib import Path

import pytest

from app.models.contacts import Customer

ROOT = Path(__file__).resolve().parents[1]


def _overdue_customer(client, db_session, name, email, send=True):
    c = Customer(name=name, email=email, is_active=True)
    db_session.add(c)
    db_session.commit()
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": c.id,
            "date": "2026-01-01",
            "due_date": "2026-01-31",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    if send:
        assert client.post(f"/api/invoices/{r.json()['id']}/send").status_code == 200
    return c.id


@pytest.fixture
def no_pdf(monkeypatch):
    """The PDF is not what is under test; don't need WeasyPrint for it."""
    import app.routes.reports.receivables as rcv

    monkeypatch.setattr(rcv, "generate_statement_pdf", lambda *a, **k: b"%PDF-")
    monkeypatch.setattr(rcv, "generate_collection_letter_pdf", lambda *a, **k: b"%PDF-")


@pytest.fixture
def outbox(monkeypatch):
    """A mail server that records what it was given; `fail` makes it refuse."""
    import app.services.email_service as es

    box = {"sent": [], "fail": False}

    def fake_send(db, to_email, subject, html_body, **kw):
        if box["fail"]:
            return False
        box["sent"].append(to_email)
        return True

    monkeypatch.setattr(es, "send_email", fake_send)
    return box


def test_no_mail_server_sends_nothing_and_says_so(
    client, db_session, seed_accounts, no_pdf
):
    _overdue_customer(client, db_session, "Acme Diner", "acme@example.com")
    r = client.post("/api/reports/batch-email-statements")
    assert r.status_code == 400, r.text
    assert "Email isn't set up yet, so no statements were sent" in r.json()["detail"]


def test_a_refused_send_is_a_failure_named_by_customer(
    client, db_session, seed_accounts, no_pdf, outbox
):
    client.put("/api/settings", json={"smtp_host": "smtp.example.com"})
    _overdue_customer(client, db_session, "Acme Diner", "acme@example.com")
    _overdue_customer(client, db_session, "No Email LLC", None)
    outbox["fail"] = True
    body = client.post("/api/reports/batch-email-statements").json()
    assert body["sent"] == 0 and body["failed"] == 2
    assert any(
        e.startswith("Acme Diner: the statement could not be sent")
        for e in body["errors"]
    )
    assert "No Email LLC: no email address on file" in body["errors"]


def test_only_real_sends_count_and_drafts_are_not_overdue(
    client, db_session, seed_accounts, no_pdf, outbox
):
    client.put("/api/settings", json={"smtp_host": "smtp.example.com"})
    _overdue_customer(client, db_session, "Acme Diner", "acme@example.com")
    _overdue_customer(client, db_session, "Draft Only", "draft@example.com", send=False)
    body = client.post("/api/reports/batch-email-statements").json()
    assert body == {"sent": 1, "failed": 0, "errors": []}
    assert outbox["sent"] == ["acme@example.com"]


def test_collection_letters_count_only_letters_that_went_out(
    client, db_session, seed_accounts, no_pdf, outbox
):
    client.put("/api/settings", json={"smtp_host": "smtp.example.com"})
    _overdue_customer(client, db_session, "Acme Diner", "acme@example.com")
    _overdue_customer(client, db_session, "Draft Only", "draft@example.com", send=False)
    outbox["fail"] = True
    body = client.post(
        "/api/reports/collection-letters",
        json={"letter_type": "30", "send_email": True},
    ).json()
    assert body["generated"] == 1  # the draft is not overdue
    assert body["emailed"] == 0
    assert body["errors"] and body["errors"][0].startswith("Acme Diner:")


def test_the_page_names_who_did_not_get_one():
    js = (ROOT / "app/static/js/reports.js").read_text(encoding="utf-8")
    start = js.index("async batchEmailStatements()")
    block = js[start : js.index("async sendCollectionLetters()")]
    assert "_sendResult(" in block and "result.errors" in block
    assert "result.failed} failed" not in block
