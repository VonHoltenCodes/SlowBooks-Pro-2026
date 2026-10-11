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


# ── keys saved before 2.22.1 (gate round 2: W-1 / NEW-51, NEW-52) ──────────


def _save(db_session, **values):
    """One save as a pre-2.22.1 build made it: settings written through the
    ORM (so the audit log records them), no provider recorded with the key."""
    for key, value in values.items():
        if key == "ai_api_key":
            value = encrypt_value(value) if value else ""
        set_setting(db_session, key, value)
    db_session.commit()


def _history(db_session, *saves):
    """Several saves, as a person makes them: seconds apart. One save's audit
    rows share its timestamp; the next save's come later."""
    from datetime import datetime, timedelta

    from app.models.audit import AuditLog

    base = datetime(2026, 9, 1, 12, 0, 0)
    for n, values in enumerate(saves):
        before = {r.id for r in db_session.query(AuditLog.id)}
        _save(db_session, **values)
        for row in db_session.query(AuditLog).filter(AuditLog.id.notin_(before)):
            row.timestamp = base + timedelta(seconds=10 * n)
        db_session.commit()


def _forget_history(db_session):
    """A company file whose audit log holds none of it (an old one)."""
    from app.models.audit import AuditLog

    db_session.query(AuditLog).filter(AuditLog.table_name == "settings").delete()
    db_session.commit()


def _cfg(client):
    return client.get("/api/analytics/ai-config").json()


def test_an_old_key_never_switched_keeps_working(client, db_session):
    """The common case: an OpenAI key saved under OpenAI on 2.22.0 needs
    nothing done to it (its sk- prefix is shared by other vendors' keys)."""
    _history(db_session, {"ai_provider": "openai", "ai_api_key": OPENAI_KEY})
    cfg = _cfg(client)
    assert cfg["has_api_key"] is True and cfg["api_key_provider"] == "openai"
    assert _read_ai_config(db_session)["api_key"] == OPENAI_KEY


def test_an_old_key_moved_by_the_old_bug_goes_to_no_one_else(client, db_session):
    """W-1's repro: an OpenAI key saved under OpenAI, then Gemini picked with
    no key on 2.22.0. Gemini takes its key in the URL; Google must not get
    the OpenAI key."""
    _history(
        db_session,
        {"ai_provider": "openai", "ai_api_key": OPENAI_KEY},
        {"ai_provider": "gemini"},
    )
    cfg = _cfg(client)
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "openai"
    assert _read_ai_config(db_session)["api_key"] == ""
    r = client.post("/api/analytics/ai-config/test", json={})
    assert r.status_code == 400 and "No API key saved for Google Gemini" in r.text
    # the first save binds it to OpenAI before anything else can move it
    cfg = _put(client, "groq")
    assert cfg["api_key_provider"] == "openai"
    assert _put(client, "openai")["has_api_key"] is True


def test_an_old_custom_key_left_under_openai_is_not_sent_to_openai(client, db_session):
    """NEW-51's L3: a Custom endpoint's key (an sk- key, as many vendors'
    are) left under OpenAI by the old behaviour."""
    _history(
        db_session,
        {
            "ai_provider": "custom",
            "ai_endpoint_url": "https://api.example.com/v1",
            "ai_api_key": "sk-qa-custom-dummy-0000",
        },
        {"ai_provider": "openai"},
    )
    cfg = _cfg(client)
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "custom"
    assert _read_ai_config(db_session)["api_key"] == ""


def test_without_history_a_vendors_prefix_says_whose_a_key_is(client, db_session):
    _history(db_session, {"ai_provider": "openai", "ai_api_key": ANT_KEY})
    _forget_history(db_session)
    cfg = _cfg(client)
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == "anthropic"
    assert _put(client, "anthropic")["has_api_key"] is True


def test_without_history_an_unrecognised_key_is_nobodys_until_entered_again(
    client, db_session
):
    _history(db_session, {"ai_provider": "gemini", "ai_api_key": OPENAI_KEY})
    _forget_history(db_session)
    cfg = _cfg(client)
    assert cfg["has_api_key"] is False and cfg["api_key_provider"] == ""
    assert cfg["api_key_unmatched"] is True
    assert _read_ai_config(db_session)["api_key"] == ""
    # saving another provider doesn't hand it the key either
    cfg = _put(client, "custom", endpoint_url="https://api.example.com/v1")
    assert cfg["has_api_key"] is False and cfg["api_key_unmatched"] is True
    db_session.expire_all()
    assert _read_ai_config(db_session)["api_key"] == ""
    # a key entered for a provider is that provider's
    cfg = _put(client, "openai", api_key=OPENAI_KEY)
    assert cfg["has_api_key"] is True and cfg["api_key_unmatched"] is False


