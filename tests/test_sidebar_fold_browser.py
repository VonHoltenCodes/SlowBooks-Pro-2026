"""Sidebar sections that fold, and the Hide sidebar control (#688), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py:

- each section heading is a real button with `aria-expanded`; folding one
  hides its items, is kept across a reload (localStorage
  slowbooks-sidebar-folded), and the section holding the page shown is
  open on load whatever was stored; the keyboard is never left inside a
  folded section;
- Hide sidebar in the top bar is named, has `aria-pressed` and
  `aria-keyshortcuts="["`; hidden, the content takes the width; kept across
  a reload (slowbooks-sidebar-hidden); `[` outside a field does the same,
  and inside a field it is typed;
- on a phone-width window the sidebar is as it was: shown, the control not
  offered, the stored choice and `[` without effect.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    ORIGIN,
    _open,
    _served_by,
    _visit,
    browser_fixture,
    company_fixture,
    new_page,
    settle,
    SERVED,
)

HEADS = """() => [...document.querySelectorAll('#sidebar .nav-links > li.nav-section')]
    .filter(li => !li.hidden).map(li => { const b = li.querySelector('button');
        return b ? { tag: b.tagName, type: b.type, name: b.textContent.trim(),
                     expanded: b.getAttribute('aria-expanded') } : null; })"""
SHOWN = """(page) => { const a = document.querySelector(`#sidebar .nav-link[data-page="${page}"]`);
    return !!a && a.getClientRects().length > 0; }"""


NO_SPLASH = "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"


def _start(browser, company):
    page, handled = _open(browser, company)
    page.evaluate(NO_SPLASH)
    return page, handled


def _reload(page, handled, route):
    page.reload()
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    settle(page, handled)
    _visit(page, handled, route)


def _section(page, name):
    return page.locator("#sidebar .nav-section__toggle", has_text=name).first


def test_a_section_folds_and_stays_folded_and_the_pages_own_is_open(
    browser, company, books
):
    page, handled = _start(browser, company)
    try:
        page.evaluate("() => localStorage.removeItem('slowbooks-sidebar-folded')")
        _visit(page, handled, "#/journal")
        heads = page.evaluate(HEADS)
        assert heads and all(
            h and h["tag"] == "BUTTON" and h["type"] == "button" for h in heads
        ), heads
        assert all(h["expanded"] == "true" for h in heads)
        names = [h["name"] for h in heads]
        assert "Banking" in names and "Accounting" in names

        banking = _section(page, "Banking")
        assert page.get_by_role("button", name="Banking", exact=True).count() == 1
        assert page.evaluate(SHOWN, "banking")
        # by the keyboard: focus on the heading, Enter folds it
        banking.focus()
        page.keyboard.press("Enter")
        assert banking.get_attribute("aria-expanded") == "false"
        assert not page.evaluate(SHOWN, "banking")
        assert not page.evaluate(SHOWN, "deposits")
        assert page.evaluate("document.activeElement.textContent.trim()") == "Banking"
        # its neighbours stay open
        assert page.evaluate(SHOWN, "journal") and page.evaluate(SHOWN, "vendors")

        # Accounting, the page's own section, folded while one of its links
        # has the keyboard: the keyboard goes to the heading, not into the fold
        page.locator('#sidebar .nav-link[data-page="journal"]').focus()
        page.evaluate(
            "() => App.toggleSection(document.querySelector('#sidebar li[data-section=\"Accounting\"]'))"
        )
        acct = _section(page, "Accounting")
        assert acct.get_attribute("aria-expanded") == "false"
        assert not page.evaluate(SHOWN, "journal")
        assert (
            page.evaluate("document.activeElement.textContent.trim()") == "Accounting"
        )
        stored = page.evaluate(
            "JSON.parse(localStorage.getItem('slowbooks-sidebar-folded'))"
        )
        assert set(stored) == {"Banking", "Accounting"}

        # reload on the journal: Banking stays folded, Accounting (the
        # page's own) is open
        _reload(page, handled, "#/journal")
        assert _section(page, "Banking").get_attribute("aria-expanded") == "false"
        assert not page.evaluate(SHOWN, "banking")
        assert _section(page, "Accounting").get_attribute("aria-expanded") == "true"
        assert page.evaluate(SHOWN, "journal")
        # the stored choice is kept: a load on another page has Accounting
        # folded (opened for the journal, it stays open for this load)
        _visit(page, handled, "#/customers")
        assert _section(page, "Accounting").get_attribute("aria-expanded") == "true"
        _reload(page, handled, "#/customers")
        assert _section(page, "Accounting").get_attribute("aria-expanded") == "false"
        assert _section(page, "Customers").get_attribute("aria-expanded") == "true"

        # opened again, it is stored open
        _section(page, "Banking").click()
        assert page.evaluate(SHOWN, "banking")
        assert "Banking" not in page.evaluate(
            "JSON.parse(localStorage.getItem('slowbooks-sidebar-folded'))"
        )
    finally:
        page.evaluate("() => localStorage.removeItem('slowbooks-sidebar-folded')")
        page.close()


def test_hide_sidebar_is_named_pressed_kept_and_on_the_bracket_key(
    browser, company, books
):
    page, handled = _start(browser, company)
    try:
        page.evaluate("() => localStorage.removeItem('slowbooks-sidebar-hidden')")
        _visit(page, handled, "#/journal")
        toggle = page.get_by_role("button", name="Hide sidebar", exact=True)
        assert toggle.count() == 1
        assert toggle.evaluate("e => e.closest('#topbar') !== null")
        assert toggle.get_attribute("aria-pressed") == "false"
        assert toggle.get_attribute("aria-keyshortcuts") == "["
        assert page.locator("#sidebar").is_visible()
        width = page.evaluate("document.getElementById('content').offsetWidth")

        toggle.click()
        assert toggle.get_attribute("aria-pressed") == "true"
        assert not page.locator("#sidebar").is_visible()
        assert page.evaluate("document.getElementById('content').offsetWidth") > width
        assert page.evaluate("localStorage.getItem('slowbooks-sidebar-hidden')") == "1"

        _reload(page, handled, "#/journal")
        toggle = page.get_by_role("button", name="Hide sidebar", exact=True)
        assert toggle.get_attribute("aria-pressed") == "true"
        assert not page.locator("#sidebar").is_visible()

        # [ shows it again, from the page
        page.locator("#page-content h2").click()
        page.keyboard.press("[")
        assert toggle.get_attribute("aria-pressed") == "false"
        assert page.locator("#sidebar").is_visible()
        assert page.evaluate("localStorage.getItem('slowbooks-sidebar-hidden')") is None

        # [ from inside the sidebar hides it and the keyboard goes to the
        # control, not into the menu gone
        page.locator('#sidebar .nav-link[data-page="journal"]').focus()
        page.keyboard.press("[")
        assert not page.locator("#sidebar").is_visible()
        assert page.evaluate("document.activeElement.id") == "sidebar-toggle"
        page.keyboard.press("[")
        assert page.locator("#sidebar").is_visible()

        # in a field it is typed, not a shortcut
        page.locator("#global-search").focus()
        page.keyboard.press("[")
        assert page.locator("#global-search").input_value() == "["
        assert page.locator("#sidebar").is_visible()
    finally:
        page.evaluate("() => localStorage.removeItem('slowbooks-sidebar-hidden')")
        page.close()


def test_a_phone_width_window_keeps_its_sidebar(browser, company, books):
    handled = []
    page = new_page(browser, viewport={"width": 600, "height": 900})
    page.route("**/*", _served_by(company, handled, SERVED))
    page.goto(f"{ORIGIN}/")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    try:
        page.evaluate("() => localStorage.setItem('slowbooks-sidebar-hidden', '1')")
        _reload(page, handled, "#/journal")
        assert page.locator("#sidebar").is_visible()
        assert not page.locator("#sidebar-toggle").is_visible()
        page.locator("#page-content h2").click()
        page.keyboard.press("[")
        assert page.locator("#sidebar").is_visible()
    finally:
        page.evaluate("() => localStorage.removeItem('slowbooks-sidebar-hidden')")
        page.close()
