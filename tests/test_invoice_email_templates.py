"""The saved invoice_email template drives both the preview and the send.

Before this, /invoices/{id}/email rendered a hardcoded body and ignored the
template the user had edited under Settings -> Email Templates, and the send
dialog posted a `message` field the route rejected. These tests pin the two
halves together: preview and send render through one code path, so what the
dialog shows is what goes out. The operator's per-send note is the one thing
preview does not show — it is typed after the preview is fetched, and is
prepended at send.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.email_log import EmailLog
from app.models.email_templates import EmailTemplate
from app.models.invoices import Invoice, InvoiceStatus
from app.models.transactions import Transaction

SUBJECT_TEMPLATE = "Invoice {{ invoice.invoice_number }} for {{ customer_name }}"
BODY_TEMPLATE = (
    "<p>{{ customer_name }} {{ invoice.total|currency }}</p>"
    "{% if invoice.balance_due == 0 %}<p>Paid in full</p>{% endif %}"
)


@pytest.fixture
def email_invoice(db_session, seed_customer):
    """A paid invoice plus a saved invoice_email template to render it with."""
    seed_customer.email = "client@example.com"
    invoice = Invoice(
        invoice_number="TEST-1",
        customer_id=seed_customer.id,
        date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        subtotal=Decimal("125"),
        total=Decimal("125"),
        amount_paid=Decimal("125"),
        balance_due=Decimal("0"),
        status=InvoiceStatus.PAID,
    )
    db_session.add(invoice)
    db_session.add(
        EmailTemplate(
            name="invoice_email",
            template_type="invoice",
            subject_template=SUBJECT_TEMPLATE,
            body_template=BODY_TEMPLATE,
        )
    )
    db_session.commit()
    return invoice


@pytest.fixture
def captured_send(monkeypatch):
    """Capture send_email() kwargs and stub the PDF so no native stack is needed."""
    import app.routes.invoices.documents as documents
    import app.services.email_service as email_service

    captured = []
    monkeypatch.setattr(documents, "generate_invoice_pdf", lambda *a: b"%PDF-test")
    monkeypatch.setattr(
        email_service, "send_email", lambda **kw: captured.append(kw) or True
    )
    return captured


def test_preview_matches_send_and_changes_nothing(
    client, db_session, email_invoice, captured_send
):
    before = (
        db_session.query(Transaction).count(),
        db_session.query(EmailLog).count(),
    )

    preview = client.post(f"/api/invoices/{email_invoice.id}/email-preview", json={})
    assert preview.status_code == 200, preview.text
    assert "Paid in full" in preview.json()["html_body"]
    assert "$125.00" in preview.json()["html_body"]
    assert preview.json()["recipient"] == "client@example.com"
    # A preview must not post a transaction or write an email log row.
    assert before == (
        db_session.query(Transaction).count(),
        db_session.query(EmailLog).count(),
    )

    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com"},
    )
    assert sent.status_code == 200, sent.text
    assert captured_send[0]["subject"] == preview.json()["subject"]
    assert captured_send[0]["html_body"] == preview.json()["html_body"]
    assert captured_send[0]["attachment_bytes"] == b"%PDF-test"
    assert captured_send[0]["to_email"] == "self@example.com"

    db_session.refresh(email_invoice)
    assert email_invoice.status == InvoiceStatus.PAID
    assert email_invoice.balance_due == 0


def test_saved_edits_apply_and_unsaved_edits_do_not_persist(
    client, db_session, email_invoice
):
    template = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()
    url = f"/api/invoices/{email_invoice.id}/email-preview"

    saved = client.put(
        f"/api/email-templates/{template.id}",
        json={
            "subject_template": "New {{ invoice.invoice_number }}",
            "body_template": "<p>New layout {{ invoice.balance_due|currency }}</p>",
        },
    )
    assert saved.status_code == 200, saved.text
    assert client.post(url, json={}).json()["subject"] == "New TEST-1"

    # The editor previews unsaved textarea content without storing it.
    unsaved = client.post(
        url, json={"subject_template": "Unsaved", "body_template": "<p>Draft</p>"}
    )
    assert unsaved.json()["subject"] == "Unsaved"
    assert client.post(url, json={}).json()["subject"] == "New TEST-1"


def test_broken_template_is_rejected_on_save_and_on_preview(
    client, db_session, email_invoice
):
    template = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()

    # A syntax error must fail at save time, not silently at send time.
    broken = client.put(
        f"/api/email-templates/{template.id}", json={"body_template": "{% if %}"}
    )
    assert broken.status_code == 400
    assert "Invalid body_template syntax" in broken.json()["detail"]

    # The sandbox blocks attribute walking back into Python objects.
    escape = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview",
        json={"body_template": "{{ invoice.__class__.__mro__ }}"},
    )
    assert escape.status_code == 400


def test_customer_name_is_escaped_and_subject_override_wins(
    client, db_session, email_invoice, captured_send
):
    email_invoice.customer.name = "<img src=x onerror=alert(1)>"
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert "<img" not in preview["html_body"]
    assert "&lt;img" in preview["html_body"]

    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com", "subject": "Test override"},
    )
    assert sent.status_code == 200, sent.text
    assert captured_send[0]["subject"] == "Test override"


def test_subject_is_not_html_escaped(client, db_session, email_invoice):
    """The subject is a mail header: "Smith & Sons" must not become "&amp;"."""
    email_invoice.customer.name = "Smith & Sons"
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert preview["subject"] == "Invoice TEST-1 for Smith & Sons"


def test_falls_back_to_builtin_body_when_no_template_exists(
    client, db_session, email_invoice
):
    db_session.query(EmailTemplate).delete()
    db_session.commit()

    preview = client.post(f"/api/invoices/{email_invoice.id}/email-preview", json={})
    assert preview.status_code == 200
    # "Amount Due:" comes from invoice_email.html and appears in no saved
    # template, so it pins the built-in body rather than just any render.
    assert "Amount Due:" in preview.json()["html_body"]


def test_sales_receipt_still_gets_its_saved_template(client, db_session, email_invoice):
    """The document label must not decide whether the template is used.

    invoice_email_label() returns Sales Receipt / Donation Receipt / Pledge
    rather than "Invoice" for those documents. Gating the saved template on
    that label means anyone using sales receipts edits a template and
    silently keeps getting the built-in body, with the editor preview
    showing the built-in body too.
    """
    email_invoice.is_sales_receipt = True
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert "Paid in full" in preview["html_body"], "saved template was ignored"

    # An unsaved editor edit must preview here too.
    edited = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview",
        json={"body_template": "<p>Draft for {{ doc_label }}</p>"},
    ).json()
    assert edited["html_body"] == "<p>Draft for Sales Receipt</p>"


def test_nonprofit_pledge_still_gets_its_saved_template(
    client, db_session, email_invoice
):
    assert (
        client.put("/api/settings", json={"company_type": "nonprofit"}).status_code
        == 200
    )
    email_invoice.is_pledge = True
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert "Paid in full" in preview["html_body"], "saved template was ignored"


def test_builtin_fallback_keeps_the_document_label(client, db_session, email_invoice):
    """With no template saved, the fallback subject still says Sales Receipt."""
    db_session.query(EmailTemplate).delete()
    email_invoice.is_sales_receipt = True
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert preview["subject"] == "Sales Receipt #TEST-1"


def test_template_cannot_read_decrypted_settings_secrets(
    client, db_session, email_invoice
):
    """Templates are user-editable, so the settings dict they render against
    must be the redacted one.

    get_all_settings() decrypts smtp_password, the Stripe secret key and the
    rest because the server needs them to actually send mail. Handing that
    dict to a template would let anyone who can edit one mail themselves the
    credentials — and /api/settings deliberately redacts all of them, for
    every role, so the template path must not be a way around that.
    """
    from app.services.settings_service import set_setting

    set_setting(db_session, "smtp_password", "hunter2-SECRET")
    set_setting(db_session, "stripe_secret_key", "sk_live_LEAKME")
    db_session.commit()

    url = f"/api/invoices/{email_invoice.id}/email-preview"
    leaked = client.post(
        url,
        json={
            "subject_template": "{{ company.stripe_secret_key }}",
            "body_template": "<p>{{ company.smtp_password }}{{ company }}</p>",
        },
    )
    assert leaked.status_code == 200, leaked.text
    rendered = leaked.json()["subject"] + leaked.json()["html_body"]
    assert "hunter2-SECRET" not in rendered
    assert "sk_live_LEAKME" not in rendered
    assert "********" in rendered

    # Non-secret company fields must still render, or the redaction is useless.
    ok = client.post(url, json={"body_template": "<p>{{ company.company_name }}</p>"})
    assert ok.json()["html_body"] == "<p>My Company</p>"


def test_blank_subject_falls_back_instead_of_mailing_an_empty_header(
    client, db_session, email_invoice, captured_send
):
    """The Subject field in the send dialog is user-clearable."""
    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com", "subject": ""},
    )
    assert sent.status_code == 200, sent.text
    assert captured_send[0]["subject"] == "Invoice TEST-1 for Test Customer"


def test_subject_template_cannot_inject_mail_headers(
    client, db_session, email_invoice, monkeypatch
):
    """A rendered subject reaches a mail header, so CRLF must not survive.

    Subjects render with autoescape off (a subject is a header, not HTML),
    which makes send_email()'s CRLF stripping load-bearing for template text
    in a way it wasn't when the subject came from a form field.
    """
    import app.services.email_service as email_service
    from app.services.settings_service import set_setting

    template = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()
    template.subject_template = "Hi\r\nBcc: attacker@example.com\r\nX: "
    db_session.commit()

    # The renderer itself leaves CRLF alone — it is not a header yet.
    rendered_subject, _ = email_service.render_invoice_message(
        db_session, email_invoice, {}, None
    )
    assert "\n" in rendered_subject

    # send_email() is where it must be neutralised, before MIMEMultipart.
    for key, value in (
        ("smtp_host", "localhost"),
        ("smtp_from_email", "billing@example.com"),
    ):
        set_setting(db_session, key, value)
    db_session.commit()

    captured = {}

    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def starttls(self, *a, **kw):
            pass

        def login(self, *a, **kw):
            pass

        def sendmail(self, from_addr, to_addrs, raw, *a, **kw):
            captured["raw"] = raw

        def quit(self):
            pass

    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)
    email_service.send_email(
        db=db_session,
        to_email="self@example.com",
        subject=rendered_subject,
        html_body="<p>body</p>",
    )

    raw = captured.get("raw", "")
    assert raw, "SMTP send was not exercised"
    subject_lines = [ln for ln in raw.splitlines() if ln.startswith("Subject:")]
    assert len(subject_lines) == 1
    assert "attacker@example.com" in subject_lines[0]
    assert not any(ln.startswith("Bcc:") for ln in raw.splitlines())


def test_render_failure_is_a_400_not_a_500(client, db_session, email_invoice):
    """Arithmetic and range blowups are bad input, same as a syntax error."""
    url = f"/api/invoices/{email_invoice.id}/email-preview"
    assert client.post(url, json={"body_template": "{{ 1/0 }}"}).status_code == 400
    assert (
        client.post(
            url, json={"body_template": "{% for i in range(9999999999) %}x{% endfor %}"}
        ).status_code
        == 400
    )


def test_create_template_also_validates_syntax(client, db_session):
    """The POST path calls the same validator the PUT path does."""
    created = client.post(
        "/api/email-templates",
        json={
            "name": "brand_new",
            "template_type": "invoice",
            "subject_template": "ok",
            "body_template": "{% if %}",
        },
    )
    assert created.status_code == 400
    assert "Invalid body_template syntax" in created.json()["detail"]


def test_operator_note_sits_above_the_saved_template_body(
    client, db_session, email_invoice, captured_send
):
    """One rule everywhere: the note is a paragraph above the body.

    It is deliberately not a template variable. A `{{ note }}` a saved
    template could omit would drop the operator's message silently — the
    same defect class as the dialog that posted a field the route refused.
    """
    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com", "message": "Ten days, as agreed."},
    )
    assert sent.status_code == 200, sent.text
    body = captured_send[0]["html_body"]
    assert "Ten days, as agreed." in body
    # Above the template's own output, not instead of it.
    assert body.index("Ten days, as agreed.") < body.index("Paid in full")


def test_operator_note_is_escaped_on_the_saved_template_path(
    client, db_session, email_invoice, captured_send
):
    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com", "message": "<script>x</script>"},
    )
    assert sent.status_code == 200, sent.text
    body = captured_send[0]["html_body"]
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body


def test_note_survives_when_no_template_is_saved(
    client, db_session, email_invoice, captured_send
):
    """The built-in body keeps its standard opening and takes the note above it."""
    db_session.query(EmailTemplate).delete()
    db_session.commit()

    sent = client.post(
        f"/api/invoices/{email_invoice.id}/email",
        json={"recipient": "self@example.com", "message": "Ten days, as agreed."},
    )
    assert sent.status_code == 200, sent.text
    body = captured_send[0]["html_body"]
    assert "Ten days, as agreed." in body
    # The standard opening is kept, not replaced — both paths read the same.
    assert "Amount Due:" in body or "Please find attached" in body


def test_note_is_not_a_template_variable(client, db_session, email_invoice):
    """A template referencing {{ note }} must not interpolate it, or operators
    would have two competing places for the same text."""
    template = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()
    template.body_template = "<p>[{{ note }}]</p>"
    db_session.commit()

    preview = client.post(
        f"/api/invoices/{email_invoice.id}/email-preview", json={}
    ).json()
    assert "[]" in preview["html_body"]


def test_note_is_escaped_on_the_builtin_fallback_path(db_session, email_invoice):
    """The f-string fallback runs only when the file template fails to load.

    Nothing else in the suite reaches it, so its html.escape() on operator
    text — an XSS-relevant control — would otherwise ship untested.
    """
    import app.services.email_service as email_service

    def _boom(*a, **kw):
        raise RuntimeError("template missing")

    original = email_service._jinja_env.get_template
    email_service._jinja_env.get_template = _boom
    try:
        body = email_service.render_invoice_email(
            email_invoice, {}, note="<script>x</script>"
        )
    finally:
        email_service._jinja_env.get_template = original

    assert "Please find attached" in body, "not the fallback path"
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
    # Above the standard opening, which is kept rather than replaced.
    assert body.index("&lt;script&gt;") < body.index("Please find attached")


def test_note_goes_inside_body_for_a_full_document_template(db_session, email_invoice):
    """A pasted designer template is a whole document. A note placed before
    <html> is dropped by Gmail and Outlook sanitisers, losing the operator's
    message silently — the defect this feature exists to prevent."""
    from app.services.email_service import render_invoice_message

    template = db_session.query(EmailTemplate).filter_by(name="invoice_email").one()
    template.body_template = (
        "<html><head><title>x</title></head><body><p>Hi</p></body></html>"
    )
    db_session.commit()

    _, html = render_invoice_message(
        db_session, email_invoice, {}, None, note="Ten days, as agreed."
    )
    assert "Ten days, as agreed." in html
    assert html.startswith("<html>"), "note was placed before <html>"
    assert html.index("<body>") < html.index("Ten days, as agreed.")
    assert html.index("Ten days, as agreed.") < html.index("Hi")


def test_note_keeps_operator_line_breaks(db_session, email_invoice):
    """The dialog offers a three-row textarea; a return in it means something."""
    from app.services.email_service import render_invoice_message

    _, body = render_invoice_message(
        db_session, email_invoice, {}, None, note="line one\nline two"
    )
    assert "line one<br>line two" in body
