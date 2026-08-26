from typing import Optional as _Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.services.pdf_service import generate_invoice_pdf
from app.services.settings_service import get_all_settings as get_settings

from app.routes.invoices._router import router


class _EmailInvoiceRequest(BaseModel):
    recipient: str
    subject: _Optional[str] = None


@router.get("/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, db: Session = Depends(get_db)):
    """Generate the invoice PDF."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    company = get_settings(db)
    pdf_bytes = generate_invoice_pdf(inv, company)
    doc_kind = "SalesReceipt" if inv.is_sales_receipt else "Invoice"
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
    html_str = template.render(inv=inv, company=company)
    # Wrap with auto-print script
    html_str = html_str.replace(
        "</body>", "<script>window.onload=function(){window.print();}</script></body>"
    )
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=html_str)


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
    subject = data.subject or f"Invoice #{inv.invoice_number}"
    try:
        from app.services.email_service import send_email, render_invoice_email
        from app.models.email_log import EmailLog

        pdf_bytes = generate_invoice_pdf(inv, company)

        # Build pay URL if any payment provider is enabled and the invoice
        # has a payment token
        from app.services.payments import enabled_providers

        pay_url = None
        if inv.payment_token and enabled_providers(db):
            base_url = str(request.base_url).rstrip("/")
            pay_url = f"{base_url}/pay/{inv.payment_token}"

        html_body = render_invoice_email(inv, company, pay_url=pay_url)
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
                f"SalesReceipt_{inv.invoice_number}.pdf"
                if inv.is_sales_receipt
                else f"Invoice_{inv.invoice_number}.pdf"
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
        from app.models.email_log import EmailLog

        log = EmailLog(
            entity_type="invoice",
            entity_id=inv.id,
            recipient=data.recipient,
            subject=subject,
            status="failed",
            error_message=str(e),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")
