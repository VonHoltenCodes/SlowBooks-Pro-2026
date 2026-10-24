"""Recent screens (#336, #462), in playwright's Chromium on the bakery's
books: the pages, registers and dialog reports opened in a session are kept
for the one "Recent ▾" line at the top of the sidebar, which names the screen
showing and opens the last seven, most recent first, each a link back; the
one showing is marked; a report lists with its period; a reload keeps the
list; Clear empties it. It is a disclosure button: arrow keys move through
the list, Esc closes it and gives the button the focus, Tab moves on. It
fits a 320 px window and meets AA in both themes. Skipped where playwright
or its Chromium is not installed (tests/test_open_tabs.py checks the
sources)."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_report_views import _hash, _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _theme,
    _visit,
    below_threshold,
    browser_fixture,
    company_fixture,
    settle,
    sweep_of,
)

MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TITLE = "() => document.getElementById('modal-title').textContent"
TABS = """() => [...document.querySelectorAll('#recent-screens .recent-screens__link')]
    .map(a => ({ label: a.textContent.trim(), key: a.dataset.key, here: a.getAttribute('aria-current') === 'page' }))"""
GROUP = "() => !!document.getElementById('recent-screens')"
TOGGLE = "#recent-toggle"
STATE = """() => { const b = document.getElementById('recent-toggle'), m = document.getElementById('recent-menu');
    return { expanded: b.getAttribute('aria-expanded'), controls: b.getAttribute('aria-controls'),
             hidden: m.hidden, name: b.textContent.replace(/\\s+/g, ' ').trim() }; }"""
FOCUSED = """() => { const a = document.activeElement;
    return a ? (a.id || a.dataset.key || a.dataset.recent || a.textContent.trim()) : null; }"""


def _ok(r):
    assert r.status_code in (200, 201), (str(r.request.url), r.status_code, r.text)
    return r.json()


def _tabs(page):
    return page.evaluate(TABS)


def _reload(page, handled):
    page.reload()
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )
    settle(page, handled)


def test_the_screens_opened_are_listed_newest_first_linked_and_marked(
    browser, company, books
):
    accounts = {a["account_number"]: a for a in _ok(company.get("/api/accounts"))}
    checking = accounts["1000"]["id"]
    page, handled = _open_at(browser, company, "#/banking")
    try:
        # the Banking page is the first screen; the Dashboard is never listed
        assert _tabs(page) == [{"label": "Banking", "key": "/banking", "here": True}]
        state = page.evaluate(STATE)
        assert state["expanded"] == "false" and state["hidden"]
        assert state["controls"] == "recent-menu"
        assert state["name"].startswith("Recent: Banking")
        # one line: no per-screen rows show while the dropdown is closed
        assert not page.is_visible("#recent-screens .recent-screens__link")
        # the register: named for its account, the page's own heading
        _visit(page, handled, f"#/banking/{checking}")
        tabs = _tabs(page)
        assert [t["label"] for t in tabs] == ["Checking", "Banking"]
        assert [t["here"] for t in tabs] == [True, False]
        assert page.evaluate(STATE)["name"].startswith("Recent: Checking")
        # a dialog report, with its period
        _visit(page, handled, "#/reports/profit-loss?period=last_month")
        page.wait_for_selector("#report-content table")
        assert page.evaluate(MODAL_SHOWN)
        tabs = _tabs(page)
        assert [t["label"] for t in tabs] == [
            "Profit & Loss · last month",
            "Checking",
            "Banking",
        ]
        assert tabs[0]["here"] and tabs[0]["key"] == _hash(page).lstrip("#")
        # the same report on another period is the same screen, renamed
        page.select_option("#report-period-select", "this_year_to_date")
        page.wait_for_function("() => /this_year_to_date/.test(location.hash)")
        settle(page, handled)
        labels = [t["label"] for t in _tabs(page)]
        assert labels == ["Profit & Loss · this year to date", "Checking", "Banking"]
        # the Report Center it opened over is not listed as a screen of its own
        assert "Reports" not in labels

        # a link back, clicked while the report is showing: a view's overlay
        # leaves the sidebar usable (W-22 / NEW-159), so the register page
        # opens, the dialog goes and the mark moves
        assert page.evaluate(
            "() => document.body.classList.contains('dialog-addressed')"
        )
        page.click(TOGGLE)
        assert page.evaluate(STATE)["expanded"] == "true"
        link = (
            '#recent-screens .recent-screens__link[data-key="/banking/%d"]' % checking
        )
        hit = page.evaluate(
            """(sel) => { const a = document.querySelector(sel);
                       const r = a.getBoundingClientRect();
                       return document.elementFromPoint(r.left + 4, r.top + r.height / 2) === a; }""",
            link,
        )
        assert hit, "the Recent link is under the overlay"
        page.click(link)
        settle(page, handled)
        assert _hash(page) == f"#/banking/{checking}"
        assert not page.evaluate(MODAL_SHOWN)
        assert not page.evaluate(
            "() => document.body.classList.contains('dialog-addressed')"
        )
        # the screen gone back to is the most recent now; the dropdown closed
        tabs = _tabs(page)
        assert [t["label"] for t in tabs] == [
            "Checking",
            "Profit & Loss · this year to date",
            "Banking",
        ]
        assert [t["here"] for t in tabs] == [True, False, False]
        assert page.evaluate(STATE)["hidden"]
        # no × on any line
        assert not page.query_selector("#recent-screens .open-screens__close")
        assert page.evaluate(
            "() => [...document.querySelectorAll('#recent-screens button')].map(b => b.dataset.recent)"
        ) == ["toggle", "clear"]

        # a reload keeps the session's list
        _reload(page, handled)
        assert len(_tabs(page)) == 3

        # Clear
        page.click(TOGGLE)
        page.click("#recent-screens .recent-screens__clear")
        assert not page.evaluate(GROUP)
        stored = page.evaluate(
            "() => JSON.parse(sessionStorage.getItem('slowbooks-open-screens'))"
        )
        assert stored["tabs"] == [] and stored["owner"].startswith("admin|")
        # the next screen starts the list again
        _visit(page, handled, "#/customers")
        assert [t["key"] for t in _tabs(page)] == ["/customers"]
    finally:
        page.close()


ROUTES = [
    "#/customers",
    "#/vendors",
    "#/invoices",
    "#/bills",
    "#/items",
    "#/estimates",
    "#/journal",
    "#/accounts",
    "#/banking",
]


def test_the_list_holds_seven_without_repeats(browser, company, books):
    page, handled = _open_at(browser, company, "#/customers")
    try:
        for r in ROUTES[1:]:
            _visit(page, handled, r)
        keys = [t["key"] for t in _tabs(page)]
        # the newest seven, newest first; the two oldest dropped off
        assert keys == [r.lstrip("#") for r in reversed(ROUTES)][:7]
        assert "/customers" not in keys and "/vendors" not in keys
        # a screen opened again moves to the top, not a second line
        _visit(page, handled, "#/items")
        keys = [t["key"] for t in _tabs(page)]
        assert keys[0] == "/items" and keys.count("/items") == 1 and len(keys) == 7
        here = [t for t in _tabs(page) if t["here"]]
        assert [t["key"] for t in here] == ["/items"]
    finally:
        page.close()


def test_the_dropdown_by_keyboard(browser, company, books):
    page, handled = _open_at(browser, company, "#/customers")
    try:
        for r in ("#/vendors", "#/invoices"):
            _visit(page, handled, r)
        page.focus(TOGGLE)
        # Enter opens it, the focus stays on the button (a disclosure)
        page.keyboard.press("Enter")
        assert page.evaluate(STATE)["expanded"] == "true"
        assert page.evaluate(FOCUSED) == "recent-toggle"
        # Down from the button: the first screen, then on through to Clear,
        # and round to the first again
        page.keyboard.press("ArrowDown")
        assert page.evaluate(FOCUSED) == "/invoices"
        page.keyboard.press("ArrowDown")
        assert page.evaluate(FOCUSED) == "/vendors"
        page.keyboard.press("End")
        assert page.evaluate(FOCUSED) == "clear"
        page.keyboard.press("ArrowDown")
        assert page.evaluate(FOCUSED) == "/invoices"
        page.keyboard.press("ArrowUp")
        assert page.evaluate(FOCUSED) == "clear"
        page.keyboard.press("Home")
        assert page.evaluate(FOCUSED) == "/invoices"
        # Esc closes it and gives the button the focus
        page.keyboard.press("Escape")
        state = page.evaluate(STATE)
        assert state["expanded"] == "false" and state["hidden"]
        assert page.evaluate(FOCUSED) == "recent-toggle"
        # Down on the closed button opens it at the first screen
        page.keyboard.press("ArrowDown")
        assert page.evaluate(STATE)["expanded"] == "true"
        assert page.evaluate(FOCUSED) == "/invoices"
        # Enter on a screen opens it
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_function("() => location.hash === '#/vendors'")
        settle(page, handled)
        assert page.evaluate(STATE)["hidden"]
        # the list closed under the keyboard: the button has it, not <body>
        assert page.evaluate(FOCUSED) == "recent-toggle"
        assert _tabs(page)[0] == {"label": "Vendors", "key": "/vendors", "here": True}
        # Tab out of the open list moves on and closes it behind
        page.focus(TOGGLE)
        page.keyboard.press("Enter")
        page.keyboard.press("End")
        page.keyboard.press("Tab")
        assert page.evaluate(STATE)["hidden"]
        assert not page.evaluate(
            "() => document.getElementById('recent-screens').contains(document.activeElement)"
        )
        # a click outside closes it too
        page.click(TOGGLE)
        assert page.evaluate(STATE)["expanded"] == "true"
        page.click("#page-content")
        assert page.evaluate(STATE)["hidden"]
        # Clear by keyboard: the list goes, the focus lands on the menu
        page.focus(TOGGLE)
        page.keyboard.press("ArrowUp")
        assert page.evaluate(FOCUSED) == "clear"
        page.keyboard.press("Enter")
        assert not page.evaluate(GROUP)
        assert page.evaluate("() => !!document.activeElement.closest('#sidebar')")
    finally:
        page.close()


def test_it_fits_a_narrow_window_and_meets_aa_in_both_themes(browser, company, books):
    inv = _ok(company.get("/api/invoices"))[0]
    page, handled = _open_at(browser, company, "#/customers")
    try:
        _visit(page, handled, "#/vendors")
        _visit(page, handled, f"#/invoices/{inv['id']}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        page.keyboard.press("Escape")
        page.wait_for_function("() => !(%s)()" % MODAL_SHOWN)
        settle(page, handled)
        page.set_viewport_size({"width": 320, "height": 640})
        page.click(TOGGLE)
        fit = page.evaluate(
            """() => { const s = document.getElementById('sidebar').getBoundingClientRect();
                const els = [...document.querySelectorAll('#recent-toggle, #recent-menu .recent-screens__link, #recent-menu .recent-screens__clear')];
                return els.map(e => { const r = e.getBoundingClientRect();
                    return r.left >= s.left - 1 && r.right <= s.right + 1; }); }"""
        )
        assert fit and all(fit)
        assert page.evaluate(
            "() => { const b = document.getElementById('recent-toggle'); return b.scrollWidth <= b.clientWidth + 1; }"
        )
        sweeps = {}
        for theme in ("light", "dark"):
            _theme(page, theme)
            sweeps[(theme, "recent")] = page.evaluate(
                sweep_of("document.getElementById('recent-screens')")
            )
        texts = {it["text"] for items in sweeps.values() for it in items}
        assert "Clear" in texts and "Vendors" in texts
        assert below_threshold(sweeps) == []
        _theme(page, "light")
    finally:
        page.close()


def test_a_document_lists_with_its_title(browser, company, books):
    inv = _ok(company.get("/api/invoices"))[0]
    page, handled = _open_at(browser, company, f"#/invoices/{inv['id']}")
    try:
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        tabs = _tabs(page)
        assert len(tabs) == 1 and tabs[0]["key"] == f"/invoices/{inv['id']}"
        assert tabs[0]["label"] == page.evaluate(TITLE).strip()
        assert tabs[0]["here"]
        # View on the list opens the same document by its address (NEW-164)
        page.keyboard.press("Escape")
        page.wait_for_function("() => !(%s)()" % MODAL_SHOWN)
        page.evaluate("async (id) => { await InvoicesPage.view(id); }", inv["id"])
        settle(page, handled)
        assert _hash(page) == f"#/invoices/{inv['id']}"
        tabs = _tabs(page)
        assert len(tabs) == 1 and tabs[0]["here"]
    finally:
        page.close()


def test_the_list_belongs_to_one_sign_in(browser, company, books):
    """Another sign-in on the same window starts with its own list (NEW-164)."""
    page, handled = _open_at(browser, company, "#/customers")
    try:
        assert len(_tabs(page)) == 1
        # a stored list of another owner is dropped once the sign-in is known
        page.evaluate(
            """() => { const s = JSON.parse(sessionStorage.getItem('slowbooks-open-screens'));
                       s.owner = 'someone-else|Another Co'; s.tabs.push({ key: '/vendors', label: 'Vendors', n: 1, load: 'old' });
                       sessionStorage.setItem('slowbooks-open-screens', JSON.stringify(s)); }"""
        )
        _reload(page, handled)
        tabs = _tabs(page)
        assert [t["key"] for t in tabs] == ["/customers"]
    finally:
        page.close()


def test_hidden_menu_and_recent_leave_each_other_alone(browser, company, books):
    """The hidden-menu setting (#334) hides a page's own link but not its
    Recent line, and the Recent control is not on the Settings list."""
    company.put("/api/settings", json={"hidden_menu": '["vendors"]'})
    page, handled = _open_at(browser, company, "#/vendors")
    try:
        _visit(page, handled, "#/settings")
        page.wait_for_selector("#hidden-menu-choices input[data-show-menu]")
        assert page.evaluate(
            "() => document.querySelector('#sidebar .nav-link[data-page=\"vendors\"]').closest('li').hidden"
        )
        assert [t["key"] for t in _tabs(page)] == ["/settings", "/vendors"]
        assert not page.evaluate(
            "() => document.getElementById('recent-screens').hidden"
        )
        keys = page.evaluate(
            "() => [...document.querySelectorAll('#hidden-menu-choices input')].map(b => b.dataset.showMenu)"
        )
        assert "vendors" in keys and not any(k.startswith("/") for k in keys)
    finally:
        page.close()
        company.put("/api/settings", json={"hidden_menu": "[]"})


def test_a_mouse_pick_leaves_the_focus_on_the_button(browser, company, books):
    page, handled = _open_at(browser, company, "#/customers")
    try:
        _visit(page, handled, "#/vendors")
        page.click(TOGGLE)
        page.click('#recent-menu .recent-screens__link[data-key="/customers"]')
        page.wait_for_function("() => location.hash === '#/customers'")
        settle(page, handled)
        assert page.evaluate(STATE)["hidden"]
        assert page.evaluate("() => document.activeElement !== document.body")
        assert page.evaluate(FOCUSED) == "recent-toggle"
    finally:
        page.close()


def test_a_list_stored_before_seven_shows_the_newest_seven(browser, company, books):
    """A list stored by the twelve-line version (#336) shows only its
    newest seven, on the Dashboard too (which adds no line of its own)."""
    page, handled = _open_at(browser, company, "#/customers")
    try:
        _visit(page, handled, "#/")
        # the stored list, owner kept, as the twelve-line version left it
        page.evaluate(
            """() => { const s = JSON.parse(sessionStorage.getItem('slowbooks-open-screens'));
                       s.tabs = Array.from({ length: 12 }, (_, i) => ({ key: `/old-${i}`, label: `Old ${i}`, n: null, load: 'old' }));
                       sessionStorage.setItem('slowbooks-open-screens', JSON.stringify(s)); }"""
        )
        _reload(page, handled)
        keys = [t["key"] for t in _tabs(page)]
        assert keys == [f"/old-{i}" for i in range(11, 4, -1)], keys
    finally:
        page.close()
