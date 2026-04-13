# ============================================================================
# Email Service — SMTP wrapper for sending invoices/documents
# Feature 8: Infrastructure B (smtplib + email.mime)
# ============================================================================

import smtplib
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
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


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


def send_email(db: Session, to_email: str, subject: str, html_body: str,
               attachment_bytes: bytes = None, attachment_name: str = None,
               entity_type: str = None, entity_id: int = None) -> bool:
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
        log = EmailLog(entity_type=entity_type or "", entity_id=entity_id or 0,
                       recipient=to_email, subject=subject, status="failed",
                       error_message="SMTP not configured")
        db.add(log)
        db.commit()
        return False

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

        log = EmailLog(entity_type=entity_type or "", entity_id=entity_id or 0,
                       recipient=to_email, subject=subject, status="sent")
        db.add(log)
        db.commit()
        return True

    except Exception as e:
        log = EmailLog(entity_type=entity_type or "", entity_id=entity_id or 0,
                       recipient=to_email, subject=subject, status="failed",
                       error_message=str(e))
        db.add(log)
        db.commit()
        return False


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
