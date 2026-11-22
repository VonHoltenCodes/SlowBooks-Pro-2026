"""#957: Email on an estimate reads a saved `estimate_email` template.

Before this the estimate's email had a fixed subject and body; invoices read
the saved `invoice_email` template. Now an estimate send renders through
`estimate_email` when one is saved, the old sentences when none is, with the
dialog's Message on top and a typed subject still winning. The template is
seeded with the others and previewed against a real estimate.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.email_templates import EmailTemplate
from app.models.estimates import Estimate


@pytest.fixture
def estimate(db_session, seed_customer):
    seed_customer.email = "client@example.com"
    est = Estimate(
        estimate_number="E-957",
        customer_id=seed_customer.id,
        date=date(2026, 9, 1),
        expiration_date=date(2026, 10, 1),
        subtotal=Decimal("1250"),
        total=Decimal("1250"),
    )
    db_session.add(est)
    db_session.commit()
    return est


@pytest.fixture
def company(db_session):
    from app.services.settings_service import set_setting

    set_setting(db_session, "company_name", "Pike Plumbing LLC")
    set_setting(db_session, "smtp_password", "hunter2-SECRET")
    db_session.commit()


@pytest.fixture
def sent(monkeypatch):
    """Capture the send instead of mailing; the PDF is a stub (no WeasyPrint)."""
    import app.routes.estimates as estimates_routes

    monkeypatch.setattr(
        estimates_routes, "generate_estimate_pdf", lambda *a, **k: b"%PDF-"
    )
    calls = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return True

    # The route imports send_email inside the handler, so patch the service.
    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    return calls


def _send(client, est, **body):
    r = client.post(f"/api/estimates/{est.id}/email", json=body)
    assert r.status_code == 200, r.text
    return r


def _save_template(db_session, subject, body):
    db_session.add(
        EmailTemplate(
            name="estimate_email",
            subject_template=subject,
            body_template=body,
            template_type="estimate",
        )
    )
    db_session.commit()


def test_with_no_saved_template_the_send_reads_as_before(
    client, db_session, estimate, company, sent
):
    _send(client, estimate)
    assert len(sent) == 1
    assert sent[0]["subject"] == "Estimate #E-957 from Pike Plumbing LLC"
    assert "Please find estimate #E-957 attached." in sent[0]["html_body"]
    assert "<p>Pike Plumbing LLC</p>" in sent[0]["html_body"]
    assert sent[0]["entity_type"] == "estimate"
    assert sent[0]["to_email"] == "client@example.com"


def test_a_saved_template_changes_subject_and_body(
    client, db_session, estimate, company, sent
):
    _save_template(
        db_session,
        "Your bid {{ estimate.estimate_number }} for {{ customer_name }}",
        "<p>Total {{ estimate.total | currency }}, good to "
        "{{ estimate.expiration_date | fdate }}.</p>",
    )
    _send(client, estimate)
    assert sent[0]["subject"] == "Your bid E-957 for Test Customer"
    body = sent[0]["html_body"]
    assert "1,250.00" in body
    assert "Please find estimate" not in body


def test_the_dialog_message_still_goes_on_top(
    client, db_session, estimate, company, sent
):
    _save_template(db_session, "S", "<p>TEMPLATE BODY</p>")
    _send(client, estimate, message="Call me <today>")
    body = sent[0]["html_body"]
    assert body.startswith("<p>Call me &lt;today&gt;</p>")
    assert "TEMPLATE BODY" in body


def test_a_typed_subject_overrides_the_template(
    client, db_session, estimate, company, sent
):
    _save_template(db_session, "From the template", "<p>x</p>")
    _send(client, estimate, subject="Typed by hand")
    assert sent[0]["subject"] == "Typed by hand"


def test_a_broken_saved_template_falls_back_rather_than_failing(
    client, db_session, estimate, company, sent
):
    _save_template(db_session, "{{ 1/0 }}", "<p>{{ 1/0 }}</p>")
    _send(client, estimate)
    assert sent[0]["subject"] == "Estimate #E-957 from Pike Plumbing LLC"
    assert "Please find estimate #E-957 attached." in sent[0]["html_body"]


def test_the_seeded_default_renders_on_a_send(
    client, db_session, estimate, company, sent
):
    r = client.post("/api/email-templates/seed-defaults")
    assert r.status_code == 200, r.text
    tpl = (
        db_session.query(EmailTemplate)
        .filter(EmailTemplate.name == "estimate_email")
        .first()
    )
    assert tpl is not None and tpl.template_type == "estimate"
    _send(client, estimate)
    assert sent[0]["subject"] == "Estimate #E-957 from Pike Plumbing LLC"
    body = sent[0]["html_body"]
    assert "Dear Test Customer" in body
    assert "Estimate #E-957" in body
    assert "1,250.00" in body
    assert "good through" in body


def test_the_seeded_default_skips_an_empty_expiration(
    client, db_session, estimate, company, sent
):
    client.post("/api/email-templates/seed-defaults")
    estimate.expiration_date = None
    db_session.commit()
    _send(client, estimate)
    assert "good through" not in sent[0]["html_body"]


def test_seed_defaults_creates_estimate_email(client, db_session):
    client.post("/api/email-templates/seed-defaults")
    names = [t["name"] for t in client.get("/api/email-templates").json()]
    assert "estimate_email" in names


def _preview(client, **extra):
    return client.post(
        "/api/email-templates/preview",
        json={
            "subject_template": "{{ doc_label }} #{{ estimate.estimate_number }}",
            "body_template": "<p>{{ customer_name }} {{ estimate.total | currency }}</p>",
            **extra,
        },
    )


def test_preview_renders_against_the_most_recent_estimate(
    client, db_session, estimate, company
):
    r = _preview(client, template_name="estimate_email")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["subject"] == "Estimate #E-957"
    assert "Test Customer" in out["html_body"]
    assert "1,250.00" in out["html_body"]
    assert out["resolved_to_nothing"] == []


def test_preview_against_a_picked_estimate(client, db_session, estimate, company):
    r = _preview(client, estimate_id=estimate.id, template_type="estimate")
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == "Estimate #E-957"


def test_preview_with_no_estimates_is_a_clear_404(client, db_session):
    r = _preview(client, template_name="estimate_email")
    assert r.status_code == 404
    assert "no estimate to preview against" in r.json()["detail"]


def test_preview_of_an_unknown_estimate_404s(client, db_session, estimate):
    r = _preview(client, estimate_id=999999, template_type="estimate")
    assert r.status_code == 404


def test_company_in_the_context_carries_no_smtp_password(
    client, db_session, estimate, company, sent
):
    body = _preview(
        client,
        template_name="estimate_email",
        body_template="<p>{{ company.smtp_password }}{{ company }}</p>",
    ).json()["html_body"]
    assert "hunter2-SECRET" not in body

    _save_template(db_session, "S", "<p>{{ company.smtp_password }}{{ company }}</p>")
    _send(client, estimate)
    assert "hunter2-SECRET" not in sent[0]["html_body"]


def test_the_context_redacts_the_company_directly(estimate):
    from app.services.email_service import estimate_email_context

    ctx = estimate_email_context(
        estimate, {"company_name": "Pike", "smtp_password": "hunter2-SECRET"}
    )
    assert ctx["company"]["smtp_password"] != "hunter2-SECRET"
    assert ctx["est"] is ctx["estimate"]
    assert ctx["doc_label"] == "Estimate"
    assert "pay_url" not in ctx
