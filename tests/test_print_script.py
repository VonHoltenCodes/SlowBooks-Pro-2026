"""Every print page prints through one script (NEW-208, 2.31.7 gate).

A print page used to say ``window.onload=function(){window.print();}``. In
the Mac desktop app that opened no dialog, ever: a WKWebView's own
``window.print()`` does nothing, and the shell's print hook is put in after
load (pywebview injects it at didFinishNavigation, then fires
``pywebviewready``). The shared script prints at load in a browser, and in
the shell once, at ``pywebviewready`` or a moment after load, whichever
comes first; a Print button on the sheet, not printed, starts it again."""

import base64
import re
from pathlib import Path

import pytest

from app.routes._print import PRINT_SCRIPT, print_html

ROOT = Path(__file__).resolve().parents[1]


def test_no_route_writes_its_own_on_load_print():
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "app").rglob("*.py")
        if "window.onload=function(){window.print" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    for name in (
        "bills.py",
        "purchase_orders.py",
        "deposits.py",
        "credit_memos.py",
        "estimates.py",
        "invoices/documents.py",
    ):
        assert "print_html(html_str)" in (ROOT / "app/routes" / name).read_text(
            encoding="utf-8"
        ), name


def test_the_script_waits_for_the_shell_and_carries_a_button():
    assert "pywebviewready" in PRINT_SCRIPT
    assert PRINT_SCRIPT.count("window.print()") == 2  # the page's, and the button's
    assert 'id="sb-print-again"' in PRINT_SCRIPT and ">Print</button>" in PRINT_SCRIPT
    assert "@media print{.sb-print-again{display:none}}" in PRINT_SCRIPT
    page = print_html("<html><body><p>x</p></body></html>")
    assert page.count(PRINT_SCRIPT) == 1 and page.endswith("</body></html>")
    assert print_html("<p>x</p>").endswith(PRINT_SCRIPT)


def test_a_report_print_page_is_titled_for_its_sheet(client, seed_accounts):
    resp = client.get("/api/budgets/print", params={"year": 2026})
    assert resp.status_code == 200
    title = re.search(r"<title>(.*?)</title>", resp.text).group(1)
    assert title != "Report" and "Budget" in title
    assert resp.text.count(PRINT_SCRIPT) == 1


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
        assert (
            page.evaluate(
                "getComputedStyle(document.getElementById('sb-print-again')).display"
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
