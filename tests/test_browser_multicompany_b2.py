# ruff: noqa: F811  (the fixtures are imported from test_switchboard_multicompany_b2)
"""The AI assistants panel with several companies on one connection, in
Chromium and (where installed) WebKit, on a Server Edition host serving
Harbor Books, Smith Plumbing and Lake Dental with the stand-in relay.

Someone who is an administrator of every company sees the list, each name
with its note; the panel's timed refresh keeps the keyboard and a selection
in a company's name when another company's note changes; no two buttons
share a name; the presses are reached and made from the keyboard. A client
company's own administrator sees that company alone.
"""

from urllib.parse import urlsplit

import pytest

from app.services import switchboard_link as link
from tests.test_switchboard_multicompany_b2 import (  # noqa: F401  (fixtures)
    _connected_with,
    _record,
    _serve,
    _signed_in,
    _switch,
    relay,
    three,
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


def _page(person, engine):
    """A page whose every request goes to this person's signed-in client."""
    page = _BROWSERS[engine].new_page(viewport={"width": 1280, "height": 900})
    errors, asked = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def confirm(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.on("dialog", confirm)

    def serve(route):
        url = urlsplit(route.request.url)
        if url.hostname != "testserver":
            return route.abort()
        response = person.request(
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
    page.goto("http://testserver/#/settings")
    if page.locator("#splash-dismiss").is_visible():
        page.locator("#splash-dismiss").click()
    page.wait_for_selector("#settings-tab-connections")
    page.click("#settings-tab-connections")
    page.wait_for_function(
        "() => { const p = document.getElementById('switchboard-relay');"
        " return p && !p.innerText.startsWith('Checking'); }"
    )
    return page, errors, asked


_NAMES = """() => [...document.querySelectorAll('#switchboard-relay button')]
    .map(b => b.getAttribute('aria-label') || b.textContent.trim())"""
_HELD = """() => ({
    focus: document.activeElement && document.activeElement.id,
    selected: String(getSelection()),
})"""


def test_a_manager_sees_every_company_and_a_refresh_keeps_what_is_held(
    three, relay, engine
):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    page, errors, _ = _page(jdoe, engine)
    try:
        page.wait_for_selector("#claude-relay-companies")
        said = page.inner_text("#claude-relay-companies")
        assert said.startswith("The AI assistant can work the books of: ") or (
            said.startswith("Connected. The AI assistant can work the books of: ")
        )
        assert "Harbor Books (this company)" in said
        assert "Smith Plumbing" in said and "Lake Dental" in said
        page.focus("#claude-take-off")
        page.evaluate("""() => getSelection().selectAllChildren(
                document.getElementById('claude-relay-co-smith_2e_db'))""")
        before = page.evaluate(_HELD)
        assert before == {"focus": "claude-take-off", "selected": "Smith Plumbing"}
        # Lake Dental stops being served: its note changes on the timed refresh
        _serve(three, ["harbor.db", "smith.db"])
        page.evaluate("SettingsPage.loadSwitchboard()")
        page.wait_for_function(
            "() => document.getElementById('claude-relay-companies').innerText"
            ".includes('Lake Dental (not served by this server now)')"
        )
        # the list was drawn again, and the keyboard and the selected name
        # were given back
        assert page.evaluate(_HELD) == before
        assert not errors, errors
    finally:
        page.close()


def test_no_two_buttons_share_a_name_with_three_companies(three, relay, engine):
    jdoe = _connected_with(three, relay, "smith.db", "lake.db")
    # Lake Dental's token answered 401 once: its entry is a dead one, which
    # someone with power over the connection may drop by its own button
    lake = next(e for e in _record()["companies"] if e["file"] == "lake.db")
    assert link.drop_secret(lake["books"]) is True
    page, errors, _ = _page(jdoe, engine)
    try:
        page.wait_for_selector("#claude-take-off-lake_2e_db")
        page.click("#claude-code")
        page.wait_for_selector("#claude-relay-code")
        assert page.inner_text("#claude-relay-code-reach") == (
            "Anyone who signs an assistant in with this code can work every "
            "company listed above."
        )
        assert "Lake Dental (turned off in that company)" in page.inner_text(
            "#claude-relay-companies"
        )
        names = page.evaluate(_NAMES)
        assert names == [
            "Copy the connector address",
            "Copy the relay name",
            "Show a sign-in code",
            "Take Harbor Books off the AI connection",
            "Take Lake Dental off the AI connection",
            "Disconnect the AI assistant",
        ]
        for name in names:
            assert page.get_by_role("button", name=name, exact=True).count() == 1
        assert not errors, errors
    finally:
        page.close()


def test_add_and_take_off_from_the_keyboard(three, relay, engine):
    jdoe = _connected_with(three, relay)
    _switch(jdoe, "smith.db")
    page, errors, asked = _page(jdoe, engine)
    try:
        page.wait_for_selector("#claude-relay-add")
        assert page.inner_text("#claude-relay-add") == (
            "Add Smith Plumbing to the AI assistant connection, and the assistant "
            "can work Smith Plumbing's books as a bookkeeper. Every change it "
            "makes is recorded in Smith Plumbing's audit log as token:AI "
            "assistant (relay)."
        )
        page.focus("#claude-add")
        page.keyboard.press("Enter")
        page.wait_for_selector("#claude-take-off")
        assert asked[-1] == (
            "Add Smith Plumbing? Every assistant already signed in to this "
            "connection, and any signed in later, will be able to read and change "
            "Smith Plumbing's books as a bookkeeper. You can take it off again here "
            "at any time."
        )
        assert [e["file"] for e in _record()["companies"]] == [
            "harbor.db",
            "smith.db",
        ]
        # the keyboard stays on the panel, not on the page's body
        assert page.evaluate("document.activeElement.id").startswith("claude-")
        page.focus("#claude-take-off")
        page.keyboard.press("Enter")
        page.wait_for_selector("#claude-add")
        assert asked[-1] == (
            "Take Smith Plumbing off? Its token, AI assistant (relay), is turned off "
            "at once and the assistant can no longer reach these books. The other "
            "companies stay connected."
        )
        assert [e["file"] for e in _record()["companies"]] == ["harbor.db"]
        assert page.evaluate("document.activeElement.id").startswith("claude-")
        assert not errors, errors
    finally:
        page.close()


def test_a_client_administrator_sees_that_company_alone(three, relay, engine):
    _connected_with(three, relay, "lake.db")
    cli = _signed_in(three, "cli", company="lake.db")
    page, errors, _ = _page(cli, engine)
    try:
        page.wait_for_selector("#claude-relay-on-link")
        assert page.inner_text("#claude-relay-on-link") == (
            "Lake Dental is on this computer's AI assistant connection. The "
            "assistant works these books as a bookkeeper; every change is recorded "
            "here as token:AI assistant (relay)."
        )
        assert page.evaluate(_NAMES) == ["Take Lake Dental off the AI connection"]
        text = page.inner_text("#switchboard-relay")
        for other in ("Harbor", "Smith", "harbor.db", "~"):
            assert other not in text
        assert not errors, errors
    finally:
        page.close()
