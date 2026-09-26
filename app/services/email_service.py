# ============================================================================
# Email Service — SMTP wrapper for sending invoices/documents
# Feature 8: Infrastructure B (smtplib + email.mime)
# ============================================================================

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from contextvars import ContextVar

from jinja2 import Environment, FileSystemLoader, Undefined
from sqlalchemy.orm import Session

from app.models.email_log import EmailLog
from app.models.settings import Settings

logger = logging.getLogger(__name__)

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
        # add_header encodes a name with accents or other letters the RFC
        # 2231 way (filename*=utf-8''…). Setting the header as one string
        # sent "Statement_Łódź Signs.pdf" as an encoded-word covering the
        # whole value, which mail programs don't read as an attachment name.
        part.add_header("Content-Disposition", "attachment", filename=attachment_name)
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

    tpl = db.query(EmailTemplate).filter(EmailTemplate.name == template_name).first()
    if tpl:
        env = template_env()
        subject = env.from_string(tpl.subject_template).render(**context)
        body = env.from_string(tpl.body_template).render(**context)
        return subject, body
    return None, None


def template_env():
    """The environment every operator-authored template is rendered in.

    autoescape=True so customer-supplied names / addresses / memo text
    injected via {{ }} can't break out of HTML context. Same rule WC3D
    applied to the file-loader Environment in commit ca6182f — keep both
    paths consistent. Sandboxed because the template text is operator-
    editable and must not reach back into Python objects through attribute
    access.

    Shared by the send and by the template editor's preview, so a preview
    cannot render under different rules than the mail it is previewing.
    """
    from jinja2.sandbox import SandboxedEnvironment

    from app.services.pdf_service import _format_currency, _format_date

    env = SandboxedEnvironment(autoescape=True)
    env.filters["currency"] = _format_currency
    env.filters["fdate"] = _format_date
    return env


_recorded_blanks: "ContextVar[list | None]" = ContextVar(
    "recorded_blanks", default=None
)


class RecordingUndefined(Undefined):
    """Renders as nothing, like the default, and remembers that it did.

    A preview that renders a blank is the one outcome that tells its author
    nothing at all. `{{ config }}`, `{{ request }}` and a name the sandbox
    refuses all resolve to undefined and render as empty — so the operator
    sees a working template with a hole in it and no reason for the hole.

    The fix is NOT to make the preview strict. The preview must render under
    the same rules as the mail, or it is not a preview — so this behaves
    exactly like `Undefined` and only records.

    **The list lives in a ContextVar, not on the class.** The first version
    appended to a class attribute, which @skytech reproduced as cross-request
    contamination: `preview_template` is a sync `def`, so FastAPI runs it in a
    threadpool, and with sixteen concurrent previews three returned another
    request's variable names and two returned none of their own. Invisible on
    a desktop; Server Edition serves a LAN, and two bookkeepers previewing at
    once got each other's. anyio copies the context per task, so a ContextVar
    is isolated per request.

    **Only a RENDER is recorded.** `{% if pay_url %}` asks whether something
    is there; that is a guard doing its job, not a hole. Reporting it would
    flag the shipped default template on every preview, which is a false
    alarm and the fastest way to teach an operator to ignore the line.
    """

    def _record(self):
        seen = _recorded_blanks.get()
        if seen is None:
            return
        name = self._undefined_name or "a value"
        if name not in seen:
            seen.append(name)

    def __str__(self):  # noqa: D105 — Jinja renders through this
        self._record()
        return ""

    def __html__(self):
        self._record()
        return ""


# Names a template MAY use that are simply absent for this particular
# invoice, as opposed to names no email template can ever use.
#
# @skytech, 2.12.1 gate: the preview banner said `pay_url` was "not available
# to an email template" while the hint line two inches above said it IS
# available, conditional on a payment provider. Two messages about one
# variable, disagreeing — which is the class this whole gate has been about,
# in the smallest possible form.
CONDITIONAL_TEMPLATE_NAMES = {
    "pay_url": (
        "only set when a payment provider is enabled — guard it with "
        "{% if pay_url %}"
    ),
}


def classify_blanks(names) -> dict:
    """Split recorded blanks into the two kinds an author needs told apart.

    `{{ config }}` can never work. `{{ pay_url }}` works and is not set for
    this invoice. Telling an operator the second is "not available" is wrong,
    and it contradicts the editor's own variable list.
    """
    unavailable, conditional = [], []
    for name in names:
        (conditional if name in CONDITIONAL_TEMPLATE_NAMES else unavailable).append(
            name
        )
    return {
        "unavailable": unavailable,
        "conditional": [
            {"name": n, "why": CONDITIONAL_TEMPLATE_NAMES[n]} for n in conditional
        ],
    }


def recording_template_env():
    """`template_env()` plus a per-request note of what rendered as nothing.

    Returns (env, seen). `seen` fills during render and belongs to this
    request only — the send path must not pay for this, and neither must
    another request.
    """
    env = template_env()
    seen: list[str] = []
    _recorded_blanks.set(seen)
    env.undefined = RecordingUndefined
    return env, seen


