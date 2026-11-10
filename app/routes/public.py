# ============================================================================
# Public Routes — pages accessible without authentication
# Serves the public invoice payment page at /pay/{token}
# ============================================================================

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice
from app.services.payments import enabled_providers, get_provider, provider_settings
from app.services.payments.recorder import record_provider_payment
from app.services.pdf_service import _format_currency, document_currency_code
from app.services.settings_service import get_all_settings as get_settings

router = APIRouter(tags=["public"])

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_jinja_env = Environment(autoescape=True, loader=FileSystemLoader(str(TEMPLATE_DIR)))
# The page prints an invoice's amounts the way its PDF does: in the
# invoice's currency, with thousands separators, and plain dollars when that
# is the home currency. It used to hard-code "$" (#761).
_jinja_env.filters["currency"] = _format_currency
_jinja_env.globals["document_currency_code"] = document_currency_code


def _confirm_return_payment(db: Session, invoice: Invoice, provider_name: str) -> bool:
    """On a ?status=success return redirect, verify with the provider
    before showing "Payment received".

    The query param alone proves nothing — anyone can append it. Poll the
    stored checkout id and record the payment if (and only if) the
    provider says it's captured. This is also what makes desktop installs
    work at all: no webhook can reach 127.0.0.1, so the return redirect
    is their recording path. Idempotent with the webhook (both key on the
    external id).
    """
    external_id = None
    if invoice.checkout_provider == provider_name:
        external_id = invoice.checkout_external_id
    if provider_name == "stripe" and not external_id:
        external_id = invoice.stripe_checkout_session_id
    if not external_id:
        return False
    try:
        provider = get_provider(provider_name)
    except KeyError:
        return False
    settings = provider_settings(db, provider)
    if not provider.is_configured(settings):
        return False
    # Already recorded (the webhook, or an earlier return/poll, got there
    # first): it's confirmed — skip poll_status, which with clearing on would
    # make an extra provider call just to fetch a fee we won't post again.
    from app.models.payments import Payment

    if db.query(Payment).filter(Payment.reference == external_id).first():
        return True
    try:
        result = provider.poll_status(external_id, settings)
    except Exception:
        # Provider unreachable — don't block the page; the webhook or a
        # later "Check payment status" poll will record it.
        return False
    if result.status == "paid" and result.amount is not None:
        record_provider_payment(
            db,
            provider.name,
            provider.display_name,
            result.invoice_id or invoice.id,
            result.external_id,
            result.amount,
            fee=result.fee,
        )
        db.refresh(invoice)
        return True
    return False


@router.get("/pay/{token}")
def public_payment_page(
    token: str,
    status: str = None,
    provider: str = None,
    db: Session = Depends(get_db),
):
    """Public invoice payment page — no auth required."""
    invoice = db.query(Invoice).filter(Invoice.payment_token == token).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    settings = get_settings(db)

    verified_success = False
    if status == "success":
        provider_name = provider or invoice.checkout_provider or "stripe"
        verified_success = _confirm_return_payment(db, invoice, provider_name)

    providers = [
        {"name": p.name, "display_name": p.display_name} for p in enabled_providers(db)
    ]

    if status == "success":
        # Only a provider-verified capture may render the success banner;
        # an unverified return shows "being confirmed" instead.
        payment_status = "success" if verified_success else None
    else:
        payment_status = status or None

    from app.services.pay_applications import (
        invoice_is_release_only,
        pay_application_number,
    )

    template = _jinja_env.get_template("public_pay.html")
    html = template.render(
        inv=invoice,
        # a pay application that only releases retainage reads as the
        # release, not a $0.00 total (#751)
        release_only=invoice_is_release_only(invoice),
        pay_application_number=pay_application_number(db, invoice),
        company=settings,
        providers=providers,
        payment_status=payment_status,
        payment_pending=(status == "success" and not verified_success),
        token=token,
    )
    return HTMLResponse(html)
