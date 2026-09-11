# ============================================================================
# Email Service — SMTP wrapper for sending invoices/documents
# Feature 8: Infrastructure B (smtplib + email.mime)
# ============================================================================

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.orm import Session

from app.models.email_log import EmailLog
from app.models.settings import Settings

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def _get_smtp_settings(db: Session) -> dict:
    """Load SMTP settings from the settings table."""
    keys = [
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_from_email",
        "smtp_from_name",
        "smtp_use_tls",
    ]
    from app.services.settings_service import _maybe_decrypt

    rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
    settings = {r.key: _maybe_decrypt(r.key, r.value) for r in rows}
    return settings


def send_email(
    db: Session,
    to_email: str,
    subject: str,
    html_body: str,
    attachment_bytes: bytes = None,
    attachment_name: str = None,
    entity_type: str = None,
    entity_id: int = None,
) -> bool:
    """Send an email via SMTP. Returns True on success."""
    smtp = _get_smtp_settings(db)

    host = smtp.get("smtp_host", "")
    port = int(smtp.get("smtp_port", "587"))
    user = smtp.get("smtp_user", "")
    password = smtp.get("smtp_password", "")
    from_email = smtp.get("smtp_from_email", user)
    from_name = smtp.get("smtp_from_name", "Slowbooks Pro")
    use_tls = smtp.get("smtp_use_tls", "true").lower() == "true"

    if not host or not from_email:
        log = EmailLog(
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="failed",
            error_message="SMTP not configured",
        )
        db.add(log)
        db.commit()
        return False

    # Sanitize email headers to prevent injection
    to_email = to_email.replace("\r", "").replace("\n", "").strip()
    subject = subject.replace("\r", "").replace("\n", " ").strip()

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    if attachment_bytes and attachment_name:
        part = MIMEApplication(attachment_bytes, Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)

    server = None
    try:
        if use_tls:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP(host, port, timeout=30)

        if user and password:
            server.login(user, password)

        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()
        server = None

        log = EmailLog(
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="sent",
        )
        db.add(log)
        db.commit()
        return True

    except Exception as e:
        if server:
            try:
                server.quit()
            except Exception:
                pass
        log = EmailLog(
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="failed",
            error_message=str(e),
        )
        db.add(log)
        db.commit()
        return False


def template_env(autoescape: bool) -> SandboxedEnvironment:
    """Sandboxed Jinja environment with the shared currency/date filters.

    Sandboxed because template text is user-editable under Settings ->
    Email Templates: a template must not be able to walk back into Python
    objects through attribute access (``{{ invoice.__class__ }}``).
    """
    from app.services.pdf_service import _format_currency, _format_date

    env = SandboxedEnvironment(autoescape=autoescape)
    env.filters["currency"] = _format_currency
    env.filters["fdate"] = _format_date
    return env


def render_template_from_db(db: Session, template_name: str, context: dict) -> tuple:
    """Load template from DB, render with Jinja2 SandboxedEnvironment, fall back to file."""
    from app.models.email_templates import EmailTemplate

    tpl = db.query(EmailTemplate).filter(EmailTemplate.name == template_name).first()
    if tpl:
        # autoescape=True so customer-supplied names / addresses / memo
        # text injected via {{ }} can't break out of HTML context. Same
        # rule WC3D applied to the file-loader Environment in commit
        # ca6182f — keep both paths consistent.
        env = template_env(autoescape=True)
        subject = env.from_string(tpl.subject_template).render(**context)
        body = env.from_string(tpl.body_template).render(**context)
        return subject, body
    return None, None


def invoice_email_label(invoice, company_settings: dict) -> str:
    """What the attached document is called in the email: Invoice, Pledge,
    Sales Receipt or Donation Receipt — the same literal face the PDF
    prints (donor_documents.invoice_doc_kind), never the vocabulary swap."""
    from app.services.donor_documents import invoice_doc_kind
    from app.services.terminology import terms_for

    kind = invoice_doc_kind(invoice, terms_for(company_settings))
    return {
        "SalesReceipt": "Sales Receipt",
        "DonationReceipt": "Donation Receipt",
    }.get(kind, kind)


