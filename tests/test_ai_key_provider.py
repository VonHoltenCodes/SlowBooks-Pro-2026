"""A saved AI key is sent only to the provider it was entered for (2.22.1
gate, NEW-46).

One key was stored whatever the provider, and Settings kept it when the key
box was left blank, so picking another provider and pressing Test sent the
first provider's key to the second: OpenAI answered "Incorrect API key
provided: sk-ant-q********0000". Gemini would have had it in the URL, and a
Custom endpoint at whatever address was typed. Now the key carries the
provider it was entered for; another provider has no key until one is
entered for it, and switching back finds the saved key again.
"""

import pytest

from app.routes.analytics import _read_ai_config
from app.services import ai_service
from app.services.crypto import encrypt_value
from app.services.settings_service import set_setting

ANT_KEY = "sk-ant-api03-test-0000000000000000"
OPENAI_KEY = "sk-proj-test-1111111111111111"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing here may reach a provider: a call would mean a key went out."""

    def refuse(*a, **kw):
        raise AssertionError("a provider was called")

    monkeypatch.setattr(ai_service, "_hardened_client", refuse)


def _put(client, provider, **extra):
    r = client.put(
        "/api/analytics/ai-config",
        json={"provider": provider, "model": "", **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_another_provider_is_not_sent_the_saved_key(client, db_session):
    cfg = _put(client, "anthropic", api_key=ANT_KEY)
    assert cfg["has_api_key"] is True and cfg["api_key_provider"] == "anthropic"
    cfg = _put(client, "openai")  # the page's Test saves the new provider first
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "anthropic"
    r = client.post("/api/analytics/ai-config/test", json={})
    assert r.status_code == 400
    assert "No API key saved for OpenAI" in r.json()["detail"]
    assert ANT_KEY not in r.text
    assert _read_ai_config(db_session)["api_key"] == ""
    # nor do the analyses
    for path in (
        "/api/analytics/ai-insights",
        "/api/analytics/ai-actions/top_customers",
    ):
        assert client.post(path).status_code == 400, path
    r = client.post("/api/analytics/ai-query", params={"question": "Who owes most?"})
    assert r.status_code == 400


def test_switching_back_finds_the_saved_key(client, db_session):
    _put(client, "anthropic", api_key=ANT_KEY)
    _put(client, "gemini")
    cfg = _put(client, "anthropic")
    assert cfg["has_api_key"] is True
    assert _read_ai_config(db_session)["api_key"] == ANT_KEY


def test_a_key_entered_for_the_new_provider_is_that_providers(client, db_session):
    _put(client, "anthropic", api_key=ANT_KEY)
    cfg = _put(client, "openai", api_key=OPENAI_KEY)
    assert cfg["has_api_key"] is True and cfg["api_key_provider"] == "openai"
    assert _read_ai_config(db_session)["api_key"] == OPENAI_KEY
    # removing it removes its provider too
    cfg = _put(client, "openai", api_key="")
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == ""


def _legacy(db_session, provider, key):
    """A company file saved before 2.22.1: a key, no provider recorded."""
    set_setting(db_session, "ai_provider", provider)
    set_setting(db_session, "ai_api_key", encrypt_value(key))
    db_session.commit()


def test_a_key_saved_before_2221_stays_with_the_provider_it_was_used_with(
    client, db_session
):
    _legacy(db_session, "openai", OPENAI_KEY)
    cfg = client.get("/api/analytics/ai-config").json()
    assert cfg["has_api_key"] is True and cfg["api_key_provider"] == "openai"
    # the first save binds it before the provider changes under it
    cfg = _put(client, "groq")
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "openai"
    db_session.expire_all()
    assert _read_ai_config(db_session)["api_key"] == ""
    cfg = _put(client, "openai")
    assert cfg["has_api_key"] is True


def test_a_vendors_own_prefix_names_a_key_already_switched_under_the_old_bug(
    client, db_session
):
    """An Anthropic key left behind on OpenAI by the old behaviour is not
    sent to OpenAI: its prefix says whose it is."""
    _legacy(db_session, "openai", ANT_KEY)
    cfg = client.get("/api/analytics/ai-config").json()
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "anthropic"
    assert _read_ai_config(db_session)["api_key"] == ""
    cfg = _put(client, "anthropic")
    assert cfg["has_api_key"] is True


def test_the_settings_page_drops_a_typed_key_and_the_saved_mark_on_a_provider_change():
    src = open("app/static/js/settings.js", encoding="utf-8").read()
    change = src[src.index("providerSel.addEventListener('change'") :]
    change = change[: change.index("});")]
    assert "keyBox.value = ''" in change
    assert "SettingsPage._aiKeyOwner(cfg) === providerSel.value" in change
    assert "savedEl.classList.toggle('hidden', !keySaved)" in change
