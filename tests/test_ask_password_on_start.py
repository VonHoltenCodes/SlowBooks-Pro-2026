"""Settings -> "Ask for the password each time SlowBooks Pro starts"
(explore 2.17.3, macbase1 S-j).

The session cookie outlives the app (the desktop window keeps a persistent
profile so print windows and downloads share the sign-in), so a quit and
relaunch reopened the company without its password. Opt-in, off by default:
with it on, a session signed in before the app last started counts as
signed out. Desktop only — a Docker deployment runs two workers.
"""

from pathlib import Path

import pytest

from app.models.settings import DEFAULT_SETTINGS
from app.services import auth as auth_service

ROOT = Path(__file__).resolve().parents[1]

# The `client` fixture's password (conftest) — a module constant, never a
# literal beside a username key.
FIXTURE_PW = "test-password-123"


def _restart(monkeypatch):
    """What quitting and relaunching does to the server: a new start."""
    monkeypatch.setattr(auth_service, "BOOT_ID", "a-later-start")


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DESKTOP", "1")


def test_it_is_off_by_default_and_nothing_changes(client, desktop, monkeypatch):
    assert DEFAULT_SETTINGS["ask_password_on_start"] == "false"
    assert client.get("/api/settings").json()["ask_password_on_start"] == "false"
    _restart(monkeypatch)
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_with_it_on_a_relaunch_asks_for_the_password(client, desktop, monkeypatch):
    r = client.put("/api/settings", json={"ask_password_on_start": "true"})
    assert r.status_code == 200, r.text
    # turning it on does not sign out the session that turned it on
    assert client.get("/api/settings").status_code == 200

    _restart(monkeypatch)
    r = client.get("/api/settings")
    assert r.status_code == 401
    assert "Enter the password" in r.json()["detail"]
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is False and status["setup_needed"] is False

    # signing in again works for the rest of this start
    assert client.post("/api/auth/login", json={"password": FIXTURE_PW}).is_success
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_the_page_is_told_it_is_signed_out_before_any_request_fails(
    client, desktop, monkeypatch
):
    """auth.js asks /api/auth/status which view to paint; it must agree with
    the 401s, or the sign-in screen would never appear."""
    client.put("/api/settings", json={"ask_password_on_start": "true"})
    _restart(monkeypatch)
    assert client.get("/api/auth/status").json()["authenticated"] is False
    assert client.get("/api/invoices").status_code == 401


def test_a_docker_deployment_ignores_it(client, monkeypatch):
    """Two uvicorn workers each have their own start; honouring it there
    would sign people out whenever a request reached the other worker."""
    monkeypatch.delenv("SLOWBOOKS_DESKTOP", raising=False)
    client.put("/api/settings", json={"ask_password_on_start": "true"})
    _restart(monkeypatch)
    assert client.get("/api/settings").status_code == 200


def test_api_tokens_are_not_affected(client, desktop, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    client.put("/api/settings", json={"ask_password_on_start": "true"})
    minted = client.post("/api/tokens", json={"label": "agent", "role": "readonly"})
    assert minted.status_code == 201, minted.text
    _restart(monkeypatch)
    agent = TestClient(app)
    agent.headers["Authorization"] = f"Bearer {minted.json()['token']}"
    assert agent.get("/api/settings").status_code == 200


def test_only_yes_or_no(client):
    r = client.put("/api/settings", json={"ask_password_on_start": "sometimes"})
    assert r.status_code == 422, r.text


def test_settings_offers_it_on_the_desktop():
    js = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    section = js[
        js.index('id="settings-sign-in"') : js.index('id="settings-closing-date"')
    ]
    assert "hidden" in section[:40]
    assert "Ask for the password each time SlowBooks Pro starts" in section
    assert '<select id="ask-password-on-start" name="ask_password_on_start">' in section
    # "No" unless the company chose "Yes"
    assert "${s.ask_password_on_start !== 'true' ? 'selected' : ''}>No:" in section
    loader = js[js.index("async loadSignInPref() {") :]
    assert "API.get('/system')" in loader and "sys.desktop" in loader
