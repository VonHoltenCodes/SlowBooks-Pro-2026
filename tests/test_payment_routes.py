"""Provider payment routes: auth exemptions, provider resolution, and the
public checkout path that was 401ing before the abstraction refactor."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Route Test Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="ROUTE-1001",
        customer_id=customer.id,
        date=date(2026, 7, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("100.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("100.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("100.00"),
        payment_token="test-token-route-1001",
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def test_public_checkout_not_blocked_by_session_auth(unauthed_client, invoice):
    """The public pay page calls create-checkout-session without a session.

    Before the provider refactor this path 401'd (it was never on the
    auth-exempt list), making the public Pay button unusable. It must get
    past the session middleware; with Stripe unconfigured in tests it then
    400s with a domain error — the point is it is NOT a 401.
    """
    resp = unauthed_client.post(
        "/api/payments/stripe/create-checkout-session",
        json={"payment_token": invoice.payment_token},
    )
    assert resp.status_code != 401
    assert resp.status_code == 400
    assert "not enabled" in resp.json()["detail"].lower()


def test_unknown_provider_is_400_not_500(unauthed_client, invoice):
    resp = unauthed_client.post(
        "/api/payments/nonexistent/create-checkout-session",
        json={"payment_token": invoice.payment_token},
    )
    assert resp.status_code == 400
    assert "unknown payment provider" in resp.json()["detail"].lower()


def test_webhook_is_auth_exempt(unauthed_client):
    """Webhook auth = provider signature; the session middleware must not
    intercept it. Unverifiable payload → 400, never 401."""
    resp = unauthed_client.post("/api/payments/stripe/webhook", content=b"{}")
    assert resp.status_code == 400


def test_legacy_stripe_webhook_alias_still_routed(unauthed_client):
    """Operator webhook configs point at /api/stripe/webhook — the alias
    must keep working (same handler, same 400-on-bad-signature)."""
    resp = unauthed_client.post("/api/stripe/webhook", content=b"{}")
    assert resp.status_code == 400


def test_check_status_requires_session(unauthed_client, invoice):
    """The polling endpoint is operator-facing and must stay session-gated."""
    resp = unauthed_client.post(f"/api/payments/stripe/check-status/{invoice.id}")
    assert resp.status_code == 401


def test_payment_link_requires_session(unauthed_client, invoice):
    resp = unauthed_client.get(f"/api/payments/payment-link/{invoice.id}")
    assert resp.status_code == 401


def test_payment_link_returns_pay_url(client, invoice):
    resp = client.get(f"/api/payments/payment-link/{invoice.id}")
    assert resp.status_code == 200
    assert f"/pay/{invoice.payment_token}" in resp.json()["url"]


def test_public_pay_page_renders_without_auth(unauthed_client, invoice):
    resp = unauthed_client.get(f"/pay/{invoice.payment_token}")
    assert resp.status_code == 200
    assert invoice.invoice_number in resp.text


def test_success_banner_not_shown_on_unverified_return(unauthed_client, invoice):
    """?status=success alone must NOT render 'Payment received' — the
    payment gets verified against the provider first (none configured
    here, so the page shows the pending message instead)."""
    resp = unauthed_client.get(f"/pay/{invoice.payment_token}?status=success")
    assert resp.status_code == 200
    assert "Payment received" not in resp.text
    assert "being confirmed" in resp.text


def test_a_read_never_makes_a_payment_link(client, db_session, invoice):
    """#659 (and the 2.18.0 gate before it): GET /payment-link answers the
    link that exists and never makes one; POST makes it, so a read-only
    sign-in cannot, and a bookkeeper's token can. Since #822 the read is a
    bookkeeper's too (tests/test_pay_link_roles_822.py)."""
    from fastapi.testclient import TestClient

    from app.main import PAY_LINK_REFUSAL, app

    def bearer(role, label):
        r = client.post("/api/tokens", json={"label": label, "role": role})
        assert r.status_code == 201, r.text
        c = TestClient(app)
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        return c

    reader, keeper = bearer("readonly", "reader"), bearer("bookkeeper", "keeper")
    had = invoice.payment_token
    ok = keeper.get(f"/api/payments/payment-link/{invoice.id}")
    assert ok.status_code == 200 and f"/pay/{had}" in ok.json()["url"]
    refused = reader.get(f"/api/payments/payment-link/{invoice.id}")
    assert refused.status_code == 403
    assert refused.json()["detail"] == PAY_LINK_REFUSAL
    invoice.payment_token = None
    db_session.commit()
    for who in (keeper, client):
        none = who.get(f"/api/payments/payment-link/{invoice.id}")
        assert none.status_code == 200, none.text
        assert none.json()["url"] is None
        assert "no payment link yet" in none.json()["note"]
        assert "Copy Payment Link" in none.json()["note"]
    # with no link a reader is refused the same, and nothing is made
    assert reader.get(f"/api/payments/payment-link/{invoice.id}").status_code == 403
    db_session.refresh(invoice)
    assert invoice.payment_token is None
    assert reader.post(f"/api/payments/payment-link/{invoice.id}").status_code == 403
    db_session.refresh(invoice)
    assert invoice.payment_token is None
    made = keeper.post(f"/api/payments/payment-link/{invoice.id}")
    assert made.status_code == 200
    db_session.refresh(invoice)
    assert invoice.payment_token
    assert f"/pay/{invoice.payment_token}" in made.json()["url"]
    # stable: making it again is the same link, and the read agrees
    again = client.post(f"/api/payments/payment-link/{invoice.id}").json()["url"]
    assert again == made.json()["url"]
    assert client.get(f"/api/payments/payment-link/{invoice.id}").json()["url"] == again
    assert client.post("/api/payments/payment-link/999999").status_code == 404


def test_an_accountant_cannot_make_a_payment_link(client, db_session, invoice):
    """#659: making the link is a POST now, and an accountant's sign-in posts
    journal entries only, so it is refused (the page already hid Copy
    Payment Link from it; over the API the old GET would have made one)."""
    from fastapi.testclient import TestClient

    from app.main import app

    r = client.post("/api/tokens", json={"label": "cpa", "role": "accountant"})
    assert r.status_code == 201, r.text
    cpa = TestClient(app)
    cpa.headers["Authorization"] = f"Bearer {r.json()['token']}"
    invoice.payment_token = None
    db_session.commit()

    made = cpa.post(f"/api/payments/payment-link/{invoice.id}")

    assert made.status_code == 403, made.text
    assert made.json()["detail"].startswith("An accountant's sign-in posts journal")
    db_session.expire_all()
    assert db_session.get(type(invoice), invoice.id).payment_token is None
