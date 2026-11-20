# ruff: noqa: F811  (the relay fixture is imported from test_switchboard_connect_1087)
"""Round 23 on the screens, in Chromium and (where installed) WebKit.

W-208: Connect with the relay out of reach says one plain sentence that
stays through the panel's refresh until Dismiss or the next press, with
Connect offered again. NEW-269: a relay name a Disconnect could not delete
comes from the server, so the panel shows it after a reload.

W-209: text on a page selects (a cell's figure, a name, a sentence) while
the chrome (the sidebar, buttons, column headings) does not, and a field's
Ctrl/Cmd+A selects its own text.
"""

import json
from urllib.parse import urlsplit

import pytest

from app.services import switchboard_link as link
from tests.test_switchboard_connect_1087 import (  # noqa: F401  (fixture)
    _connected,
    _free_port,
    relay,
)

sync_api = pytest.importorskip("playwright.sync_api")

_BROWSERS = {}


@pytest.fixture(scope="module", autouse=True)
def _browsers():
    with sync_api.sync_playwright() as p:
        try:
            _BROWSERS["chromium"] = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        try:
            _BROWSERS["webkit"] = p.webkit.launch()
        except Exception:
            pass  # WebKit is optional on this machine
        try:
            yield
        finally:
            for b in _BROWSERS.values():
                b.close()
            _BROWSERS.clear()


@pytest.fixture(params=["chromium", "webkit"])
def engine(request):
    if request.param not in _BROWSERS:
        pytest.skip(f"Playwright {request.param} is unavailable")
    return request.param


def _page(client, engine, start="#/"):
    page = _BROWSERS[engine].new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        url = urlsplit(route.request.url)
        if url.hostname != "testserver":
            return route.abort()
        response = client.request(
            route.request.method,
            url.path + ("?" + url.query if url.query else ""),
            content=route.request.post_data_buffer,
            headers={
                "Content-Type": route.request.headers.get(
                    "content-type", "application/json"
                )
            },
        )
        route.fulfill(
            status=response.status_code,
            body=response.content,
            content_type=response.headers.get(
                "content-type", "application/octet-stream"
            ),
        )

    page.route("**/*", serve)
    page.goto(f"http://testserver/{start}")
    page.locator("#splash-dismiss").click()
    return page, errors


def _connections(page):
    page.evaluate("location.hash = '#/settings'")
    page.wait_for_selector("#settings-tab-connections")
    page.click("#settings-tab-connections")
    page.wait_for_selector("#claude-connect, #claude-disconnect")


# -- W-208 / NEW-269 --------------------------------------------------------------


def test_connect_with_the_relay_away_keeps_its_sentence_until_dismissed(
    relay, client, monkeypatch, engine
):
    monkeypatch.setenv(link.RELAY_ENV, f"http://127.0.0.1:{_free_port()}")
    page, errors = _page(client, engine)
    try:
        _connections(page)
        page.click("#claude-connect")
        page.wait_for_selector("#claude-relay-error")
        said = page.inner_text("#claude-relay-error")
        assert link.RELAY_AWAY in said
        assert "127.0.0.1" not in said and "Errno" not in said
        # the panel is plain: Not connected, Connect offered again
        assert page.inner_text("#claude-relay-state") == "Not connected"
        assert page.is_enabled("#claude-connect")
        # the panel's refresh does not drop it
        page.evaluate("SettingsPage.loadSwitchboard()")
        page.wait_for_timeout(300)
        assert page.is_visible("#claude-relay-error")
        # each Dismiss names what it dismisses (round 24's note)
        assert (
            page.get_attribute("#claude-relay-error-dismiss", "aria-label")
            == "Dismiss the relay message"
        )
        page.click("#claude-relay-error-dismiss")
        assert page.locator("#claude-relay-error").count() == 0
        assert not errors, errors
    finally:
        page.close()


def test_a_relay_name_left_by_a_disconnect_shows_after_a_reload(relay, client, engine):
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            assert relay.slug in page.inner_text("#claude-relay-fallback")
            said = page.inner_text("#claude-relay-fallback")
            assert "will keep trying to remove that record itself" in said
            assert "Send that name" not in said
            assert page.get_by_role(
                "button", name="Dismiss the removal notice", exact=True
            ).is_visible()
            page.click("#claude-relay-fallback-dismiss")
            page.wait_for_function(
                "() => !document.getElementById('claude-relay-fallback')"
            )
            assert link.pending_notices()[0]["dismissed"] is True
            assert not errors, errors
        finally:
            page.close()
    finally:
        link.stop_retry()


# -- W-209 -------------------------------------------------------------------------


PYWEBVIEW_DEFAULT = (
    "body {-webkit-user-select: none; user-select: none; cursor: default;}"
)


def _selectable(page, selector):
    return page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const s = getComputedStyle(el);
            return (s.userSelect || s.webkitUserSelect) !== 'none';
        }""",
        selector,
    )


def test_page_text_selects_and_the_chrome_does_not(client, seed_customer, engine):
    page, errors = _page(client, engine, "#/customers")
    try:
        # the page as the desktop window had it, plus the app's own rules:
        # the app's rules must win where text is meant to select
        page.wait_for_selector("#page-content table tbody tr td")
        cell = "#page-content table tbody tr td"
        assert _selectable(page, cell) is True
        assert _selectable(page, "#page-content h2") is True
        for chrome in ("#sidebar .nav-link", "#topbar .tb-btn", "thead th"):
            assert _selectable(page, chrome) is False, chrome
        # a selection of a cell's text holds it (with user-select: none it
        # came back empty, as the QA's script found)
        picked = page.evaluate(
            """(sel) => {
                const cell = [...document.querySelectorAll(sel)]
                    .find(td => td.innerText.trim());
                getSelection().selectAllChildren(cell);
                return String(getSelection()).trim();
            }""",
            cell,
        )
        assert picked, "a cell's text could not be selected"
        # and a heading of the sidebar's does not
        chrome = page.evaluate("""() => { getSelection().selectAllChildren(
                document.querySelector('#sidebar .nav-link'));
                return String(getSelection()).trim(); }""")
        assert chrome == ""
        # a field's own select-all
        page.fill("#global-search", "hello there")
        page.focus("#global-search")
        page.keyboard.press("ControlOrMeta+a")
        sel = page.evaluate(
            "(() => { const f = document.activeElement;"
            " return f.value.slice(f.selectionStart, f.selectionEnd); })()"
        )
        assert sel == "hello there"
        assert not errors, errors
    finally:
        page.close()


def test_the_launcher_turns_pywebviews_rule_off():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "desktop_launcher.py").read_text(
        encoding="utf-8"
    )
    main = src[src.index("api._window = webview.create_window(") :]
    main = main[: main.index(")\n")]
    assert "text_select=True" in main
    assert PYWEBVIEW_DEFAULT  # what text_select=False injects, for the record
