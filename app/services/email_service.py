# ============================================================================
# Email Service — SMTP wrapper for sending invoices/documents
# Feature 8: Infrastructure B (smtplib + email.mime)
# ============================================================================

import html
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, pass_context
from sqlalchemy.orm import Session

from app.models.email_log import EmailLog
from app.models.settings import Settings
from app.services.formatting import format_currency, format_date

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))


@pass_context
def _format_currency(context, value):
    return format_currency(value, context.get("company", {}))


@pass_context
def _format_date(context, value):
    return format_date(value, context.get("company", {}))


_jinja_env.filters["currency"] = _format_currency
_jinja_env.filters["fdate"] = _format_date


def _get_smtp_settings(db: Session) -> dict:
    """Load SMTP settings from the settings table."""
    keys = ["smtp_host", "smtp_port", "smtp_user", "smtp_password",
            "smtp_from_email", "smtp_from_name", "smtp_use_tls"]
    rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
    settings = {r.key: r.value for r in rows}
    return settings


def _log_email(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    recipient: str,
    subject: str,
    status: str,
    error_message: str | None = None,
):
    log = EmailLog(
        entity_type=entity_type or "",
        entity_id=entity_id or 0,
        recipient=recipient,
        subject=subject,
        status=status,
        error_message=error_message,
    )
    db.add(log)
    db.commit()


def _send_email_impl(
    db: Session,
    *,
    to_email: str,
    subject: str,
    html_body: str,
    attachment_bytes: bytes = None,
    attachment_name: str = None,
    entity_type: str = None,
    entity_id: int = None,
) -> str | None:
    smtp = _get_smtp_settings(db)

    host = smtp.get("smtp_host", "")
    port = int(smtp.get("smtp_port", "587"))
    user = smtp.get("smtp_user", "")
    password = smtp.get("smtp_password", "")
    from_email = smtp.get("smtp_from_email", user)
    from_name = smtp.get("smtp_from_name", "Slowbooks Pro")
    use_tls = smtp.get("smtp_use_tls", "true").lower() == "true"

    if not to_email:
        error = "Recipient email is required"
        _log_email(
            db,
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="failed",
            error_message=error,
        )
        return error

    if not host or not from_email:
        error = "SMTP not configured"
        _log_email(
            db,
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="failed",
            error_message=error,
        )
        return error

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    if attachment_bytes and attachment_name:
        part = MIMEApplication(attachment_bytes, Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)

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

        _log_email(
            db,
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="sent",
        )
        return None
    except Exception as exc:
        error = str(exc)
        _log_email(
            db,
            entity_type=entity_type or "",
            entity_id=entity_id or 0,
            recipient=to_email,
            subject=subject,
            status="failed",
            error_message=error,
        )
        return error


def send_email(db: Session, to_email: str, subject: str, html_body: str,
               attachment_bytes: bytes = None, attachment_name: str = None,
               entity_type: str = None, entity_id: int = None) -> bool:
    """Send an email via SMTP. Returns True on success."""
    return _send_email_impl(
        db,
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
        entity_type=entity_type,
        entity_id=entity_id,
    ) is None


def send_email_or_raise(db: Session, to_email: str, subject: str, html_body: str,
                        attachment_bytes: bytes = None, attachment_name: str = None,
                        entity_type: str = None, entity_id: int = None) -> None:
    error = _send_email_impl(
        db,
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if error:
        raise ValueError(error)


def render_invoice_email(invoice, company_settings: dict) -> str:
    """Render the invoice email HTML body."""
    try:
        template = _jinja_env.get_template("invoice_email.html")
        return template.render(inv=invoice, company=company_settings)
    except Exception:
        # Fallback simple email
        company_name = company_settings.get("company_name", "Our Company")
        total = format_currency(invoice.total, company_settings)
        due_date = format_date(invoice.due_date, company_settings)
        return f"""<html><body>
        <p>Dear {invoice.customer.name if invoice.customer else 'Customer'},</p>
        <p>Please find attached Invoice #{invoice.invoice_number} for {total}.</p>
        <p>Payment is due by {due_date}.</p>
        <p>Thank you for your business.</p>
        <p>{company_name}</p>
        </body></html>"""


def _format_document_value(value, company_settings: dict) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return format_date(value, company_settings)
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return format_date(value, company_settings)
        except Exception:
            return html.escape(str(value))
    if isinstance(value, (int, float)) or hasattr(value, "quantize"):
        try:
            return format_currency(value, company_settings)
        except Exception:
            return html.escape(str(value))
    return html.escape(str(value))


def render_document_email(
    *,
    document_label: str,
    company_settings: dict,
    recipient_name: str | None = None,
    document_number: str | None = None,
    amount=None,
    action_label: str | None = None,
    action_value=None,
    message: str | None = None,
) -> str:
    company_name = html.escape(company_settings.get("company_name", "Our Company"))
    recipient = html.escape(recipient_name or "there")
    doc_label = html.escape(document_label)
    number_text = f" #{html.escape(document_number)}" if document_number else ""
    amount_html = (
        f"<p><strong>Amount:</strong> {_format_document_value(amount, company_settings)}</p>"
        if amount is not None else ""
    )
    action_html = (
        f"<p><strong>{html.escape(action_label)}:</strong> {_format_document_value(action_value, company_settings)}</p>"
        if action_label and action_value is not None else ""
    )
    body_message = html.escape(message or f"Please find attached your {document_label.lower()}.")
    return f"""<html><body>
    <p>Dear {recipient},</p>
    <p>{body_message}</p>
    <p><strong>Document:</strong> {doc_label}{number_text}</p>
    {action_html}
    {amount_html}
    <p>Thank you,</p>
    <p>{company_name}</p>
    </body></html>"""


def send_document_email(
    db: Session,
    *,
    to_email: str,
    subject: str,
    html_body: str,
    attachment_bytes: bytes,
    attachment_name: str,
    entity_type: str,
    entity_id: int,
) -> None:
    send_email_or_raise(
        db,
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
        entity_type=entity_type,
        entity_id=entity_id,
    )