def invoice_email_label(invoice, company_settings: dict) -> str:
    """What the attached document is called in the email: Invoice, Pledge,
    Sales Receipt or Donation Receipt — the same literal face the PDF
    prints (donor_documents.invoice_doc_kind), never the vocabulary swap."""
    from app.services.donor_documents import document_label
    from app.services.terminology import terms_for

    return document_label(invoice, terms_for(company_settings))


def invoice_email_context(invoice, company_settings: dict, pay_url: str = None) -> dict:
    """What a saved `invoice_email` template can reference.

    Kept in one place so the preview and the send cannot drift — the reason
    they could before is that there was no shared renderer at all.
    """
    from app.services.settings_service import redact_secrets
    from app.services.terminology import terms_for

    terms = terms_for(company_settings)
    return {
        "invoice": invoice,
        "inv": invoice,  # the file template's name for it
        # GHSA-c3v4-f43f-4wqm. The `invoice_email` template is operator-
        # editable and, since #140, actually rendered — so `{{ company }}`
        # would dump every decrypted credential into an email addressed to
        # whoever the sender chooses. Redacted at the point the context is
        # built, so no caller can forget.
        "company": redact_secrets(company_settings),
        "customer_name": (
            invoice.customer.name if invoice.customer else terms("Customer")
        ),
        # Omitted rather than None when no provider is enabled. `{{ pay_url }}`
        # used to render the literal text "None" into a customer's email, and
        # `resolved_to_nothing` could not flag it because None is a real
        # value. Undefined renders as empty, is reported, and `{% if pay_url %}`
        # — which the shipped default uses — is still correctly falsy.
        # @skytech, 2.12.1 gate: the one gap the feature is shaped to catch
        # and structurally could not.
        **({"pay_url": pay_url} if pay_url else {}),
        "doc_label": invoice_email_label(invoice, company_settings),
        "terms": terms,
    }


def _note_paragraph(note: str) -> str:
    """The operator's own message, as an escaped paragraph.

    It is prepended to whatever body we end up with and is deliberately NOT
    a template variable. A `{{ note }}` a saved template can omit would drop
    the operator's message silently — the same failure class #140 is about,
    moved rather than fixed. @mdornich's call, and it is the right one:
    placement control is additive later, a silently dropped message is not.
    """
    import html as _html

    note = (note or "").strip()
    return f"<p>{_html.escape(note)}</p>\n" if note else ""


def render_invoice_email_parts(
    invoice,
    company_settings: dict,
    pay_url: str = None,
    note: str = None,
    db=None,
    subject: str = None,
) -> tuple[str, str]:
    """(subject, html_body) for an invoice email — the one renderer.

    Order, and the whole point of #140: **the template saved under Settings
    -> Email Templates is used if it exists.** Before this it was never
    loaded by anything; `render_template_from_db` existed and had exactly
    one caller, the donor acknowledgment. Editing `invoice_email` saved
    correctly and changed nothing about the email a customer received.

    The template is looked up by the fixed name `invoice_email`, never by
    the document's face. `invoice_email_label()` answers "Sales Receipt" or
    "Donation Receipt" for those kinds, so selecting on it would skip the
    saved template for every sales receipt — which is ordinary businesses,
    not only nonprofit installs (#140, third part).
    """
    ctx = invoice_email_context(invoice, company_settings, pay_url=pay_url)
    default_subject = f"{ctx['doc_label']} #{invoice.invoice_number}"

    body = None
    tpl_subject = None
    if db is not None:
        try:
            tpl_subject, tpl_body = render_template_from_db(db, "invoice_email", ctx)
            if tpl_body:
                body = tpl_body
        except Exception:
            # A saved template with a bad expression must not stop the mail
            # going out; fall through to the built-in body.
            logger.exception("saved invoice_email template failed to render")

    if body is None:
        try:
            body = _jinja_env.get_template("invoice_email.html").render(**ctx)
        except Exception:
            body = _fallback_invoice_body(invoice, company_settings, ctx)

    return (subject or tpl_subject or default_subject), _note_paragraph(note) + body


def render_invoice_email(
    invoice, company_settings: dict, pay_url: str = None, note: str = None, db=None
) -> str:
    """The HTML body alone. Kept because callers and tests use it."""
    return render_invoice_email_parts(
        invoice, company_settings, pay_url=pay_url, note=note, db=db
    )[1]


def _fallback_invoice_body(invoice, company_settings: dict, ctx: dict) -> str:
    """Last resort when neither a saved nor a file template renders.

    Escaped by hand because there is no Jinja autoescape on this path:
    customer and company names are user-controlled text.
    """
    import html as _html

    customer_name = _html.escape(ctx["customer_name"])
    company_name = _html.escape(company_settings.get("company_name", "Our Company"))
    invoice_number = _html.escape(str(invoice.invoice_number))
    doc_label = ctx["doc_label"]
    thanks = (
        "Thank you for your support."
        if ctx["terms"].is_nonprofit
        else "Thank you for your business."
    )
    return f"""<html><body>
        <p>Dear {customer_name},</p>
        <p>Please find attached {doc_label} #{invoice_number} for ${float(invoice.total):,.2f}.</p>
        <p>Payment is due by {invoice.due_date}.</p>
        <p>{thanks}</p>
        <p>{company_name}</p>
        </body></html>"""
