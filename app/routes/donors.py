"""Donor documents — acknowledgment letters (and, later, year-end giving
statements). A gift is a donation receipt (kind=invoice), a payment that
is not a receipt's own payment (kind=payment: a pledge payment or an
unapplied gift), or property (kind=in-kind)."""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.services.donor_documents import (
    collect_gifts,
    gift_irs,
    giving_statement_irs_text,
    NotAGift,
    load_gift,
    render_acknowledgment,
)
from app.services.email_service import send_email
from app.services.pdf_service import (
    generate_acknowledgment_letter_pdf,
    generate_giving_statement_pdf,
)
from app.services.settings_service import get_all_settings
from app.services.request_utils import content_disposition

router = APIRouter(prefix="/api/donors", tags=["donors"])
logger = logging.getLogger(__name__)

GIFT_KINDS = ("invoice", "payment", "in-kind")


class AcknowledgmentEmail(StrictModel):
    recipient: str | None = None
    subject: str | None = None


def _gift_or_404(db: Session, kind: str, gift_id: int):
    if kind not in GIFT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown gift kind")
    try:
        gift = load_gift(db, kind, gift_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NotAGift as exc:
        raise HTTPException(status_code=400, detail=exc.reason)
    customer = db.get(Customer, gift["customer_id"])
    if customer is None:
        raise HTTPException(status_code=404, detail="Donor not found")
    return gift, customer


def _letter(db: Session, kind: str, gift_id: int):
    gift, customer = _gift_or_404(db, kind, gift_id)
    company = get_all_settings(db)
    subject, body = render_acknowledgment(db, company, customer, gift)
    pdf = generate_acknowledgment_letter_pdf(
        customer, gift, gift_irs(company, gift), company, body, date.today()
    )
    return gift, customer, subject, body, pdf


@router.get("/gifts/{kind}/{gift_id}/acknowledgment/preview")
def acknowledgment_preview(kind: str, gift_id: int, db: Session = Depends(get_db)):
    """Is this a gift that gets a letter, and for how much? (A receipt's own
    payment is not — the receipt is acknowledged instead.)"""
    if kind not in GIFT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown gift kind")
    try:
        gift = load_gift(db, kind, gift_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NotAGift as exc:
        return {"eligible": False, "amount": None, "reason": exc.reason}
    return {
        "eligible": True,
        "amount": float(gift["amount"]) if gift["amount"] is not None else None,
        "reason": None,
    }


@router.get("/gifts/{kind}/{gift_id}/acknowledgment/pdf")
def acknowledgment_pdf(kind: str, gift_id: int, db: Session = Depends(get_db)):
    gift, customer, _subject, _body, pdf = _letter(db, kind, gift_id)
    number = str(gift["number"] or gift_id).replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(f"Acknowledgment_{number}.pdf")
        },
    )


@router.post("/gifts/{kind}/{gift_id}/acknowledgment/email")
def acknowledgment_email(
    kind: str, gift_id: int, data: AcknowledgmentEmail, db: Session = Depends(get_db)
):
    gift, customer, subject, body, pdf = _letter(db, kind, gift_id)
    recipient = (data.recipient or customer.email or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="The donor has no email address")
    number = str(gift["number"] or gift_id).replace("/", "-")
    # send_email writes its own EmailLog row on every path.
    sent = send_email(
        db=db,
        to_email=recipient,
        subject=data.subject or subject,
        html_body=body,
        attachment_bytes=pdf,
        attachment_name=f"Acknowledgment_{number}.pdf",
        entity_type=f"acknowledgment_{kind}",
        entity_id=gift_id,
    )
    if not sent:
        raise HTTPException(
            status_code=502, detail="Email could not be sent (check SMTP settings)"
        )
    return {"sent": True, "recipient": recipient}


# ── Year-end giving statements ───────────────────────────────────────────


class GivingStatementBatch(StrictModel):
    year: int
    customer_ids: Optional[list[int]] = None


def _statement(db: Session, company: dict, customer: Customer, year: int) -> dict:
    gifts = collect_gifts(db, customer.id, year)
    return {
        "customer": customer,
        "cash": gifts["cash"],
        "in_kind": gifts["in_kind"],
        "totals": gifts["totals"],
        "irs_text": giving_statement_irs_text(company, gifts["totals"]),
    }


def _donors_with_gifts(db: Session, year: int, customer_ids=None) -> list[Customer]:
    q = db.query(Customer).filter(Customer.is_active.is_(True))
    if customer_ids:
        q = q.filter(Customer.id.in_(customer_ids))
    out = []
    for c in q.order_by(Customer.name).all():
        g = collect_gifts(db, c.id, year)
        if g["cash"] or g["in_kind"]:
            out.append(c)
    return out


@router.get("/giving-statements/pdf")
def giving_statements_pdf(
    year: int, customer_ids: Optional[str] = None, db: Session = Depends(get_db)
):
    """Every donor with a gift that year, one PDF, a page break per donor."""
    ids = [int(x) for x in (customer_ids or "").split(",") if x.strip().isdigit()]
    company = get_all_settings(db)
    donors = _donors_with_gifts(db, year, ids or None)
    statements = [_statement(db, company, c, year) for c in donors]
    pdf = generate_giving_statement_pdf(statements, company, year)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=GivingStatements_{year}.pdf"
        },
    )


@router.get("/{customer_id}/giving-statement/pdf")
def giving_statement_pdf(customer_id: int, year: int, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Donor not found")
    company = get_all_settings(db)
    pdf = generate_giving_statement_pdf(
        [_statement(db, company, customer, year)], company, year
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                f"GivingStatement_{year}_{customer.name}.pdf"
            )
        },
    )


@router.post("/giving-statements/batch-email")
def giving_statements_batch_email(
    data: GivingStatementBatch, db: Session = Depends(get_db)
):
    """Email each donor their statement (skipping those who opted out)."""
    company = get_all_settings(db)
    sent = failed = skipped = 0
    errors: list[str] = []
    for customer in _donors_with_gifts(db, data.year, data.customer_ids):
        if not customer.send_year_end_statement:
            skipped += 1
            continue
        if not customer.email:
            failed += 1
            errors.append(f"{customer.name}: no email address")
            continue
        try:
            pdf = generate_giving_statement_pdf(
                [_statement(db, company, customer, data.year)], company, data.year
            )
            ok = send_email(
                db=db,
                to_email=customer.email,
                subject=f"Your {data.year} giving statement from {company.get('company_name', '')}".strip(),
                html_body=(
                    f"<p>{customer.salutation or 'Dear ' + customer.name},</p>"
                    f"<p>Thank you for your support in {data.year}. Your giving statement is attached.</p>"
                    f"<p>{company.get('company_name', '')}</p>"
                ),
                attachment_bytes=pdf,
                attachment_name=f"GivingStatement_{data.year}.pdf",
                entity_type="giving_statement",
                entity_id=customer.id,
            )
            if ok:
                sent += 1
            else:
                failed += 1
                errors.append(f"{customer.name}: email could not be sent")
        except Exception:
            logger.exception("giving statement failed for customer %s", customer.id)
            failed += 1
            errors.append(f"{customer.name}: unable to send")
    return {"sent": sent, "failed": failed, "skipped": skipped, "errors": errors}
