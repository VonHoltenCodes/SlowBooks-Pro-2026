# ============================================================================
# Stripe Service — Checkout Session creation and webhook verification
# Online payment integration for invoice collection
# ============================================================================

import stripe
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.invoices import Invoice
from app.models.settings import Settings


def get_stripe_settings(db: Session) -> dict:
    """Load Stripe settings from the settings table."""
    keys = [
        "stripe_enabled",
        "stripe_publishable_key",
        "stripe_secret_key",
        "stripe_webhook_secret",
    ]
    rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
    result = {k: "" for k in keys}
    for r in rows:
        result[r.key] = r.value
    return result


def create_checkout_session(
    invoice: Invoice, settings: dict, base_url: str
) -> tuple[str, str]:
    """Create a Stripe Checkout Session. Returns (checkout_url, session_id)."""
    stripe.api_key = settings["stripe_secret_key"]
    amount_cents = int(Decimal(str(invoice.balance_due)) * 100)

    customer_email = None
    if invoice.customer and invoice.customer.email:
        customer_email = invoice.customer.email

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Invoice #{invoice.invoice_number}",
                        "description": f"Payment for invoice #{invoice.invoice_number}",
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }
        ],
        metadata={
            "invoice_id": str(invoice.id),
            "payment_token": invoice.payment_token,
        },
        customer_email=customer_email,
        success_url=f"{base_url}/pay/{invoice.payment_token}?status=success",
        cancel_url=f"{base_url}/pay/{invoice.payment_token}?status=cancelled",
    )
    return session.url, session.id


def verify_webhook_event(payload: bytes, sig_header: str, webhook_secret: str):
    """Verify and return a Stripe webhook event."""
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
