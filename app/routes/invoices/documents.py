from typing import Optional as _Optional

from fastapi import Depends, HTTPException, Request
from pydantic import Field as _Field
from fastapi.responses import Response
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.services.pdf_service import generate_invoice_pdf
from app.services.settings_service import get_all_settings as get_settings
from app.services.terminology import terms_for
from app.services.donor_documents import invoice_doc_kind, invoice_pdf_context

from app.routes.invoices._router import router


class _EmailInvoiceRequest(StrictModel):
    recipient: str
    subject: _Optional[str] = None
    # The Email Invoice dialog has always posted `message`, and this model
    # is a StrictModel — so every send from the interface was rejected 422
    # before it reached any of the code below. The box was not merely
    # ignored; it broke the button it sat on (issue #140, mdornich).
    # Bounded because it becomes an email body: unbounded operator text is
    # an unbounded SMTP send.
    message: _Optional[str] = _Field(None, max_length=5000)


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    """Generate the invoice PDF."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    pdf_bytes = generate_invoice_pdf(inv, company)
    doc_kind = invoice_doc_kind(inv, terms_for(company))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename={doc_kind}_{inv.invoice_number}.pdf"
        },
    )


@router.get("/{invoice_id}/print-preview")
def invoice_print_preview(invoice_id: int, db: Session = Depends(get_db)):
    """Render invoice as HTML page for browser print dialog (window.print())"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path

    template_dir = Path(__file__).parent.parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    from app.services.pdf_service import _format_currency, _format_date

    env.filters["currency"] = _format_currency
    env.filters["fdate"] = _format_date
    template = env.get_template("invoice_pdf.html")
    # Add customer_name to invoice object for template
    if inv.customer and not hasattr(inv, "customer_name"):
        inv.customer_name = inv.customer.name
    html_str = template.render(
        inv=inv,
        company=company,
        terms=terms_for(company),
        **invoice_pdf_context(inv, company),
    )
    # Wrap with auto-print script
    html_str = html_str.replace(
        "</body>", "<script>window.onload=function(){window.print();}</script></body>"
    )
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=html_str)


class _EmailPreviewRequest(StrictModel):
    subject_template: _Optional[str] = None
    body_template: _Optional[str] = None


def _invoice_pay_url(db: Session, inv, request: Request) -> _Optional[str]:
    """Public pay link, or None when no payment provider is enabled."""
    from app.services.payments import enabled_providers

    if inv.payment_token and enabled_providers(db):
        return f"{str(request.base_url).rstrip('/')}/pay/{inv.payment_token}"
    return None


@router.post("/{invoice_id}/email-preview")
def preview_invoice_email(
    invoice_id: int,
    data: _EmailPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Render saved or unsaved template text without emailing or posting.

    The template editor sends its unsaved textareas here, so "Preview" can
    show an edit before it is saved. Read-only: no EmailLog row, no
    transaction, no change to the invoice.
    """
    from app.services.email_service import render_invoice_message

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    overrides = data.model_dump(exclude_unset=True) or None
    try:
        subject, body = render_invoice_message(
            db,
            inv,
            get_settings(db),
            _invoice_pay_url(db, inv, request),
            overrides,
        )
    except Exception as exc:
        # Deliberately broad. Everything inside the try is rendering text the
        # client supplied, and Jinja is only one of the things that can raise:
        # a syntax error and a sandbox escape are TemplateError, but
        # "{{ 1/0 }}" is ZeroDivisionError and a huge range() is OverflowError.
        # All of them are bad input, so all of them are a 400, and the message
        # stays generic because the text came from the client.
        raise HTTPException(
            status_code=400,
            detail=(
                "Template could not be rendered. Check the template "
                "variables and syntax."
            ),
        ) from exc
    return {
        "subject": subject,
        "html_body": body,
        "pdf_url": f"/api/invoices/{inv.id}/pdf",
        "recipient": inv.customer.email if inv.customer else "",
    }


@router.post("/{invoice_id}/email")
def email_invoice(
    invoice_id: int,
    data: _EmailInvoiceRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Email invoice as PDF attachment — Feature 8"""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    from app.services.email_service import invoice_email_label

    # Provisional subject for the failure path below: if rendering itself
    # raises, the EmailLog row still needs a subject, and it should say
    # "Donation Receipt #12" for a nonprofit, not "Invoice #12".
    subject = (
        data.subject or f"{invoice_email_label(inv, company)} #{inv.invoice_number}"
    )

    try:
        from app.services.email_service import send_email, render_invoice_message
        from app.models.email_log import EmailLog

        pdf_bytes = generate_invoice_pdf(inv, company)

        rendered_subject, html_body = render_invoice_message(
            db, inv, company, _invoice_pay_url(db, inv, request), note=data.message
        )
        # `or`, not `is not None`: the Subject field is user-clearable, and
        # an empty string must fall back rather than mail a blank header.
        subject = data.subject or rendered_subject
        # send_email() writes its own EmailLog row on every path (sent,
        # failed, and SMTP-not-configured), so the route must not log again
        # or every send produces two rows.
        sent = send_email(
            db=db,
            to_email=data.recipient,
            subject=subject,
            html_body=html_body,
            attachment_bytes=pdf_bytes,
            attachment_name=(
                f"{invoice_doc_kind(inv, terms_for(company))}_{inv.invoice_number}.pdf"
            ),
            entity_type="invoice",
            entity_id=inv.id,
        )
        # It returns False rather than raising when SMTP is unconfigured or
        # the send fails; reporting "sent" regardless would be a lie.
        if not sent:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Email could not be sent. Check the SMTP settings under "
                    "Settings -> Email; the failure is recorded in the email log."
                ),
            )
        return {"status": "sent"}
    except HTTPException:
        raise
    except Exception as e:
        # A failure before send_email() ran (rendering, the payment URL)
        # has no EmailLog row yet; write one, with the same sanitised text
        # the response gets — SMTP and provider errors carry hostnames.
        from app.models.email_log import EmailLog
        from app.services.safe_errors import safe_message

        message = safe_message(e, "invoice email")
        log = EmailLog(
            entity_type="invoice",
            entity_id=inv.id,
            recipient=data.recipient,
            subject=subject,
            status="failed",
            error_message=message,
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Email failed: {message}")
