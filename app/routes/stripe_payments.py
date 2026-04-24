# ============================================================================
# Stripe Payments — Checkout session creation and webhook handler
# Accepts online payments via Stripe Checkout (hosted)
# ============================================================================

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice, InvoiceStatus
from app.models.payments import Payment, PaymentAllocation


class CheckoutSessionRequest(BaseModel):
    payment_token: str
from app.services.accounting import (
    create_journal_entry, get_ar_account_id, get_undeposited_funds_id,
)
from app.services.stripe_service import (
    get_stripe_settings, create_checkout_session, verify_webhook_event,
)

router = APIRouter(prefix="/api/stripe", tags=["stripe"])


def _require_stripe(db: Session) -> dict:
    """Load and validate Stripe settings."""
    settings = get_stripe_settings(db)
    if settings.get("stripe_enabled") != "true":
        raise HTTPException(status_code=400, detail="Online payments are not enabled")
    if not settings.get("stripe_secret_key"):
        raise HTTPException(status_code=400, detail="Stripe secret key not configured")
    return settings


@router.post("/create-checkout-session")
def create_checkout(data: CheckoutSessionRequest, request: Request, db: Session = Depends(get_db)):
    """Create a Stripe Checkout Session for an invoice."""
    settings = _require_stripe(db)

    invoice = db.query(Invoice).filter(Invoice.payment_token == data.payment_token).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        raise HTTPException(status_code=400, detail="Invoice is already paid or void")
    if invoice.balance_due <= 0:
        raise HTTPException(status_code=400, detail="No balance due")

    base_url = str(request.base_url).rstrip("/")
    checkout_url, session_id = create_checkout_session(invoice, settings, base_url)

    invoice.stripe_checkout_session_id = session_id
    db.commit()

    return {"checkout_url": checkout_url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Stripe webhook events. Verifies signature, records payment."""
    settings = get_stripe_settings(db)
    webhook_secret = settings.get("stripe_webhook_secret", "")
    if not webhook_secret:
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = verify_webhook_event(payload, sig_header, webhook_secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] != "checkout.session.completed":
        return {"status": "ignored"}

    session = event["data"]["object"]
    invoice_id = int(session["metadata"]["invoice_id"])
    session_id = session["id"]

    # Idempotency: check if payment already recorded for this session
    existing = db.query(Payment).filter(Payment.reference == session_id).first()
    if existing:
        return {"status": "already_processed"}

    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return {"status": "invoice_not_found"}
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        return {"status": "invoice_already_settled"}

    # Calculate payment amount (Stripe uses cents)
    amount_cents = session.get("amount_total", 0)
    amount = Decimal(amount_cents) / Decimal("100")

    # Cap at balance due
    if amount > invoice.balance_due:
        amount = invoice.balance_due

    # Create payment record
    payment = Payment(
        customer_id=invoice.customer_id,
        date=date.today(),
        amount=amount,
        method="stripe",
        reference=session_id,
        notes=f"Online payment via Stripe Checkout",
    )
    db.add(payment)
    db.flush()

    # Create allocation
    alloc = PaymentAllocation(
        payment_id=payment.id,
        invoice_id=invoice.id,
        amount=amount,
    )
    db.add(alloc)

    # Update invoice
    invoice.amount_paid += amount
    invoice.balance_due -= amount
    if invoice.balance_due <= 0:
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIAL

    # Journal entry: DR Undeposited Funds, CR A/R
    ar_id = get_ar_account_id(db)
    deposit_id = get_undeposited_funds_id(db)

    if ar_id and deposit_id:
        customer_name = invoice.customer.name if invoice.customer else "Customer"
        journal_lines = [
            {
                "account_id": deposit_id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": f"Stripe payment from {customer_name}",
            },
            {
                "account_id": ar_id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": f"Stripe payment from {customer_name}",
            },
        ]
        txn = create_journal_entry(
            db, date.today(),
            f"Stripe payment — Invoice #{invoice.invoice_number}",
            journal_lines, source_type="payment", source_id=payment.id,
            reference=session_id,
        )
        payment.transaction_id = txn.id

    db.commit()
    return {"status": "payment_recorded"}


@router.get("/payment-link/{invoice_id}")
def get_payment_link(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Get the public payment URL for an invoice."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Generate token if missing
    if not invoice.payment_token:
        invoice.payment_token = str(uuid.uuid4())
        db.commit()

    base_url = str(request.base_url).rstrip("/")
    return {"url": f"{base_url}/pay/{invoice.payment_token}"}
