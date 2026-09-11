# ============================================================================
# Email Templates — CRUD for customizable email templates
# Phase 10: Quick Wins + Medium Effort Features
# ============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.email_templates import EmailTemplate
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
        "subject_template": "Invoice #{{ invoice.invoice_number }} from {{ company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>Please find attached Invoice #{{ invoice.invoice_number }} for {{ invoice.total | currency }}.</p>
<p>Payment is due by {{ invoice.due_date | fdate }}.</p>
{% if pay_url %}<p><a href="{{ pay_url }}">Pay Online</a></p>{% endif %}
<p>Thank you for your business.</p>
<p>{{ company.company_name }}</p>""",
        "template_type": "invoice",
    },
    {
        "name": "payment_receipt",
        "subject_template": "Payment Receipt from {{ company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>We have received your payment of {{ amount | currency }}. Thank you!</p>
<p>{{ company.company_name }}</p>""",
        "template_type": "payment_receipt",
    },
    {
        "name": "past_due_reminder",
        "subject_template": "Reminder: Invoice #{{ invoice.invoice_number }} is past due",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>This is a friendly reminder that Invoice #{{ invoice.invoice_number }} for {{ invoice.balance_due | currency }} was due on {{ invoice.due_date | fdate }}.</p>
<p>Please arrange payment at your earliest convenience.</p>
<p>{{ company.company_name }}</p>""",
        "template_type": "past_due",
    },
    {
        "name": "collection_letter_30",
        "subject_template": "Payment Reminder — {{ company.company_name }}",
        "body_template": """<p>Dear {{ customer_name }},</p>
<p>Our records indicate that payment of {{ total_due | currency }} is past due. Please review the enclosed statement and remit payment promptly.</p>
<p>If you have already sent payment, please disregard this notice.</p>
<p>{{ company.company_name }}</p>""",
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


def _validate_template_fields(data):
    """Reject unparseable template text at save time.

    Without this a typo like "{% if %}" saves fine and only surfaces later
    as a failed send, with the invoice already marked sent. Compile-only —
    rendering (and the sandbox check that comes with it) happens on
    preview and send.
    """
    from jinja2 import TemplateSyntaxError

    from app.services.email_service import template_env

    env = template_env(autoescape=True)
    for name in ("subject_template", "body_template"):
        value = getattr(data, name, None)
        if value is None:
            continue
        try:
            env.from_string(value)
        except TemplateSyntaxError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid {name} syntax on line {exc.lineno}.",
            ) from exc


@router.post("", response_model=EmailTemplateResponse, status_code=201)
def create_template(data: EmailTemplateCreate, db: Session = Depends(get_db)):
    existing = db.query(EmailTemplate).filter(EmailTemplate.name == data.name).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="Template with this name already exists"
        )
    _validate_template_fields(data)
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
    _validate_template_fields(data)
    for key, val in data.model_dump(exclude_unset=True).items():
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


@router.post("/seed-defaults")
def seed_defaults(db: Session = Depends(get_db)):
    """Create default email templates if they don't exist."""
    created = 0
    for tpl in DEFAULT_TEMPLATES:
        existing = (
            db.query(EmailTemplate).filter(EmailTemplate.name == tpl["name"]).first()
        )
        if not existing:
            db.add(EmailTemplate(**tpl))
            created += 1
    db.commit()
    return {"created": created, "total_defaults": len(DEFAULT_TEMPLATES)}