def test_a_custom_endpoint_on_a_vendors_own_host_keeps_that_vendors_key(
    client, db_session
):
    """NEW-52: Custom pointed at Anthropic's OpenAI-compatible endpoint with
    an Anthropic key worked on 2.22.0; the key is going home."""
    _history(
        db_session,
        {
            "ai_provider": "custom",
            "ai_endpoint_url": "https://api.anthropic.com/v1",
            "ai_api_key": ANT_KEY,
        },
    )
    assert _cfg(client)["has_api_key"] is True  # from the audit log
    _forget_history(db_session)
    cfg = _cfg(client)  # and from its host, with no history
    assert cfg["has_api_key"] is True and cfg["api_key_provider"] == "custom"


def test_one_saves_provider_is_applied_before_its_key(db_session):
    """A save flushes once, so its audit rows share a timestamp in no set
    order: a key row ahead of its own provider row is still that
    provider's key."""
    from datetime import datetime

    from app.models.audit import AuditLog
    from app.models.settings import Settings
    from app.routes.analytics import _owner_from_audit

    _history(db_session, {"ai_provider": "anthropic", "ai_api_key": ANT_KEY})
    _forget_history(db_session)
    ids = dict(db_session.query(Settings.key, Settings.id))
    when = datetime(2026, 9, 2, 9, 0, 0)
    for record, values in (
        (ids["ai_api_key"], {"value": "fernet:v1:x"}),
        (ids["ai_provider"], {"value": "grok"}),
    ):
        db_session.add(
            AuditLog(
                table_name="settings",
                record_id=record,
                action="UPDATE",
                new_values=values,
                timestamp=when,
            )
        )
    db_session.commit()
    assert _owner_from_audit(db_session) == "grok"


def test_every_ai_call_names_the_provider_that_has_no_key(client, db_session):
    """W-2: AI Insights and the analyses called a provider with no key of its
    own "not configured"."""
    _put(client, "anthropic", api_key=ANT_KEY)
    _put(client, "openai")
    for r in (
        client.post("/api/analytics/ai-insights"),
        client.post("/api/analytics/ai-actions/top_customers"),
        client.post("/api/analytics/ai-query", params={"question": "Who owes most?"}),
        client.post("/api/analytics/ai-config/test", json={}),
    ):
        assert r.status_code == 400
        assert "No API key saved for OpenAI" in r.json()["detail"], r.text
    analytics = open("app/static/js/analytics.js", encoding="utf-8").read()
    assert analytics.count("msg.match(/No API key saved[^]*$/i)") == 2


def test_the_settings_page_drops_a_typed_key_and_the_saved_mark_on_a_provider_change():
    src = open("app/static/js/settings.js", encoding="utf-8").read()
    change = src[src.index("providerSel.addEventListener('change'") :]
    change = change[: change.index("});")]
    assert "document.getElementById('ai-settings-key').value = ''" in change
    assert "syncKeyMark();" in change
    mark = src[src.index("const syncKeyMark = () => {") :]
    mark = mark[: mark.index("};")]
    assert "SettingsPage._aiKeyOwner(cfg) === providerSel.value" in mark
    assert "savedEl.classList.toggle('hidden', !keySaved)" in mark
    assert "unmatched.classList.toggle('hidden', !cfg.api_key_unmatched)" in mark


def test_test_refreshes_the_saved_mark_from_what_it_saved():
    """NEW-53: after Test saved OpenAI's key, picking Anthropic still showed
    "(saved ✓)" for the key Test had replaced."""
    src = open("app/static/js/settings.js", encoding="utf-8").read()
    test = src[src.index("testBtn.addEventListener('click'") :]
    test = test[: test.index("testRes.textContent = 'Testing…'")]
    assert "Object.assign(cfg, updated);" in test and "syncKeyMark();" in test


