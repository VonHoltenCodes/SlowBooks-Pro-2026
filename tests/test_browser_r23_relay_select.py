# ruff: noqa: F811  (the relay fixture is imported from test_switchboard_connect_1087)
"""Round 23 on the screens, in Chromium and (where installed) WebKit.

W-208: Connect with the relay out of reach says one plain sentence that
stays through the panel's refresh until Dismiss or the next press, with
Connect offered again. NEW-269: a relay name a Disconnect could not delete
comes from the server, so the panel shows it after a reload.

Later: a refresh of the panel that changes nothing draws nothing, so the
keyboard focus and a selection stay; no two of its buttons share a name;
what the relay says is shown as text.

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


# -- the panel's own rule: a refresh takes nothing from the person ------------------


def _held(page):
    return page.evaluate("""() => ({
            focus: document.activeElement && document.activeElement.id,
            selected: String(getSelection()),
        })""")


def test_a_timed_refresh_keeps_the_keyboard_focus_and_the_selection(
    relay, client, engine
):
    """The notice for a relay name stays up for as long as the relay cannot
    be reached, and the panel asks the server again every few seconds: the
    Dismiss button keeps the keyboard and the relay name stays selected."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            page.focus("#claude-relay-fallback-dismiss")
            page.evaluate("""() => {
                const name = document.getElementById('claude-relay-fallback-name');
                name._same = true;
                getSelection().selectAllChildren(name);
            }""")
            before = _held(page)
            assert before == {
                "focus": "claude-relay-fallback-dismiss",
                "selected": relay.slug,
            }
            for _ in range(3):  # what the timer does, three times over
                page.evaluate("SettingsPage.loadSwitchboard()")
                page.wait_for_timeout(150)
            assert _held(page) == before
            # nothing was drawn again: the very same nodes, so a screen
            # reader is not told the alert a second time either
            assert page.evaluate(
                "document.getElementById('claude-relay-fallback-name')._same"
            )
            assert not errors, errors
        finally:
            page.close()
    finally:
        link.stop_retry()


def test_a_redraw_that_changes_the_panel_gives_back_what_was_held(
    relay, client, engine
):
    _connected(relay, client)
    page, errors = _page(client, engine)
    try:
        _connections(page)
        page.wait_for_selector("#claude-relay-name")
        page.focus("#claude-code")
        page.evaluate("""() => getSelection().selectAllChildren(
                document.getElementById('claude-relay-name'))""")
        # the panel changes under it: a sign-in code appears
        page.evaluate("SettingsPage.claudeCode()")
        page.wait_for_selector("#claude-relay-code")
        assert _held(page) == {"focus": "claude-code", "selected": relay.slug}
        assert not errors, errors
    finally:
        page.close()


_SELECT = """(id) => {
    const node = document.getElementById(id);
    node._same = true;
    getSelection().selectAllChildren(node);
}"""


