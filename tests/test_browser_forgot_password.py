"""Forgot your password? (#719) on the real sign-in page, in a browser.

The sign-in page (app/static/js/auth.js) shows the sentence /api/auth/status
gives for the company. In the desktop window, for a file that is not
encrypted, or one just opened with its recovery key, it offers Set a new
administrator password: the password typed twice, sent through the
launcher's bridge (window.pywebview.api.reset_admin_password, stubbed here),
then a sign-in with it. A browser has no bridge and is never offered it.
The page is the real one (tests/test_browser_ui.py's fixtures)."""

import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open_raw, ORIGIN  # noqa: E402
from tests.test_browser_ui import browser  # noqa: E402,F401

HERE = (
    "This company file is on this computer and is not encrypted, so you can "
    "set a new administrator password here."
)
ASK = "Ask an administrator to reset it under Settings, Users."
OPENED = (
    "You opened this company with its recovery key, so you can set a new "
    "password for one of its administrators now."
)

ELSEWHERE = (
    "A new administrator password can be set only in the SlowBooks Pro window, "
    "on the computer that holds the company file. Open the company in the app "
    "there and choose Forgot your password?"
)

BRIDGE = """(admins) => {
    window.__resets = [];
    window.pywebview = { api: {
        reset_admins: () => Promise.resolve({ success: true, admins: admins,
                                              multi_user: admins.length > 1 }),
        reset_admin_password: (user, password) => {
            window.__resets.push([user, password]);
            return Promise.resolve({ success: true, username: user || 'admin',
                                     multi_user: !!user });
        },
    } };
}"""


def _sign_in_page(
    browser,  # noqa: F811
    forgot,
    *,
    bridge,
    multi_user=False,
    admins=("admin",),
):
    page = _open_raw(browser, 1280, 800)
    logins = []
    status = {
        "authenticated": False,
        "setup_needed": False,
        "multi_user": multi_user,
        "usernames": ["admin", "bob"] if multi_user else [],
        "company_name": "Harbor Light Bakery",
        "desktop": bridge,
        "forgot": forgot,
    }
    page.route(
        "**/api/auth/status",
        lambda r: r.fulfill(content_type="application/json", body=json.dumps(status)),
    )

    def login(route):
        logins.append(json.loads(route.request.post_data))
        route.fulfill(json={"status": "ok", "authenticated": True})

    page.route("**/api/auth/login", login)
    if bridge:
        page.add_init_script(f"({BRIDGE})({json.dumps(list(admins))})")
    page.goto(f"{ORIGIN}/")
    page.wait_for_selector("#auth-overlay #auth-forgot")
    return page, logins


def test_a_plain_desktop_file_sets_a_new_password_at_the_window(browser):  # noqa: F811
    forgot = {"kind": "reset_here", "message": HERE, "elsewhere": ELSEWHERE}
    page, logins = _sign_in_page(browser, forgot, bridge=True)
    try:
        assert page.is_hidden("#auth-forgot-panel")
        page.click("#auth-forgot")
        assert page.inner_text("#auth-forgot-text") == HERE
        assert page.get_attribute("#auth-forgot", "aria-expanded") == "true"
        # the keyboard reaches the offer: it has the focus
        assert page.evaluate("() => document.activeElement.id") == "auth-reset-here"
        page.keyboard.press("Enter")
        page.wait_for_selector("#auth-reset-password")
        assert page.inner_text("#auth-title") == "Set a new administrator password"
        # one administrator: nothing to choose
        assert page.query_selector("#auth-reset-user") is None
        page.fill("#auth-reset-password", "a-brand-new-password")
        page.fill("#auth-reset-confirm", "a-different-password")
        page.click("#auth-submit")
        assert page.inner_text("#auth-error") == "Passwords do not match."
        page.fill("#auth-reset-password", "short")
        page.fill("#auth-reset-confirm", "short")
        page.click("#auth-submit")  # the field's own minimum stops it
        assert page.evaluate("() => window.__resets") == []
        page.fill("#auth-reset-password", "a-brand-new-password")
        page.fill("#auth-reset-confirm", "a-brand-new-password")
        with page.expect_request("**/api/auth/login"):
            page.click("#auth-submit")
        assert logins and logins[0]["password"] == "a-brand-new-password"
    finally:
        page.close()


def test_back_to_sign_in_returns_to_the_password(browser):  # noqa: F811
    forgot = {"kind": "reset_here", "message": HERE, "elsewhere": ELSEWHERE}
    page, _ = _sign_in_page(browser, forgot, bridge=True)
    try:
        page.click("#auth-forgot")
        page.click("#auth-reset-here")
        page.click("#auth-reset-cancel")
        page.wait_for_selector("#auth-password")
        assert page.query_selector("#auth-reset-password") is None
    finally:
        page.close()


def test_a_browser_is_told_to_ask_an_administrator(browser):  # noqa: F811
    forgot = {"kind": "ask_admin", "message": ASK}
    page, _ = _sign_in_page(browser, forgot, bridge=False, multi_user=True)
    try:
        page.click("#auth-forgot")
        assert page.inner_text("#auth-forgot-text") == ASK
        assert page.query_selector("#auth-reset-here") is None
        assert page.query_selector("#auth-recovery-notice") is None
        # a second click folds it away
        page.click("#auth-forgot")
        assert page.is_hidden("#auth-forgot-panel")
    finally:
        page.close()


