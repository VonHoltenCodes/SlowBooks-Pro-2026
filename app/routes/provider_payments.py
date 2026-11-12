# ============================================================================
# Provider payments — generic checkout/webhook/poll routes for every online
# payment provider (Stripe today; PayPal/Square plug into the registry).
#
# Replaces app/routes/stripe_payments.py. The /api/stripe/webhook path is
# kept as an alias so operator webhook configs in the Stripe dashboard
# keep working.
#
# Auth model:
#   - create-checkout-session: UNAUTHENTICATED (called from the public pay
#     page) but rate-limited; the payment_token IS the capability.
#   - webhook: UNAUTHENTICATED; the provider signature is the auth. A
#     payload that fails verification is a 400 and records nothing.
#   - check-status: authenticated (SPA) — the desktop-mode fallback since
#     webhooks can't reach 127.0.0.1.
# ============================================================================

import uuid
from datetime import date as dt_date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoices import Invoice, InvoiceStatus
from app.services.payments import get_provider, provider_settings
from app.services.payments.recorder import record_provider_payment
from app.services.rate_limit import limiter


class CheckoutSessionRequest(StrictModel):
    payment_token: str


class PayoutRequest(StrictModel):
    """A provider payout that arrived without a bank feed (#584): the net
    that landed in the bank, the processor's fee for the batch (0 when it
    was already booked at capture), the date, and the bank account."""

    date: dt_date
    bank_account_id: int
    net: Decimal
    fee: Decimal = Decimal("0")


router = APIRouter(prefix="/api/payments", tags=["payments"])


def _resolve_provider(name: str):
    try:
        return get_provider(name)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown payment provider: {name}")


def _require_provider(db: Session, name: str):
    """Resolve provider + settings; 400 unless enabled and configured."""
    provider = _resolve_provider(name)
    settings = provider_settings(db, provider)
    if not provider.is_enabled(settings):
        raise HTTPException(status_code=400, detail="Online payments are not enabled")
    if not provider.is_configured(settings):
        raise HTTPException(
            status_code=400,
            detail=f"{provider.display_name} is not configured",
        )
    return provider, settings


