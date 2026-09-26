# ============================================================================
# Stripe provider — wraps the official `stripe` SDK behind PaymentProvider.
# Moved from app/services/stripe_service.py in the provider-abstraction
# refactor; the SDK calls are unchanged.
# ============================================================================

from decimal import Decimal
from typing import Mapping, Optional

import stripe

from app.models.invoices import Invoice
from app.services.payments.base import CheckoutSession, PaymentProvider, PaymentResult
from app.services.donor_documents import document_label
from app.services.terminology import document_reference, terms_for

# The SDK otherwise creates a persistent telemetry ID and sends it, platform
# details, and previous-request metrics alongside operator-initiated API calls.
stripe.enable_telemetry = False


class StripeProvider(PaymentProvider):
    name = "stripe"
    display_name = "Stripe"
    settings_keys = (
        "stripe_enabled",
        "stripe_publishable_key",
        "stripe_secret_key",
        "stripe_webhook_secret",
    )
    secret_keys = ("stripe_secret_key", "stripe_webhook_secret")

    def is_configured(self, settings: Mapping[str, str]) -> bool:
        return bool(settings.get("stripe_secret_key"))

    def create_checkout(
        self, invoice: Invoice, settings: Mapping[str, str], base_url: str
    ) -> CheckoutSession:
        # what the payer sees on the hosted page; the ids and metadata
        # the webhook resolves by are untouched
        terms = terms_for(settings)
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
                            "name": document_reference(
                                document_label(invoice, terms), invoice.invoice_number
                            ),
                            "description": f"Payment for {document_label(invoice, terms).lower()} #{invoice.invoice_number}",
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
            success_url=f"{base_url}/pay/{invoice.payment_token}?status=success&provider=stripe",
            cancel_url=f"{base_url}/pay/{invoice.payment_token}?status=cancelled&provider=stripe",
        )
        return CheckoutSession(url=session.url, external_id=session.id)

    def verify_webhook(
        self, payload: bytes, headers: Mapping[str, str], settings: Mapping[str, str]
    ) -> Optional[PaymentResult]:
        webhook_secret = settings.get("stripe_webhook_secret", "")
        if not webhook_secret:
            raise ValueError("Webhook secret not configured")
        sig_header = headers.get("stripe-signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as exc:
            raise ValueError("Invalid webhook signature") from exc

        if event["type"] != "checkout.session.completed":
            return None

        session = event["data"]["object"]
        amount_cents = session.get("amount_total", 0)
        return PaymentResult(
            status="paid",
            external_id=session["id"],
            amount=Decimal(amount_cents) / Decimal("100"),
            invoice_id=int(session["metadata"]["invoice_id"]),
            raw=dict(session),
        )

    def poll_status(
        self, external_id: str, settings: Mapping[str, str]
    ) -> PaymentResult:
        stripe.api_key = settings["stripe_secret_key"]
        session = stripe.checkout.Session.retrieve(external_id)
        paid = session.get("payment_status") == "paid"
        cancelled = session.get("status") == "expired"
        amount_cents = session.get("amount_total") or 0
        invoice_id = None
        metadata = session.get("metadata") or {}
        if metadata.get("invoice_id"):
            invoice_id = int(metadata["invoice_id"])
        return PaymentResult(
            status="paid" if paid else ("cancelled" if cancelled else "pending"),
            external_id=external_id,
            amount=Decimal(amount_cents) / Decimal("100") if paid else None,
            invoice_id=invoice_id,
            raw=dict(session),
        )