def test_opened_with_the_recovery_key_offers_it_at_once(browser):  # noqa: F811
    forgot = {"kind": "recovery_reset", "message": OPENED, "elsewhere": ELSEWHERE}
    page, logins = _sign_in_page(
        browser, forgot, bridge=True, multi_user=True, admins=("admin", "bob")
    )
    try:
        page.wait_for_selector("#auth-reset-open")
        notice = page.inner_text("#auth-recovery-notice")
        assert OPENED in notice and "Set a new administrator password" in notice
        page.click("#auth-reset-open")
        page.wait_for_selector("#auth-reset-user")
        assert page.eval_on_selector_all(
            "#auth-reset-user option", "os => os.map(o => o.value)"
        ) == ["admin", "bob"]
        page.select_option("#auth-reset-user", "bob")
        page.fill("#auth-reset-password", "a-brand-new-password")
        page.fill("#auth-reset-confirm", "a-brand-new-password")
        page.click("#auth-submit")
        page.wait_for_selector("#auth-recovery-still")
        assert page.evaluate("() => window.__resets") == [
            ["bob", "a-brand-new-password"]
        ]
        assert logins == [{"password": "a-brand-new-password", "username": "bob"}]
        still = page.inner_text("#auth-recovery-still")
        assert "still opens this company file" in still
        assert "New recovery key" in still
        assert page.evaluate("() => document.activeElement.id") == (
            "auth-reset-continue"
        )
    finally:
        page.close()


def test_a_browser_at_the_apps_address_is_not_offered_the_reset(browser):  # noqa: F811
    """W-125: the status says reset_here to anything on the desktop's own
    loopback, a browser included. Without the launcher's bridge the page
    offers no reset and no form, and says where it can be done."""
    forgot = {"kind": "reset_here", "message": HERE, "elsewhere": ELSEWHERE}
    page, logins = _sign_in_page(browser, forgot, bridge=False)
    try:
        page.click("#auth-forgot")
        # the sentence is there at once: no wait for a bridge a browser
        # never gets (it was up to five seconds, #813)
        page.wait_for_selector("#auth-forgot-text", timeout=1000)
        assert page.inner_text("#auth-forgot-text") == ELSEWHERE
        assert page.query_selector("#auth-reset-here") is None
        assert page.query_selector("#auth-reset-password") is None
        assert "set a new administrator password here" not in page.inner_text(
            "#auth-overlay"
        )
        # folded away and opened again: the answer is there at once
        page.click("#auth-forgot")
        page.click("#auth-forgot")
        assert page.inner_text("#auth-forgot-text") == ELSEWHERE
        assert logins == []
    finally:
        page.close()


def test_a_bridge_that_arrives_late_still_offers_the_reset(browser):  # noqa: F811
    """The Mac app's bridge comes after the page (#813): the browser's
    sentence shows at once, and the reset takes its place when the bridge
    arrives while the panel is open."""
    forgot = {"kind": "reset_here", "message": HERE, "elsewhere": ELSEWHERE}
    page, _ = _sign_in_page(browser, forgot, bridge=False)
    try:
        page.click("#auth-forgot")
        page.wait_for_selector("#auth-forgot-text", timeout=1000)
        assert page.inner_text("#auth-forgot-text") == ELSEWHERE
        page.evaluate(f"({BRIDGE})(['admin'])")
        page.evaluate("() => window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_selector("#auth-reset-here", timeout=2000)
        assert page.inner_text("#auth-forgot-text") == HERE
    finally:
        page.close()


def test_the_open_panel_fits_the_window_at_150_percent(browser):  # noqa: F811
    """At 150% Text size the sign-in form with Forgot your password? open
    ran past the bottom of an 800-pixel window (#813): the overlay's and
    the form's margins shrink with the size, so it fits."""
    forgot = {"kind": "reset_here", "message": HERE, "elsewhere": ELSEWHERE}
    page, _ = _sign_in_page(browser, forgot, bridge=True)
    try:
        page.evaluate("() => localStorage.setItem('slowbooks.textsize', '150')")
        page.reload()
        page.wait_for_selector("#auth-overlay #auth-forgot")
        page.click("#auth-forgot")
        page.wait_for_selector("#auth-reset-here")
        box = page.evaluate(
            """() => { const o = document.getElementById('auth-overlay');
                const f = o.querySelector('form');
                return [f.offsetTop + f.offsetHeight, o.clientHeight,
                        document.documentElement.getAttribute('data-text-size')]; }"""
        )
        assert box[2] == "150"
        assert box[0] <= box[1], box
    finally:
        page.close()


def test_a_browser_sees_no_recovery_notice(browser):  # noqa: F811
    forgot = {"kind": "recovery_reset", "message": OPENED, "elsewhere": ELSEWHERE}
    page, _ = _sign_in_page(browser, forgot, bridge=False, multi_user=True)
    try:
        page.click("#auth-forgot")
        page.wait_for_selector("#auth-forgot-text", timeout=1000)
        assert page.inner_text("#auth-forgot-text") == ELSEWHERE
        assert page.is_hidden("#auth-recovery-notice")
        assert page.query_selector("#auth-reset-open") is None
    finally:
        page.close()
