"""Payment provider implementations: registry resolution and the PayPal
provider's request builders / webhook verification / capture-on-poll.

Request builders are pure functions (ai_service build_request pattern),
so most assertions need no network mocking; the flows that do talk HTTP
monkeypatch the shared _http.send transport.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe

from app.services.payments import get_provider, enabled_providers
from app.services.payments import paypal as pp
from app.services.payments.paypal import PayPalProvider

SETTINGS = {
    "paypal_enabled": "true",
    "paypal_environment": "sandbox",
    "paypal_client_id": "test-client-id",
    "paypal_client_secret": "test-client-secret",
    "paypal_webhook_id": "WH-123",
}


def _invoice():
    return SimpleNamespace(
        id=42,
        invoice_number="INV-1042",
        balance_due=Decimal("123.45"),
        payment_token="tok-abc",
        customer=None,
        date=date(2026, 7, 1),
    )


@pytest.fixture(autouse=True)
def _clear_token_cache():
    pp._token_cache.clear()
    yield
    pp._token_cache.clear()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


# ── Registry ─────────────────────────────────────────────────────────────


def test_stripe_sdk_telemetry_is_disabled():
    assert stripe.enable_telemetry is False


def test_registry_resolves_paypal():
    provider = get_provider("paypal")
    assert isinstance(provider, PayPalProvider)
    assert provider.display_name == "PayPal"


def test_unknown_provider_raises_keyerror():
    with pytest.raises(KeyError):
        get_provider("venmo")


def test_enabled_providers_reads_settings(db_session):
    from app.services.settings_service import set_setting

    assert enabled_providers(db_session) == []
    set_setting(db_session, "paypal_enabled", "true")
    set_setting(db_session, "paypal_client_id", "cid")
    set_setting(db_session, "paypal_client_secret", "sec")
    db_session.commit()
    names = [p.name for p in enabled_providers(db_session)]
    assert names == ["paypal"]


# ── Pure request builders ────────────────────────────────────────────────


def test_token_request_shape():
    req = pp.build_token_request(SETTINGS)
    assert req["url"] == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    assert req["headers"]["Authorization"].startswith("Basic ")
    assert req["data"] == {"grant_type": "client_credentials"}


def test_live_environment_switches_base_url():
    live = dict(SETTINGS, paypal_environment="live")
    assert pp.build_token_request(live)["url"].startswith("https://api-m.paypal.com/")


def test_order_request_shape():
    req = pp.build_order_request(_invoice(), SETTINGS, "https://books.example", "TOK")
    body = req["json"]
    assert req["url"].endswith("/v2/checkout/orders")
    assert req["headers"]["Authorization"] == "Bearer TOK"
    assert body["intent"] == "CAPTURE"
    unit = body["purchase_units"][0]
    assert unit["amount"] == {"currency_code": "USD", "value": "123.45"}
    assert unit["custom_id"] == "42"
    assert unit["invoice_id"] == "INV-1042"
    ctx = body["application_context"]
    assert ctx["return_url"] == (
        "https://books.example/pay/tok-abc?status=success&provider=paypal"
    )
    assert ctx["cancel_url"].endswith("status=cancelled&provider=paypal")


def test_capture_request_is_idempotent_keyed():
    req = pp.build_capture_request("ORDER9", SETTINGS, "TOK")
    assert req["url"].endswith("/v2/checkout/orders/ORDER9/capture")
    assert req["headers"]["PayPal-Request-Id"] == "capture-ORDER9"


def test_verify_webhook_request_shape():
    payload = b'{"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}'
    headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
        "paypal-transmission-id": "tid",
        "paypal-transmission-sig": "sig",
        "paypal-transmission-time": "t",
    }
    req = pp.build_verify_webhook_request(payload, headers, SETTINGS, "TOK")
    body = req["json"]
    assert body["webhook_id"] == "WH-123"
    assert body["transmission_id"] == "tid"
    assert body["webhook_event"]["event_type"] == "PAYMENT.CAPTURE.COMPLETED"


# ── Webhook verification (transport mocked) ──────────────────────────────


def _token_response():
    return FakeResponse(200, {"access_token": "TOK", "expires_in": 3600})


def test_webhook_verified_capture_yields_paid_result(monkeypatch):
    event = {
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-1",
            "amount": {"value": "123.45", "currency_code": "USD"},
            "custom_id": "42",
            "supplementary_data": {"related_ids": {"order_id": "ORDER9"}},
        },
    }
    import json

    responses = [
        _token_response(),
        FakeResponse(200, {"verification_status": "SUCCESS"}),
    ]
    monkeypatch.setattr(pp._http, "send", lambda req, **kw: responses.pop(0))

    result = PayPalProvider().verify_webhook(json.dumps(event).encode(), {}, SETTINGS)
    assert result.status == "paid"
    assert result.external_id == "ORDER9"
    assert result.amount == Decimal("123.45")
    assert result.invoice_id == 42


def test_webhook_bad_signature_raises(monkeypatch):
    responses = [
        _token_response(),
        FakeResponse(200, {"verification_status": "FAILURE"}),
    ]
    monkeypatch.setattr(pp._http, "send", lambda req, **kw: responses.pop(0))
    with pytest.raises(ValueError):
        PayPalProvider().verify_webhook(b'{"event_type": "X"}', {}, SETTINGS)


def test_webhook_ignored_event_returns_none(monkeypatch):
    responses = [
        _token_response(),
        FakeResponse(200, {"verification_status": "SUCCESS"}),
    ]
    monkeypatch.setattr(pp._http, "send", lambda req, **kw: responses.pop(0))
    result = PayPalProvider().verify_webhook(
        b'{"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {}}', {}, SETTINGS
    )
    assert result is None


def test_webhook_without_webhook_id_raises():
    settings = dict(SETTINGS, paypal_webhook_id="")
    with pytest.raises(ValueError):
        PayPalProvider().verify_webhook(b"{}", {}, settings)


# ── Polling: capture-on-APPROVED ─────────────────────────────────────────


def test_poll_captures_approved_order(monkeypatch):
    """An APPROVED order gets captured during the poll — the return-
    redirect / desktop recording path."""
    calls = []

    def fake_send(req, **kw):
        calls.append(req["url"])
        if req["url"].endswith("/v1/oauth2/token"):
            return _token_response()
        if req["url"].endswith("/capture"):
            return FakeResponse(
                201,
                {
                    "id": "ORDER9",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {
                            "custom_id": "42",
                            "payments": {
                                "captures": [
                                    {
                                        "status": "COMPLETED",
                                        "amount": {"value": "123.45"},
                                    }
                                ]
                            },
                        }
                    ],
                },
            )
        return FakeResponse(200, {"id": "ORDER9", "status": "APPROVED"})

    monkeypatch.setattr(pp._http, "send", fake_send)
    result = PayPalProvider().poll_status("ORDER9", SETTINGS)
    assert result.status == "paid"
    assert result.amount == Decimal("123.45")
    assert result.invoice_id == 42
    assert any(url.endswith("/capture") for url in calls)


def test_poll_pending_order_not_captured(monkeypatch):
    def fake_send(req, **kw):
        if req["url"].endswith("/v1/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"id": "ORDER9", "status": "CREATED"})

    monkeypatch.setattr(pp._http, "send", fake_send)
    result = PayPalProvider().poll_status("ORDER9", SETTINGS)
    assert result.status == "pending"


def test_token_cache_reused(monkeypatch):
    token_calls = []

    def fake_send(req, **kw):
        if req["url"].endswith("/v1/oauth2/token"):
            token_calls.append(1)
            return _token_response()
        return FakeResponse(200, {"id": "O", "status": "CREATED"})

    monkeypatch.setattr(pp._http, "send", fake_send)
    provider = PayPalProvider()
    provider.poll_status("O", SETTINGS)
    provider.poll_status("O", SETTINGS)
    assert len(token_calls) == 1
