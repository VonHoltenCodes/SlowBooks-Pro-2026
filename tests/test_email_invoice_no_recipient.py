"""Emailing an invoice with no recipient is a 400, not a 500 (#496).

The Email Invoice request let the recipient default to nothing, but
EmailLog.recipient is NOT NULL, so a send with no address crashed on its own
log row and the route's error handler crashed again logging the same None.
The route now settles the recipient first (the typed address, else the
customer's email on file) and, with neither, answers 400 before logging.
"""

import app.routes.invoices.documents as docs
from app.models.contacts import Customer
from app.models.email_log import EmailLog


def _customer(db_session, email=None, name="Acme Diner"):
    c = Customer(name=name, email=email, is_active=True)
    db_session.add(c)
    db_session.commit()
    return c


def _invoice(client, customer_id):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": customer_id,
            "date": "2026-03-01",
            "due_date": "2026-03-01",
            "tax_rate": 0,
            "lines": [{"description": "Sign", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_no_recipient_and_no_customer_email_is_400_and_logs_nothing(
    client, db_session, seed_accounts, monkeypatch
):
    # The PDF stack is not under test here.
    monkeypatch.setattr(docs, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")

    cust = _customer(db_session, email=None)
    inv_id = _invoice(client, cust.id)
    client.put("/api/settings", json={"smtp_host": "smtp.example.com"})

    r = client.post(f"/api/invoices/{inv_id}/email", json={})
    assert r.status_code == 400, r.text
    assert "email address" in r.json()["detail"]
    assert db_session.query(EmailLog).count() == 0


def test_no_recipient_falls_back_to_customer_email(
    client, db_session, seed_accounts, monkeypatch
):
    monkeypatch.setattr(docs, "generate_invoice_pdf", lambda *a, **k: b"%PDF-")

    captured = {}

    def _fake_send(*, db, to_email, **kwargs):
        captured["to_email"] = to_email
        return True

    import app.services.email_service as email_service

    monkeypatch.setattr(email_service, "send_email", _fake_send)

    cust = _customer(db_session, email="billing@acme.test")
    inv_id = _invoice(client, cust.id)
    client.put("/api/settings", json={"smtp_host": "smtp.example.com"})

    r = client.post(f"/api/invoices/{inv_id}/email", json={})
    assert r.status_code == 200, r.text
    assert captured["to_email"] == "billing@acme.test"