# ── gate round 3: W-3 / NEW-55, NEW-54 ─────────────────────────────────────


@pytest.mark.parametrize("new_key", [OPENAI_KEY, ""], ids=["a new key", "Remove"])
def test_the_first_key_change_over_an_old_key_saves(client, db_session, new_key):
    """W-3 / NEW-55: on a company whose key predates 2.22.1, the first save
    that entered a key, or Remove, was a 500: the old key's provider and the
    new one were each written as a new row in one save. The save goes
    through, and the key is the new provider's or gone."""
    _history(
        db_session,
        {
            "ai_provider": "custom",
            "ai_endpoint_url": "https://api.example.com/v1",
            "ai_api_key": "sk-qa-custom-dummy-0000",
        },
        {"ai_provider": "openai"},
    )
    cfg = _put(client, "openai", api_key=new_key)
    assert cfg["has_api_key"] is bool(new_key)
    assert cfg["api_key_provider"] == ("openai" if new_key else "")
    db_session.expire_all()
    assert _read_ai_config(db_session)["api_key"] == new_key


def test_a_setting_written_twice_before_a_flush_is_one_row(db_session):
    from app.models.settings import Settings

    set_setting(db_session, "ai_api_key_provider", "custom")
    set_setting(db_session, "ai_api_key_provider", "openai")
    db_session.commit()
    rows = (
        db_session.query(Settings).filter(Settings.key == "ai_api_key_provider").all()
    )
    assert [r.value for r in rows] == ["openai"]


def _second(db_session, *writes):
    """Audit rows in one second, in id order: ("p", provider) or ("k", key)."""
    from datetime import datetime

    from app.models.audit import AuditLog
    from app.models.settings import Settings

    _history(db_session, {"ai_provider": "anthropic", "ai_api_key": ANT_KEY})
    _forget_history(db_session)
    ids = dict(db_session.query(Settings.key, Settings.id))
    when = datetime(2026, 9, 2, 9, 0, 0)
    for kind, value in writes:
        record = ids["ai_provider"] if kind == "p" else ids["ai_api_key"]
        db_session.add(
            AuditLog(
                table_name="settings",
                record_id=record,
                action="UPDATE",
                new_values={"value": value},
                timestamp=when,
            )
        )
        db_session.flush()  # ids in the order given
    db_session.commit()


@pytest.mark.parametrize(
    "writes",
    [
        [("p", "custom"), ("k", "fernet:v1:x"), ("p", "openai")],
        [("k", "fernet:v1:x"), ("p", "custom"), ("p", "openai")],
    ],
    ids=["provider first", "key first"],
)
def test_two_saves_in_one_second_whose_order_cant_be_told_are_nobodys(
    db_session, writes
):
    """NEW-54: a script saved a Custom key and switched to OpenAI in the same
    second; the key was read as OpenAI's. A save's own rows come in no set
    order, so whether the provider written straight after the key was its
    own save's or the next one's can't be told: the key is nobody's."""
    from app.routes.analytics import _KEY_OWNER_UNKNOWN, _owner_from_audit

    _second(db_session, *writes)
    assert _owner_from_audit(db_session) == _KEY_OWNER_UNKNOWN


def test_one_save_with_its_key_logged_first_is_still_its_providers(db_session):
    """A key inserted for the first time is logged ahead of its own provider:
    the commonest save, a provider picked and its key entered, reads right."""
    from app.routes.analytics import _owner_from_audit

    _second(db_session, ("k", "fernet:v1:x"), ("p", "openai"))
    assert _owner_from_audit(db_session) == "openai"


def test_several_saves_in_one_second_that_can_be_told_apart_are_read(db_session):
    """Several saves in one second where the order doesn't matter: the
    provider after the key is the one already in effect, or there is none."""
    from app.routes.analytics import _owner_from_audit

    _second(db_session, ("p", "groq"), ("k", "fernet:v1:x"), ("p", "groq"))
    assert _owner_from_audit(db_session) == "groq"


def test_a_key_with_no_provider_after_it_takes_the_one_before(db_session):
    from app.routes.analytics import _owner_from_audit

    _second(db_session, ("p", "gemini"), ("p", "grok"), ("k", "fernet:v1:y"))
    assert _owner_from_audit(db_session) == "grok"
