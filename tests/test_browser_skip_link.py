"""The skip link's home in the top bar (2.31.8), in a real browser.

"Skip to main content" showed, on the keyboard's focus, as a large blue
box at the window's top left, over the menu button, the Slowbooks Pro
wordmark and Back. It is now a small icon button at the start of the top
bar, always there and quiet, styled like the bar's other controls: named
"Skip to main content" with a tooltip saying so, still the first Tab stop on
every page, drawing the ring the bar's other controls draw, in both themes,
and still moving focus to the page's content. Its box meets no other
control's, at 100% in a common laptop's window and at 150% in the desktop
app's smallest one.
"""

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_text_size import SHOTS, _open  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)

NAME = "Skip to main content"

FOCUSED = """() => {
    const a = document.activeElement, cs = getComputedStyle(a);
    const box = el => { const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
    const bar = document.getElementById('topbar');
    return {
        id: a.id, tag: a.tagName, cls: String(a.className),
        label: a.getAttribute('aria-label'), title: a.title,
        href: a.getAttribute('href'),
        inBar: bar.contains(a),
        keyboard: a.matches(':focus-visible'),
        outline: [cs.outlineWidth, cs.outlineStyle, cs.outlineColor].join(' '),
        offset: cs.outlineOffset,
        box: box(a),
        bar: box(bar),
        others: ['sidebar-toggle', 'back-btn'].map(id => document.getElementById(id))
            .concat([document.querySelector('#topbar .topbar-brand')])
            .filter(el => el && el.offsetWidth > 0)
            .map(el => ({ id: el.id || el.className, box: box(el) })),
        ring: getComputedStyle(document.getElementById('back-btn')).getPropertyValue('--focus-ring').trim(),
    };
}"""


def _meets(a, b):
    """Whether two boxes share any area (touching edges do not)."""
    return (
        a["left"] < b["right"] - 0.5
        and b["left"] < a["right"] - 0.5
        and a["top"] < b["bottom"] - 0.5
        and b["top"] < a["bottom"] - 0.5
    )


def _first_stop(page):
    """Tab on a page just opened: its first stop."""
    page.keyboard.press("Tab")
    return page.evaluate(FOCUSED)


def _check(got, where):
    assert got["id"] == "skip-link" and "skip-link" in got["cls"], (where, got)
    assert got["label"] == NAME and got["title"] == NAME, (where, got)
    assert got["href"] == "#page-content", (where, got)
    assert got["inBar"] and got["keyboard"], (where, got)
    # the bar's ring: 2px of solid gold, 2px out
    assert got["outline"].startswith("2px solid"), (where, got)
    assert got["offset"] == "2px", (where, got)
    # inside the bar, meeting nothing in it
    assert got["box"]["top"] >= got["bar"]["top"] - 0.5, (where, got)
    assert got["box"]["bottom"] <= got["bar"]["bottom"] + 0.5, (where, got)
    assert got["box"]["left"] >= 0, (where, got)
    for other in got["others"]:
        assert not _meets(got["box"], other["box"]), (where, other, got["box"])


PAGES = [
    ("dashboard", "#/", "#page-content"),
    ("invoices", "#/invoices", "#page-content table"),
    ("settings", "#/settings", "#settings-groups [role='tab']"),
]


@pytest.mark.parametrize(
    "size, width, height", [("100", 1366, 768), ("150", 1366, 768), ("150", 900, 600)]
)
def test_the_skip_link_is_the_first_stop_and_covers_nothing(
    browser, company, books, size, width, height
):
    page, handled = _open(browser, company, "#/", size, width=width, height=height)
    try:
        pages = PAGES + [("register", f"#/banking/{books['checking']}", "#reg-start")]
        for name, route, ready in pages:
            # each page loaded afresh, as a person opens it, and Tab
            page.evaluate("(h) => { location.hash = h; }", route)
            page.reload()
            page.wait_for_function("window.App && document.readyState === 'complete'")
            page.evaluate(
                "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
            )
            page.wait_for_selector(ready)
            settle(page, handled)
            got = _first_stop(page)
            _check(got, (size, width, name))
        # and the ring's colour is the bar's own, in both themes
        for theme in ("light", "dark"):
            page.evaluate(
                "(t) => document.documentElement.setAttribute('data-theme', t)", theme
            )
            got = page.evaluate(FOCUSED)
            assert got["id"] == "skip-link"
            ring = page.evaluate(
                "() => getComputedStyle(document.activeElement).outlineColor"
            )
            bar_ring = page.evaluate(
                """() => { const b = document.createElement('button');
                    b.className = 'tb-btn'; document.getElementById('topbar').appendChild(b);
                    const c = getComputedStyle(b).getPropertyValue('--focus-ring').trim();
                    b.remove(); return c; }"""
            )
            assert ring == _rgb(page, bar_ring), (theme, ring, bar_ring)
            if SHOTS:
                page.screenshot(
                    path=os.path.join(
                        SHOTS, f"skip-focused-{width}x{height}-{size}-{theme}.png"
                    ),
                    clip={"x": 0, "y": 0, "width": min(width, 700), "height": 80},
                )
    finally:
        page.close()


def _rgb(page, colour):
    """A CSS colour as getComputedStyle writes it."""
    return page.evaluate(
        """(c) => { const d = document.createElement('div'); d.style.color = c;
            document.body.appendChild(d); const v = getComputedStyle(d).color;
            d.remove(); return v; }""",
        colour,
    )


def test_enter_on_the_skip_link_moves_focus_to_the_content(browser, company, books):
    page, handled = _open(browser, company, "#/invoices", "100")
    try:
        page.wait_for_selector("#page-content table")
        _first_stop(page)
        here = page.evaluate("location.hash")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.id === 'page-content'")
        assert page.evaluate("location.hash") == here == "#/invoices"
        # the next Tab is the content's first control, not the bar's
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!document.activeElement.closest('#page-content')")
        # a click on it does the same, and draws no ring
        page.click("#skip-link")
        assert page.evaluate("document.activeElement.id") == "page-content"
    finally:
        page.close()
