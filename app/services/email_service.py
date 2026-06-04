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
    rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
    settings = {r.key: r.value for r in rows}
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


def render_template_from_db(db: Session, template_name: str, context: dict) -> tuple:
    """Load template from DB, render with Jinja2 SandboxedEnvironment, fall back to file."""
    from app.models.email_templates import EmailTemplate
    from jinja2.sandbox import SandboxedEnvironment

    tpl = db.query(EmailTemplate).filter(EmailTemplate.name == template_name).first()
    if tpl:
        # autoescape=True so customer-supplied names / addresses / memo
        # text injected via {{ }} can't break out of HTML context. Same
        # rule WC3D applied to the file-loader Environment in commit
        # ca6182f — keep both paths consistent.
        env = SandboxedEnvironment(autoescape=True)
        from app.services.pdf_service import _format_currency, _format_date

        env.filters["currency"] = _format_currency
        env.filters["fdate"] = _format_date
        subject = env.from_string(tpl.subject_template).render(**context)
        body = env.from_string(tpl.body_template).render(**context)
        return subject, body
    return None, None


def render_invoice_email(invoice, company_settings: dict, pay_url: str = None) -> str:
    """Render the invoice email HTML body."""
    try:
        template = _jinja_env.get_template("invoice_email.html")
        return template.render(inv=invoice, company=company_settings, pay_url=pay_url)
    except Exception:
        # Fallback simple email. Customer name + company name are escaped
        # via html.escape() since they can contain user-controlled text
        # (e.g. a customer named `<script>...`). Invoice number is a
        # generated string but escaped defensively. Float and date come
        # from server-side formatting — no need to escape.
        import html as _html

        customer_name = _html.escape(
            invoice.customer.name if invoice.customer else "Customer"
        )
        company_name = _html.escape(company_settings.get("company_name", "Our Company"))
        invoice_number = _html.escape(str(invoice.invoice_number))
        return f"""<html><body>
        <p>Dear {customer_name},</p>
        <p>Please find attached Invoice #{invoice_number} for ${float(invoice.total):,.2f}.</p>
        <p>Payment is due by {invoice.due_date}.</p>
        <p>Thank you for your business.</p>
        <p>{company_name}</p>
        </body></html>"""
