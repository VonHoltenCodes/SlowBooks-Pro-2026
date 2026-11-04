"""The top bar at 1280 wide, in a real browser: with Contact support beside
About, the company name was the one thing in the bar that could shrink, and
it shrank to an "N" (2.30.0 gate, macbase1). It keeps room for a word, the
search box gives next, and below 1366 the clock goes; nothing in the bar is
pushed past the window's edge."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

MEASURE = """() => {
    const bar = document.getElementById('topbar');
    const company = document.getElementById('topbar-company');
    company.textContent = 'Riverbend Construction Services LLC';
    const right = document.querySelector('.topbar-right').getBoundingClientRect();
    const support = document.getElementById('support-btn').getBoundingClientRect();
    return {
        company: company.getBoundingClientRect().width,
        right: right.right,
        support: support.width,
        overflow: bar.scrollWidth - bar.clientWidth,
        clock: getComputedStyle(document.getElementById('topbar-clock')).display,
        window: window.innerWidth,
        quick: [...document.querySelectorAll('#topbar .tb-btn')]
            .filter(b => b.offsetWidth > 0).map(b => b.textContent.trim()),
        signOut: document.getElementById('logout-btn').getBoundingClientRect().right,
    };
}"""

# The five quick-entry buttons, in the bar's order
ALL_FIVE = [
    "New Customer",
    "Create Invoice",
    "Receive Payment",
    "Quick Entry",
    "Bill Entry",
]


@pytest.mark.parametrize(
    "width",
    [
        884,
        900,
        1024,
        1109,
        1110,
        1249,
        1250,
        1264,
        1279,
        1280,
        1366,
        1400,
        1403,
        1409,
        1410,
        1600,
    ],
)
def test_the_company_name_keeps_its_room_beside_contact_support(browser, width):
    page = _open(browser, width, 800, "#/")
    got = page.evaluate(MEASURE)
    page.close()
    assert got["window"] == width
    assert got["company"] >= 100, got
    assert got["support"] >= 80, got  # the button still says Contact support
    assert got["right"] <= width, got
    assert got["overflow"] <= 0, got
    assert got["clock"] == (
        "none" if width < 1410 else "block"
    ), got  # W-105: tight below 1400
    assert got["signOut"] <= width, got
    # The quick-entry buttons stay wherever they fit (2.31.1 gate W-87:
    # Windows' own default window is a 1264px page, and hiding all five
    # below 1280 took them from every Windows install): all five at 1250
    # and wider, the three that open a form from 1110, none below.
    shown = [t for t in got["quick"] if t in ALL_FIVE]
    if width >= 1250:
        assert shown == ALL_FIVE, got
    elif width >= 1110:
        assert shown == ALL_FIVE[:3], got
    else:
        assert shown == [], got


def test_the_bar_names_its_shortcuts_and_quick_entry_is_on_the_sidebar(browser):
    """W-87: Alt+Q was labelled nowhere, and invoice Quick Entry had no
    sidebar link, so a narrow window left it no way in but the shortcut.
    The bar's buttons that have a shortcut name it, as the pages' own
    buttons do, and Quick Entry has its sidebar link."""
    page = _open(browser, 1280, 800, "#/")
    got = page.evaluate(
        """() => [...document.querySelectorAll('#topbar .tb-btn')].map(b =>
            [b.textContent.trim(), b.getAttribute('aria-keyshortcuts'), b.title])"""
    )
    link = page.evaluate(
        "() => !!document.querySelector('#sidebar a.nav-link[href=\"#/quick-entry\"]')"
    )
    page.close()
    keys = {name: key for name, key, _title in got}
    assert keys["Home"] == "Alt+H" and keys["Create Invoice"] == "Alt+N", got
    assert keys["Receive Payment"] == "Alt+P" and keys["Quick Entry"] == "Alt+Q", got
    assert keys["New Customer"] is None and keys["Bill Entry"] is None, got
    titles = {name: title for name, _key, title in got}
    assert titles["Quick Entry"] in ("Quick Entry (Alt+Q)", "Quick Entry (⌥Q)"), got
    assert link
