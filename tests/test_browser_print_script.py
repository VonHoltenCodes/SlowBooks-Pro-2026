"""The shared print script in a real browser (NEW-208, 2.31.7 gate): at load
in a browser; in a document window (not an http page) once, at the shell's
ready event or a moment after load; the sheet's own Print button prints
again and is not printed. See tests/test_print_script.py."""

import base64

import pytest

from app.routes._print import print_html

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

SHEET = "<html><head><title>t</title></head><body><p>sheet</p></body></html>"
COUNT = "window.__prints = 0; window.print = function () { window.__prints += 1; };"


def _page(browser):
    page = browser.new_page()
    page.add_init_script(COUNT)
    return page


def test_in_a_browser_it_prints_at_load(browser):
    page = _page(browser)
    try:
        page.route(
            "http://print.test/sheet",
            lambda r: r.fulfill(content_type="text/html", body=print_html(SHEET)),
        )
        page.goto("http://print.test/sheet")
        page.wait_for_function("window.__prints === 1", timeout=1000)
        # the sheet's own button prints again, and is not printed
        page.click("#sb-print-again")
        assert page.evaluate("window.__prints") == 2
        page.emulate_media(media="print")
        # the strip that holds it is not printed (#813)
        assert (
            page.evaluate(
                "getComputedStyle(document.getElementById('sb-print-bar')).display"
            )
            == "none"
        )
    finally:
        page.close()


def _data_url(html: str) -> str:
    return "data:text/html;base64," + base64.b64encode(html.encode()).decode()


def test_in_the_shell_it_waits_for_the_print_hook(browser):
    """A document window is not an http page: nothing prints at load; the
    shell's ready event prints once, and only once."""
    page = _page(browser)
    try:
        page.goto(_data_url(print_html(SHEET)))
        page.wait_for_load_state("load")
        assert page.evaluate("window.__prints") == 0
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        assert page.evaluate("window.__prints") == 1
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_timeout(1800)
        assert page.evaluate("window.__prints") == 1
    finally:
        page.close()


def test_in_the_shell_with_no_ready_event_it_prints_a_moment_after_load(browser):
    page = _page(browser)
    try:
        page.goto(_data_url(print_html(SHEET)))
        page.wait_for_load_state("load")
        assert page.evaluate("window.__prints") == 0
        page.wait_for_function("window.__prints === 1", timeout=4000)
    finally:
        page.close()


def test_the_sheets_last_figures_clear_its_print_button(browser):
    """W-124 (2.31.7 gate): fixed at the bottom right, the button sat on the
    last row's figures with the window scrolled to the end, and, with room
    made under the sheet, still over the bottom-right figures at the top of
    a long one (Budgets' November and December, #813). It is in a strip
    above the sheet now, in the page's flow: no figure is under it at the
    top or at the end, and the strip is not printed."""
    rows = "".join(f"<tr><td>Account {i}</td><td>{i}.00</td></tr>" for i in range(60))
    sheet = (
        "<html><head><title>t</title></head><body>"
        f"<table style='width:100%'>{rows}</table></body></html>"
    )
    page = _page(browser)
    try:
        page.set_viewport_size({"width": 784, "height": 561})
        page.route(
            "http://print.test/long",
            lambda r: r.fulfill(content_type="text/html", body=print_html(sheet)),
        )
        page.goto("http://print.test/long")
        under = """() => {
                const btn = document.getElementById('sb-print-again').getBoundingClientRect();
                return [...document.querySelectorAll('td')].filter(td => {
                    const r = td.getBoundingClientRect();
                    return r.left < btn.right && r.right > btn.left
                        && r.top < btn.bottom && r.bottom > btn.top;
                }).length; }"""
        for at in ("0", "document.documentElement.scrollHeight"):
            page.evaluate(f"window.scrollTo(0, {at})")
            assert page.evaluate(under) == 0, at
        # the strip is the body's first child, above the sheet
        assert page.evaluate("document.body.firstElementChild.id") == "sb-print-bar"
        page.emulate_media(media="print")
        assert (
            page.evaluate(
                "getComputedStyle(document.getElementById('sb-print-bar')).display"
            )
            == "none"
        )
    finally:
        page.close()
