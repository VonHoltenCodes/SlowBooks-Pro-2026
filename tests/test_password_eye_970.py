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


def test_attaching_the_eye_gives_focus_back_and_the_ring_is_the_themes():
    # #988: the sign-in page focuses the field before the observer wraps it
    js = (ROOT / "app/static/js/password_eye.js").read_text(encoding="utf-8")
    attach = js.split("function attach(input)")[1].split("function scan(")[0]
    assert "document.activeElement === input" in attach
    assert "input.focus()" in attach and "setSelectionRange" in attach
    # #990: the theme's focus ring, never the gold alone
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".pw-eye:focus-visible { outline: 2px solid var(--focus-ring)" in css
    assert "var(--qb-gold)" not in css.split(".pw-eye:focus-visible")[1].split("}")[0]


def test_enter_on_the_launchers_eye_does_not_unlock():
    # #989
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    line = next(
        l for l in src.splitlines() if "e.key === 'Enter'" in l and "unlock()" in l
    )
    assert "!e.target.closest('.pw-eye')" in line
    assert "outline: 2px solid #8f6a1e" in src and "#cc9933; outline-offset" not in src


def test_the_eyes_click_is_its_own_and_shown_text_is_typed_as_is():
    # #996: never a click on a wrapping label's control; #999: no
    # auto-capitalising or correcting while shown
    js = (ROOT / "app/static/js/password_eye.js").read_text(encoding="utf-8")
    assert "e.preventDefault(); e.stopPropagation(); toggle(input, button);" in js
    for attr in (
        "'autocapitalize', 'off'",
        "'autocorrect', 'off'",
        "'spellcheck', 'false'",
    ):
        assert attr in js
    settings = (ROOT / "app/static/js/settings.js").read_text(encoding="utf-8")
    assert '<label class="form-field" for="ai-settings-key">' in settings


def test_the_launchers_eye_shows_its_state_and_hides_again():
    # #997
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert (
        "function setEye(show)" in src
        and "b.innerHTML = show ? EYE_OFF : EYE_ON;" in src
    )
    assert src.count("setEye(false);") == 2  # showUnlock and the failed try
    assert "#unlock .pw-eye, #unlock .pw-eye:disabled" in src
    assert (
        'id="unlock-pass" type="password" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false"'
        in src
    )
    assert "&#128065;" not in src
