"""The optional chime when a transaction posts (#476, from a QuickBooks
Desktop user who keys a stack and misses the toast).

- the per-user preference post_sound round-trips through the preferences
  routes as {"enabled": bool}, and the key passes the route's validation;
- the Settings page shows the per-user toggle and saves it through the
  preferences API, not the company-settings form;
- utils.js rings the chime from a success toast only.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_JS = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
UTILS_JS = (ROOT / "app/static/js/utils.js").read_text(encoding="utf-8")


def test_the_post_sound_preference_round_trips(client):
    assert client.get("/api/preferences/post_sound").json() == {
        "key": "post_sound",
        "value": None,
    }
    saved = client.put("/api/preferences/post_sound", json={"value": {"enabled": True}})
    assert saved.status_code == 200, saved.text
    assert client.get("/api/preferences/post_sound").json()["value"] == {
        "enabled": True
    }
    # turning it off keeps the row, with enabled false
    client.put("/api/preferences/post_sound", json={"value": {"enabled": False}})
    assert client.get("/api/preferences/post_sound").json()["value"] == {
        "enabled": False
    }
    # delete is back to the default (off)
    assert client.delete("/api/preferences/post_sound").status_code == 200
    assert client.get("/api/preferences/post_sound").json()["value"] is None


def test_the_settings_toggle_uses_the_preferences_api(client):
    assert 'id="post-sound-pref"' in SETTINGS_JS
    assert "SettingsPage.savePostSound(this.checked)" in SETTINGS_JS
    assert (
        "API.put('/preferences/post_sound', { value: { enabled: !!on } })"
        in SETTINGS_JS
    )
    assert "API.get('/preferences/post_sound')" in SETTINGS_JS


def test_the_chime_rings_only_from_a_success_toast():
    # the hook is inside toast(), gated on the success type
    assert "if (type === 'success') playPostSound();" in UTILS_JS
    # guarded for a browser with no AudioContext
    assert "window.AudioContext || window.webkitAudioContext" in UTILS_JS
