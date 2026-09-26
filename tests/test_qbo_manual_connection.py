"""Manual completion of QBO authorization using a pasted code and realm."""

from intuitlib.exceptions import AuthClientError

from app.main import _role_allows
from app.services import qbo_service


class FakeAuthClient:
    calls = []
    error = None

    def __init__(self, db):
        self.access_token = None
        self.refresh_token = None
        self.expires_in = 3600

    def get_authorization_url(self, scopes, state_token):
        return f"https://intuit.example/authorize?state={state_token}"

    def get_bearer_token(self, code, realm_id):
        self.calls.append((code, realm_id))
        if self.error:
            raise RuntimeError(self.error)
        self.access_token = "test-access-token"
        self.refresh_token = "test-refresh-token"


def test_manual_connection_requires_both_values(client, monkeypatch):
    monkeypatch.setattr(qbo_service, "_make_auth_client", FakeAuthClient)
    FakeAuthClient.calls = []
    response = client.post(
        "/api/qbo/connect-manual",
        json={"authorization_code": " ", "realm_id": "123"},
    )
    assert response.status_code == 400
    assert FakeAuthClient.calls == []


def test_manual_connection_exchanges_code_and_clears_state(client, db_session, monkeypatch):
    monkeypatch.setattr(qbo_service, "_make_auth_client", FakeAuthClient)
    FakeAuthClient.calls = []
    FakeAuthClient.error = None

    # A code obtained through Intuit's OAuth Playground can be redeemed
    # without starting an additional SlowBooks browser redirect.
    response = client.post(
        "/api/qbo/connect-manual",
        json={"authorization_code": "  one-time-code  ", "realm_id": " 123 "},
    )
    assert response.status_code == 200
    assert response.json() == {"connected": True}
    assert FakeAuthClient.calls == [("one-time-code", "123")]
    assert qbo_service.is_connected(db_session)
    assert qbo_service._get_setting(db_session, "qbo_realm_id") == "123"
    assert qbo_service._get_setting(db_session, "qbo_refresh_token") == "test-refresh-token"
    assert qbo_service._get_setting(db_session, "qbo_oauth_state") == ""


def test_automatic_callback_still_checks_state(client, db_session, monkeypatch):
    monkeypatch.setattr(qbo_service, "_make_auth_client", FakeAuthClient)
    FakeAuthClient.calls = []
    assert client.get("/api/qbo/auth-url").status_code == 200
    response = client.get(
        "/api/qbo/callback",
        params={"code": "code", "realmId": "123", "state": "incorrect"},
    )
    assert response.status_code == 400
    assert FakeAuthClient.calls == []
    assert not qbo_service.is_connected(db_session)


def test_manual_connection_hides_exchange_error(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr(qbo_service, "_make_auth_client", FakeAuthClient)
    FakeAuthClient.error = "provider-secret-do-not-show"
    response = client.post(
        "/api/qbo/connect-manual",
        json={"authorization_code": "private-code", "realm_id": "123"},
    )
    assert response.status_code == 400
    assert "private-code" not in response.text + caplog.text
    assert "provider-secret-do-not-show" not in response.text + caplog.text
    assert not qbo_service.is_connected(db_session)
    FakeAuthClient.error = None


def test_manual_connection_explains_provider_rejection_without_echo(client, monkeypatch, caplog):
    class ProviderResponse:
        status_code = 400
        content = b"invalid_grant: private-code provider-secret-do-not-show"
        headers = {}

    def reject(db, code, realm_id):
        raise AuthClientError(ProviderResponse())

    monkeypatch.setattr(qbo_service, "exchange_authorization_code", reject)
    response = client.post(
        "/api/qbo/connect-manual",
        json={"authorization_code": "private-code", "realm_id": "123"},
    )
    assert response.status_code == 400
    assert "Redirect URI" in response.text
    assert "private-code" not in response.text + caplog.text
    assert "provider-secret-do-not-show" not in response.text + caplog.text


def test_bookkeeper_cannot_post_manual_connection():
    assert not _role_allows("bookkeeper", "POST", "/api/qbo/connect-manual")
