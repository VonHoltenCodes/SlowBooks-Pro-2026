# ============================================================================
# Email Templates — CRUD for customizable email templates
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import reject_null_updates
from app.models.email_templates import EmailTemplate
from app.schemas.common import StrictModel
from app.schemas.email_templates import (
    EmailTemplateCreate,
    EmailTemplateUpdate,
    EmailTemplateResponse,
)

from app.services.donor_documents import ACK_BODY, ACK_SUBJECT, ACK_TEMPLATE_NAME

router = APIRouter(prefix="/api/email-templates", tags=["email-templates"])

DEFAULT_TEMPLATES = [
    {
        "name": "invoice_email",
        "subject_template": "{{ doc_label }} #{{ invoice.invoice_number }} from {{ company.display_name or company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>Please find attached {{ doc_label }} #{{ invoice.invoice_number }} for {{ invoice.total | currency }}.</p>
<p>Payment is due by {{ invoice.due_date | fdate }}.</p>
{% if pay_url %}<p><a href="{{ pay_url }}">Pay Online</a></p>{% endif %}
<p>Thank you for your business.</p>
<p>{{ company.display_name or company.company_name }}</p>""",
        "template_type": "invoice",
    },
    {
        "name": "payment_receipt",
        "subject_template": "Payment Receipt from {{ company.display_name or company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>We have received your payment of {{ amount | currency }}. Thank you!</p>
<p>{{ company.display_name or company.company_name }}</p>""",
        "template_type": "payment_receipt",
    },
    {
        "name": "past_due_reminder",
        "subject_template": "Reminder: {{ doc_label }} #{{ invoice.invoice_number }} is past due",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>This is a friendly reminder that {{ doc_label }} #{{ invoice.invoice_number }} for {{ invoice.balance_due | currency }} was due on {{ invoice.due_date | fdate }}.</p>
<p>Please arrange payment at your earliest convenience.</p>
<p>{{ company.display_name or company.company_name }}</p>""",
        "template_type": "past_due",
    },
    {
        "name": "collection_letter_30",
        "subject_template": "Payment Reminder — {{ company.display_name or company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>Our records indicate that payment of {{ total_due | currency }} is past due. Please review the enclosed statement and remit payment promptly.</p>
<p>If you have already sent payment, please disregard this notice.</p>
<p>{{ company.display_name or company.company_name }}</p>""",
        "template_type": "collection",
    },
    # Nonprofit: the donor acknowledgment letter (PDF + email). Variables:
    # donor, donor_name, gift (amount, date, number, fair_value_amount,
    # in_kind_lines), irs.text (the IRS Pub. 1771 sentence), company.
    {
        "name": ACK_TEMPLATE_NAME,
        "subject_template": ACK_SUBJECT,
        "body_template": ACK_BODY,
        "template_type": "acknowledgment",
    },
]


@router.get("", response_model=list[EmailTemplateResponse])
def list_templates(db: Session = Depends(get_db)):
    return (
        db.query(EmailTemplate)
        .order_by(EmailTemplate.template_type, EmailTemplate.name)
        .all()
    )


@router.get("/{template_id}", response_model=EmailTemplateResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(EmailTemplate).filter(EmailTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


class _TemplatePreviewRequest(StrictModel):
    invoice_id: int
    subject_template: str = ""
    body_template: str = ""


@router.post("/preview")
def preview_template(
    data: _TemplatePreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Render candidate template text against a real invoice, without saving.

    #140 gave the send dialog a preview of what it is about to send. This is
    the other half: the operator editing `invoice_email` under Settings can
    see an edit before committing it, rather than saving over a working
    template to find out. Read-only — nothing is written, nothing is mailed.
    """
    from jinja2 import TemplateError

    from app.models.invoices import Invoice
    from app.services.email_service import (
        classify_blanks,
        invoice_email_context,
        recording_template_env,
    )
    from app.services.payments import enabled_providers
    from app.services.settings_service import get_all_settings

    inv = db.query(Invoice).filter(Invoice.id == data.invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    company = get_all_settings(db)
    pay_url = None
    if inv.payment_token and enabled_providers(db):
        pay_url = f"{str(request.base_url).rstrip('/')}/pay/{inv.payment_token}"

    ctx = invoice_email_context(inv, company, pay_url=pay_url)
    env, resolved_to_nothing = recording_template_env()
    try:
        subject = env.from_string(data.subject_template).render(**ctx)
        body = env.from_string(data.body_template).render(**ctx)
    except (TemplateError, Exception) as exc:
        # Everything here renders text the client supplied: a syntax error, a
        # sandbox escape and "{{ 1/0 }}" are all bad input, so all are a 400.
        # The message stays generic because the text came from the client.
        raise HTTPException(
            status_code=400,
            detail=(
                "Template could not be rendered. Check the template "
                "variables and syntax."
            ),
        ) from exc
    # A blank where the author expected content is the one outcome that
    # explains nothing. `{{ config }}`, `{{ request }}` and anything the
    # sandbox refuses all render as empty, so the preview looks like a
    # working template with a hole in it. The body is byte-identical to what
    # would be sent — the preview would not be a preview otherwise — and the
    # names that resolved to nothing are reported beside it.
    # Two kinds of blank, and calling them both "not available" contradicted
    # the editor's own variable list for `pay_url` (@skytech, 2.12.1 gate).
    blanks = list(resolved_to_nothing)
    return {
        "subject": subject,
        "html_body": body,
        "resolved_to_nothing": blanks,
        **classify_blanks(blanks),
    }


@router.post("", response_model=EmailTemplateResponse, status_code=201)
def create_template(data: EmailTemplateCreate, db: Session = Depends(get_db)):
    existing = db.query(EmailTemplate).filter(EmailTemplate.name == data.name).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="Template with this name already exists"
        )
    template = EmailTemplate(**data.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.put("/{template_id}", response_model=EmailTemplateResponse)
def update_template(
    template_id: int, data: EmailTemplateUpdate, db: Session = Depends(get_db)
):
    template = db.query(EmailTemplate).filter(EmailTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    for key, val in reject_null_updates(
        EmailTemplate, data.model_dump(exclude_unset=True)
    ).items():
        setattr(template, key, val)
    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(EmailTemplate).filter(EmailTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(template)
    db.commit()
    return {"status": "deleted"}


_JINJA_TAG = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\})", re.S)


def _prose_in_company_words(template_text: str, terms) -> str:
    """Swap the words in the prose and leave every Jinja expression alone —
    `{{ invoice.invoice_number }}` is a variable name, not a sentence, and
    the first cut turned it into `{{ pledge.invoice_number }}`."""
    parts = _JINJA_TAG.split(template_text)
    return "".join(part if i % 2 else terms.text(part) for i, part in enumerate(parts))


@router.post("/seed-defaults")
def seed_defaults(db: Session = Depends(get_db)):
    """Create default email templates if they don't exist."""
    from app.services.terminology import terms_from_db

    # Seeded in the company's words: a nonprofit's saved, editable template
    # should not open with "Invoice #" when every screen says Pledge.
    terms = terms_from_db(db)
    created = 0
    for tpl in DEFAULT_TEMPLATES:
        existing = (
            db.query(EmailTemplate).filter(EmailTemplate.name == tpl["name"]).first()
        )
        if not existing:
            worded = dict(tpl)
            for field in ("subject_template", "body_template"):
                worded[field] = _prose_in_company_words(tpl[field], terms)
            db.add(EmailTemplate(**worded))
            created += 1
    db.commit()
    return {"created": created, "total_defaults": len(DEFAULT_TEMPLATES)}
