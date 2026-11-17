"""The show-password eye (#970, the owner's request): on every password
field in the app and on the launcher's Unlock page."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_shell_loads_the_eye_and_styles_it():
    shell = (ROOT / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/password_eye.js"></script>' in shell
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".pw-eye {" in css and ".pw-eye:focus-visible" in css


def test_the_eye_script_covers_every_password_field_as_it_appears():
    js = (ROOT / "app/static/js/password_eye.js").read_text(encoding="utf-8")
    assert "MutationObserver" in js
    assert 'input[type="password"]' in js
    assert "aria-label', 'Show password'" in js and "'Hide password'" in js
    assert "data-no-eye" in js


def test_the_eye_is_served(client):
    r = client.get("/static/js/password_eye.js")
    assert r.status_code == 200 and "PasswordEye" in r.text


def test_the_launchers_unlock_page_has_the_eye():
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert 'id="unlock-eye"' in src and 'aria-label="Show password"' in src
    assert "function toggleEye()" in src
    assert ".pw-eye {" in src


def test_no_password_field_opts_out_by_accident():
    import re

    hits = []
    for f in list((ROOT / "app/static/js").glob("*.js")) + [ROOT / "index.html"]:
        for m in re.finditer(
            r"<input[^>]*type=\"password\"[^>]*>", f.read_text(encoding="utf-8")
        ):
            if "data-no-eye" in m.group(0):
                hits.append((f.name, m.group(0)[:80]))
    assert hits == [], hits