def test_a_notice_going_on_a_refresh_leaves_the_selection_below_it(
    relay, client, engine
):
    """Connected again while an old relay name waits: the new relay name is
    selected to copy it, and the old notice goes on a timed refresh (the
    relay deleted the record, or it was dismissed elsewhere). The block that
    went was above the selection; the selection and the keyboard stay."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        old = link.pending_notices()[0]["slug"]
        _connected(relay, client)
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            page.focus("#claude-code")
            page.evaluate(_SELECT, "claude-relay-name")
            before = _held(page)
            assert before["focus"] == "claude-code" and before["selected"]
            r = client.post("/api/switchboard/dismiss", json={"slug": old})
            assert r.status_code == 200, r.text
            page.evaluate("SettingsPage.loadSwitchboard()")  # what the timer does
            page.wait_for_function(
                "() => !document.getElementById('claude-relay-fallback')"
            )
            assert _held(page) == before
            # the line was not drawn again: the very node that was selected
            assert page.evaluate("document.getElementById('claude-relay-name')._same")
            assert not errors, errors
        finally:
            page.close()
    finally:
        link.stop_retry()


def test_a_sentence_arriving_on_a_refresh_leaves_the_selection_below_it(
    relay, client, engine
):
    """Just connected, the address selected to copy it; a timed refresh
    turns "Connecting…" into "The relay cannot be reached" and adds its
    sentence above the address. The selection and the keyboard stay."""
    _connected(relay, client)
    page, errors = _page(client, engine)
    try:
        _connections(page)
        page.wait_for_selector("#claude-relay-address")
        assert page.locator("#claude-relay-sentence").count() == 0
        page.focus("#claude-code")
        page.evaluate(_SELECT, "claude-relay-address")
        before = _held(page)
        assert before["focus"] == "claude-code"
        assert before["selected"].endswith("/mcp")
        was = page.inner_text("#claude-relay-state")
        page.evaluate(
            """(sentence) => {
            const c = SettingsPage._claude;
            c.status = { ...c.status, state: 'unreachable', sentence };
            SettingsPage._drawSwitchboard();
        }""",
            link.UNREACHABLE,
        )
        assert page.inner_text("#claude-relay-state") != was
        assert page.inner_text("#claude-relay-sentence") == link.UNREACHABLE
        assert _held(page) == before
        assert page.evaluate("document.getElementById('claude-relay-address')._same")
        # words selected inside the block that did change are selected
        # again in the block drawn in its place
        page.evaluate("""() => getSelection().selectAllChildren(
                document.querySelector('#claude-relay-status span:last-child'))""")
        company = _held(page)["selected"]
        assert "Company" in company
        page.evaluate("""() => {
            const c = SettingsPage._claude;
            c.status = { ...c.status, state: 'connected', sentence: '' };
            SettingsPage._drawSwitchboard();
        }""")
        assert page.inner_text("#claude-relay-state") == "Connected"
        assert page.locator("#claude-relay-sentence").count() == 0
        assert _held(page) == {"focus": "claude-code", "selected": company}
        assert not errors, errors
    finally:
        page.close()


def test_no_two_buttons_of_the_panel_share_a_name(relay, client, engine):
    """An old relay name still waiting to be removed, and a new connection:
    the panel has two relay names with Copy, an error with Dismiss and the
    notice with Dismiss, each button with a name of its own."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        _connected(relay, client)  # connected again while the old name waits
        st = client.get("/api/switchboard/status").json()
        assert st["linked"] is True and st["pending"]
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            page.wait_for_selector("#claude-relay-name")
            # and a press that failed, so the relay message shows too
            relay.fail["/v1/code"] = 503
            page.click("#claude-code")
            page.wait_for_selector("#claude-relay-error")
            names = page.evaluate("""() => [...document.querySelectorAll(
                    '#switchboard-relay button')].map(b =>
                    b.getAttribute('aria-label') || b.textContent.trim())""")
            assert len(names) == len(set(names)), names
            assert set(names) == {
                "Dismiss the relay message",
                "Copy the relay name in the removal notice",
                "Dismiss the removal notice",
                "Copy the connector address",
                "Copy the relay name",
                "Show a sign-in code",
                "Disconnect",
            }
            for name in names:
                assert page.get_by_role("button", name=name, exact=True).count() == 1
            assert not errors, errors
        finally:
            page.close()
    finally:
        link.stop_retry()


def test_what_the_relay_says_is_shown_as_text_never_as_markup(relay, client, engine):
    relay.link_status = 403
    relay.link_error = '<img src=x onerror="window.relaySaid = 1"> <b>closed</b>'
    page, errors = _page(client, engine)
    try:
        _connections(page)
        page.click("#claude-connect")
        page.wait_for_selector("#claude-relay-error")
        said = page.inner_text("#claude-relay-error")
        assert link.RELAY_REFUSED in said
        assert "<img src=x" in said and "<b>closed</b>" in said
        assert (
            page.locator("#claude-relay-error img, #claude-relay-error b").count() == 0
        )
        assert page.evaluate("window.relaySaid") is None
        assert "127.0.0.1" not in said and "403" not in said
        assert not errors, errors
    finally:
        page.close()


