"""The top bar at 1280 wide, in a real browser: with Contact support beside
About, the company name was the one thing in the bar that could shrink, and
it shrank to an "N" (2.30.0 gate, macbase1). It keeps room for a word, the
search box gives next, and below 1366 the clock goes; nothing in the bar is
pushed past the window's edge.

The bar's middle is the Fast Rack since #912: the buttons each sign-in
chooses, every one 80px wide. Which of them fit is measured: the rack folds
into More the last first, so what shows is always the rack's first few, in
its order, and every one folded is listed under More."""

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
        rack: [...document.querySelectorAll('#fast-rack .tb-btn')]
            .filter(b => b.offsetWidth > 0).map(b => b.textContent.trim()),
        rackSizes: [...document.querySelectorAll('#fast-rack .tb-btn')]
            .filter(b => b.offsetWidth > 0).map(b => [b.offsetWidth, b.offsetHeight]),
        signOut: document.getElementById('logout-btn').getBoundingClientRect().right,
    };
}"""

# The five quick-entry buttons, in the bar's order
ALL_FIVE = [
    "New Customer",
    "Create Invoice",
    "Receive Payment",
    "Quick Entry",
    "Bill Quick Entry",
]
# The Fast Rack's shipped set (#912): the bar as it was, Home to Reports
RACK = ["Home", *ALL_FIVE, "Reports"]
MORE_LIST = """() => [...document.querySelectorAll('#topbar-more-menu > *')]
    .map(el => el.textContent.trim())"""


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
        1419,
        1425,
        1426,
        1600,
    ],
)
def test_the_company_name_keeps_its_room_beside_contact_support(browser, width):
    page = _open(browser, width, 800, "#/")
    got = page.evaluate(MEASURE)
    listed = []
    if len(got["rack"]) < len(RACK):
        page.click("#topbar-more")
        listed = page.evaluate(MORE_LIST)
    page.close()
    assert got["window"] == width
    assert got["company"] >= 100, got
    assert got["support"] >= 80, got  # the button still says Contact support
    assert got["right"] <= width, got
    assert got["overflow"] <= 0, got
    # W-105: tight below 1410; the clock was back only from 1426 once the bar
    # said Bill Quick Entry, and from 1410 again with the Fast Rack (#912),
    # whose seven are narrower than the shipped seven were
    assert got["clock"] == ("none" if width < 1410 else "block"), got
    assert got["signOut"] <= width, got
    # The quick-entry buttons stay wherever they fit (2.31.1 gate W-87:
    # Windows' own default window is a 1264px page, and hiding all five
    # below 1280 took them from every Windows install): all five at 1250
    # and wider, the three that open a form from 1110 at least. Below,
    # the Fast Rack (#912) keeps the first ones that fit, Home first.
    shown = [t for t in got["quick"] if t in ALL_FIVE]
    if width >= 1250:
        assert shown == ALL_FIVE, got
    elif width >= 1110:
        assert shown[:3] == ALL_FIVE[:3], got
    # what shows is the rack's first few, in its order; the rest are in More
    assert got["rack"] == RACK[: len(got["rack"])], got
    assert got["rack"], got  # Home, at the least
    for name in RACK[len(got["rack"]) :]:
        assert name in listed, (width, name, listed)
    # every rack button the same size (#912)
    assert {tuple(s) for s in got["rackSizes"]} == {(80, 32)}, got


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
    # the buttons are the Fast Rack's (#912), drawn from the sign-in's list,
    # a group the screen reader names
    rack = page.evaluate(
        """() => { const r = document.getElementById('fast-rack');
            return [r.getAttribute('role'), r.getAttribute('aria-label'), r.dataset.rendered]; }"""
    )
    assert rack == ["group", "Fast Rack", "1"], rack
    link = page.evaluate(
        "() => !!document.querySelector('#sidebar a.nav-link[href=\"#/quick-entry\"]')"
    )
    page.close()
    keys = {name: key for name, key, _title in got}
    assert keys["Home"] == "Alt+H" and keys["Create Invoice"] == "Alt+N", got
    assert keys["Receive Payment"] == "Alt+P" and keys["Quick Entry"] == "Alt+Q", got
    assert keys["New Customer"] is None and keys["Bill Quick Entry"] is None, got
    titles = {name: title for name, _key, title in got}
    assert titles["Quick Entry"] in ("Quick Entry (Alt+Q)", "Quick Entry (⌥Q)"), got
    assert link