@router.post("/{provider_name}/create-checkout-session")
@limiter.limit("10/minute")
def create_checkout(
    provider_name: str,
    data: CheckoutSessionRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a hosted checkout for an invoice (public; token = capability)."""
    provider, settings = _require_provider(db, provider_name)

    invoice = (
        db.query(Invoice).filter(Invoice.payment_token == data.payment_token).first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.VOID):
        raise HTTPException(status_code=400, detail="Invoice is already paid or void")
    if invoice.balance_due <= 0:
        raise HTTPException(status_code=400, detail="No balance due")
    from app.services.currency import document_currency, home_currency

    if document_currency(invoice, db) != home_currency(db):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online checkout is only available for home-currency invoices; "
                "record this payment manually"
            ),
        )

    base_url = str(request.base_url).rstrip("/")
    checkout = provider.create_checkout(invoice, settings, base_url)

    invoice.checkout_provider = provider.name
    invoice.checkout_external_id = checkout.external_id
    if provider.name == "stripe":
        # Kept mirrored during the transition off the provider-specific column
        invoice.stripe_checkout_session_id = checkout.external_id
    db.commit()

    return {"checkout_url": checkout.url}


@router.post("/{provider_name}/webhook")
async def provider_webhook(
    provider_name: str, request: Request, db: Session = Depends(get_db)
):
    """Handle a provider webhook. Signature verification IS the auth."""
    provider = _resolve_provider(provider_name)
    settings = provider_settings(db, provider)

    payload = await request.body()
    try:
        result = provider.verify_webhook(payload, request.headers, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result is None:
        return {"status": "ignored"}
    if result.status != "paid" or result.amount is None:
        return {"status": "ignored"}

    invoice_id = result.invoice_id
    if invoice_id is None:
        # Providers without metadata passthrough resolve the invoice by the
        # stored checkout id.
        invoice = (
            db.query(Invoice)
            .filter(Invoice.checkout_external_id == result.external_id)
            .first()
        )
        if not invoice:
            return {"status": "invoice_not_found"}
        invoice_id = invoice.id

    status = record_provider_payment(
        db,
        provider.name,
        provider.display_name,
        invoice_id,
        result.external_id,
        result.amount,
        fee=result.fee,
    )
    return {"status": status}


@router.post("/{provider_name}/check-status/{invoice_id}")
def check_status(provider_name: str, invoice_id: int, db: Session = Depends(get_db)):
    """Poll the provider for a checkout's state and record it if paid.

    The desktop-mode fallback: webhooks can't reach 127.0.0.1, so the
    invoice page's "Check payment status" button exercises this instead.
    Idempotent with the webhook path — both key on external_id.
    """
    provider, settings = _require_provider(db, provider_name)

    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    external_id = (
        invoice.checkout_external_id
        if invoice.checkout_provider == provider.name
        else None
    )
    if provider.name == "stripe" and not external_id:
        external_id = invoice.stripe_checkout_session_id
    if not external_id:
        return {"status": "no_checkout"}

    # Already recorded (the webhook, or an earlier poll, got there first):
    # skip poll_status, which with clearing on would make an extra provider
    # call just to fetch a fee we won't post again.
    from app.models.payments import Payment

    if db.query(Payment).filter(Payment.reference == external_id).first():
        return {"status": "already_processed", "provider_status": "paid"}

    result = provider.poll_status(external_id, settings)
    if result.status == "paid" and result.amount is not None:
        recorded = record_provider_payment(
            db,
            provider.name,
            provider.display_name,
            result.invoice_id or invoice.id,
            result.external_id,
            result.amount,
            fee=result.fee,
        )
        return {"status": recorded, "provider_status": result.status}
    return {"status": "not_paid", "provider_status": result.status}


NO_PAYMENT_LINK = (
    "This invoice has no payment link yet. Copy Payment Link on the invoice "
    "makes it (POST on this path)."
)


def _pay_url(request: Request, invoice: Invoice) -> dict:
    base_url = str(request.base_url).rstrip("/")
    return {"url": f"{base_url}/pay/{invoice.payment_token}"}


@router.get("/payment-link/{invoice_id}")
def get_payment_link(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """The invoice's public payment URL as it stands. A read (#659): it used
    to make the link when the invoice had none, a write a bookkeeper's
    token or an agent's read tool reached. With none, ``url`` is null and
    ``note`` says how to make one; POST on this path makes it. The read is
    a bookkeeper's or an administrator's (#822): the middleware refuses a
    read-only or accountant sign-in or token before this runs
    (app.main._SENDING_READ_RE), since copying the link is how the invoice
    is sent."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not invoice.payment_token:
        return {"url": None, "note": NO_PAYMENT_LINK}
    return _pay_url(request, invoice)


@router.post("/payment-link/{invoice_id}")
def make_payment_link(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """Make the invoice's public payment link, or answer the one it has
    (the link is stable: making it twice is the same link). A write, so
    the roles that may write reach it and a read-only one cannot (#659)."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not invoice.payment_token:
        invoice.payment_token = str(uuid.uuid4())
        db.commit()
    return _pay_url(request, invoice)


@router.post("/{provider_name}/payout")
def record_payout(
    provider_name: str, data: PayoutRequest, db: Session = Depends(get_db)
):
    """Record a provider payout that arrived without a bank feed (#584):
    post it from the bank account, clearing the provider's clearing account
    for the gross (net + fee) and booking any fee, the same split a feed
    payout uses. Needs clearing on, or a balance still waiting in the clearing
    account; the gross may not exceed that balance."""
    from app.models.accounts import Account
    from app.services.accounting import _q
    from app.services.bank_posting import post_bank_entry, require_bank_account
    from app.services.payments.clearing import (
        DISPLAY_NAMES,
        PROVIDER_NAMES,
        clearing_account_id,
        clearing_balance,
        fee_account_id,
        payouts_allowed,
    )

    if provider_name not in PROVIDER_NAMES:
        raise HTTPException(
            status_code=400, detail=f"Unknown payment provider: {provider_name}"
        )
    display = DISPLAY_NAMES[provider_name]
    # Allowed while clearing is on, and after it is turned off until the
    # clearing balance is paid out.
    if not payouts_allowed(db, provider_name):
        raise HTTPException(
            status_code=400,
            detail=f"{display} clearing is off and holds nothing to pay out; turn it on first",
        )
    net, fee = _q(data.net), _q(data.fee)
    if fee < 0:
        raise HTTPException(status_code=400, detail="The fee cannot be negative")
    if net <= 0:
        raise HTTPException(status_code=400, detail="The payout must be more than zero")

    gross = net + fee  # CR clearing the gross, DR fee, DR bank the net
    held = clearing_balance(db, provider_name)
    if gross > held:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The gross ({gross:,.2f}) is more than {display} Clearing holds "
                f"({held:,.2f}). Enter the net that reached the bank and only the "
                "fee not already booked at capture."
            ),
        )
    bank = require_bank_account(db, data.bank_account_id)  # 400 if not bank-kind
    clearing = db.get(Account, clearing_account_id(db, provider_name))
    splits = [(clearing, gross, f"{display} payout")]
    if fee > 0:
        fee_acct = db.get(Account, fee_account_id(db, provider_name))
        splits.append((fee_acct, -fee, f"{display} payout fee"))
    txn = post_bank_entry(
        db,
        bank,
        data.date,
        net,
        clearing,
        payee=f"{display} payout",
        splits=splits,
    )
    db.commit()
    return {"status": "payout_recorded", "transaction_id": txn.id}


# ---------------------------------------------------------------------------
# Legacy alias: operator webhook configs in the Stripe dashboard point at
# /api/stripe/webhook. Keep it working by delegating to the generic handler.
# ---------------------------------------------------------------------------

legacy_stripe_router = APIRouter(prefix="/api/stripe", tags=["payments"])


@legacy_stripe_router.post("/webhook")
async def legacy_stripe_webhook(request: Request, db: Session = Depends(get_db)):
    return await provider_webhook("stripe", request, db)
