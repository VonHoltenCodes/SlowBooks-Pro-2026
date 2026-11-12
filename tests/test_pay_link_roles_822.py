"""The invoice pay link is a bookkeeper's to read (#822, the 2.31.2 gate's
W-88): `GET /api/payments/payment-link/{invoice_id}` answers an
administrator or a bookkeeper, signed in or by token; a read-only or
accountant sign-in or token gets a 403 with one sentence. Copying the link
is a step in sending the invoice, and those roles never write or send. The
customer's page at /pay/<token> stays public. An API token is never an
administrator (since v2.29.0), so the administrator is checked signed in."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import PAY_LINK_REFUSAL, _role_allows, _role_refusal, app
from app.models.contacts import Customer
from app.models.invoices import Invoice, InvoiceStatus
from app.models.users import User
from app.services import auth as auth_service

PW = "pay-link-password-822"
SENTENCE = "Copying a payment link is a bookkeeper's; ask one to send it."


@pytest.fixture
def invoice(db_session, seed_accounts):
    customer = Customer(name="Pay Link Customer", is_active=True)
    db_session.add(customer)
    db_session.flush()
    inv = Invoice(
        invoice_number="PL-822",
        customer_id=customer.id,
        date=date(2026, 11, 1),
        status=InvoiceStatus.SENT,
        subtotal=Decimal("250.00"),
        tax_rate=Decimal("0"),
        tax_amount=Decimal("0"),
        total=Decimal("250.00"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("250.00"),
        payment_token="pay-link-token-822",
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def _signed_in(db_session, role):
    username = f"{role}-822"
    db_session.add(
        User(
            username=username,
            display_name=username,
            password_hash=auth_service.hash_password(PW),
            role=role,
            is_active=True,
        )
    )
    db_session.commit()
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _token(client, role):
    r = client.post("/api/tokens", json={"label": f"{role}-822", "role": role})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


def _link(who, invoice):
    return who.get(f"/api/payments/payment-link/{invoice.id}")


def test_the_sentence_is_the_issues():
    assert PAY_LINK_REFUSAL == SENTENCE


def test_an_administrator_signed_in_reads_the_link(client, invoice):
    r = _link(client, invoice)
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(f"/pay/{invoice.payment_token}")


@pytest.mark.parametrize("how", ["session", "token"])
def test_a_bookkeeper_reads_the_link(client, db_session, invoice, how):
    if how == "session":
        who = _signed_in(db_session, "bookkeeper")
    else:
        who = _token(client, "bookkeeper")
    r = _link(who, invoice)
    assert r.status_code == 200, r.text
    assert r.json()["url"].endswith(f"/pay/{invoice.payment_token}")


@pytest.mark.parametrize("role", ["readonly", "accountant"])
@pytest.mark.parametrize("how", ["session", "token"])
def test_a_reader_or_an_accountant_is_refused_with_the_sentence(
    client, db_session, invoice, role, how
):
    who = _signed_in(db_session, role) if how == "session" else _token(client, role)
    r = _link(who, invoice)
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": SENTENCE}
    assert invoice.payment_token not in r.text
    # with no link yet the answer is the same, and nothing is made
    invoice.payment_token = None
    db_session.commit()
    r = _link(who, invoice)
    assert r.status_code == 403 and r.json()["detail"] == SENTENCE
    db_session.expire_all()
    assert db_session.get(Invoice, invoice.id).payment_token is None


def test_an_administrators_token_is_not_made(client):
    """The fourth role has no token to check: one is never made."""
    r = client.post("/api/tokens", json={"label": "admin-822", "role": "admin"})
    assert r.status_code in (400, 403, 422), r.text


def test_the_customers_page_stays_public(unauthed_client, invoice):
    r = unauthed_client.get(f"/pay/{invoice.payment_token}")
    assert r.status_code == 200
    assert invoice.invoice_number in r.text


def test_the_role_table_holds_the_refusal():
    path = "/api/payments/payment-link/7"
    assert _role_allows("admin", "GET", path)
    assert _role_allows("bookkeeper", "GET", path)
    assert not _role_allows("readonly", "GET", path)
    assert not _role_allows("accountant", "GET", path)
    assert _role_refusal("readonly", "GET", path) == SENTENCE
    assert _role_refusal("accountant", "GET", path) == SENTENCE
    # the invoice itself stays a read for both
    assert _role_allows("readonly", "GET", "/api/invoices/7")
    assert _role_allows("accountant", "GET", "/api/invoices/7")
    # making the link keeps the write refusals (#659)
    assert not _role_allows("readonly", "POST", path)
    assert not _role_allows("accountant", "POST", path)
    assert _role_refusal("accountant", "POST", path).startswith(
        "An accountant's sign-in posts journal"
    )


def test_the_window_does_not_offer_copy_payment_link_to_those_roles():
    from pathlib import Path

    js = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
    app_js = (js / "app.js").read_text(encoding="utf-8")
    writes = app_js[app_js.index("WRITE_ACTIONS: new Set([") :][:600]
    assert "'InvoicesPage.copyPaymentLink'" in writes
    assert "if (!root || !App.isLocked()) return;" in app_js
    assert "isLocked() { return App.isReadOnly() || App.isAccountant(); }" in app_js
    accountant = app_js[app_js.index("ACCOUNTANT_ACTIONS: new Set([") :][:1500]
    accountant = accountant[: accountant.index("])")]
    assert "copyPaymentLink" not in accountant
    inv = (js / "invoices.js").read_text(encoding="utf-8")
    assert 'onclick="InvoicesPage.copyPaymentLink(${inv.id})">Copy Payment Link' in inv