def render_invoice_email(
    invoice, company_settings: dict, pay_url: str = None, note: str = None
) -> str:
    """Render the invoice email HTML body.

    `note` is the operator's own message from the Email Invoice dialog. It
    is escaped and rendered as a paragraph above the standard text — the
    box has existed in the interface for a long time and, until #140, was
    not merely discarded but made the whole request fail validation.
    """
    from app.services.terminology import terms_for

    doc_label = invoice_email_label(invoice, company_settings)
    try:
        template = _jinja_env.get_template("invoice_email.html")
        return template.render(
            inv=invoice,
            company=company_settings,
            pay_url=pay_url,
            doc_label=doc_label,
            note=(note or "").strip() or None,
            terms=terms_for(company_settings),
        )
    except Exception:
        # Fallback simple email. Customer name + company name are escaped
        # via html.escape() since they can contain user-controlled text
        # (e.g. a customer named `<script>...`). Invoice number is a
        # generated string but escaped defensively. Float and date come
        # from server-side formatting — no need to escape.
        import html as _html

        customer_name = _html.escape(
            invoice.customer.name
            if invoice.customer
            else terms_for(company_settings)("Customer")
        )
        company_name = _html.escape(company_settings.get("company_name", "Our Company"))
        invoice_number = _html.escape(str(invoice.invoice_number))
        # The operator's message is a paragraph above the body, never a
        # replacement for part of it, so the standard opening stays.
        opening = _note_html(note) + (
            f"<p>Please find attached {doc_label} #{invoice_number} "
            f"for ${float(invoice.total):,.2f}.</p>"
        )
        return f"""<html><body>
        <p>Dear {customer_name},</p>
        {opening}
        <p>Payment is due by {invoice.due_date}.</p>
        <p>{'Thank you for your support.' if terms_for(company_settings).is_nonprofit else 'Thank you for your business.'}</p>
        <p>{company_name}</p>
        </body></html>"""


def _note_html(note: str) -> str:
    """The operator's message as one escaped paragraph, or "" if there isn't one.

    Escaped here rather than by Jinja because this is concatenated onto an
    already-rendered body — which also means a note containing {{ }} or
    {% %} is inert rather than a second pass of template syntax. Line
    breaks are kept: the dialog offers a three-row textarea, and an
    operator who presses return means it.
    """
    import html as _html

    if not note or not note.strip():
        return ""
    return "<p>" + _html.escape(note.strip()).replace("\n", "<br>") + "</p>"


def _insert_note(body: str, note_html: str) -> str:
    """Put the note above the body, staying inside <body> when there is one.

    A saved template can be a whole document pasted from a designer. Text
    placed before <html> is dropped by the sanitisers in Gmail and Outlook,
    which would lose the operator's message silently — the defect this is
    supposed to prevent.
    """
    import re as _re

    if not note_html:
        return body
    match = _re.search(r"<body[^>]*>", body, _re.I)
    if match:
        return body[: match.end()] + note_html + body[match.end() :]
    return note_html + body


def render_invoice_message(
    db: Session,
    invoice,
    company: dict,
    pay_url: str = None,
    overrides: dict = None,
    note: str = None,
) -> tuple:
    """Subject + HTML body for an invoice email, shared by preview and send.

    A saved ``invoice_email`` template wins when one exists; ``overrides``
    carries unsaved editor text so the template editor can preview an edit
    before it is saved. With neither, fall back to the built-in
    ``invoice_email.html`` body so an install that never touched the
    template keeps the email it has today.

    ``note`` is the operator's per-send message from the Email Invoice
    dialog. It is rendered as an escaped paragraph immediately above the
    body (inside ``<body>`` when the template is a whole document) and is
    deliberately *not* a template variable: a ``{{ note }}`` that a saved
    template can omit would drop the operator's message silently, which is
    the defect this endpoint exists to fix.

    On this path the note lands above whatever the template begins with,
    including a salutation — unlike the built-in body, where it sits below
    the greeting. Raised on #140.
    """
    from app.models.email_templates import EmailTemplate
    from app.services.settings_service import redact_secrets
    from app.services.terminology import terms_for

    label = invoice_email_label(invoice, company)
    template = (
        db.query(EmailTemplate).filter(EmailTemplate.name == "invoice_email").first()
    )
    if template is None and overrides is None:
        return (
            f"{label} #{invoice.invoice_number}",
            render_invoice_email(invoice, company, pay_url, note=note),
        )

    subject = f"{label} #{invoice.invoice_number}"
    body = ""
    if template is not None:
        subject = template.subject_template
        body = template.body_template
    if overrides is not None:
        subject = overrides.get("subject_template", subject)
        body = overrides.get("body_template", body)

    context = {
        "invoice": invoice,
        "inv": invoice,
        # Redacted: get_all_settings() decrypts smtp_password, the Stripe
        # secret key and the rest, and this template is user-editable, so
        # the raw dict here would let anyone who can edit a template mail
        # themselves the credentials.
        "company": redact_secrets(company),
        "pay_url": pay_url,
        # Same name the file template uses, so a nonprofit can write
        # "{{ doc_label }}" instead of hardcoding the word Invoice.
        "doc_label": label,
        "customer_name": (
            invoice.customer.name
            if invoice.customer
            else terms_for(company)("Customer")
        ),
    }
    # The subject is a mail header, not HTML. Escaping it would put a
    # literal "&amp;" in the inbox for a customer named "Smith & Sons";
    # header sanitisation stays in send_email().
    rendered_subject = template_env(autoescape=False).from_string(subject)
    rendered_body = template_env(autoescape=True).from_string(body)
    html = _insert_note(rendered_body.render(**context), _note_html(note))
    return rendered_subject.render(**context), html