# -- the panel's own rule: the keyboard never falls to the page, and a press ---------
# -- being worked on keeps its button through a refresh ------------------------------

_FOCUS = "document.activeElement && (document.activeElement.id || document.activeElement.tagName)"


@pytest.mark.parametrize(
    "on", ["claude-relay-fallback-dismiss", "claude-relay-fallback-copy"]
)
def test_a_notice_going_under_the_keyboard_hands_it_to_its_neighbor(
    relay, client, engine, on
):
    """The quiet retry deleted the record while the keyboard was on the
    notice's own button: the notice goes on the timed refresh and the
    keyboard goes to the next control of the panel, not to the page's body
    (where Tab would start again from the top of the page)."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            page.focus(f"#{on}")
            assert page.evaluate(_FOCUS) == on
            with link._pending_lock:
                link._save_pending([])  # what a retry that deleted it does
            page.evaluate("SettingsPage.loadSwitchboard()")  # what the timer does
            page.wait_for_function(
                "() => !document.getElementById('claude-relay-fallback')"
            )
            assert page.evaluate(_FOCUS) == "claude-connect"
            assert not errors, errors
        finally:
            page.close()
    finally:
        link.stop_retry()


_HOLD_CONNECT = """() => {
    const real = API.post.bind(API);
    window.__presses = 0;
    API.post = (path, ...rest) => {
        if (path !== '/switchboard/connect') return real(path, ...rest);
        window.__presses += 1;
        // the relay is slow to link: the answer comes when the test says
        return new Promise((resolve, reject) => {
            window.__answer = () => real(path, ...rest).then(resolve, reject);
        });
    };
}"""
_CONNECT_BUTTON = """() => {
    const b = document.getElementById('claude-connect');
    return b ? [b.disabled, b.textContent.trim()] : null;
}"""


def test_a_refresh_while_connect_is_worked_on_does_not_give_the_button_back(
    relay, client, engine
):
    """A removal notice is up (so the panel refreshes on a timer) and the
    relay is slow to link: the refresh that lands during the press leaves
    Connect off, a second press asks for nothing, and the connected panel
    says nothing about a press still being worked on."""
    _connected(relay, client)
    record = json.loads(link.link_path().read_text())
    link._save(dict(record, relay=f"http://127.0.0.1:{_free_port()}"))
    assert client.post("/api/switchboard/disconnect").json()["unlinked"] is False
    try:
        links = sum(1 for c in relay.calls if c[1] == "/v1/link")
        page, errors = _page(client, engine)
        try:
            _connections(page)
            page.wait_for_selector("#claude-relay-fallback")
            page.evaluate(_HOLD_CONNECT)
            page.focus("#claude-connect")
            page.keyboard.press("Enter")
            page.wait_for_function("() => window.__presses === 1")
            assert page.evaluate(_CONNECT_BUTTON) == [True, "Connecting…"]
            for _ in range(2):  # what the timer does, with the press in flight
                page.evaluate("SettingsPage.loadSwitchboard()")
                page.wait_for_timeout(150)
                assert page.evaluate(_CONNECT_BUTTON) == [True, "Connecting…"]
            # a second press, however it is made, is not sent
            page.evaluate("SettingsPage.connectClaude()")
            assert page.evaluate("window.__presses") == 1
            page.evaluate("window.__answer()")
            page.wait_for_selector("#claude-relay-address")
            assert page.locator("#claude-relay-error").count() == 0
            assert "being worked on" not in page.inner_text("#switchboard-relay")
            # the keyboard is on the panel's next control, not on the page
            assert page.evaluate(_FOCUS) == "claude-code"
            assert sum(1 for c in relay.calls if c[1] == "/v1/link") == links + 1
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
