"""The keyboard in dialogs and on the page, in playwright's Chromium on the
bakery's books of tests/test_theme_contrast.py (2.22.0 gate, round 2):

- NEW-25: inside a dialog, Tab and Shift+Tab are moved in script through
  every control, wrapping at the ends, so WebKit's Tab (which, under
  macOS's default keyboard setting, skips buttons and links and leaves the
  dialog) reaches "Back to …", Prev/Next and a report's links. The walk is
  checked against the browser's own: the sequence Chromium's native Tab
  takes with the handler removed is the sequence the handler takes, on
  the New Invoice form (date fields and their segments, type-ahead boxes,
  checkboxes, a line table), a drill-down (links in every row, Prev/Next,
  Back to …) and the P&L by Class grid (the focusable scroll region, the
  column picker, a link per cell). A dialog with no date field is walked
  by synthetic Tab events alone, which move no focus by themselves: the
  WebKit case.
- NEW-33: Escape in a date field leaves the field rather than closing the
  dialog; the next Escape closes it.
- NEW-41: the Alt shortcuts go by the key's position (e.code), so a Mac's
  Option-D ("∂") still toggles the theme; inside a field no Alt letter
  fires, on any platform (W-6), and over a form being filled in Alt+N, P,
  Q and H do nothing from any of its controls, as Back does; ⌘K finds
  like Ctrl+K; Ctrl+Alt (AltGr) is left alone.
- NEW-30: the toolbar's Back, enabled only while an app page is behind,
  with Alt+← (⌘[ on a Mac); a reload keeps it.
- NEW-32: no dialog opens with focus on Void or Delete; a dialog whose
  first control is one takes focus itself.
- NEW-38: the customer page's invoice rows carry a real link, reachable
  by the keyboard.
- NEW-37: a line's Class cell is named for its line ("Class, line 2").
- NEW-39: the drill-down's "n of m" is a status, read with the account.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import re
import sys

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_classes import classed as _classed  # noqa: E402,F401
from tests.test_browser_grid import (  # noqa: E402,F401  (eleven classes)
    BY_CLASS_URL,
    divisions_fixture,
)
from tests.test_browser_report_views import SEPT, _open_at  # noqa: E402
from tests.test_dialog_contrast import (  # noqa: E402,F401  (the dialogs)
    BANKING,
    NONPROFIT_DIALOGS,
    NONPROFIT_PAGES,
    PEOPLE,
    PURCHASES,
    REPORTS,
    SALES,
    SETTINGS,
    nonprofit,
)
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    HOST_PLATFORM,
    REDIRECTS,
    _open,
    _platform,
    _theme,
    _visit,
    browser_fixture,
    company_fixture,
    contrast,
    settle,
)


@pytest.fixture(name="classed")
def classed_fixture(_classed):  # noqa: F811  (the fixture, by its name)
    return _classed


MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TITLE = "() => document.getElementById('modal-title').textContent"

# Every element of the dialog numbered, so a focus can be named exactly.
NUMBER = """() => { const m = document.getElementById('modal');
    [...m.querySelectorAll('*')].forEach((el, i) => { el.dataset.tabProbe = String(i); }); }"""
# Where focus is, as "tag#number", or null once it has left the dialog.
WHERE = """() => { const a = document.activeElement, m = document.getElementById('modal');
    return a && a !== m && m.contains(a) ? `${a.tagName.toLowerCase()}#${a.dataset.tabProbe}` : null; }"""
# The first and the last control of the dialog's body take focus
FIRST = "() => { _tabStops(document.getElementById('modal-body'), null)[0].focus(); }"
LAST = "() => { _tabStops(document.getElementById('modal-body'), null).pop().focus(); }"
CLOSE_X = (
    "() => `button#${document.getElementById('modal-close-btn').dataset.tabProbe}`"
)
HANDLER_OFF = "() => document.removeEventListener('keydown', modalKeydown)"
HANDLER_ON = "() => document.addEventListener('keydown', modalKeydown)"
SYNTHETIC_TAB = """(shift) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'Tab', code: 'Tab', shiftKey: !!shift, bubbles: true, cancelable: true }))"""


def _account(company, number):
    [a] = [
        x for x in company.get("/api/accounts").json() if x["account_number"] == number
    ]
    return a


def _open_dialog(page, handled, call, ready):
    page.evaluate(f"async () => {{ await {call}; }}")
    page.wait_for_function(MODAL_SHOWN, timeout=5000)
    page.wait_for_selector(ready)
    settle(page, handled)


def _press(page, shift=False, synthetic=False):
    """One Tab: a real key (the browser's Tab and the handler together, as
    on Chromium) or a synthetic keydown alone, which moves no focus by
    itself, so the handler is doing all of it (WebKit's case)."""
    if synthetic:
        page.evaluate(SYNTHETIC_TAB, shift)
        page.wait_for_timeout(5)  # a date field's check runs a tick later
    else:
        page.keyboard.press("Shift+Tab" if shift else "Tab")


def _native_walk(page, start, shift=False, cap=600):
    """The sequence the browser's own Tab takes from `start`, with the
    app's handler out of the way, until it leaves the dialog."""
    page.evaluate(HANDLER_OFF)
    try:
        page.evaluate(start)
        seq = [page.evaluate(WHERE)]
        for _ in range(cap):
            page.keyboard.press("Shift+Tab" if shift else "Tab")
            at = page.evaluate(WHERE)
            if at is None:
                break
            seq.append(at)
        else:
            raise AssertionError("the native walk never left the dialog")
    finally:
        page.evaluate(HANDLER_ON)
    return seq


def _walk(page, start, steps, shift=False, synthetic=False):
    """The sequence the handler takes from `start`, `steps` presses on."""
    page.evaluate(start)
    seq = [page.evaluate(WHERE)]
    for _ in range(steps):
        _press(page, shift, synthetic)
        seq.append(page.evaluate(WHERE))
    return seq


DESCRIBE = """(seq) => seq.map(s => { if (!s) return s;
    const el = document.querySelector(`[data-tab-probe="${s.split('#')[1]}"]`);
    return `${s} ${el.type || ''} .${el.className} tabindex=${el.tabIndex} "${(el.getAttribute('aria-label') || el.textContent).trim().slice(0, 30)}"`; })"""


def _same(page, mine, native):
    """The two sequences, element for element, said in full where not."""
    if mine == native:
        return
    i = next(k for k, (a, b) in enumerate(zip(mine, native)) if a != b)
    raise AssertionError(
        f"at {i}: the handler's walk {page.evaluate(DESCRIBE, mine[max(0, i - 2) : i + 3])}"
        f" vs the browser's {page.evaluate(DESCRIBE, native[max(0, i - 2) : i + 3])}"
    )


def _check_walk(page, handled, reverse=True, synthetic=False):
    """Forward from the body's first control, the handler's sequence is
    the browser's own, then wraps to the dialog's Close ×; backward from
    the body's last control, the same, then wraps from the × to the last."""
    page.evaluate(NUMBER)
    native = _native_walk(page, FIRST)
    assert len(native) >= 3, native
    _same(page, _walk(page, FIRST, len(native) - 1, synthetic=synthetic), native)
    close = page.evaluate(CLOSE_X)
    _press(page, synthetic=synthetic)
    assert page.evaluate(WHERE) == close
    if not reverse:
        return native
    back_native = _native_walk(page, LAST, shift=True)
    assert back_native[-1] == close, back_native
    _same(
        page,
        _walk(page, LAST, len(back_native) - 1, shift=True, synthetic=synthetic),
        back_native,
    )
    _press(page, shift=True, synthetic=synthetic)
    assert page.evaluate(WHERE) == back_native[0]
    return native


# ── NEW-25: Tab inside a dialog ───────────────────────────────────────────


def test_tab_walks_the_new_invoice_form_as_the_browser_does(browser, company, books):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        native = _check_walk(page, handled)
        kinds = page.evaluate(
            """() => [...document.querySelectorAll('#modal [data-tab-probe]')]
                .filter(el => el.matches('input[type=date], input[type=checkbox], [role=combobox], textarea, select:not(.cbx-select)'))
                .map(el => `${el.tagName.toLowerCase()}#${el.dataset.tabProbe}`)"""
        )
        # the walk took in the date fields (their segments, so each more
        # than once), the type-ahead boxes, a checkbox, the plain selects,
        # the notes; not the selects the type-ahead boxes stand in for
        assert all(k in native for k in kinds), [k for k in kinds if k not in native]
        hidden = page.evaluate(
            "() => [...document.querySelectorAll('#modal select.cbx-select')].map(el => `select#${el.dataset.tabProbe}`)"
        )
        assert hidden and not any(h in native for h in hidden)
        dates = page.evaluate(
            "() => [...document.querySelectorAll('#modal input[type=date]')].map(el => `input#${el.dataset.tabProbe}`)"
        )
        assert dates and all(native.count(d) >= 3 for d in dates), [
            (d, native.count(d)) for d in dates
        ]
    finally:
        page.close()


def test_tab_walks_a_drill_down_and_its_links_as_the_browser_does(
    browser, company, books
):
    cogs = _account(company, "5000")
    url = (
        f"#/reports/account-transactions?account_id={cogs['id']}"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}&from=trial-balance"
    )
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#drilldown-body table")
        native = _check_walk(page, handled)
        named = page.evaluate(
            """() => [...document.querySelectorAll('#modal [data-tab-probe]')]
                .filter(el => el.matches('#drill-prev, #drill-next, [data-back-to], #report-save-btn, #drilldown-body a[href], #report-custom-start'))
                .map(el => `${el.tagName.toLowerCase()}#${el.dataset.tabProbe}`)"""
        )
        assert len(named) > 5 and all(n in native for n in named), [
            n for n in named if n not in native
        ]
    finally:
        page.close()


def test_tab_walks_the_grid_and_its_cells_as_the_browser_does(
    browser, company, books, divisions
):
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        native = _check_walk(page, handled, reverse=False)
        named = page.evaluate(
            """() => [...document.querySelectorAll('#modal [data-tab-probe]')]
                .filter(el => el.matches('#grid-scroll, #grid-toolbar [role=combobox], #grid-toolbar button, .pivot-grid a[href], #grid-choose-btn, #grid-empty'))
                .map(el => `${el.tagName.toLowerCase()}#${el.dataset.tabProbe}`)"""
        )
        assert len(named) > 20 and all(n in native for n in named), [
            n for n in named if n not in native
        ]
        # the folded chooser's boxes are not stops; opened, they are
        folded = page.evaluate(
            "() => [...document.querySelectorAll('#grid-chooser input')].map(el => `input#${el.dataset.tabProbe}`)"
        )
        assert folded and not any(f in native for f in folded)
        page.evaluate("() => ReportsPage.toggleChooser()")
        page.wait_for_timeout(50)
        page.evaluate(NUMBER)
        opened = _native_walk(page, FIRST)
        assert _walk(page, FIRST, len(opened) - 1) == opened
        folded = page.evaluate(
            "() => [...document.querySelectorAll('#grid-chooser input')].map(el => `input#${el.dataset.tabProbe}`)"
        )
        assert all(f in opened for f in folded)
    finally:
        page.close()


def test_synthetic_tabs_alone_walk_a_dialog_the_webkit_way(browser, company, books):
    """A keydown the browser does nothing with (WebKit's, under the Mac's
    default setting, for a button or a link) still moves focus along the
    same sequence: the handler does it."""
    cogs = _account(company, "5000")
    page, handled = _open_at(
        browser,
        company,
        f"#/reports/account-transactions?account_id={cogs['id']}&from=trial-balance",
    )
    try:
        page.wait_for_selector("#drilldown-body table")
        assert (
            page.evaluate(
                "() => document.getElementById('report-custom-start').checkVisibility()"
            )
            is False
        )
        _check_walk(page, handled, synthetic=True)
        # a Tab a control has claimed is left to it
        page.evaluate(FIRST)
        at = page.evaluate(WHERE)
        page.evaluate(
            """() => { const stop = (e) => { if (e.key === 'Tab') e.preventDefault(); };
                document.activeElement.addEventListener('keydown', stop, { once: true }); }"""
        )
        page.evaluate(SYNTHETIC_TAB, False)
        assert page.evaluate(WHERE) == at
        # from the dialog itself (focused by script) Tab goes to its first
        # control, Shift+Tab to its last
        page.evaluate("() => document.getElementById('modal').focus()")
        page.evaluate(SYNTHETIC_TAB, False)
        assert page.evaluate("() => document.activeElement.id") == "modal-close-btn"
        page.evaluate("() => document.getElementById('modal').focus()")
        page.evaluate(SYNTHETIC_TAB, True)
        assert (
            page.evaluate("() => document.activeElement.textContent.trim()") == "Close"
        )
    finally:
        page.close()


# ── NEW-33: Escape in a date field ────────────────────────────────────────


def test_escape_in_a_date_field_leaves_the_field_and_the_next_closes_the_dialog(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.fill("#modal textarea", "a note worth keeping")
        page.focus("#modal input[type=date]")
        # the key WebKit sends on after closing the calendar (a real one
        # here, with no calendar open, reaches the page the same way)
        page.keyboard.press("Escape")
        assert page.evaluate(MODAL_SHOWN)
        assert page.evaluate("() => document.activeElement.id") == "modal"
        assert page.input_value("#modal textarea") == "a note worth keeping"
        # as a synthetic key at the field (WebKit's, after its calendar)
        page.focus("#modal input[type=date]")
        page.evaluate(
            """() => document.activeElement.dispatchEvent(new KeyboardEvent('keydown',
                { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }))"""
        )
        assert page.evaluate(MODAL_SHOWN)
        assert page.evaluate("() => document.activeElement.id") == "modal"
        # the next Escape closes the dialog
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        # any other field: one Escape closes it, as before
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.focus("#modal textarea")
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
    finally:
        page.close()


# ── NEW-41: the shortcuts by key position; ⌘K ────────────────────────────

KEY = """([key, code, mods]) => document.body.dispatchEvent(new KeyboardEvent('keydown',
    { key, code, bubbles: true, cancelable: true, ...mods }))"""
THEME = "() => document.documentElement.getAttribute('data-theme') || 'light'"


def test_the_alt_shortcuts_go_by_the_keys_position_and_cmd_k_finds(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/customers")
        was = page.evaluate(THEME)
        # Option-D on a Mac: the character is "∂", the key is still KeyD
        page.evaluate(KEY, ["∂", "KeyD", {"altKey": True}])
        assert page.evaluate(THEME) != was
        page.evaluate(KEY, ["∂", "KeyD", {"altKey": True}])
        assert page.evaluate(THEME) == was
        # a real Alt+D too
        page.keyboard.press("Alt+KeyD")
        assert page.evaluate(THEME) != was
        page.keyboard.press("Alt+KeyD")
        assert page.evaluate(THEME) == was
        # Ctrl+Alt (AltGr on some layouts) types a character, and is left alone
        page.evaluate(KEY, ["đ", "KeyD", {"altKey": True, "ctrlKey": True}])
        assert page.evaluate(THEME) == was
        # Option-H ("˙") goes home
        page.evaluate(KEY, ["˙", "KeyH", {"altKey": True}])
        page.wait_for_function("() => location.hash === '#/'")
        settle(page, handled)
        # Option-N ("˜") opens a new invoice
        page.evaluate(KEY, ["˜", "KeyN", {"altKey": True}])
        page.wait_for_function(MODAL_SHOWN)
        assert page.evaluate(TITLE).startswith("New Invoice")
        page.evaluate("() => closeModal()")
        page.wait_for_function(f"() => !({MODAL_SHOWN})()")
        # Option-B ("∫") enters a bill, Option-E (a dead key) an expense (#264)
        page.evaluate(KEY, ["∫", "KeyB", {"altKey": True}])
        page.wait_for_function(MODAL_SHOWN)
        assert page.evaluate(TITLE) == "Enter Bill"
        page.evaluate("() => closeModal()")
        page.wait_for_function(f"() => !({MODAL_SHOWN})()")
        page.evaluate(KEY, ["Dead", "KeyE", {"altKey": True}])
        page.wait_for_function(MODAL_SHOWN)
        assert page.evaluate(TITLE) == "Enter Expense"
        page.evaluate("() => closeModal()")
        page.wait_for_function(f"() => !({MODAL_SHOWN})()")
        # the toolbar button says so: a screen reader hears Alt+B on Enter Bill
        _visit(page, handled, "#/bills")
        said = page.evaluate(
            """() => { const b = [...document.querySelectorAll('#page-content button')]
                .find(b => b.textContent.trim() === '+ Enter Bill');
                return b && { keys: b.getAttribute('aria-keyshortcuts'), title: b.title }; }"""
        )
        assert said == {"keys": "Alt+B", "title": "Enter Bill (Alt+B)"}, said
        # ⌘K finds, as Ctrl+K does
        page.evaluate("() => document.body.focus()")
        page.evaluate(KEY, ["k", "KeyK", {"metaKey": True}])
        assert page.evaluate("() => document.activeElement.id") == "global-search"
        page.evaluate("() => document.body.focus()")
        page.keyboard.press("Control+KeyK")
        assert page.evaluate("() => document.activeElement.id") == "global-search"
    finally:
        page.close()


# ── NEW-30: Back, within the app ──────────────────────────────────────────

BACK = """() => { const b = document.getElementById('back-btn');
    return { disabled: b.disabled, name: b.getAttribute('aria-label'), title: b.title,
             keys: b.getAttribute('aria-keyshortcuts') }; }"""
AT = "(h) => location.hash === h"


def _no_splash(page):
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def _sidebar(page, handled, href):
    page.click(f'#sidebar a[href="{href}"]')
    page.wait_for_function(AT, arg=href)
    settle(page, handled)


def test_back_goes_back_within_the_app_while_there_is_somewhere_to_go(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        # the session's first page: named, with its shortcut, nowhere to go
        assert page.evaluate(BACK) == {
            "disabled": True,
            "name": "Back",
            "title": "Back (Alt+←)",
            "keys": "Alt+ArrowLeft",
        }
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_timeout(100)
        assert page.evaluate("location.hash") == "#/"
        # a sidebar link: an entry the browser made, which Back knows about
        _sidebar(page, handled, "#/customers")
        assert page.evaluate(BACK)["disabled"] is False
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg="#/")
        settle(page, handled)
        assert page.evaluate(BACK)["disabled"] is True
        # the button, and Forward's entry still ahead
        _sidebar(page, handled, "#/vendors")
        page.click("#back-btn")
        page.wait_for_function(AT, arg="#/")
        settle(page, handled)
        assert page.evaluate(BACK)["disabled"] is True
        # a report pushed from the Report Center: Back closes it and returns
        _sidebar(page, handled, "#/reports")
        page.evaluate(
            """() => document.querySelector('#page-content a.card-link[data-row-key="report:profit-loss"]').click()"""
        )
        page.wait_for_selector("#report-content table")
        settle(page, handled)
        assert page.evaluate("location.hash").startswith("#/reports/profit-loss?")
        assert page.evaluate(BACK)["disabled"] is False
        page.click("#back-btn")
        page.wait_for_function(AT, arg="#/reports")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        settle(page, handled)
        assert page.evaluate(BACK)["disabled"] is False  # the dashboard is behind
        # a reload keeps the entry's Back
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        _no_splash(page)
        settle(page, handled)
        assert page.evaluate(BACK)["disabled"] is False
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg="#/")
        settle(page, handled)
        assert page.evaluate(BACK)["disabled"] is True
        # on a Mac: ⌘[, said on the button; Alt+← is the Mac's word-left
        page.evaluate("""() => { Object.defineProperty(Navigator.prototype, 'platform',
                { get: () => 'MacIntel', configurable: true }); App.labelBack(); }""")
        assert page.evaluate(BACK)["title"] == "Back (⌘[)"
        assert page.evaluate(BACK)["keys"] == "Meta+["
        _sidebar(page, handled, "#/customers")
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_timeout(100)
        assert page.evaluate("location.hash") == "#/customers"
        page.keyboard.press("Meta+BracketLeft")
        page.wait_for_function(AT, arg="#/")
    finally:
        page.close()


# ── NEW-32: no dialog opens on Void or Delete ────────────────────────────

# What has focus: its tag, its words, and whether it undoes something
FOCUSED = """() => { const a = document.activeElement;
    return { id: a.id || '', tag: a.tagName.toLowerCase(), text: a.textContent.trim().slice(0, 40),
             destructive: a.hasAttribute('data-destructive') }; }"""
DESTRUCTIVE = re.compile(r"^(void|delete|remove)\b", re.I)


def _opens_safely(page, handled, books, groups):
    """Every dialog of `groups` opened as the app opens it: the control
    focused is never one that undoes. Returns what was focused per dialog."""
    focused, offenders, unopened = {}, [], []
    for route, openers in groups:
        _visit(page, handled, route.format(**books))
        for opener in openers:
            call = opener.format(**books)
            page.evaluate("() => closeModal()")
            try:
                page.evaluate(f"async () => {{ await {call}; }}")
                page.wait_for_function(MODAL_SHOWN, timeout=5000)
            except (
                Exception
            ) as exc:  # the contrast sweep reports a dialog that won't open
                unopened.append((call, str(exc)[:120]))
                continue
            settle(page, handled)
            at = page.evaluate(FOCUSED)
            focused[call] = at
            if at["destructive"] or DESTRUCTIVE.match(at["text"]):
                offenders.append((call, at))
        page.evaluate("() => closeModal()")
    assert not offenders, offenders
    return focused, unopened


def test_no_dialog_opens_with_focus_on_void_or_delete(browser, company, books):
    groups = [
        ("#/invoices", SALES),
        ("#/bills", PURCHASES),
        ("#/banking/{checking}", BANKING),
        ("#/reports", REPORTS),
        ("#/employees", PEOPLE),
        ("#/settings", SETTINGS),
    ]
    page, handled = _open(browser, company)
    confirms = []
    page.on("dialog", lambda d: (confirms.append(d.message), d.dismiss()))
    try:
        # two of the three the gate named open on Void (the third, the
        # journal entry's view, has a file input first since 2.22.3, below):
        # the dialog itself takes focus, named by its title, and Return
        # there does nothing
        _visit(page, handled, "#/journal")
        for call in (
            f"DepositsPage.view({books['deposit']})",
            f"JobCostsPage.view({books['job_cost']})",
        ):
            page.evaluate("() => closeModal()")
            page.evaluate(f"async () => {{ await {call}; }}")
            page.wait_for_function(MODAL_SHOWN)
            settle(page, handled)
            assert page.evaluate(FOCUSED)["id"] == "modal", (
                call,
                page.evaluate(FOCUSED),
            )
            assert (
                page.evaluate(
                    "() => document.activeElement.getAttribute('aria-labelledby')"
                )
                == "modal-title"
            )
            assert page.locator("#modal-body [data-destructive]").count() == 1
            page.keyboard.press("Enter")
            page.wait_for_timeout(100)
            assert page.evaluate(MODAL_SHOWN) and not confirms
            # Tab from there: the dialog's Close ×, then Void, then Close
            page.keyboard.press("Tab")
            assert page.evaluate("() => document.activeElement.id") == "modal-close-btn"
        # a dialog whose first control is harmless keeps it
        page.evaluate("() => closeModal()")
        page.evaluate("async () => { await InvoicesPage.showForm(); }")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert page.evaluate(FOCUSED)["id"] != "modal"
        # the journal entry's view (#281): its Attachments block, the same
        # as an expense's, puts the file input ahead of Void, so the view
        # opens there, as the expense's does — harmless, and Void is still
        # never the first stop
        page.evaluate("() => closeModal()")
        page.evaluate(f"async () => {{ await JournalPage.view({books['journal']}); }}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        at = page.evaluate(FOCUSED)
        assert at["id"] == "journal-attach-file" and not at["destructive"], at
        assert page.locator("#modal-body [data-destructive]").count() == 1
        # and every dialog the app opens (the last of them as a read-only
        # sign-in sees it, which is how the sweep leaves the page)
        focused, unopened = _opens_safely(page, handled, books, groups)
        assert len(focused) >= 100, (len(focused), unopened)
    finally:
        page.close()


def test_a_nonprofits_dialogs_open_safely_too(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    page, handled = _open(browser, client)
    try:
        for route in NONPROFIT_PAGES:  # the pages load what their dialogs need
            _visit(page, handled, route.format(**nonprofit))
        focused, unopened = _opens_safely(
            page, handled, nonprofit, [("#/invoices", NONPROFIT_DIALOGS)]
        )
        assert len(focused) == len(NONPROFIT_DIALOGS), unopened
        views = [
            c
            for c in focused
            if re.search(r"(Releases|Allocations|InKind)Page\.view\(", c)
        ]
        assert len(views) == 3, views
        assert all(focused[c]["id"] == "modal" for c in views), {
            c: focused[c] for c in views
        }
    finally:
        page.close()


# ── NEW-38: the customer page's invoice rows are links ───────────────────


def test_the_customer_pages_invoice_rows_open_from_the_keyboard(
    browser, company, books
):
    page, handled = _open_at(browser, company, f"#/customers/{books['customer']}")
    try:
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        links = page.evaluate(
            """() => [...document.querySelectorAll('#modal a[href^="#/invoices/"]')]
                .map(a => [a.getAttribute('href'), a.textContent.trim()])"""
        )
        assert len(links) >= 3, links
        href, text = links[0]
        inv = company.get(f"/api/invoices/{href.rsplit('/', 1)[1]}").json()
        assert text == inv["invoice_number"]  # named by the number
        # Enter on the link opens the invoice at its address; Back returns
        page.focus(f'#modal a[href="{href}"]')
        page.keyboard.press("Enter")
        page.wait_for_function(AT, arg=href)
        page.wait_for_function(
            f"() => document.getElementById('modal-title').textContent.includes('#{inv['invoice_number']}')"
        )
        settle(page, handled)
        page.go_back()
        page.wait_for_function(AT, arg=f"#/customers/{books['customer']}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert page.locator(f'#modal a[href="{href}"]').count() == 1
        # and the link left has the focus back (review: as a report's row)
        assert (
            page.evaluate("() => document.activeElement.dataset.rowKey")
            == f"invoice:{inv['id']}"
        )
        # the row's own click still opens it
        page.click(
            f'#modal a[href="{href}"] >> xpath=ancestor::tr',
            position={"x": 300, "y": 8},
        )
        page.wait_for_function(AT, arg=href)
        page.go_back()
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        # a payment row the same: its date is the link, and opens the payment
        # at its own address; Back returns to the page, focus on the link
        pay = page.locator('#modal a[href^="#/payments/"]').first
        assert pay.count() == 1 and pay.text_content().strip()
        pay_href = pay.get_attribute("href")
        pay.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(AT, arg=pay_href)
        page.wait_for_function(f"() => {TITLE.split('=> ')[1]} === 'Payment Details'")
        settle(page, handled)
        page.go_back()
        page.wait_for_function(AT, arg=f"#/customers/{books['customer']}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert (
            page.evaluate("() => document.activeElement.getAttribute('href')")
            == pay_href
        )
    finally:
        page.close()


# ── NEW-37: a line's Class cell is named for its line ────────────────────

NAMES = "(sel) => [...document.querySelectorAll(sel)].map(el => el.getAttribute('aria-label'))"


def test_a_lines_class_cell_says_its_line(browser, company, books, classed):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/bills")
        _open_dialog(page, handled, "BillsPage.showForm()", "#bill-lines tr")
        assert page.evaluate(NAMES, "#bill-lines .line-function-fund") == [
            "Class, line 1"
        ]
        page.evaluate("() => BillsPage.addLine()")
        settle(page, handled)
        assert page.evaluate(NAMES, "#bill-lines .line-function-fund") == [
            "Class, line 1",
            "Class, line 2",
        ]
        # as a screen reader finds them: one each
        assert page.get_by_label("Class, line 2").count() == 1
        page.evaluate("() => closeModal()")
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.evaluate("() => InvoicesPage.addLine()")
        settle(page, handled)
        assert page.evaluate(NAMES, "#inv-lines .line-class-fund") == [
            "Class, line 1",
            "Class, line 2",
        ]
        page.evaluate("() => closeModal()")
        _visit(page, handled, "#/journal")
        _open_dialog(page, handled, "JournalPage.showForm()", "#je-lines tr")
        assert page.evaluate(NAMES, "#je-lines .je-function-fund") == [
            "Class, line 1",
            "Class, line 2",
        ]
    finally:
        page.close()


def test_a_nonprofits_fund_and_function_cells_say_their_line(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    page, handled = _open(browser, client)
    try:
        _visit(page, handled, "#/journal")
        _open_dialog(page, handled, "JournalPage.showForm()", "#je-lines tr")
        heads = page.evaluate(
            "() => [...document.querySelectorAll('#modal .line-items-table thead th')].map(t => t.textContent.trim())"
        )
        fund = heads[
            heads.index("Function") - 1
        ]  # the class column, by the nonprofit's word
        assert page.evaluate(NAMES, "#je-lines .je-function-fund") == [
            f"{fund}, line 1",
            f"{fund}, line 2",
        ]
        assert page.evaluate(NAMES, "#je-lines select.je-function") == [
            "Function, line 1",
            "Function, line 2",
        ]
    finally:
        page.close()


# ── NEW-39: the drill-down's "n of m" is a status ────────────────────────


def test_the_drill_downs_position_is_a_status_that_names_the_account(
    browser, company, books
):
    checking = _account(company, "1000")
    url = (
        f"#/reports/account-transactions?account_id={checking['id']}"
        f"&start_date={SEPT[0]}&end_date={SEPT[1]}&from=trial-balance"
    )
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#drilldown-body table")
        pos = page.locator("#drill-position")
        assert pos.get_attribute("role") == "status"
        assert pos.get_attribute("aria-live") == "polite"
        where = pos.text_content().strip()
        m = re.fullmatch(r"(\d+) of (\d+): (.+)", where)
        assert m and m.group(3).endswith(checking["name"]), where
        n, total = int(m.group(1)), int(m.group(2))
        assert total > 3

        def step(button):
            was = pos.text_content()
            page.focus(button)
            page.keyboard.press("Enter")
            page.wait_for_function(
                "(was) => document.getElementById('drill-position').textContent !== was",
                arg=was,
            )
            settle(page, handled)
            return pos.text_content().strip()

        # Next, by keyboard: focus stays on Next, the status says where
        after = step("#drill-next")
        m2 = re.fullmatch(r"(\d+) of (\d+): (.+)", after)
        assert m2 and int(m2.group(1)) == n + 1 and int(m2.group(2)) == total, after
        assert page.evaluate("() => document.activeElement.id") == "drill-next"
        assert m2.group(3).split(" - ", 1)[-1] in page.evaluate(TITLE)  # its name
        assert page.get_by_role("status").filter(has_text=after).count() == 1
        # at the last account Next is disabled: Prev takes the focus, so
        # the keyboard keeps its place (and Next again from the first)
        page.select_option(
            "#drill-account",
            page.eval_on_selector_all(
                "#drill-account option", "os => os[os.length - 2].value"
            ),
        )
        settle(page, handled)
        last = step("#drill-next")
        assert last.startswith(f"{total} of {total}: "), last
        assert page.evaluate("() => document.activeElement.id") == "drill-prev"
        assert page.locator("#drill-next").is_disabled()
    finally:
        page.close()


# ── Review, round 2: the Alt letters inside a field ──────────────────────

KEY_AT = """([sel, key, code, mods]) => document.querySelector(sel).dispatchEvent(new KeyboardEvent('keydown',
    { key, code, bubbles: true, cancelable: true, ...mods }))"""
MAC = """(on) => { Object.defineProperty(Navigator.prototype, 'platform',
    { get: () => on ? 'MacIntel' : 'Linux x86_64', configurable: true }); App.labelBack(); }"""


ALT_LETTERS = ("Alt+KeyN", "Alt+KeyP", "Alt+KeyQ", "Alt+KeyH", "Alt+KeyD")


def test_inside_a_field_no_alt_letter_is_a_shortcut_on_any_platform(
    browser, company, books
):
    """A Mac's Option types a character in a field ("∂", or a dead key for
    Option-N's tilde), so the plain letter alone was let through there as
    the shortcut — and the plain letter is what Windows and Linux send for
    Alt+N, so on Windows Alt+N in a bill's Description opened New Invoice
    in its place and the bill was gone (W-6). Inside an input, a textarea,
    a select or an editable region no Alt letter fires, on any platform;
    outside a field the key's position is enough; Shift makes it another
    shortcut; Ctrl+K (⌘K) finds from anywhere, the search box included."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.fill("#modal textarea", "a note being typed")
        page.focus("#modal textarea")
        was = page.evaluate(THEME)
        # Option-N in the Notes: the tilde's dead key, not a new invoice
        page.evaluate(KEY_AT, ["#modal textarea", "Dead", "KeyN", {"altKey": True}])
        page.wait_for_timeout(100)
        assert page.input_value("#modal textarea") == "a note being typed"
        assert page.evaluate("() => document.activeElement.tagName") == "TEXTAREA"
        # Option-D in the Notes types "∂": no theme change
        page.evaluate(KEY_AT, ["#modal textarea", "∂", "KeyD", {"altKey": True}])
        assert page.evaluate(THEME) == was
        # Shift+Alt+D anywhere is not the shortcut either
        page.evaluate(KEY, ["D", "KeyD", {"altKey": True, "shiftKey": True}])
        assert page.evaluate(THEME) == was
        # the plain letter with Alt, as Windows and Linux send it, in the
        # Notes: nothing opens, nothing moves, and the note stays (W-6) —
        # as an event, and from the keyboard
        page.evaluate(KEY_AT, ["#modal textarea", "n", "KeyN", {"altKey": True}])
        page.focus("#modal textarea")
        for keys in ALT_LETTERS:
            page.keyboard.press(keys)
        page.wait_for_timeout(150)
        settle(page, handled)
        assert page.input_value("#modal textarea") == "a note being typed"
        assert page.evaluate(TITLE).startswith("New Invoice")
        assert page.evaluate("location.hash") == "#/invoices"
        assert page.evaluate(THEME) == was
        # in a line's Description, where the Windows gate lost its bill
        page.fill("#inv-lines .line-desc", "half a line")
        page.focus("#inv-lines .line-desc")
        for keys in ALT_LETTERS:
            page.keyboard.press(keys)
        page.wait_for_timeout(150)
        settle(page, handled)
        assert page.input_value("#inv-lines .line-desc") == "half a line"
        assert page.input_value("#modal textarea") == "a note being typed"
        assert page.evaluate("location.hash") == "#/invoices"
        assert page.evaluate(THEME) == was
        # Ctrl+K over the open form does nothing (NEW-45: the dialog is
        # modal; the search lies under its overlay): focus stays in the field
        page.keyboard.press("Control+KeyK")
        assert page.evaluate("() => document.activeElement.id") != "global-search"
        assert page.input_value("#inv-lines .line-desc") == "half a line"
        assert page.evaluate(MODAL_SHOWN)
        page.evaluate("() => closeModal()")
        # the search box: Alt+N opens nothing, Ctrl+K keeps it
        page.focus("#global-search")
        page.keyboard.press("Alt+KeyN")
        page.wait_for_timeout(100)
        assert not page.evaluate(MODAL_SHOWN)
        page.keyboard.press("Control+KeyK")
        assert page.evaluate("() => document.activeElement.id") == "global-search"
        # a select: no theme change, no going home
        _visit(page, handled, "#/classes")
        page.wait_for_selector("#classes-period")
        here = page.evaluate("location.hash")
        page.focus("#classes-period")
        page.keyboard.press("Alt+KeyD")
        page.keyboard.press("Alt+KeyH")
        page.wait_for_timeout(100)
        assert page.evaluate(THEME) == was
        assert page.evaluate("location.hash") == here
        # outside a field the key's position is enough, dead key or not
        page.evaluate("() => document.body.focus()")
        page.evaluate(KEY, ["Dead", "KeyN", {"altKey": True}])
        page.wait_for_function(MODAL_SHOWN)
        assert page.evaluate(TITLE).startswith("New Invoice")
        page.evaluate("() => closeModal()")
        # on a Mac, ⌘⌥[ is not Back; ⌘[ is
        _sidebar(page, handled, "#/customers")
        page.evaluate(MAC, True)
        page.evaluate(KEY, ["[", "BracketLeft", {"metaKey": True, "altKey": True}])
        page.wait_for_timeout(100)
        assert page.evaluate("location.hash") == "#/customers"
        page.evaluate(KEY, ["[", "BracketLeft", {"metaKey": True}])
        page.wait_for_function(AT, arg=here)  # the page before Customers
        page.evaluate(MAC, False)
    finally:
        page.close()


LEAVING = ("Alt+KeyN", "Alt+KeyP", "Alt+KeyQ", "Alt+KeyH")
FORM_CONTROLS = (
    "#modal-body button[type=submit]",
    "#modal-close-btn",
    "#modal-body button:has-text('Add Line')",
)


def test_over_a_form_alt_n_p_q_h_do_nothing_from_any_of_its_controls(
    browser, company, books
):
    """Inside a field no Alt letter fires, but one Tab away — on the form's
    Save button, its ×, Add Line — Alt+N, P, Q or H opened another form
    in its place and the typed one was gone: the loss W-6 describes
    (round-3 review). Over a form being filled in (App.editingDialog, as
    Back is) the four that leave it do nothing from any of its controls;
    Alt+D, the theme, still fires there; over an addressed dialog (a
    report) and from the page they fire as before."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.fill("#modal textarea", "a note being typed")
        page.fill("#inv-lines .line-desc", "half a line")
        assert page.evaluate("() => App.editingDialog()")
        was = page.evaluate(THEME)
        for sel in FORM_CONTROLS:
            page.focus(sel)
            assert page.evaluate("() => document.activeElement.tagName") == "BUTTON"
            for keys in LEAVING:
                page.keyboard.press(keys)
            page.wait_for_timeout(150)
            settle(page, handled)
            assert page.evaluate(MODAL_SHOWN), sel
            assert page.evaluate(TITLE).startswith("New Invoice"), sel
            assert page.input_value("#modal textarea") == "a note being typed", sel
            assert page.input_value("#inv-lines .line-desc") == "half a line", sel
            assert page.evaluate("location.hash") == "#/invoices", sel
            assert page.evaluate(THEME) == was, sel
        # the theme still switches from the Save button, and the form stays
        page.focus("#modal-body button[type=submit]")
        page.keyboard.press("Alt+KeyD")
        assert page.evaluate(THEME) != was
        page.keyboard.press("Alt+KeyD")
        assert page.evaluate(THEME) == was
        assert page.input_value("#modal textarea") == "a note being typed"
        assert page.evaluate(TITLE).startswith("New Invoice")
        page.evaluate("() => closeModal()")
        # over a report view, an addressed dialog, Alt+N opens New Invoice
        _visit(
            page,
            handled,
            f"#/reports/profit-loss?start_date={SEPT[0]}&end_date={SEPT[1]}",
        )
        page.wait_for_selector("#report-content table")
        page.focus("#modal-close-btn")
        assert not page.evaluate("() => App.editingDialog()")
        page.keyboard.press("Alt+KeyN")
        page.wait_for_function(f"() => ({TITLE})().startsWith('New Invoice')")
        page.evaluate("() => closeModal()")
        # and from the page, Alt+H goes home
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.evaluate("() => document.body.focus()")
        page.keyboard.press("Alt+KeyH")
        page.wait_for_function(AT, arg="#/")
    finally:
        page.close()


# ── Review, round 2: Tab into a textarea keeps the caret ─────────────────

BEFORE = """(sel) => { const m = document.getElementById('modal'), el = document.querySelector(sel);
    const stops = _tabStops(m, null); stops[stops.indexOf(el) - 1].focus(); }"""
SELECTION = "(sel) => { const el = document.querySelector(sel); return [el.selectionStart, el.selectionEnd, el.value.length]; }"


def test_tab_into_a_textarea_keeps_the_caret_and_into_a_text_box_selects(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/invoices")
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.fill("#modal textarea", "two lines\nof notes")
        page.fill("#modal .line-desc", "Rounding probe")
        # the handler's Tab into the notes: no selection (the browser's own
        # Tab selects nothing in a textarea, and a note must not vanish)
        page.evaluate(BEFORE, "#modal textarea")
        page.evaluate(SYNTHETIC_TAB, False)
        assert page.evaluate("() => document.activeElement.tagName") == "TEXTAREA"
        start, end, _ = page.evaluate(SELECTION, "#modal textarea")
        assert start == end
        # into a text box, its words selected, as the browser's Tab has it
        page.evaluate(BEFORE, "#modal .line-desc")
        page.evaluate(SYNTHETIC_TAB, False)
        assert page.evaluate("() => document.activeElement.className") == "line-desc"
        assert page.evaluate(SELECTION, "#modal .line-desc") == [0, 14, 14]
    finally:
        page.close()


# ── Review, round 2: Back is inert over a form ───────────────────────────

AT_POINT = """() => { const b = document.getElementById('back-btn'), r = b.getBoundingClientRect();
    return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === b; }"""


def test_back_is_inert_over_a_form_but_live_over_an_addressed_dialog(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/invoices")
        assert page.evaluate(AT_POINT) and page.evaluate("() => App.backAllowed()")
        # a plain form over the page: the button is under the overlay, the
        # chord does nothing, and the note typed stays
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        page.fill("#modal textarea", "half typed")
        assert not page.evaluate(AT_POINT)
        assert page.evaluate("() => App.editingDialog() && !App.backAllowed()")
        assert not page.evaluate(BACK)["disabled"]  # somewhere to go, still
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_timeout(150)
        assert page.evaluate("location.hash") == "#/invoices"
        assert page.evaluate(MODAL_SHOWN)
        assert page.input_value("#modal textarea") == "half typed"
        page.evaluate(MAC, True)
        page.keyboard.press("Meta+BracketLeft")
        page.wait_for_timeout(150)
        assert page.evaluate("location.hash") == "#/invoices" and page.evaluate(
            MODAL_SHOWN
        )
        page.evaluate(MAC, False)
        # the form closed, Back is back
        page.evaluate("() => closeModal()")
        assert page.evaluate(AT_POINT) and page.evaluate("() => App.backAllowed()")
        # a report view has its own address: the button is above the
        # overlay and takes the report back to the Report Center
        _sidebar(page, handled, "#/reports")
        page.evaluate(
            """() => document.querySelector('#page-content a.card-link[data-row-key="report:profit-loss"]').click()"""
        )
        page.wait_for_selector("#report-content table")
        settle(page, handled)
        assert page.evaluate(AT_POINT) and not page.evaluate(
            "() => App.editingDialog()"
        )
        page.click("#back-btn")
        page.wait_for_function(AT, arg="#/reports")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        # a customer's page has one too; a form opened over it has none
        _visit(page, handled, f"#/customers/{books['customer']}")
        page.wait_for_function(MODAL_SHOWN)
        assert page.evaluate(AT_POINT)
        _open_dialog(
            page, handled, f"CustomersPage.showForm({books['customer']})", "#modal form"
        )
        assert not page.evaluate(AT_POINT) and page.evaluate(
            "() => App.editingDialog()"
        )
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_timeout(150)
        assert page.evaluate("location.hash") == f"#/customers/{books['customer']}"
        assert page.evaluate(MODAL_SHOWN)
        page.evaluate("() => closeModal()")
        # dismissing the form dismissed the page it stood in for (one modal):
        # the list's address is on the bar (NEW-23); the page again, then a
        # document at its address: live, and Back returns to the page before
        assert page.evaluate("location.hash") == "#/customers"
        _visit(page, handled, f"#/customers/{books['customer']}")
        page.wait_for_function(MODAL_SHOWN)
        _visit(page, handled, f"#/invoices/{books['sent']}")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        assert page.evaluate(AT_POINT) and page.evaluate("() => App.backAllowed()")
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg=f"#/customers/{books['customer']}")
    finally:
        page.close()


# ── Review, round 2: the button is never lit with nowhere to go ──────────

CONSISTENT = "() => document.getElementById('back-btn').disabled === !App.canGoBack()"


def test_a_replaced_address_keeps_the_entrys_state_and_back_follows(
    browser, company, books
):
    page, handled = _open(browser, company)
    page.on("dialog", lambda d: d.dismiss())
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/reports")
        # a report nobody registered: the address falls back to the Report
        # Center's, and the entry keeps where it was pushed from
        _visit(page, handled, "#/reports/nonsense")
        page.wait_for_function(AT, arg="#/reports")
        assert page.evaluate("() => history.state.from") == "#/reports"
        assert page.evaluate("() => App.canGoBack()") and page.evaluate(CONSISTENT)
        # the Settings leave guard, declined: the address put back keeps the
        # entry's state, and the button follows what is behind
        _sidebar(page, handled, "#/settings")
        assert page.evaluate(CONSISTENT)
        dirty = page.evaluate(
            """() => { const i = document.querySelector('#page-content input[type=text], #page-content input:not([type])');
                i.value += ' x'; i.dispatchEvent(new Event('input', { bubbles: true })); return SettingsPage.isDirty(); }"""
        )
        assert dirty
        page.click('#sidebar a[href="#/customers"]')  # the confirm is declined
        page.wait_for_function(AT, arg="#/settings")
        settle(page, handled)
        assert page.evaluate("() => SettingsPage.isDirty()")
        assert page.evaluate(CONSISTENT)
    finally:
        page.close()


def test_the_first_page_at_a_report_that_cannot_open_has_nowhere_to_go(
    browser, company, books
):
    page, handled = _open_at(browser, company, "#/reports/nonsense")
    try:
        page.wait_for_function(AT, arg="#/reports")
        settle(page, handled)
        assert page.evaluate("() => history.state.from") is None
        assert not page.evaluate("() => App.canGoBack()") and page.evaluate(CONSISTENT)
    finally:
        page.close()


# ── Review, round 2: a radio group is one stop, as the browser has it ────

RADIOS = """(checked) => { const body = document.getElementById('modal-body');
    body.insertAdjacentHTML('afterbegin', `<fieldset id="rg"><legend>Choice</legend>
        <label><input type="radio" name="rg" value="a"> a</label>
        <label><input type="radio" name="rg" value="b"> b</label>
        <label><input type="radio" name="rg" value="c"> c</label></fieldset>`);
    if (checked) body.querySelector(`#rg input[value="${checked}"]`).checked = true; }"""


def test_a_radio_group_is_one_stop_as_the_browser_has_it(browser, company, books):
    """None checked: Tab and Shift+Tab land on the group's first radio, and
    leave the group from any of its own; one checked: that one alone. The
    walk is checked against Chromium's native Tab, as the other dialogs."""
    page, handled = _open(browser, company)
    try:
        _visit(page, handled, "#/journal")
        _open_dialog(
            page, handled, f"JournalPage.view({books['journal']})", "#modal-body"
        )
        page.evaluate(RADIOS, None)
        native = _check_walk(page, handled)
        radios = page.evaluate(
            "() => [...document.querySelectorAll('#rg input')].map(el => `input#${el.dataset.tabProbe}`)"
        )
        assert radios[0] in native and not any(r in native for r in radios[1:]), native
        # from the group's second radio (a click lands there) Tab leaves the group
        page.evaluate("() => document.querySelector('#rg input[value=\"b\"]').focus()")
        page.evaluate(SYNTHETIC_TAB, False)
        assert page.evaluate(WHERE) not in radios
        # one checked: that one is the stop
        page.evaluate("() => closeModal()")
        _open_dialog(
            page, handled, f"JournalPage.view({books['journal']})", "#modal-body"
        )
        page.evaluate(RADIOS, "b")
        native = _check_walk(page, handled)
        radios = page.evaluate(
            "() => [...document.querySelectorAll('#rg input')].map(el => `input#${el.dataset.tabProbe}`)"
        )
        assert (
            radios[1] in native and radios[0] not in native and radios[2] not in native
        )
    finally:
        page.close()


# ── Review, round 3: Back is live over every report view ─────────────────

VIEW_STATE = """() => ({ shown: !document.getElementById('modal-overlay').classList.contains('hidden'),
    mark: document.getElementById('modal').dataset.address || null, hash: location.hash,
    allowed: App.backAllowed(), editing: App.editingDialog() })"""


def _views_live(page, handled):
    """Every registered view opened by its own call, as its Report Center
    card does: each that shows a dialog has its address as the dialog's
    mark, and Back live over it. Returns the views shown, and the silent."""
    live, silent = [], []
    for name in page.evaluate("() => Object.keys(ReportsPage._VIEWS)"):
        page.evaluate("() => closeModal()")
        err = page.evaluate(
            "async (n) => { try { await ReportsPage._VIEWS[n].open({}); return null; } catch (e) { return String(e); } }",
            name,
        )
        settle(page, handled)
        state = page.evaluate(VIEW_STATE)
        if not state["shown"]:
            silent.append((name, err))
            continue
        assert state["mark"] == state["hash"], (name, state, err)
        assert state["allowed"] and not state["editing"], (name, state, err)
        live.append(name)
    page.evaluate("() => closeModal()")
    return live, silent


def test_back_is_live_over_every_report_view_the_company_has(browser, company, books):
    """Including the views that put their address on the bar before they
    open (the statement picker, the 1099 summary, the fixed-asset
    reconciliation), where openModal used to clear the mark."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/reports")
        live, silent = _views_live(page, handled)
        for name in (
            "profit-loss",
            "customer-statement",
            "1099-summary",
            "fixed-asset-reconciliation",
            "profit-loss-by-class",
            "general-ledger",
        ):
            assert name in live, (name, silent)
        assert len(live) > 10, (live, silent)
    finally:
        page.close()


def test_back_is_live_over_a_nonprofits_report_views_too(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    page, handled = _open(browser, client)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/reports")
        live, silent = _views_live(page, handled)
        assert "giving-statements" in live, (live, silent)
    finally:
        page.close()


# ── Review, round 3: a declined leave from Settings ──────────────────────

EDIT = """() => { const i = document.querySelector('#page-content input[type=text], #page-content input:not([type])');
    i.value += ' x'; i.dispatchEvent(new Event('input', { bubbles: true })); return i.value; }"""
VALUE = "() => document.querySelector('#page-content input[type=text], #page-content input:not([type])').value"


def test_a_declined_leave_from_settings_stays_on_its_entry_and_back_goes_behind(
    browser, company, books
):
    page, handled = _open(browser, company)
    answer, confirms = {"accept": False}, []
    page.on(
        "dialog",
        lambda d: (
            confirms.append(d.message),
            d.accept() if answer["accept"] else d.dismiss(),
        ),
    )

    def declined(n):
        for _ in range(100):
            if len(confirms) >= n:
                break
            page.wait_for_timeout(50)
        assert len(confirms) == n, confirms
        page.wait_for_function("() => location.hash === '#/settings' && !App._stay")
        settle(page, handled)

    try:
        _no_splash(page)
        _sidebar(page, handled, "#/reports")
        _sidebar(page, handled, "#/settings")
        edited = page.evaluate(EDIT)
        assert page.evaluate("() => SettingsPage.isDirty()")
        # a sidebar link, declined: the Settings entry itself again, the
        # edits untouched (no re-render), Back lit, the Report Center behind
        page.click('#sidebar a[href="#/customers"]')
        declined(1)
        assert (
            page.evaluate("() => SettingsPage.isDirty()")
            and page.evaluate(VALUE) == edited
        )
        assert page.evaluate("() => history.state.from") == "#/reports"
        assert (
            page.evaluate("() => App.canGoBack()")
            and not page.evaluate(BACK)["disabled"]
        )
        # a Back declined: the move went behind, so the way back is forward
        page.click("#back-btn")
        declined(2)
        assert (
            page.evaluate("() => SettingsPage.isDirty()")
            and page.evaluate(VALUE) == edited
        )
        assert page.evaluate("() => history.state.from") == "#/reports"
        assert (
            page.evaluate("() => App.canGoBack()")
            and not page.evaluate(BACK)["disabled"]
        )
        # Back then goes to the page that was behind, the leave confirmed
        answer["accept"] = True
        page.click("#back-btn")
        page.wait_for_function(AT, arg="#/reports")
        settle(page, handled)
        assert len(confirms) == 3
        assert page.locator("#page-content .card-grid .card").count() > 5
    finally:
        page.close()


# ── Round 3, NEW-42: Back is a bordered gold button that reads "← Back" ──

# The button as drawn: its words, and the computed colour, ground, border
# and opacity the sweep would read.
LOOKS = """() => { const b = document.getElementById('back-btn'), cs = getComputedStyle(b);
    return { text: b.textContent.trim(), disabled: b.disabled, color: cs.color,
             background: cs.backgroundColor, opacity: cs.opacity,
             border: [cs.borderTopWidth, cs.borderTopStyle, cs.borderTopColor].join(' ') }; }"""
# The keyboard's ring (--focus-ring): a deeper gold than the brand's in
# light, #a37a29, and the brand's own in dark, #e0a840; the lit Back's
# border, the button's edge against the bar: deeper still in light, #8f6a1e.
RING_GOLD = {"light": "rgb(163, 122, 41)", "dark": "rgb(224, 168, 64)"}
BORDER = {"light": "rgb(143, 106, 30)", "dark": "rgb(224, 168, 64)"}
GOLD = RING_GOLD  # the ring, where the tests read it
NONE = "rgba(0, 0, 0, 0)"  # a transparent border or ground, as computed
# The bar's ground: its gradient's stops, each scored (the sweep's rule)
BAR_STOPS = """() => getComputedStyle(document.getElementById('topbar')).backgroundImage
    .match(/rgba?\\([^)]*\\)/g) || []"""
# The ground under the focused control: the nearest opaque background
GROUND = """() => { let e = document.activeElement;
    while (e && e !== document.documentElement) {
        const c = getComputedStyle(e).backgroundColor, m = c.match(/[\\d.]+/g);
        if (m && (m.length < 4 || +m[3] >= 0.999)) return c;
        e = e.parentElement; }
    return getComputedStyle(document.body).backgroundColor; }"""


def _edge_clears(page, border, what):
    """The border's colour against every stop of the bar it sits on: 3:1
    (WCAG 1.4.11, a component's boundary)."""
    stops = page.evaluate(BAR_STOPS)
    assert len(stops) >= 2, stops
    ratios = {stop: contrast(border, stop) for stop in stops}
    assert min(ratios.values()) >= 3.0, (what, ratios)


def test_back_is_a_bordered_gold_button_that_is_never_grey_while_it_works(
    browser, company, books
):
    """The ← was a bare grey glyph that got its border on hover alone, and
    disabled it was the same glyph dimmed; with a card open the owner read
    it as unusable, an arrow pointing at the brand (macOS gate, round 2).
    Lit, it is a bordered gold button reading "← Back" in both themes, its
    words AA against its own ground and the border gold under the pointer
    too; with nowhere to go it is muted and borderless, and still named."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        rest = page.evaluate(LOOKS)
        assert rest["text"] == "← Back" and rest["disabled"]
        assert rest["border"].endswith(NONE) and rest["background"] == NONE
        assert float(rest["opacity"]) < 1
        assert page.evaluate(BACK)["name"] == "Back"
        _sidebar(page, handled, "#/customers")
        for theme in ("light", "dark"):
            _theme(page, theme)
            lit = page.evaluate(LOOKS)
            assert lit["text"] == "← Back" and not lit["disabled"]
            assert lit["border"] == f"2px solid {BORDER[theme]}", (theme, lit)
            assert lit["opacity"] == "1" and lit["background"] != NONE
            assert contrast(lit["color"], lit["background"]) >= 4.5, (theme, lit)
            # not the bar's grey: the navy on a pale gold tint, or the gold
            assert (
                lit["color"]
                == {"light": "rgb(0, 51, 102)", "dark": GOLD["dark"]}[theme]
            ), (theme, lit)
            # the border is the button's edge against the bar: 3:1 on every
            # stop of the bar's gradient (WCAG 1.4.11), lit and under the
            # pointer
            _edge_clears(page, BORDER[theme], (theme, "lit"))
            page.hover("#back-btn")
            hovered = page.evaluate(LOOKS)
            assert hovered["border"] not in (
                "1px solid rgb(176, 184, 200)",  # .tb-btn:hover, light
                "2px solid rgb(176, 184, 200)",
                "2px solid rgb(74, 78, 88)",  # .tb-btn:hover, dark
            ), (theme, hovered)
            assert hovered["border"].startswith("2px solid ")
            assert contrast(hovered["color"], hovered["background"]) >= 4.5, hovered
            _edge_clears(page, hovered["border"].split(" ", 2)[2], (theme, "hover"))
            page.mouse.move(0, 0)
        _theme(page, "light")
        # over a plain form it sits under the overlay, as before
        _open_dialog(page, handled, "CustomersPage.showForm()", "#modal form")
        assert not page.evaluate(AT_POINT)
        page.evaluate("() => closeModal()")
        assert page.evaluate(AT_POINT)
        # Back: nowhere to go, and the muted, borderless look is back
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg="#/")
        settle(page, handled)
        rest = page.evaluate(LOOKS)
        assert rest["disabled"] and rest["border"].endswith(NONE)
        assert rest["background"] == NONE and float(rest["opacity"]) < 1
    finally:
        page.close()


# ── Round 3, NEW-43: the keyboard's place is visible on links and buttons ──

# Where focus is, and the ring drawn there: the computed outline, and
# whether the browser counts this focus as the keyboard's.
RING = """() => { const a = document.activeElement, cs = getComputedStyle(a);
    return { at: a.dataset.rowKey || a.id || a.getAttribute('href') || a.tagName.toLowerCase(),
             tag: a.tagName.toLowerCase(), offset: cs.outlineOffset,
             outline: [cs.outlineWidth, cs.outlineStyle, cs.outlineColor].join(' '),
             keyboard: a.matches(':focus-visible') }; }"""
# a button's colours fade (transition: all 0.1s): the ring is read settled
SETTLED = "() => document.getAnimations().forEach(a => a.finish())"


def _tab_until(page, test, cap=40, shift=False):
    for _ in range(cap):
        page.keyboard.press("Shift+Tab" if shift else "Tab")
        if page.evaluate(test):
            page.evaluate(SETTLED)
            return page.evaluate(RING)
    raise AssertionError(f"Tab never reached {test}")


def test_tab_shows_where_it_is_on_a_report_row_and_a_mouse_click_draws_no_ring(
    browser, company, books
):
    """style.css had no focus style for links or buttons, and WebKit draws
    no ring on a link with the Mac's keyboard navigation off: the owner
    tabbed through A/R Aging and never saw the customer names take focus
    (macOS gate, round 2). Now every link and button draws the skip link's
    ring, 2px of gold with an offset (a deeper gold than the brand's in
    light: 3:1 against its ground), in both themes — on keyboard focus
    only."""
    page, handled = _open_at(
        browser, company, "#/reports/ar-aging?as_of_date=2026-09-30"
    )
    try:
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector('#modal a[data-row-key^="customer:"]')
        settle(page, handled)
        page.evaluate(FIRST)
        row = _tab_until(
            page,
            "() => (document.activeElement.dataset.rowKey || '').startsWith('customer:')",
        )
        assert row["tag"] == "a" and row["keyboard"], row
        for theme in ("light", "dark"):
            _theme(page, theme)
            on = page.evaluate(RING)
            assert on["outline"] == f"2px solid {GOLD[theme]}", (theme, on)
            assert on["offset"] == "2px", (theme, on)
            # the ring against what it sits on: the row's ground, and white
            # (the page's) in light — 3:1 (WCAG 1.4.11)
            ground = page.evaluate(GROUND)
            assert contrast(GOLD[theme], ground) >= 3.0, (theme, ground)
            if theme == "light":
                assert contrast(GOLD[theme], "rgb(255, 255, 255)") >= 3.0
        # a button before the table the same: Apply Late Fees, Email All
        # Overdue, Send Collection Letters
        button = _tab_until(
            page, "() => document.activeElement.tagName === 'BUTTON'", shift=True
        )
        assert button["keyboard"] and button["outline"] == f"2px solid {GOLD['dark']}"
        _theme(page, "light")
        page.evaluate(SETTLED)
        assert page.evaluate(RING)["outline"] == f"2px solid {GOLD['light']}"
        # the dialog closed from the keyboard; a mouse click on a button:
        # focus, and no ring
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.click("#theme-toggle")
        page.evaluate(SETTLED)
        clicked = page.evaluate(RING)
        assert clicked["at"] == "theme-toggle" and not clicked["keyboard"], clicked
        assert "none" in clicked["outline"], clicked
        page.click("#theme-toggle")  # light again
        # a link clicked: the same; the next Tab from it draws the ring
        page.click('#sidebar a[href="#/customers"]')
        page.wait_for_function(AT, arg="#/customers")
        settle(page, handled)
        page.evaluate(SETTLED)
        link = page.evaluate(RING)
        assert link["at"] == "#/customers" and not link["keyboard"], link
        assert "none" in link["outline"], link
        page.keyboard.press("Tab")
        page.evaluate(SETTLED)
        after = page.evaluate(RING)
        assert after["keyboard"] and after["outline"] == f"2px solid {GOLD['light']}"
    finally:
        page.close()


# ── Round 3, NEW-44: leaving a page's Notes box as it was writes nothing ─


@pytest.mark.parametrize(
    ("kind", "box"), [("customers", "cust-notes"), ("vendors", "vend-notes")]
)
def test_a_tab_through_a_page_writes_no_note_and_a_change_saves_once(
    browser, company, books, kind, box
):
    """The customer page's Notes box saved on every blur, changed or not,
    and so did the vendor page's: now that Tab walks every control in a
    dialog, a Tab through the page wrote the company file and an audit
    entry each time, and turned a note never written (null) into ""
    (macOS gate, round 2). Only a change is saved, empty and none alike."""
    rid = books["customer" if kind == "customers" else "vendor"]
    api = f"/api/{kind}/{rid}"
    audit = f"/api/audit?table_name={kind}&record_id={rid}"
    status = f"#{box.replace('notes', 'note-status')}-{rid}"
    assert company.get(api).json()["notes"] is None  # never written
    before = len(company.get(audit).json())
    page, handled = _open_at(browser, company, f"#/{kind}/{rid}")
    puts = []
    page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
    try:
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector(f"#{box}-{rid}")
        settle(page, handled)
        # Tab through the whole page, the box included, and in and out of
        # the box itself
        stops = page.evaluate(
            "() => _tabStops(document.getElementById('modal-body'), null).length"
        )
        page.evaluate(FIRST)
        for _ in range(stops + 1):
            page.keyboard.press("Tab")
        page.focus(f"#{box}-{rid}")
        for keys in ("Tab", "Shift+Tab", "Tab"):
            page.keyboard.press(keys)
        settle(page, handled)
        assert puts == []
        assert page.inner_text(status).strip() == ""
        assert company.get(api).json()["notes"] is None  # not turned into ""
        assert len(company.get(audit).json()) == before
        # a change saves, once, and says so
        page.fill(f"#{box}-{rid}", "Prefers email.")
        page.keyboard.press("Tab")
        page.wait_for_function(
            "(s) => document.querySelector(s).textContent === '✓ saved'", arg=status
        )
        settle(page, handled)
        assert len(puts) == 1 and puts[0].endswith(api), puts
        assert company.get(api).json()["notes"] == "Prefers email."
        assert len(company.get(audit).json()) == before + 1
        # left as saved: nothing more
        page.focus(f"#{box}-{rid}")
        page.keyboard.press("Tab")
        settle(page, handled)
        assert len(puts) == 1
        # cleared: a change again, saved as empty; then nothing more
        page.fill(f"#{box}-{rid}", "")
        page.keyboard.press("Tab")
        settle(page, handled)
        assert len(puts) == 2
        assert (company.get(api).json()["notes"] or "") == ""
        page.focus(f"#{box}-{rid}")
        page.keyboard.press("Tab")
        settle(page, handled)
        assert len(puts) == 2
    finally:
        page.close()


# ── Round 3, W-7: Back refocuses the chart's account and the drill-down's line ──

ROW_KEY = (
    "() => (document.activeElement && document.activeElement.dataset.rowKey) || null"
)
WORDS = "() => document.activeElement.textContent.trim()"


def test_back_from_a_register_or_a_drill_down_refocuses_the_charts_account(
    browser, company, books
):
    """Chart of Accounts → a register (or a drill-down) → Back landed on
    the page body (Windows gate W-7). The chart's account links carry a
    key, noted on the way out as a report's rows are, and the page's
    render on return puts the keyboard back on it — by Alt+←, by the
    toolbar's button, and after a mouse click on the name too."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/accounts")
        links = page.evaluate(
            """() => [...document.querySelectorAll('#page-content a[data-row-key^="account:"]')]
                .map(a => [a.dataset.rowKey, a.getAttribute('href'), a.textContent.trim()])"""
        )
        bank = next(x for x in links if x[1].startswith("#/banking/"))
        drill = next(
            x for x in links if x[1].startswith("#/reports/account-transactions?")
        )

        def leave(key, where, click=False):
            sel = f'#page-content a[data-row-key="{key}"]'
            if click:
                page.click(sel)
            else:
                page.focus(sel)
                page.keyboard.press("Enter")
            page.wait_for_function("(s) => location.hash.startsWith(s)", arg=where)
            settle(page, handled)
            assert page.evaluate(ROW_KEY) != key  # left

        def back(key, words, button=False):
            if button:
                page.click("#back-btn")
            else:
                page.keyboard.press("Alt+ArrowLeft")
            page.wait_for_function(AT, arg="#/accounts")
            settle(page, handled)
            assert page.evaluate(ROW_KEY) == key
            assert page.evaluate(WORDS) == words

        # a bank account's name opens its register, a page of its own
        leave(bank[0], "#/banking/")
        back(bank[0], bank[2])
        # an expense account's name opens its drill-down, a dialog over
        # the Report Center; the toolbar's button returns the same
        leave(drill[0], "#/reports/account-transactions?")
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        back(drill[0], drill[2], button=True)
        # by mouse: the click puts focus on the name, and that is noted
        leave(bank[0], "#/banking/", click=True)
        back(bank[0], bank[2])
    finally:
        page.close()


def test_back_from_a_drill_downs_document_refocuses_the_line(browser, company, books):
    """A drill-down's line → its document → Back landed on the period
    select (Windows gate W-7). Each line's link carries its own key, noted
    on the way out, and the drill-down's render on return focuses it."""
    checking = _account(company, "1000")
    url = (
        f"#/reports/account-transactions?account_id={checking['id']}"
        "&start_date=2026-01-01&end_date=2026-12-31&from=trial-balance"
    )
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        here = page.evaluate("location.hash")
        lines = page.evaluate(
            """() => [...document.querySelectorAll('#drilldown-body a[data-row-key^="line:"]')]
                .map(a => [a.dataset.rowKey, a.getAttribute('href'), a.textContent.trim()])"""
        )
        assert len(lines) >= 3, lines
        assert len({k for k, _, _ in lines}) == len(lines)  # each line its own
        for key, href, words in (lines[1], lines[-1]):
            doc = "#" + href.split("#", 1)[1]
            page.focus(f'#drilldown-body a[data-row-key="{key}"]')
            page.keyboard.press("Enter")
            page.wait_for_function(AT, arg=doc)
            page.wait_for_function("() => !document.getElementById('drilldown-body')")
            page.wait_for_function(MODAL_SHOWN)
            settle(page, handled)
            page.keyboard.press("Alt+ArrowLeft")
            page.wait_for_function(AT, arg=here)
            page.wait_for_selector("#drilldown-body table")
            settle(page, handled)
            assert page.evaluate(ROW_KEY) == key, (key, words)
            assert page.evaluate(WORDS) == words
            assert page.evaluate("() => document.activeElement.id") != (
                "report-period-select"
            )
    finally:
        page.close()


def test_the_dialog_itself_and_a_field_take_focus_without_the_ring(
    browser, company, books
):
    """The ring is a control's. The dialog itself (tabindex −1), which
    takes focus when its first control is Void (NEW-32; the deposit's view
    here — the journal entry's opens on its file input since 2.22.3), is a
    place and draws none, whether the keyboard or a script put focus there;
    the next Tab lands on a control with the ring. A field keeps its own
    focus style, the blue border and pale ground, with no ring by keyboard
    and none on a click into a box (review of NEW-43)."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/journal")
        _open_dialog(
            page,
            handled,
            f"DepositsPage.view({books['deposit']})",
            "#modal-body button",
        )
        assert page.evaluate(FOCUSED)["id"] == "modal"
        opened = page.evaluate(RING)
        assert opened["at"] == "modal" and "none" in opened["outline"], opened
        control = _tab_until(
            page, "() => ['A', 'BUTTON'].includes(document.activeElement.tagName)"
        )
        assert control["keyboard"], control
        assert control["outline"] == f"2px solid {GOLD['light']}", control
        # back on the dialog by script, after the keyboard: still none
        page.evaluate("() => document.getElementById('modal').focus()")
        again = page.evaluate(RING)
        assert again["at"] == "modal" and "none" in again["outline"], again
        page.evaluate("() => closeModal()")
        # a field: the period select, by keyboard and then by mouse
        _visit(page, handled, "#/reports/ar-aging?as_of_date=2026-09-30")
        page.wait_for_function(MODAL_SHOWN)
        settle(page, handled)
        page.evaluate(FIRST)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        field = page.evaluate(RING)
        assert field["at"] == "report-period-select" and field["keyboard"], field
        assert "none" in field["outline"], field
        page.click("#report-period-select")
        page.evaluate(SETTLED)
        box = page.evaluate(RING)
        assert box["at"] == "report-period-select" and "none" in box["outline"], box
    finally:
        page.close()


# ── Round 3 review: the ring as painted, on every ground it meets ───────
#
# Read off the screen, as the macOS gate reads it: the pixels the browser
# painted around the focused control, not the stylesheet's colours.

# The focused control's border box and its outline, as computed
PAINT_BOX = """() => { const el = document.activeElement, cs = getComputedStyle(el),
        r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height, dpr: devicePixelRatio,
             off: parseFloat(cs.outlineOffset) || 0, wid: parseFloat(cs.outlineWidth) || 0,
             color: cs.outlineColor, shadow: cs.boxShadow, vw: innerWidth, vh: innerHeight }; }"""
SIDES = ("top", "right", "bottom", "left")
# the keyboard's halo, each theme's own ground (style.css --focus-halo)
HALO = {"light": "rgb(255, 255, 255)", "dark": "rgb(20, 22, 28)"}


def _rgb(c):
    return "rgb(%d, %d, %d)" % tuple(int(round(v)) for v in c[:3])


def _far(a, b):
    return max(abs(a[k] - b[k]) for k in range(3))


def _between(a, b, c, slack=8):
    """b lies between a and c, and is neither: an anti-aliased edge."""
    inside = all(
        min(a[k], c[k]) - slack <= b[k] <= max(a[k], c[k]) + slack for k in range(3)
    )
    return inside and _far(a, b) > 12 and _far(b, c) > 12


def _painted(page):
    """The ring around the focused control as painted. Per side: the ring's
    colour (the pixels of the outline's computed colour where the outline
    lies, outline-offset to offset + width from the border box, and on
    while that colour goes on), its width in pixels, the colour painted
    against it on its inner and its outer side (an anti-aliased pixel
    between the two skipped): medians over the middle of the side, clear
    of the corners; and the ground as the macOS gate reads it, the pixels
    1 to 5 past the ring, the median on each line across the side and the
    line where it stands least against the ring. A side with no ring
    painted is None; the outer colour and the ground are None where the
    ring meets the window's edge (`edge`)."""
    import io
    import math
    import re
    import statistics

    from PIL import Image

    b = page.evaluate(PAINT_BOX)
    s, off, wid = b["dpr"], b["off"], b["wid"] or 2
    pad = max(off, 0) + wid + 12
    # the clip on whole pixels, so a pixel of the shot is a pixel of the page
    x0, y0 = max(math.floor(b["x"] - pad), 0), max(math.floor(b["y"] - pad), 0)
    x1 = min(math.ceil(b["x"] + b["w"] + pad), b["vw"])
    y1 = min(math.ceil(b["y"] + b["h"] + pad), b["vh"])
    shot = page.screenshot(clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
    img = Image.open(io.BytesIO(shot)).convert("RGB")
    px, w, h = img.load(), img.width, img.height
    want = [float(v) for v in re.findall(r"[\d.]+", b["color"])[:3]]

    def dev(v):  # CSS px, in device pixels
        return int(round(v * s))

    # the border box's edges as painted (snapped to the pixel grid)
    left, top = dev(b["x"]) - dev(x0), dev(b["y"]) - dev(y0)
    right = dev(b["x"] + b["w"]) - dev(x0)
    bottom = dev(b["y"] + b["h"]) - dev(y0)
    at = {  # the pixel `d` out from the border box on a side, at `p` along it
        "top": lambda d, p: (p, top - 1 - d),
        "right": lambda d, p: (right + d, p),
        "bottom": lambda d, p: (p, bottom + d),
        "left": lambda d, p: (left - 1 - d, p),
    }
    edge = {
        "top": y0 <= 0,
        "left": x0 <= 0,
        "right": x1 >= b["vw"],
        "bottom": y1 >= b["vh"],
    }

    def middle(a, z):
        n = z - a
        return (
            [a + n // 2]
            if n <= 8 * s
            else sorted({a + n * f // 20 for f in range(6, 15)})
        )

    sides = {}
    for side in SIDES:
        along = (
            middle(top, bottom) if side in ("left", "right") else middle(left, right)
        )
        prof, lines = {}, {p: {} for p in along}
        for d in range(dev(off - 6), dev(off + wid + 10)):
            got = []
            for p in along:
                x, y = at[side](d, p)
                if 0 <= x < w and 0 <= y < h:
                    got.append(px[x, y])
                    lines[p][d] = px[x, y]
            if got:
                prof[d] = [statistics.median(g[k] for g in got) for k in range(3)]
        hits = [
            d
            for d in range(dev(off) - 1, dev(off + wid) + 1)
            if d in prof and _far(prof[d], want) <= 40
        ]
        if not hits:
            sides[side] = None
            continue
        r0, r1 = min(hits), max(hits)
        while r0 - 1 in prof and _far(prof[r0 - 1], want) <= 40:
            r0 -= 1
        while r1 + 1 in prof and _far(prof[r1 + 1], want) <= 40:
            r1 += 1
        ring = [
            statistics.median(prof[d][k] for d in range(r0, r1 + 1)) for k in range(3)
        ]

        def against(d, step, ring=ring, prof=prof):
            for _ in range(2):
                nxt = prof.get(d + step)
                if d in prof and nxt is not None and _between(ring, prof[d], nxt):
                    d += step
                else:
                    break
            return prof.get(d)

        # the gate's ground: the 1st to 5th pixel past the ring, on each
        # line across the side; the line where it is nearest the ring
        grounds = []
        for line in lines.values():
            band = [line[d] for d in range(r1 + 1, r1 + 6) if d in line]
            if band:
                grounds.append(
                    [statistics.median(c[k] for c in band) for k in range(3)]
                )
        sides[side] = {
            "ring": ring,
            "px": r1 - r0 + 1,
            "inner": against(r0 - 1, -1),
            "outer": against(r1 + 1, 1),
            "ground": (
                min(grounds, key=lambda g, ring=ring: contrast(_rgb(ring), _rgb(g)))
                if grounds
                else None
            ),
            "edge": edge[side],
        }
    return sides


def _ring_clears(page, what, ring, need=3.0):
    """The ring painted on all four sides in its colour (`ring`, computed),
    and 3:1 against what is painted right against it on each side of it —
    the halo where there is one, the ground where not (WCAG 1.4.11): the
    side of a control on a bad ground still has a 3:1 edge on both sides of
    its line; and 3:1 against the ground the macOS gate reads, 1 to 5px
    beyond the ring. Returns the painted figures, for a caller's message."""
    page.evaluate(SETTLED)
    sides = _painted(page)
    report = {}
    for side in SIDES:
        got = sides[side]
        assert got, (what, side, "no ring painted", sides)
        assert contrast(_rgb(got["ring"]), ring) <= 1.1, (
            what,
            side,
            _rgb(got["ring"]),
            ring,
        )
        report[side] = {"ring": _rgb(got["ring"])}
        for k in ("inner", "outer"):
            if got[k] is None:
                assert k == "outer" and got["edge"], (what, side, k, got)
                continue
            ratio = contrast(_rgb(got["ring"]), _rgb(got[k]))
            report[side][k] = (_rgb(got[k]), ratio)
            assert ratio >= need, (what, side, k, report)
        if got["ground"] is not None:
            ratio = contrast(_rgb(got["ring"]), _rgb(got["ground"]))
            report[side]["ground"] = (_rgb(got["ground"]), ratio)
            assert ratio >= need, (what, side, "the gate's ground", report)
    return report


# the sidebar's halo is its own navy (style.css #sidebar --focus-halo)
NAV_HALO = {"light": "rgb(0, 34, 68)", "dark": "rgb(8, 12, 20)"}


def _both_themes(page, what, ring_for, halo=HALO, bar=False):
    """The ring at the focused control, in light and then dark, and light
    again; and, unless `halo` is None, the halo painted against the gold
    on its outer side, in each theme's colour; with `bar`, the gold against
    the toolbar's own ground too (_bar_clears)."""
    seen = {}
    for theme in ("light", "dark"):
        _theme(page, theme)
        page.evaluate(SETTLED)
        on = page.evaluate(RING)
        assert on["keyboard"], (what, theme, on)
        seen[theme] = _ring_clears(page, (what, theme), ring_for[theme])
        if bar:
            _bar_clears(page, (what, theme), ring_for[theme])
        if halo:
            outer = [v["outer"] for v in seen[theme].values() if "outer" in v]
            assert outer and all(contrast(c, halo[theme]) <= 1.1 for c, _ in outer), (
                what,
                theme,
                seen[theme],
            )
    _theme(page, "light")
    return seen


TOOLBAR_GROUND = "() => !!document.activeElement.closest('#topbar') && document.activeElement.tagName === 'BUTTON'"
# The toolbar's ring: the Back border's deeper gold in light, so the gold
# clears the bar itself; the theme's own in dark
TOOLBAR_GOLD = {"light": "rgb(143, 106, 30)", "dark": "rgb(224, 168, 64)"}
# A column of the bar with no control on it, between the search box and
# the right-hand group, and the rows the focused control's ring runs on
BAR_COLUMN = """() => { const s = document.getElementById('global-search').getBoundingClientRect(),
        r = document.querySelector('#topbar .topbar-right').getBoundingClientRect(),
        bar = document.getElementById('topbar').getBoundingClientRect(),
        a = document.activeElement.getBoundingClientRect();
    return { x: Math.round((s.right + r.left) / 2), gap: r.left - s.right,
             top: bar.top, bottom: bar.bottom, y0: a.top - 4, y1: a.bottom + 4 }; }"""


def _bar_clears(page, what, ring):
    """The gold against the bar's own ground as painted, behind its halo:
    the bar's gradient runs top to bottom, so a column of it with no
    control on it gives the ground on every row the ring runs on; the gold
    is 3:1 against each (the stricter reading of a two-colour ring: the
    gold clears the ground beyond the halo too, not only the halo)."""
    import io
    import math

    from PIL import Image

    c = page.evaluate(BAR_COLUMN)
    assert c["gap"] > 20, c
    y0 = max(math.floor(c["y0"]), math.ceil(c["top"]))
    y1 = min(math.ceil(c["y1"]), math.floor(c["bottom"]) - 1)
    shot = page.screenshot(clip={"x": c["x"], "y": y0, "width": 1, "height": y1 - y0})
    img = Image.open(io.BytesIO(shot)).convert("RGB")
    rows = [img.getpixel((0, i)) for i in range(img.height)]
    ratios = [contrast(ring, _rgb(p)) for p in rows]
    assert min(ratios) >= 3.0, (
        what,
        min(ratios),
        _rgb(rows[ratios.index(min(ratios))]),
    )
    return min(ratios)


def test_the_ring_clears_3_to_1_on_every_ground_it_meets_as_painted(
    browser, company, books, divisions
):
    """The light theme's gold ring alone painted under 3:1 on the toolbar's
    gradient (2.54:1 against its darker stop below every button) and on a
    dialog's blue title bar (1.73 to 1.97 beside the ×), and under 3 against
    a table's header row above a report's first row (review of NEW-43). The
    gold now meets a halo of the theme's own ground on both its sides,
    whatever lies beyond, and the halo is also what the macOS gate reads as
    the ground, 1 to 5px beyond the ring: measured as painted at every
    toolbar button (the lit Back too), the × on a dialog, a report's row
    link and a dialog's button, the grid region (its own ring) and a
    summary, in both themes. On the toolbar the light theme's gold is the
    Back border's deeper one, and clears the bar itself as well as its
    halo, on every row the ring runs on."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/customers")  # an app page behind: Back is lit
        assert not page.evaluate(BACK)["disabled"]
        page.focus(".skip-link")  # the first stop: Tab from there
        buttons = []
        for _ in range(30):
            page.keyboard.press("Tab")
            if page.evaluate("() => !!document.activeElement.closest('#sidebar')"):
                break
            if page.evaluate(TOOLBAR_GROUND):
                page.evaluate(SETTLED)
                buttons.append(page.evaluate(RING)["at"])
                _both_themes(page, ("toolbar", buttons[-1]), TOOLBAR_GOLD, bar=True)
        # every button on the bar, the lit Back first among them
        on_bar = page.evaluate(
            "() => [...document.querySelectorAll('#topbar button.tb-btn')].filter(b => !b.disabled && b.offsetParent).length"
        )
        assert buttons[0] == "back-btn" and len(buttons) == on_bar, buttons
        # A/R Aging: a customer's row link, a button, the dialog's ×
        _visit(page, handled, "#/reports/ar-aging?as_of_date=2026-09-30")
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector('#modal a[data-row-key^="customer:"]')
        settle(page, handled)
        page.evaluate(FIRST)
        _tab_until(
            page,
            "() => (document.activeElement.dataset.rowKey || '').startsWith('customer:')",
        )
        _both_themes(page, "row link", GOLD)
        _tab_until(
            page,
            "() => document.activeElement.tagName === 'BUTTON' && !!document.activeElement.closest('#modal-body')",
            shift=True,
        )
        _both_themes(page, "dialog button", GOLD)
        _tab_until(
            page, "() => document.activeElement.id === 'modal-close-btn'", cap=60
        )
        _both_themes(page, "the dialog's ×", GOLD)
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        # the grid region: its own ring, inside its edge, no halo
        _visit(page, handled, BY_CLASS_URL)
        page.wait_for_selector("#grid-scroll")
        settle(page, handled)
        page.evaluate(FIRST)
        _tab_until(page, "() => document.activeElement.id === 'grid-scroll'", cap=60)
        _both_themes(page, "grid", OWN["grid"], halo=None)
        page.keyboard.press("Escape")
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        # a summary: the invoice email's preview
        _open_dialog(
            page,
            handled,
            f"InvoicesPage.emailInvoice({books['sent']})",
            "#modal summary",
        )
        page.evaluate(FIRST)
        _tab_until(page, "() => document.activeElement.tagName === 'SUMMARY'")
        _both_themes(page, "summary", GOLD)
    finally:
        page.close()


def test_a_sidebar_link_draws_the_ring_on_all_four_sides_as_painted(
    browser, company, books
):
    """#sidebar scrolls (overflow-y), which cut the ring at +2px to its top
    and bottom lines: a link spans the sidebar, and its sides fell outside
    (review of NEW-43). The ring and its halo are drawn inside the link's
    edge, the halo the sidebar's own navy: all four sides painted, the gold
    3:1 against what meets it and against the ground beyond, in both themes
    — on a link, on the open page's (its lighter ground; its gold marker
    takes the halo's colour while it is focused), and on the link below it,
    which meets that lighter ground (2.35:1 with the ring at -2px)."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/customers")
        # the menu's own Customers link, not the Open group's (#336) above it
        page.focus('#sidebar .nav-link[data-page="customers"]')
        page.keyboard.press("Shift+Tab")
        for href in ("#/", "#/customers", "#/jobs"):
            at = page.evaluate("() => document.activeElement.getAttribute('href')")
            assert at == href, (at, href)
            assert page.evaluate(
                "() => document.activeElement.closest('#sidebar') !== null"
            )
            _both_themes(page, ("sidebar", href), GOLD, NAV_HALO)
            page.keyboard.press("Tab")
    finally:
        page.close()


# The controls' own rings: a search result's blue, inside its edge; the
# grid's navy (light) or blue (dark), on its edge
OWN = {
    "search": {"light": "rgb(51, 102, 153)", "dark": "rgb(107, 179, 232)"},
    "grid": {"light": "rgb(0, 51, 102)", "dark": "rgb(107, 179, 232)"},
}
# What the browser computed for the focused control's ring
OWN_RING = """() => { const a = document.activeElement, cs = getComputedStyle(a);
    return { cls: String(a.className), id: a.id, offset: cs.outlineOffset, shadow: cs.boxShadow,
             outline: [cs.outlineWidth, cs.outlineStyle, cs.outlineColor].join(' '),
             keyboard: a.matches(':focus-visible') }; }"""


def test_a_controls_own_ring_wins_over_the_keyboards(
    browser, company, books, divisions
):
    """The generic ring's selector, [tabindex]:not([tabindex="-1"]):focus-
    visible, is (0,3,0) and beat the search results' and the grid's own
    rings, (0,2,0): a result drew the gold at +2px, cut by the list to its
    top line, and the grid the gold in light and blue in dark. Inside
    :where() it counts for nothing, and each draws its own, as before the
    gold came: the blue ring inside a search result, the navy (light) or
    blue (dark) ring on the grid's edge (inside it, the frozen header and
    Account column painted over half of it), with no halo."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        page.click("#global-search")
        page.keyboard.type("Harbor")
        page.wait_for_selector("#search-results:not(.hidden) .search-item[tabindex]")
        settle(page, handled)
        page.keyboard.press("ArrowDown")
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate(SETTLED)
            item = page.evaluate(OWN_RING)
            assert "search-item" in item["cls"] and item["keyboard"], item
            assert item["outline"] == f"2px solid {OWN['search'][theme]}", (theme, item)
            assert item["offset"] == "-2px" and item["shadow"] == "none", (theme, item)
            # its own ring, painted on all four sides (not cut to a line), and
            # clear of what meets it: 3.5:1 in dark, where the theme's blue
            # was 3.0 against the border below a result
            _ring_clears(
                page,
                ("search result", theme),
                OWN["search"][theme],
                need={"light": 3.0, "dark": 3.5}[theme],
            )
        _theme(page, "light")
        page.keyboard.press("Escape")
        _visit(page, handled, BY_CLASS_URL)
        page.wait_for_selector("#grid-scroll")
        settle(page, handled)
        page.evaluate(FIRST)
        _tab_until(page, "() => document.activeElement.id === 'grid-scroll'", cap=60)
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate(SETTLED)
            grid = page.evaluate(OWN_RING)
            assert grid["id"] == "grid-scroll" and grid["keyboard"], grid
            assert grid["outline"] == f"2px solid {OWN['grid'][theme]}", (theme, grid)
            assert grid["offset"] == "0px" and grid["shadow"] == "none", (theme, grid)
    finally:
        page.close()


def test_a_checkbox_draws_the_ring_by_keyboard_and_none_by_mouse(
    browser, company, books, divisions
):
    """`.form-group input:focus { outline: none }` took the outline off a
    checkbox as off a text box, and a checkbox shows neither the field's
    border nor its ground: on the P&L by Class chooser's classes and "Show
    classes with no activity" the keyboard's place could not be seen. A
    checkbox draws the ring and its halo by keyboard, 3:1 as painted on all
    four sides in both themes, and none on a mouse click."""
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#grid-scroll")
        settle(page, handled)
        page.evaluate(FIRST)
        _tab_until(page, "() => document.activeElement.id === 'grid-empty'", cap=60)
        assert page.evaluate("() => !!document.activeElement.closest('.form-group')")
        _both_themes(page, "Show classes with no activity", GOLD)
        on = page.evaluate(OWN_RING)
        assert on["outline"] == f"2px solid {GOLD['light']}" and on["offset"] == "2px"
        assert on["shadow"] == f"{HALO['light']} 0px 0px 0px 8px", on
        # the chooser: Enter on its button puts focus on its first class
        _tab_until(page, "() => document.activeElement.id === 'grid-choose-btn'")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => document.activeElement.matches('#grid-chooser input[type=checkbox]')"
        )
        _both_themes(page, "the chooser's first class", GOLD)
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.activeElement.matches('#grid-chooser input[type=checkbox]')"
        )
        _both_themes(page, "the chooser's second class", GOLD)
        # by mouse: focus, and no ring
        page.click("#grid-chooser input[type=checkbox] >> nth=2")
        page.evaluate(SETTLED)
        clicked = page.evaluate(OWN_RING)
        assert not clicked["keyboard"] and "none" in clicked["outline"], clicked
        assert clicked["shadow"] != f"{HALO['light']} 0px 0px 0px 8px", clicked
    finally:
        page.close()


# The last row's last button of the page's table, and its container's overflow
LAST_ROW_BUTTON = """() => [...document.querySelectorAll(
    '#page-content .table-container tbody tr:last-child button')].pop()"""
CONTAINER_OVERFLOW = """() => getComputedStyle(document.querySelector(
    '#page-content .table-container')).overflow"""


def test_a_tables_last_row_draws_its_whole_halo(browser, company, books):
    """.table-container clips what overflows it, which cut the keyboard's
    halo below the last row's buttons: the gate's ground there was the
    container's edge and the page, 2.2:1 (review). The container has room
    for it inside, below its table, and clips up and down as it always did
    (sideways it scrolls a table wider than itself): the last row's Void
    draws its ring and halo whole, 3:1 as painted on all four sides, in
    both themes — at 1280, 1024, 900 and 390px wide, where at 390 the
    container is scrolled to it."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        for width in (1280, 1024, 900, 390):
            page.set_viewport_size({"width": width, "height": 800})
            _visit(page, handled, "#/journal")
            page.evaluate(f"() => ({LAST_ROW_BUTTON})().focus()")
            page.keyboard.press("Shift+Tab")
            page.keyboard.press("Tab")
            last = page.evaluate("""() => [document.activeElement.textContent.trim(),
                    document.activeElement === (""" + LAST_ROW_BUTTON + """)()]""")
            assert last[1], (width, last)
            assert page.evaluate(CONTAINER_OVERFLOW) == "auto hidden"
            inside = page.evaluate(
                """() => { const a = document.activeElement.getBoundingClientRect(),
                    c = document.activeElement.closest('.table-container').getBoundingClientRect();
                    return a.left >= c.left && a.right <= c.right && a.bottom <= c.bottom; }"""
            )
            assert inside, width  # shown, not clipped, even at 390
            if width == 1280:
                _both_themes(page, ("the last row's " + last[0], width), GOLD)
            else:
                _theme(page, "light")
                page.evaluate(SETTLED)
                _ring_clears(page, ("the last row's " + last[0], width), GOLD["light"])
    finally:
        page.close()


# Where the content and the first plain table container stand
SPILL = """() => { const c = [...document.querySelectorAll('#page-content .table-container')]
        .find(c => c.offsetParent && !c.classList.contains('table-container--scroll')),
        content = document.getElementById('content'), r = c.getBoundingClientRect();
    return { content: [content.scrollWidth, content.clientWidth], box: [Math.round(r.left), Math.round(r.right)],
             overflow: getComputedStyle(c).overflow }; }"""
FIRST_IN_TABLE = """() => [...document.querySelectorAll('#page-content .table-container')]
    .find(c => c.offsetParent && !c.classList.contains('table-container--scroll'))
    .querySelector('tbody button, tbody a[href]')"""


def test_focus_in_a_wide_table_spills_nothing(browser, company, books):
    """Letting a table's overflow out while focus was inside it spilled a
    wide table past its container (Jobs, Settings, Fixed Assets at 1024; at
    900, the desktop window's least; at 390), by a mouse's focus too. The
    container keeps its box (sideways it scrolls a table wider than
    itself): at 1280, 1024, 900 and 390px wide, a control in the table
    focused by the mouse or by the keyboard leaves the page's width and the
    container's box as they were."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        for width in (1280, 1024, 900, 390):
            page.set_viewport_size({"width": width, "height": 800})
            for route in ("#/jobs", "#/settings", "#/fixed-assets"):
                _visit(page, handled, route)
                rest = page.evaluate(SPILL)
                assert rest["overflow"] == "auto hidden", (width, route, rest)
                # a mouse's focus: the button pressed, not released
                box = page.evaluate(
                    f"""() => {{ const r = ({FIRST_IN_TABLE})().getBoundingClientRect();
                        return {{ x: r.left + Math.min(r.width / 2, 4), y: r.top + r.height / 2 }}; }}"""
                )
                page.mouse.move(box["x"], box["y"])
                page.mouse.down()
                pressed = page.evaluate(SPILL)
                page.mouse.up()
                page.keyboard.press("Escape")
                # the keyboard's focus
                page.evaluate(f"() => ({FIRST_IN_TABLE})().focus()")
                keyed = page.evaluate(SPILL)
                for what, now in (("mouse", pressed), ("keyboard", keyed)):
                    assert now["content"] == rest["content"], (
                        width,
                        route,
                        what,
                        rest,
                        now,
                    )
                    assert now["box"] == rest["box"], (width, route, what, rest, now)
                    assert now["overflow"] == "auto hidden", (width, route, what, now)
                page.evaluate("() => document.activeElement.blur()")
    finally:
        page.set_viewport_size({"width": 1500, "height": 980})
        page.close()


# The focused stop in the grid: whether the browser finds it at its left,
# middle and right (not the frozen Account column or header row painted
# over it), and how far inside what the grid shows its ring and halo (8px
# out) end on each side: right of the Account column, below the header row
# (a heading: below the grid's top), inside the grid's edges
GRID_STOP = """() => { const a = document.activeElement, g = document.getElementById('grid-scroll');
    if (!g || a === g || !g.contains(a)) return null;
    const r = a.getBoundingClientRect(), port = g.getBoundingClientRect(),
        frozen = g.querySelector('thead th:first-child').getBoundingClientRect().right,
        head = a.closest('thead') ? port.top + g.clientTop : g.querySelector('thead').getBoundingClientRect().bottom,
        y = r.top + r.height / 2, cell = a.closest('td, th');
    return { at: `${cell.parentElement.rowIndex}:${cell.cellIndex} ${a.textContent.trim()}`,
             scroll: [Math.round(g.scrollLeft), Math.round(g.scrollTop)],
             found: [r.left + 1, r.left + r.width / 2, r.right - 1].map(x => {
                 const h = document.elementFromPoint(x, y); return !!h && (h === a || a.contains(h)); }),
             left: r.left - 8 - frozen, top: r.top - 8 - head,
             right: port.left + g.clientLeft + g.clientWidth - (r.right + 8),
             bottom: port.top + g.clientTop + g.clientHeight - (r.bottom + 8) }; }"""
GRID_FIT = """() => { const g = document.getElementById('grid-scroll'), cs = getComputedStyle(g);
    return { padding: [cs.scrollPaddingTop, cs.scrollPaddingLeft],
             frozen: [g.querySelector('thead').offsetHeight + 'px',
                      g.querySelector('thead th:first-child').offsetWidth + 'px'],
             scrolls: [g.scrollWidth > g.clientWidth, g.scrollHeight > g.clientHeight] }; }"""
LAST_IN_GRID = (
    "() => [...document.querySelectorAll('#grid-scroll a[href]')].pop().focus()"
)


def _grid_walk(page, start, key, paint_all):
    """Every stop of the grid from `start`, by `key`, until focus leaves
    it, and what is wrong at each: not found where it is, its ring and halo
    not inside what the grid shows, or (read from the pixels, at every stop
    with `paint_all`, else where it is within 4px of an edge) its ring not
    painted on all four sides."""
    page.evaluate(start)
    stops, bad = [], []
    for _ in range(400):
        page.keyboard.press(key)
        page.evaluate(SETTLED)
        at = page.evaluate(GRID_STOP)
        if at is None:
            break
        stops.append(at["at"])
        reach = {s: round(at[s], 1) for s in ("left", "top", "right", "bottom")}
        if not all(at["found"]) or min(reach.values()) < -0.5:
            bad.append((at["at"], at["scroll"], at["found"], reach))
        elif paint_all or min(reach.values()) < 4:
            sides = _painted(page)
            cut = [s for s in SIDES if not sides[s] or sides[s]["px"] < 2]
            if cut:
                bad.append((at["at"], at["scroll"], "ring not painted", cut))
    return stops, bad


def test_tab_brings_every_grid_stop_out_from_under_the_frozen_parts(
    browser, company, books, divisions
):
    """The P&L by Class grid scrolls under its frozen header row and
    Account column, and a cell Tab moved to was scrolled only into the
    grid: 13 of 85 stops forward sat under the Account column, and the
    Roofing heading's ring was cut at the grid's right edge, Chromium
    taking a link a pixel past it for one in view (review). The grid's
    scroll padding is the header row's height and the column's width
    (measured, and again when the window changes), a keyboard stop is
    brought in whole with its scroll-margin, and the headings and the last
    row have the ring and halo's room inside the grid's edges. Walked by
    Tab and by Shift+Tab at 1440 x 900, at 1024 x 360 (where the grid
    scrolls both ways) and at 390 x 800 (a phone's narrower Account
    column): every stop is what the browser finds at its left, middle and
    right, its ring and halo end inside what the grid shows on every side,
    and its ring is painted on all four (every stop at 1440; elsewhere
    those within 4px of an edge). A mouse's press on a heading the grid's
    edge cuts scrolls nothing: the link stays under the pointer."""
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        for width, height in ((1440, 900), (1024, 360), (390, 800)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(100)
            fit = page.evaluate(GRID_FIT)
            assert fit["padding"] == fit["frozen"], (width, fit)
            assert fit["scrolls"] == [True, height == 360], (width, fit)
            forward, bad = _grid_walk(
                page,
                "() => document.getElementById('grid-scroll').focus()",
                "Tab",
                paint_all=width == 1440,
            )
            assert len(forward) > 80 and not bad, (width, len(forward), bad)
            back, bad = _grid_walk(
                page, LAST_IN_GRID, "Shift+Tab", paint_all=width == 1440
            )
            # from the last link back: every stop but the last
            assert back == forward[-2::-1] and not bad, (width, len(back), bad)
        # a mouse's focus stays where the pointer pressed: the grid at its
        # left, a heading cut by its right edge pressed, nothing scrolls
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(100)
        cut = page.evaluate(
            """() => { const g = document.getElementById('grid-scroll');
                g.scrollLeft = 0; g.scrollTop = 0; document.activeElement.blur();
                const edge = g.getBoundingClientRect().left + g.clientLeft + g.clientWidth;
                const a = [...g.querySelectorAll('thead a')].find(a => {
                    const r = a.getBoundingClientRect(); return r.left < edge - 20 && r.right + 8 > edge; });
                const r = a.getBoundingClientRect();
                return { name: a.textContent.trim(), x: r.left + 10, y: r.top + r.height / 2 }; }"""
        )
        page.mouse.move(cut["x"], cut["y"])
        page.mouse.down()
        pressed = page.evaluate("""() => [document.activeElement.textContent.trim(),
                document.activeElement.matches(':focus-visible'),
                document.getElementById('grid-scroll').scrollLeft]""")
        page.mouse.move(cut["x"], cut["y"] + 200)
        page.mouse.up()
        assert pressed == [cut["name"], False, 0], (cut, pressed)
    finally:
        page.close()


def _keyed(page, element):
    """Focus on `element` (a script expression) as the keyboard puts it
    there: placed, then Shift+Tab and Tab back."""
    page.evaluate(f"() => ({element}).focus()")
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    assert page.evaluate(f"() => document.activeElement === ({element})"), element


GL_URL = "#/reports/general-ledger?start_date=2025-01-01&end_date=2026-12-31"
GL_NAMES = "[...document.querySelectorAll('#report-content h3 a')]"
LOOKUPS = """[...document.querySelectorAll('#modal-body a')]
    .filter(a => /^Open .+ lookup$/.test(a.textContent.trim()))"""


def test_a_link_in_running_text_has_the_halo_where_the_gate_reads_its_ground(
    browser, company, books
):
    """A link in running text kept 2px of halo past its ring, and the
    macOS gate's ground, 1 to 5px past the ring, was the line or the table
    header beyond it: the General Ledger's account names (2.24:1, 1.02 in
    dark), Banking's bridge.simplefin.org (2.94), Settings'
    developer.intuit.com (1.44), a customer's permit lookup links (1.36)
    (review). Every control's halo reaches 4px past the ring now, a
    button's as before: at each of those links, the keyboard's focus on
    it, the ring is painted on all four sides in each theme, 3:1 against
    the halo on both its sides and against the gate's ground."""
    page, handled = _open_at(browser, company, GL_URL)
    try:
        page.wait_for_selector("#report-content h3 a")
        settle(page, handled)
        names = page.evaluate(f"() => {GL_NAMES}.map(a => a.textContent.trim())")
        assert len(names) > 3, names
        for i, name in enumerate(names):
            _keyed(page, f"{GL_NAMES}[{i}]")
            _both_themes(page, ("General Ledger account", name), GOLD)
    finally:
        page.close()
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        # (Settings' developer.intuit.com link left with the QuickBooks
        # Online connector in 2.23.2)
        for route, link in (("#/banking", 'a[href="https://bridge.simplefin.org"]'),):
            _visit(page, handled, route)
            _keyed(page, f"document.querySelector('#page-content {link}')")
            _both_themes(page, (route, link), GOLD)
    finally:
        page.close()
    page, handled = _open_at(browser, company, f"#/customers/{books['customer']}")
    try:
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_function(f"() => {LOOKUPS}.length > 0")
        settle(page, handled)
        lookups = page.evaluate(f"() => {LOOKUPS}.length")
        for i in range(lookups):
            _keyed(page, f"{LOOKUPS}[{i}]")
            _both_themes(page, ("the permit lookup", i), GOLD)
    finally:
        page.close()


# A field's own focus style, each theme's (style.css / dark.css .form-group
# and .field): the blue border, the pale ground, no outline
FIELD_FOCUS = {
    "light": {"border": "rgb(51, 102, 204)", "ground": "rgb(255, 255, 248)"},
    "dark": {"border": "rgb(91, 155, 213)", "ground": "rgb(26, 30, 40)"},
}
FIELD_LOOK = """() => { const a = document.activeElement, cs = getComputedStyle(a), r = a.getBoundingClientRect();
    return { id: a.id, outline: cs.outlineStyle, border: cs.borderTopColor, ground: cs.backgroundColor,
             x: r.left, y: r.top, w: r.width, h: r.height }; }"""


def _field_focus_painted(page, what, theme):
    """The focused field in its own focus style, as computed and as
    painted: its border the theme's focus blue on every side, at the middle
    of the side, and nothing drawn just outside it — the 1st to 3rd pixel
    out are what they are with the field at rest: no ring of the browser's
    or the keyboard's."""
    import io
    import math

    from PIL import Image

    look = page.evaluate(FIELD_LOOK)
    want = FIELD_FOCUS[theme]
    assert look["outline"] == "none", (what, theme, look)
    assert look["border"] == want["border"], (what, theme, look)
    assert look["ground"] == want["ground"], (what, theme, look)
    x0, y0 = math.floor(look["x"]) - 8, math.floor(look["y"]) - 8
    x1 = math.ceil(look["x"] + look["w"]) + 8
    y1 = math.ceil(look["y"] + look["h"]) + 8
    clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

    def grab():
        return Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")

    img = grab()
    # the same pixels with the field at rest, then focus back on it
    page.evaluate(
        "() => { window.__field = document.activeElement; window.__field.blur(); }"
    )
    page.evaluate(SETTLED)
    rest = grab()
    page.evaluate("() => window.__field.focus({ preventScroll: true })")
    page.evaluate(SETTLED)
    left, top = round(look["x"]) - x0, round(look["y"]) - y0
    right = round(look["x"] + look["w"]) - x0 - 1
    bottom = round(look["y"] + look["h"]) - y0 - 1
    mx, my = (left + right) // 2, (top + bottom) // 2
    blue = [int(v) for v in re.findall(r"\d+", want["border"])]
    for side, at in (
        ("top", lambda d: (mx, top - d)),
        ("bottom", lambda d: (mx, bottom + d)),
        ("left", lambda d: (left - d, my)),
        ("right", lambda d: (right + d, my)),
    ):
        edge = img.getpixel(at(0))
        assert _far(edge, blue) <= 40, (what, theme, side, edge)
        out = [img.getpixel(at(d)) for d in (1, 2, 3)]
        was = [rest.getpixel(at(d)) for d in (1, 2, 3)]
        assert all(_far(o, w) <= 12 for o, w in zip(out, was)), (
            what,
            theme,
            side,
            out,
            was,
        )


def test_a_field_outside_a_form_group_keeps_a_fields_own_focus_style(
    browser, company, books
):
    """A customer's and a vendor's Notes box and a report's From and To are
    not in a form group, and drew the browser's own ring on focus, where
    every other field shows its own style (review). They take a field's
    look and its focus style (every field's now): by the keyboard and by a
    click, in each theme, the blue border on every side, the pale ground
    and no ring around them, as computed and as painted."""
    pl = f"#/reports/profit-loss?start_date={SEPT[0]}&end_date={SEPT[1]}"
    for where, fields in (
        (f"#/customers/{books['customer']}", [f"#cust-notes-{books['customer']}"]),
        (f"#/vendors/{books['vendor']}", [f"#vend-notes-{books['vendor']}"]),
        (pl, ["#report-custom-start", "#report-custom-end"]),
    ):
        page, handled = _open_at(browser, company, where)
        try:
            page.wait_for_function(MODAL_SHOWN)
            for sel in fields:
                page.wait_for_selector(sel)
            settle(page, handled)
            for sel in fields:
                for theme in ("light", "dark"):
                    _theme(page, theme)
                    # by the keyboard: from the control before it
                    page.focus(sel)
                    page.keyboard.press("Shift+Tab")
                    page.keyboard.press("Tab")
                    assert page.evaluate("() => '#' + document.activeElement.id") == sel
                    page.evaluate(SETTLED)
                    _field_focus_painted(page, (sel, "keyboard"), theme)
                    page.evaluate("() => document.activeElement.blur()")
                    # by a click
                    page.click(sel, position={"x": 4, "y": 4})
                    assert page.evaluate("() => '#' + document.activeElement.id") == sel
                    page.evaluate(SETTLED)
                    _field_focus_painted(page, (sel, "click"), theme)
                    page.evaluate("() => document.activeElement.blur()")
                _theme(page, "light")
        finally:
            page.close()


# The focused control on Settings against the save bar held to the window's
# bottom: its ring and halo (8px) end above the bar, and it is what the
# browser finds at its middle; the bar's own controls apart
SAVEBAR_STOP = """() => { const a = document.activeElement, bar = document.getElementById('settings-savebar');
    if (!a || !a.closest('#page-content')) return null;
    const r = a.getBoundingClientRect(), b = bar.getBoundingClientRect();
    const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return { at: a.id || a.name || (a.textContent || '').trim().slice(0, 30) || a.tagName,
             own: bar.contains(a), size: r.width * r.height,
             over: r.bottom + 8 - b.top, found: !!h && (h === a || a.contains(h)) }; }"""


def test_tab_through_settings_never_lands_under_its_save_bar(browser, company, books):
    """Settings' save bar is held to the window's bottom over what scrolls
    under it, and Tab moved to 22 controls there, out of sight (review).
    A control the keyboard puts under it is scrolled up clear of it: walked
    by Tab from the page's first control to its last, at 1440 x 900, 1280 x
    600 and 1024 x 640, no stop's ring and halo reach the bar (the invoice
    notes box too, which the browser brings only its caret line into view
    for) and every stop is what the browser finds at its middle; Tab still
    reaches the bar's Save; and a mouse's press on a control the bar half
    covers scrolls nothing — a button, and at 1440 x 900 a click into the
    invoice notes box (a text box takes :focus-visible on a click too, and
    the page moved under the click: final review), which Tab then still
    brings clear."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        for width, height in ((1440, 900), (1280, 600), (1024, 640)):
            page.set_viewport_size({"width": width, "height": height})
            _visit(page, handled, "#/settings")
            page.wait_for_selector("#settings-savebar")
            settle(page, handled)
            page.evaluate(
                "() => _tabStops(document.getElementById('page-content'), null)[0].focus()"
            )
            stops, bad, saved = 0, [], False
            for _ in range(400):
                page.keyboard.press("Tab")
                at = page.evaluate(SAVEBAR_STOP)
                if at is None:
                    break
                stops += 1
                if at["own"]:
                    saved = saved or at["at"] == "settings-save-btn"
                    continue
                if at["size"] and (at["over"] > 0.5 or not at["found"]):
                    bad.append((at["at"], round(at["over"], 1), at["found"]))
            assert stops > 100 and saved, (width, stops, saved)
            assert not bad, (width, len(bad), bad)
        # a mouse's press on a button the bar half covers: no scroll, so
        # the release lands on the button pressed
        half = page.evaluate(
            """() => { const bar = document.getElementById('settings-savebar'),
                    content = document.getElementById('content');
                const b = [...document.querySelectorAll('#settings-form button')]
                    .find(el => !bar.contains(el) && !el.disabled && el.offsetHeight > 14);
                content.scrollTop += b.getBoundingClientRect().top - (bar.getBoundingClientRect().top - 8);
                const r = b.getBoundingClientRect();
                b.dataset.half = '1';
                return { x: r.left + r.width / 2, y: r.top + 4, top: content.scrollTop,
                         covered: r.bottom > bar.getBoundingClientRect().top }; }"""
        )
        assert half["covered"], half
        page.mouse.move(half["x"], half["y"])
        page.mouse.down()
        pressed = page.evaluate("""() => [document.activeElement.dataset.half === '1',
                document.activeElement.matches(':focus-visible'),
                document.getElementById('content').scrollTop]""")
        page.mouse.move(half["x"], half["y"] - 300)
        page.mouse.up()
        assert pressed == [True, False, half["top"]], (half, pressed)
        # a click into a text box the bar half covers: a text box takes
        # :focus-visible on a click too, and the page moved under it
        # (review); now nothing moves, and Tab to it still brings it clear
        page.set_viewport_size({"width": 1440, "height": 900})
        _visit(page, handled, "#/settings")
        page.wait_for_selector("#settings-savebar")
        settle(page, handled)
        box = page.evaluate(
            """() => { const bar = document.getElementById('settings-savebar'),
                    content = document.getElementById('content'),
                    t = document.querySelector('#settings-form [name="invoice_notes"]');
                content.scrollTop += t.getBoundingClientRect().top - (bar.getBoundingClientRect().top - 12);
                const r = t.getBoundingClientRect();
                return { x: r.left + 20, y: r.top + 5, top: content.scrollTop,
                         covered: r.bottom > bar.getBoundingClientRect().top }; }"""
        )
        assert box["covered"], box
        page.mouse.click(box["x"], box["y"])
        clicked = page.evaluate(
            """() => [document.activeElement.name, document.getElementById('content').scrollTop]"""
        )
        assert clicked == ["invoice_notes", box["top"]], (box, clicked)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        keyed = page.evaluate(SAVEBAR_STOP)
        assert page.evaluate("() => document.activeElement.name") == "invoice_notes"
        assert keyed["over"] <= 0.5 and keyed["found"], keyed
    finally:
        page.set_viewport_size({"width": 1500, "height": 980})
        page.close()


def test_the_skip_link_draws_the_ring_and_enter_goes_to_the_content_in_place(
    browser, company, books
):
    """The skip link, Tab's first stop, kept the brand's pale gold (1.95:1
    on the toolbar) at the window's very corner, where its top and left
    lines fell off; and Enter on it set the address to #page-content, which
    the router showed as "Page not found". It draws the ring and halo on
    all four sides, in the toolbar's gold, which clears the toolbar it
    lands on as well as its halo (the theme's #a37a29, which it had in
    light, is 2.51:1 against the bar's darker stop: re-review); and Enter
    puts focus on the page's content, which takes it as a place (no ring),
    with the address and history as they were; the next Tab is the
    content's first control."""
    page, handled = _open_at(browser, company, "#/customers")
    try:
        page.keyboard.press("Tab")
        skip = page.evaluate(OWN_RING)
        assert "skip-link" in skip["cls"] and skip["keyboard"], skip
        _both_themes(page, "skip link", TOOLBAR_GOLD, bar=True)
        here, length = page.evaluate("location.hash"), page.evaluate("history.length")
        words = page.inner_text("#page-content")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement.id === 'page-content'")
        settle(page, handled)
        assert page.evaluate("location.hash") == here == "#/customers"
        assert page.evaluate("history.length") == length
        assert "Page not found" not in page.inner_text("#page-content")
        assert page.inner_text("#page-content") == words
        place = page.evaluate(OWN_RING)
        assert "none" in place["outline"] and place["shadow"] == "none", place
        assert page.get_attribute("#page-content", "tabindex") == "-1"
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.activeElement !== document.getElementById('page-content')"
            " && !!document.activeElement.closest('#page-content')"
        )
        # a place only while the skip link puts focus there
        assert page.get_attribute("#page-content", "tabindex") is None
    finally:
        page.close()


# A row of the chart, by its index: its cells and where the content is
CHART_ROW = """(i) => { const row = document.querySelectorAll('#page-content tbody tr[data-account-row]')[i];
    const td = row.children[2], r = td.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2,
             scroll: document.getElementById('content').scrollTop }; }"""


def test_a_click_on_blank_content_then_tab_goes_on_from_there(browser, company, books):
    """With a standing tabindex on #page-content, a mouse click on any blank
    part of a page focused the content, so the next Tab went to the page's
    first control and scrolled back to the top (2.22.0 review). The content
    is a place only while the skip link puts focus there: a click on a row's
    plain cell, then Tab, goes to the next control after that point, with
    the page where it was, as before the skip link's change."""
    page, handled = _open_at(browser, company, "#/accounts")
    try:
        page.wait_for_selector("#page-content tbody tr[data-account-row]")
        settle(page, handled)
        rows = page.evaluate(
            "() => document.querySelectorAll('#page-content tbody tr[data-account-row]').length"
        )
        i = rows * 2 // 3
        page.evaluate(
            """(i) => document.querySelectorAll('#page-content tbody tr[data-account-row]')[i]
                .scrollIntoView({ block: 'center' })""",
            i,
        )
        settle(page, handled)
        at = page.evaluate(CHART_ROW, i)
        assert at["scroll"] > 100, at  # mid-page
        page.mouse.click(at["x"], at["y"])
        assert page.evaluate("() => document.activeElement === document.body")
        page.keyboard.press("Tab")
        settle(page, handled)
        landed = page.evaluate(
            """(i) => { const row = document.querySelectorAll('#page-content tbody tr[data-account-row]')[i];
                const a = document.activeElement;
                return { inRow: row.contains(a), tag: a.tagName, text: a.textContent.trim(),
                         scroll: document.getElementById('content').scrollTop }; }""",
            i,
        )
        assert landed["inRow"] and landed["tag"] == "BUTTON", landed
        assert abs(landed["scroll"] - at["scroll"]) < 2, (at, landed)
    finally:
        page.close()


# The saves of a page's Notes box, held until let go: what the page asks
# for and when, without a race against the server.
HOLD_PUTS = """() => { window.__puts = []; const put = API.put.bind(API);
    API.put = (path, body) => new Promise((ok, no) => window.__puts.push(
        { path, body, go: () => put(path, body).then(ok, no) })); }"""
PUTS = "() => window.__puts.map(p => p.body.notes)"
LET_GO = "() => { const p = window.__puts.find(x => !x.sent); p.sent = true; p.go(); }"


@pytest.mark.parametrize(
    ("kind", "box"), [("customers", "cust-notes"), ("vendors", "vend-notes")]
)
def test_the_notes_box_saves_a_change_once_trimmed_and_in_order(
    browser, company, books, kind, box
):
    """Leaving the box again before its save came back sent a second one
    (it compared with what the box was loaded with until the first was
    back), and a space alone counted as a change (review of NEW-44). Now
    what is on file is the save on its way, words are compared trimmed — a
    space at the end is no change, a note of spaces is no note, and is
    saved empty — and a note is saved as typed; a change made while a save
    is on its way goes after it."""
    rid = books["customer" if kind == "customers" else "vendor"]
    api = f"/api/{kind}/{rid}"
    sel = f"#{box}-{rid}"
    status = f"#{box.replace('notes', 'note-status')}-{rid}"
    assert company.get(api).json()["notes"] is None
    page, handled = _open_at(browser, company, f"#/{kind}/{rid}")
    sent = []
    page.on(
        "request",
        lambda r: sent.append(r.post_data_json["notes"]) if r.method == "PUT" else None,
    )

    def leave(words=None):
        page.focus(sel)
        if words is not None:
            page.fill(sel, words)
        page.keyboard.press("Tab")
        page.wait_for_timeout(30)

    def saved(n):
        page.evaluate(LET_GO)
        page.wait_for_function(
            "(n) => window.__puts.filter(p => p.sent).length === n", arg=n
        )
        page.wait_for_function(
            "(s) => document.querySelector(s).textContent === '✓ saved'", arg=status
        )
        settle(page, handled)

    try:
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector(sel)
        settle(page, handled)
        page.evaluate(HOLD_PUTS)
        # spaces where there was no note: no note, nothing to save
        leave("   ")
        assert page.evaluate(PUTS) == [] and company.get(api).json()["notes"] is None
        # a note; left again before its save is back: once
        leave("Prefers email.")
        leave()
        leave()
        assert page.evaluate(PUTS) == ["Prefers email."]
        saved(1)
        assert sent == ["Prefers email."]
        assert company.get(api).json()["notes"] == "Prefers email."
        # a space added at the end alone: no change
        leave("Prefers email. ")
        assert page.evaluate(PUTS) == ["Prefers email."]
        # a real change with spaces at its end: saved as typed
        leave("Prefers email; calls on Fridays.  ")
        assert page.evaluate(PUTS)[-1] == "Prefers email; calls on Fridays.  "
        saved(2)
        assert company.get(api).json()["notes"] == "Prefers email; calls on Fridays.  "
        # a change while one is on its way goes after it, not beside it
        leave("First.")
        leave("Second.")
        assert page.evaluate(PUTS)[2:] == ["First."]  # the second waits
        saved(3)
        page.wait_for_function("() => window.__puts.length === 4")
        assert page.evaluate(PUTS)[2:] == ["First.", "Second."]
        saved(4)
        assert sent[2:] == ["First.", "Second."]
        assert company.get(api).json()["notes"] == "Second."
        # a note of spaces in place of a note: cleared, saved empty, once
        leave("    ")
        assert page.evaluate(PUTS)[-1] == ""
        saved(5)
        assert (company.get(api).json()["notes"] or "") == ""
        leave("  ")
        leave()
        assert len(page.evaluate(PUTS)) == 5 and len(sent) == 5
    finally:
        page.close()


@pytest.mark.parametrize(
    ("kind", "box"), [("customers", "cust-notes"), ("vendors", "vend-notes")]
)
def test_a_failed_save_says_so_after_a_saved_one(browser, company, books, kind, box):
    """A "✓ saved" was cleared 1.5s later whatever the box said by then,
    so a "⚠ save failed" shown within those 1.5s was wiped too, and the
    failure went unsaid (review). A save that fails just after one that
    went through says so, and still does once the 1.5s are long past; a
    "✓ saved" alone clears as before."""
    rid = books["customer" if kind == "customers" else "vendor"]
    api = f"/api/{kind}/{rid}"
    sel = f"#{box}-{rid}"
    status = f"#{box.replace('notes', 'note-status')}-{rid}"
    page, handled = _open_at(browser, company, f"#/{kind}/{rid}")

    def leave(words):
        page.focus(sel)
        page.fill(sel, words)
        page.keyboard.press("Tab")

    def says():
        return page.evaluate("(s) => document.querySelector(s).textContent", status)

    try:
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector(sel)
        settle(page, handled)
        leave("Ships on Tuesdays.")
        page.wait_for_function(
            "(s) => document.querySelector(s).textContent === '✓ saved'", arg=status
        )
        # the next save fails, within the first one's 1.5s
        page.evaluate(
            """() => { window.__put = API.put;
                API.put = () => Promise.reject(new Error('The server did not answer')); }"""
        )
        leave("Ships on Tuesdays and Fridays.")
        page.wait_for_function(
            "(s) => document.querySelector(s).textContent === '⚠ save failed'",
            arg=status,
        )
        page.wait_for_timeout(2000)
        assert says() == "⚠ save failed"
        assert company.get(api).json()["notes"] == "Ships on Tuesdays."
        # a save that goes through clears its "✓ saved" as before
        page.evaluate("() => { API.put = window.__put; }")
        leave("Ships on Fridays.")
        page.wait_for_function(
            "(s) => document.querySelector(s).textContent === '✓ saved'", arg=status
        )
        page.wait_for_timeout(2000)
        assert says() == ""
        assert company.get(api).json()["notes"] == "Ships on Fridays."
    finally:
        page.close()
        company.put(api, json={"notes": ""})


# A click that, as in WebKit, does not put focus on the link clicked:
# the mouse goes down without moving focus; where focus was is noted.
WEBKIT_CLICKS = """() => {
    document.addEventListener('mousedown', e => e.preventDefault(), true);
    document.addEventListener('click', () => { window.__focusAtClick =
        document.activeElement && document.activeElement.dataset.rowKey; }, true); }"""


def test_back_after_a_mouse_click_keys_on_the_link_not_on_focus(
    browser, company, books
):
    """WebKit does not focus a link a mouse clicks, so the chart's account
    links and the drill-down's lines, noting document.activeElement, noted
    whatever had focus before: Back then went there (review of W-7). The
    link passes itself (ReportsPage._leaveFrom(this)): with focus left on
    another link, a click on an account name and on a line comes back to
    the one clicked."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _sidebar(page, handled, "#/accounts")
        links = page.evaluate(
            """() => [...document.querySelectorAll('#page-content a[data-row-key^="account:"]')]
                .map(a => [a.dataset.rowKey, a.getAttribute('href'), a.textContent.trim()])"""
        )
        bank = next(x for x in links if x[1].startswith("#/banking/"))
        other = next(x for x in links if x[0] != bank[0])
        page.focus(f'#page-content a[data-row-key="{other[0]}"]')
        page.evaluate(WEBKIT_CLICKS)
        page.click(f'#page-content a[data-row-key="{bank[0]}"]')
        page.wait_for_function("() => location.hash.startsWith('#/banking/')")
        settle(page, handled)
        assert page.evaluate("() => window.__focusAtClick") == other[0]
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg="#/accounts")
        settle(page, handled)
        assert page.evaluate(ROW_KEY) == bank[0]
        assert page.evaluate(WORDS) == bank[2]
        # a drill-down's line, the same
        checking = _account(company, "1000")
        _visit(
            page,
            handled,
            f"#/reports/account-transactions?account_id={checking['id']}"
            "&start_date=2026-01-01&end_date=2026-12-31&from=trial-balance",
        )
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        here = page.evaluate("location.hash")
        lines = page.evaluate(
            """() => [...document.querySelectorAll('#drilldown-body a[data-row-key^="line:"]')]
                .map(a => [a.dataset.rowKey, a.getAttribute('href'), a.textContent.trim()])"""
        )
        assert len(lines) >= 3, lines
        first, clicked = lines[0], lines[2]
        page.focus(f'#drilldown-body a[data-row-key="{first[0]}"]')
        page.click(f'#drilldown-body a[data-row-key="{clicked[0]}"]')
        page.wait_for_function(AT, arg="#" + clicked[1].split("#", 1)[1])
        page.wait_for_function("() => !document.getElementById('drilldown-body')")
        settle(page, handled)
        assert page.evaluate("() => window.__focusAtClick") == first[0]
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg=here)
        page.wait_for_selector("#drilldown-body table")
        settle(page, handled)
        assert page.evaluate(ROW_KEY) == clicked[0]
        assert page.evaluate(WORDS) == clicked[2]
    finally:
        page.close()


def _webkit_hop_and_back(page, handled, focus_sel, click_sel, gone, key):
    """Focus left on one element, a WebKit click (no focus move) on another
    that hops away; Back to the address left: focus on the one clicked, by
    its key."""
    here = page.evaluate("location.hash")
    page.focus(focus_sel)
    page.click(click_sel)
    page.wait_for_function(gone)
    settle(page, handled)
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_function(AT, arg=here)
    page.wait_for_function(
        "(k) => document.activeElement && document.activeElement.dataset.rowKey === k",
        arg=key,
    )
    return page.evaluate("() => window.__focusAtClick")


def test_back_after_a_mouse_click_keys_on_the_row_clicked_on_every_hop(
    browser, company, books
):
    """The other row hops noted the focused element too, so a WebKit click
    (which focuses nothing) brought Back to whatever had focus before
    (review of W-7): a report's row to a customer's page, a customer's
    invoice row to the invoice, a trial balance's account to its
    drill-down, a job's row to the job's page. Each passes the element
    clicked (its `this`; a drill-down's link is the click's own element),
    and Back puts focus on the row clicked."""
    customer, customer2 = books["customer"], books["customer2"]
    aging = "#/reports/ar-aging?as_of_date=2026-09-30"
    page, handled = _open_at(browser, company, aging)
    try:
        page.wait_for_selector(f'#report-content a[data-row-key="customer:{customer}"]')
        settle(page, handled)
        page.evaluate(WEBKIT_CLICKS)
        # a report's row → the customer's page → Back
        was = _webkit_hop_and_back(
            page,
            handled,
            f'#report-content a[data-row-key="customer:{customer2}"]',
            f'#report-content a[data-row-key="customer:{customer}"]',
            f"() => location.hash === '#/customers/{customer}'",
            f"customer:{customer}",
        )
        assert was == f"customer:{customer2}"  # focus was elsewhere at the click
        # the customer's page: an invoice row's amount → the invoice → Back
        _visit(page, handled, f"#/customers/{customer}")
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector('#modal-body a[data-row-key^="invoice:"]')
        settle(page, handled)
        keys = page.evaluate(
            """() => [...document.querySelectorAll('#modal-body a[data-row-key^="invoice:"]')]
                .map(a => a.dataset.rowKey)"""
        )
        assert len(keys) >= 2, keys
        first, row = keys[0], keys[-1]
        was = _webkit_hop_and_back(
            page,
            handled,
            f'#modal-body a[data-row-key="{first}"]',
            f'#modal-body tr:has(a[data-row-key="{row}"]) td.amount',
            "() => location.hash.startsWith('#/invoices/')",
            row,
        )
        assert was == first
        # a trial balance's account → its drill-down → Back
        tb = "#/reports/trial-balance?as_of_date=2026-09-30"
        _visit(page, handled, tb)
        page.wait_for_selector('#report-content a[data-row-key^="drill:"]')
        settle(page, handled)
        drills = page.evaluate(
            """() => [...document.querySelectorAll('#report-content a[data-row-key^="drill:"]')]
                .map(a => a.dataset.rowKey)"""
        )
        assert len(drills) >= 3, drills
        was = _webkit_hop_and_back(
            page,
            handled,
            f'#report-content a[data-row-key="{drills[0]}"]',
            f'#report-content a[data-row-key="{drills[2]}"]',
            "() => location.hash.startsWith('#/reports/account-transactions')",
            drills[2],
        )
        assert was == drills[0]
        # a job's row (its contract amount) → the job's page → Back
        jp = "#/reports/job-profitability?start_date=2026-01-01&end_date=2026-12-31"
        _visit(page, handled, jp)
        job = f"job:{books['job']}"
        page.wait_for_selector(f'#report-content a[data-row-key="{job}"]')
        settle(page, handled)
        was = _webkit_hop_and_back(
            page,
            handled,
            f'#report-content a[data-row-key="customer:{customer}"]',
            f'#report-content tr:has(a[data-row-key="{job}"]) td.amount >> nth=1',
            f"() => location.hash.startsWith('#/jobs/{books['job']}')",
            job,
        )
        assert was == f"customer:{customer}"
    finally:
        page.close()


# window.open, recorded instead of opened: the statement's download
HOLD_OPEN = """() => { window.__opened = [];
    window.open = (url, target) => { window.__opened.push([url, target]); return null; }; }"""


def test_back_from_a_pages_report_refocuses_its_link(browser, company, books):
    """The Reports links on a customer's and a vendor's page noted no row,
    so Back from the report put focus where the page puts it on opening
    (review of W-7). Each is still a real link to the report's address,
    with a key, and notes itself on its click: Back puts focus on the link
    left, by keyboard (Enter) and after a WebKit-style mouse click (focus
    not moved, left on another control). The statement (PDF) is a
    download: its click opens it and notes nothing."""
    pages = (
        (
            "customers",
            books["customer"],
            "report:income-by-customer",
            "report:ar-aging",
        ),
        ("vendors", books["vendor"], "report:ap-aging", None),
    )
    for kind, rid, key, other in pages:
        page, handled = _open_at(browser, company, f"#/{kind}/{rid}")
        try:
            page.wait_for_function(MODAL_SHOWN)
            link = f'#modal-body a[data-row-key="{key}"]'
            page.wait_for_selector(link)
            settle(page, handled)
            here = page.evaluate("location.hash")
            assert page.get_attribute(link, "href").startswith("#/reports/")
            # by keyboard: Enter on the link → the report → Back
            page.focus(link)
            page.keyboard.press("Enter")
            page.wait_for_function("() => location.hash.startsWith('#/reports/')")
            page.wait_for_function(MODAL_SHOWN)
            settle(page, handled)
            page.keyboard.press("Alt+ArrowLeft")
            page.wait_for_function(AT, arg=here)
            page.wait_for_function(
                "(k) => document.activeElement && document.activeElement.dataset.rowKey === k",
                arg=key,
            )
            # by a WebKit-style mouse click, focus left on another control
            if other is None:  # the vendor's: its first bill's link
                other = page.evaluate(
                    """() => document.querySelector('#modal-body a[data-row-key^="bill:"]')
                        .dataset.rowKey"""
                )
            page.evaluate(WEBKIT_CLICKS)
            was = _webkit_hop_and_back(
                page,
                handled,
                f'#modal-body a[data-row-key="{other}"]',
                link,
                "() => location.hash.startsWith('#/reports/')",
                key,
            )
            assert was == other
            if kind == "customers":
                # the statement: a download, opened; nothing noted, no hop
                page.evaluate(HOLD_OPEN)
                page.get_by_role("button", name="Statement (PDF)").click()
                opened = page.evaluate("() => window.__opened")
                assert len(opened) == 1 and opened[0][1] == "_blank", opened
                assert opened[0][0].startswith(
                    f"/api/reports/customer-statement/{rid}/pdf?as_of_date="
                )
                assert page.evaluate("location.hash") == here
                assert not page.evaluate("() => history.state && history.state.focus")
        finally:
            page.close()


ACTIVE_KEY = """() => { const a = document.activeElement;
    return (a.dataset && a.dataset.rowKey) || a.id || a.tagName.toLowerCase(); }"""
NOTED = "() => (history.state && history.state.focus) || null"


def test_a_click_that_opens_elsewhere_notes_nothing_and_steals_nothing(
    browser, company, books
):
    """A link that notes itself on its click (the chart's accounts, a
    page's Reports links) noted itself on a Ctrl+click too, which opens it
    in a new tab and leaves this page where it is: the page's next draw
    put focus on the link, off wherever the user had gone on to (review).
    Ctrl+click and Shift+click (a new tab, a new window) note nothing now,
    and the next draw leaves focus where it is: the chart, redrawn with
    the search box focused, and a customer's page, opened again, with
    focus where any opening puts it. A plain click still notes."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        # the browser opens the tab, with its own host's key (⌘ on a Mac,
        # Ctrl elsewhere): the page is told the platform it really is
        _platform(page, HOST_PLATFORM)
        _visit(page, handled, "#/accounts")
        link = '#page-content a[data-row-key^="account:"]'
        key = page.get_attribute(link, "data-row-key")
        for held in ("ControlOrMeta", "Shift"):
            with page.context.expect_page() as opened:
                page.click(link, modifiers=[held])
            opened.value.close()
            assert page.evaluate("location.hash") == "#/accounts", held
            assert page.evaluate(NOTED) is None, held
            page.focus("#global-search")
            _visit(page, handled, "#/accounts")
            assert page.evaluate(ACTIVE_KEY) == "global-search", held
        # a plain click, cut short before it leaves: still noted
        page.evaluate(
            "() => document.addEventListener('click', e => e.preventDefault(), { once: true })"
        )
        page.click(link)
        assert page.evaluate(NOTED) == key
        page.evaluate(
            "() => history.replaceState({ ...history.state, focus: null }, '')"
        )
    finally:
        page.close()
    page, handled = _open_at(browser, company, f"#/customers/{books['customer']}")
    try:
        _platform(page, HOST_PLATFORM)
        report = '#modal-body a[data-row-key="report:ar-aging"]'
        page.wait_for_selector(report)
        settle(page, handled)
        here = page.evaluate("location.hash")
        with page.context.expect_page() as opened:
            page.click(report, modifiers=["ControlOrMeta"])
        opened.value.close()
        assert page.evaluate("location.hash") == here
        assert page.evaluate(NOTED) is None
        page.focus("#modal-body textarea")
        page.evaluate("async () => { await App.navigate(location.hash); }")
        page.wait_for_selector(report)
        settle(page, handled)
        assert page.evaluate(ACTIVE_KEY) != "report:ar-aging"
    finally:
        page.close()


CARDS = "[...document.querySelectorAll('#page-content a.card-link')]"
CARD_LIST = f"""() => {CARDS}.map(a => ({{ key: a.dataset.rowKey, href: a.getAttribute('href'),
    title: a.querySelector('.card-header').textContent.trim(),
    text: a.querySelector('p').textContent.trim() }}))"""
FOCUS_KEY = "() => document.activeElement && document.activeElement.dataset.rowKey"
ON_PIN = "() => !!document.activeElement && document.activeElement.matches('.card-pin')"
# A card whose report puts another view's address in place of its own: the
# Unclassified P&L is the default class's P&L
OPENS_AT = {"report:profit-loss-unclassified": "#/reports/profit-loss-class"}


def _ax(page, selector):
    """The role, name and description the browser itself gives the element
    (Chromium's accessibility tree, over CDP)."""
    cdp = page.context.new_cdp_session(page)
    try:
        doc = cdp.send("DOM.getDocument", {"depth": 1})
        node = cdp.send(
            "DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector}
        )
        obj = cdp.send("DOM.resolveNode", {"nodeId": node["nodeId"]})
        tree = cdp.send(
            "Accessibility.getPartialAXTree",
            {"objectId": obj["object"]["objectId"], "fetchRelatives": False},
        )
        n = tree["nodes"][0]
        return {
            k: (n.get(k) or {}).get("value") for k in ("role", "name", "description")
        }
    finally:
        cdp.detach()


def _cards_by_keyboard(page, handled, cards, opened=None, home="#/reports"):
    """Tab reaches every card in turn (each Report Center card's pin, a
    star reached right after it, #395, is stepped over); Enter on each
    (those in `opened`, or all) opens what it links to — a report over the
    Report Center at its view's address (or the one it puts in place of
    it, OPENS_AT), or a page — and Back returns to `home` with focus on
    the card."""
    first = f'#page-content a[data-row-key="{cards[0]["key"]}"]'
    page.focus(first)
    page.keyboard.press("Shift+Tab")
    walked = []
    for _ in cards:
        page.keyboard.press("Tab")
        if page.evaluate(ON_PIN):
            page.keyboard.press("Tab")
        walked.append(page.evaluate(FOCUS_KEY))
    assert walked == [c["key"] for c in cards], walked
    assert page.evaluate("() => document.activeElement.matches(':focus-visible')")
    for c in cards:
        if opened is not None and c["key"] not in opened:
            continue
        sel = f'#page-content a[data-row-key="{c["key"]}"]'
        page.focus(sel)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUS_KEY) == c["key"]
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(h) => location.hash === h || location.hash.startsWith(h + '?')",
            arg=OPENS_AT.get(c["key"], c["href"]),
        )
        if c["href"].startswith("#/reports/"):
            page.wait_for_function(MODAL_SHOWN)
        else:
            page.wait_for_function(
                '(k) => !document.querySelector(`#page-content a[data-row-key="${k}"]`)',
                arg=c["key"],
            )
        settle(page, handled)
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg=home)
        page.wait_for_function(f"!({MODAL_SHOWN})()")
        page.wait_for_function(f"(k) => ({FOCUS_KEY})() === k", arg=c["key"])
        settle(page, handled)


def test_every_report_center_card_is_reached_and_opened_by_the_keyboard(
    browser, company, books
):
    """The Report Center's cards were a <div> with a click alone: the
    keyboard could not reach a report from there, nor a screen reader
    name one (review). Each is a link to its report's own address, named
    by its title and described by the line under it: Tab reaches every
    card in turn; Enter on each opens that report over the Report Center
    at its view's address, and Back returns with focus on the card; Space
    opens one too. It looks as the card did, and draws the ring and halo,
    as painted, in both themes."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/reports")
        cards = page.evaluate(CARD_LIST)
        assert (
            len(cards) == 28
        ), cards  # 2.24.0: four accountant's cards (#341–#343, #337); #395: one; #414: one; #435: two; #436: one
        for c in cards:
            view = c["key"].split(":", 1)[1]
            assert c["href"] == f"#/reports/{view}", c
            ax = _ax(page, f'#page-content a[data-row-key="{c["key"]}"]')
            assert ax == {
                "role": "link",
                "name": c["title"],
                "description": c["text"],
            }, (
                c,
                ax,
            )
        # the look: as a <div class="card"> with the same words beside it
        look = page.evaluate(
            f"""() => {{ const a = {CARDS}[0], d = document.createElement('div');
                d.className = 'card'; d.innerHTML = a.innerHTML.replace(/ id="[^"]*"/g, '');
                a.after(d);
                const style = (el) => {{ const cs = getComputedStyle(el);
                    return [cs.color, cs.backgroundColor, cs.borderTopColor, cs.borderTopWidth,
                            cs.paddingTop, cs.paddingLeft, cs.textDecorationLine, cs.fontSize,
                            cs.boxShadow, el.getBoundingClientRect().height].join(' | '); }};
                const parts = (el) => [el, el.querySelector('.card-header'), el.querySelector('p')].map(style);
                const out = [parts(a), parts(d), getComputedStyle(a).cursor];
                d.remove();
                return out; }}"""
        )
        assert look[0] == look[1] and look[2] == "pointer", look
        _cards_by_keyboard(page, handled, cards)
        # Space opens a card too
        sel = '#page-content a[data-row-key="report:general-ledger"]'
        page.focus(sel)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        page.keyboard.press(" ")
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_function(
            "() => location.hash.startsWith('#/reports/general-ledger')"
        )
        settle(page, handled)
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(
            f"(k) => ({FOCUS_KEY})() === k", arg="report:general-ledger"
        )
        settle(page, handled)
        # the ring and halo
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        _both_themes(page, "a Report Center card", GOLD)
    finally:
        page.close()


def test_a_nonprofits_report_center_cards_are_reached_and_opened_by_the_keyboard(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    """A nonprofit's Report Center leads with its own statements: their
    cards are links too, reached by Tab in turn, and Enter on the
    Statement of Activities opens it, Back returning to its card."""
    page, handled = _open(browser, client)
    try:
        _no_splash(page)
        _visit(page, handled, "#/reports")
        cards = page.evaluate(CARD_LIST)
        keys = [c["key"] for c in cards]
        assert keys[:6] == [
            "report:statement-of-activities",
            "report:statement-of-financial-position",
            "report:fund-balances",
            "report:functional-expenses",
            "report:pledges",
            "report:giving-statements",
        ], keys
        assert "report:profit-loss" not in keys and "report:balance-sheet" not in keys
        _cards_by_keyboard(
            page, handled, cards, opened={"report:statement-of-activities"}
        )
    finally:
        page.close()


# Every field under `root` (the page, or the open dialog) that Tab can reach,
# each focused in turn without a scroll and read: how it looks focused, and
# its ground at rest. Not a checkbox, a radio, a file or a button: those are
# controls, with the keyboard's ring.
FIELD_LOOKS = """(root) => { const r = document.querySelector(root);
    if (!r) return [];
    const skip = ['checkbox', 'radio', 'file', 'range', 'color', 'hidden', 'submit', 'button', 'reset', 'image'];
    const fields = [...r.querySelectorAll('input, select, textarea')].filter(el =>
        !skip.includes(el.type) && !el.disabled && el.tabIndex >= 0 && el.offsetWidth > 2
        && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
    return fields.map(el => {
        const rest = getComputedStyle(el);
        const ground = [rest.backgroundColor, rest.backgroundImage];
        el.focus({ preventScroll: true });
        const cs = getComputedStyle(el);
        const out = { at: el.id || el.name || el.getAttribute('aria-label') || (el.className || '').toString().slice(0, 30) || el.tagName.toLowerCase(),
            tag: el.tagName.toLowerCase(), type: el.type || '', focused: document.activeElement === el,
            readonly: el.readOnly === true, outline: cs.outlineStyle,
            border: [cs.borderTopStyle, cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor],
            ground: cs.backgroundColor, image: cs.backgroundImage, shadow: cs.boxShadow, rest: ground };
        el.blur();
        return out;
    }); }"""
# The quick-add boxes inside a form, shown as choosing "+ New …" shows them
QUICK_ADDS = [
    "(async () => {{ await InvoicesPage.showForm();"
    " document.getElementById('inv-new-customer-form').style.display = 'block'; }})()",
    "(async () => {{ await EstimatesPage.showForm();"
    " document.getElementById('est-new-customer-form').style.display = 'block'; }})()",
    "(async () => {{ await SalesReceiptsPage.showForm();"
    " document.getElementById('sr-new-customer-form').style.display = 'block'; }})()",
    "(async () => {{ await BillsPage.showForm();"
    " document.getElementById('bill-vendor-new').style.display = 'block'; }})()",
    "(async () => {{ await ExpensesPage.showForm();"
    " document.getElementById('expense-vendor-new').style.display = 'block'; }})()",
]


def _dark_ground(rest):
    """The ground a field shows at rest: its colour, or the colours of its
    gradient over a transparent one; dark (luminance under 0.05) or not."""
    colour, image = rest
    found = re.findall(r"rgba?\(([^)]*)\)", image or "")
    if not found or "gradient" not in (image or ""):
        found = re.findall(r"rgba?\(([^)]*)\)", colour)
    out = []
    for f in found:
        parts = [float(v) for v in f.split(",")[:4]]
        if len(parts) == 4 and parts[3] == 0:
            continue
        rgb = [v / 255 for v in parts[:3]]
        lin = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        out.append(0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])
    return bool(out) and max(out) < 0.05


def _fields_wrong(page, root, where, theme):
    """What is wrong with each field under `root` in `theme`: focused, it
    draws the field's focus style — no outline (no browser ring, no gold),
    the theme's blue on every side of its border, the pale ground (a
    read-only field keeps its grey), no halo — and at rest in the dark
    theme its ground is dark."""
    want = FIELD_FOCUS[theme]
    wrong, seen = [], 0
    for f in page.evaluate(FIELD_LOOKS, root):
        seen += 1
        bad = []
        if not f["focused"]:
            bad.append("no focus")
        if f["outline"] != "none":
            bad.append(f"outline {f['outline']}")
        if f["border"][0] == "none" or any(
            c != want["border"] for c in f["border"][1:]
        ):
            bad.append(f"border {f['border']}")
        if f["ground"] != want["ground"] and not f["readonly"]:
            bad.append(f"ground {f['ground']} {f['image'][:40]}")
        if "8px" in f["shadow"] and "inset" not in f["shadow"]:
            bad.append(f"halo {f['shadow']}")
        if theme == "dark" and not _dark_ground(f["rest"]):
            bad.append(f"rest {f['rest'][0]} {f['rest'][1][:40]}")
        if bad:
            wrong.append((where, theme, f["tag"], f["type"], f["at"], bad))
    return wrong, seen


def _sweep_fields(page, handled, books, pages, dialog_groups):
    """Every field of every page in `pages` and every dialog in
    `dialog_groups` ([(route, openers)]), in both themes."""
    wrong, seen, unopened = [], 0, []
    for route in pages:
        _visit(page, handled, route.format(**books))
        for theme in ("light", "dark"):
            _theme(page, theme)
            w, n = _fields_wrong(page, "#page-content", route, theme)
            wrong += w
            seen += n
        _theme(page, "light")
    for route, openers in dialog_groups:
        _visit(page, handled, route.format(**books))
        for opener in openers:
            call = opener.format(**books)
            page.evaluate("() => closeModal()")
            try:
                page.evaluate(f"async () => {{ await {call}; }}")
                page.wait_for_function(MODAL_SHOWN, timeout=5000)
            except Exception as exc:  # the call failed, or opened nothing
                unopened.append(f"{call}: {str(exc).splitlines()[0]}")
                continue
            settle(page, handled)
            for theme in ("light", "dark"):
                _theme(page, theme)
                w, n = _fields_wrong(page, "#modal", call, theme)
                wrong += w
                seen += n
            _theme(page, "light")
        page.evaluate("() => closeModal()")
    return wrong, seen, unopened


# One field of each kind that kept the browser's look, where it is: by the
# keyboard, as painted, in both themes; and with the system's high-contrast
# colours, the system's highlight as its ring
FIELD_KINDS = [
    ("#/customers", "#customer-search"),  # a list's search box
    ("#/invoices", "#inv-status-filter"),  # a list's filter
    ("#/jobs", "#job-filter-customer-box"),  # a type-ahead box
    ("#/budgets", "#page-content .budget-cell"),  # a budget's cell
    ("#/accounts", "#accounts-filter"),  # the chart's filter
    ("#/settings", "#new-class-name"),  # a Settings list's box
    ("#/settings", "#ai-settings-provider"),  # an AI panel's own box
    ("#/quick-entry", "#page-content .line-desc"),  # a line item
    ("#/deposits", "#deposit-date"),  # a toolbar's date
]


def test_every_field_keeps_a_fields_focus_style_on_every_page_and_dialog(
    browser, company, books
):
    """A field outside a form group — a list's search box and filters, a
    line item, a type-ahead box, Budgets' cells, Settings' lists, the
    chart's filter, a report's pickers and dates, a quick-add box — took
    the browser's own look, white in the dark theme, and its ring on focus
    (review); and since round 3 a form's own fields drew the light pale
    ground when focused in the dark theme (the light rule outranked the
    dark one). Every field of every page of the app and of every dialog
    the contrast sweeps open, and the quick-add boxes, in both themes:
    focused, it draws a field's own focus style (no outline: neither the
    browser's ring nor the keyboard's gold; the theme's focus blue on
    every side of its border; the pale ground, a read-only field's own
    grey kept; no halo), and at rest in the dark theme its ground is dark.
    One field of each kind, by the keyboard, as painted in both themes:
    the blue border on every side and nothing drawn outside it; and with
    the system's high-contrast colours, the system's highlight as its
    ring."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        pages = [f"#{p}" for p in paths if p not in REDIRECTS]
        pages += ["#/jobs/{job}", "#/banking/{checking}"]
        wrong, seen, unopened = _sweep_fields(
            page,
            handled,
            books,
            pages,
            [
                ("#/invoices", SALES + QUICK_ADDS),
                ("#/bills", PURCHASES),
                ("#/banking/{checking}", BANKING),
                ("#/reports", REPORTS),
                ("#/employees", PEOPLE),
                ("#/settings", SETTINGS),
            ],
        )
        assert unopened == []
        assert seen > 2000, seen
        assert wrong == [], (len(wrong), wrong)
        for route, sel in FIELD_KINDS:
            _visit(page, handled, route)
            page.wait_for_selector(sel)
            settle(page, handled)
            _keyed(page, f"document.querySelector({sel!r})")
            for theme in ("light", "dark"):
                _theme(page, theme)
                page.evaluate(SETTLED)
                _field_focus_painted(page, (route, sel), theme)
            _theme(page, "light")
            page.emulate_media(forced_colors="active")
            ring = page.evaluate(
                "() => { const cs = getComputedStyle(document.activeElement);"
                " return [cs.outlineStyle, cs.outlineWidth]; }"
            )
            page.emulate_media(forced_colors="none")
            assert ring == ["solid", "2px"], (route, sel, ring)
            page.evaluate("() => document.activeElement.blur()")
    finally:
        page.emulate_media(forced_colors="none")
        page.close()


def test_a_nonprofits_fields_keep_a_fields_focus_style_too(
    browser, client, nonprofit  # noqa: F811  (the fixture, imported above)
):
    """A nonprofit's own pages and dialogs: every field, in both themes,
    focused draws a field's own focus style, and at rest in the dark theme
    its ground is dark."""
    page, handled = _open(browser, client)
    try:
        _no_splash(page)
        wrong, seen, unopened = _sweep_fields(
            page,
            handled,
            nonprofit,
            NONPROFIT_PAGES,
            [("#/invoices", NONPROFIT_DIALOGS)],
        )
    finally:
        page.close()
    assert unopened == []
    assert seen > 100, seen
    assert wrong == [], (len(wrong), wrong)


# A page's card links: key, address, name (aria-label) and the words of its
# description (what aria-describedby names), as the page has them
CARD_LINKS = """(sel) => [...document.querySelectorAll(sel)].map(a => ({ key: a.dataset.rowKey,
    href: a.getAttribute('href'), title: a.getAttribute('aria-label'),
    text: (a.getAttribute('aria-describedby') || '').split(/\\s+/)
        .map(id => document.getElementById(id)).filter(Boolean)
        .map(el => el.textContent.replace(/\\s+/g, ' ').trim()).join(' ') }))"""
# The first card link beside a <div class="card"> with the same words: the
# computed look of the card and of each element in it, and its height
CARD_LOOK = """(sel) => { const a = document.querySelector(sel), d = document.createElement('div');
    d.className = 'card'; d.innerHTML = a.innerHTML.replace(/ id="[^"]*"/g, '');
    a.after(d);
    const style = (el) => { const cs = getComputedStyle(el);
        return [cs.color, cs.backgroundColor, cs.borderTopColor, cs.borderTopWidth,
                cs.paddingTop, cs.paddingLeft, cs.textDecorationLine, cs.fontSize,
                cs.boxShadow, el.getBoundingClientRect().height].join(' | '); };
    const parts = (el) => [el, ...el.querySelectorAll('*')].map(style);
    const out = [parts(a), parts(d), getComputedStyle(a).cursor];
    d.remove();
    return out; }"""


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _bank_cards(page, handled, route, sel):
    """The bank cards on `route`: links to their registers, named by the
    account and described by the rest of the card, as the browser computes
    it; looking as the card did; reached, opened and come back to by the
    keyboard (Enter, and Space on the first); the ring and halo painted in
    both themes."""
    _visit(page, handled, route)
    page.wait_for_selector(sel)
    settle(page, handled)
    cards = page.evaluate(CARD_LINKS, sel)
    assert len(cards) >= 2, (route, cards)
    for c in cards:
        account = c["key"].split(":", 1)[1]
        assert c["key"] == f"bank:{account}" and c["href"] == f"#/banking/{account}", c
        ax = _ax(page, f'#page-content a[data-row-key="{c["key"]}"]')
        assert ax["role"] == "link" and ax["name"] == c["title"], (c, ax)
        assert _norm(ax["description"]) == _norm(c["text"]) and c["text"], (c, ax)
    look = page.evaluate(CARD_LOOK, sel)
    assert look[0] == look[1] and look[2] == "pointer", look
    _cards_by_keyboard(page, handled, cards, home=route)
    # Space opens one too
    first = f'#page-content a[data-row-key="{cards[0]["key"]}"]'
    page.focus(first)
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    page.keyboard.press(" ")
    page.wait_for_function("(h) => location.hash === h", arg=cards[0]["href"])
    settle(page, handled)
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_function(f"(k) => ({FOCUS_KEY})() === k", arg=cards[0]["key"])
    settle(page, handled)
    # the ring and halo
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    assert page.evaluate(FOCUS_KEY) == cards[0]["key"]
    _both_themes(page, ("a bank card", route), GOLD)


def test_banking_and_the_dashboards_bank_cards_are_links_the_keyboard_opens(
    browser, company, books
):
    """Banking's account cards and the dashboard's bank cards were a <div>
    with a click alone, as the Report Center's were (review). Each is a
    link to its account's register, named by the account and described by
    the rest of the card: on Banking and on the dashboard, the browser
    computes that name and description; the card looks as a <div
    class="card"> with the same words (the computed look of it and of
    everything in it, and its height); Tab reaches every card in turn,
    Enter on each opens its register, and Back returns with focus on the
    card; Space opens one; and the focused card draws the ring and halo,
    as painted, in light and dark."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _bank_cards(page, handled, "#/banking", "#page-content a.card-link")
        _bank_cards(
            page,
            handled,
            "#/",
            '#page-content [data-widget="bank_balances"] a.card-link',
        )
    finally:
        page.close()


# The far end of a field, where the browser draws its own part (a date's
# calendar icon, a select's arrow), inside the border: its ground (the
# median pixel), the part (the dozen pixels furthest from the ground), and
# the field's color-scheme
FIELD_END = """(sel) => { const e = document.querySelector(sel), r = e.getBoundingClientRect();
    return { x: r.left + r.width - 26, y: r.top + 3, w: 22, h: r.height - 6,
             scheme: getComputedStyle(e).colorScheme }; }"""


def _part_contrast(page, sel):
    """The browser's own part at the field's far end, as painted: its
    contrast against the field's ground there, and the field's
    color-scheme."""
    import io
    import math
    import statistics

    from PIL import Image

    b = page.evaluate(FIELD_END, sel)
    clip = {
        "x": math.floor(b["x"]),
        "y": math.floor(b["y"]),
        "width": math.ceil(b["w"]),
        "height": math.ceil(b["h"]),
    }
    img = Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")
    px = list(img.getdata())
    ground = [statistics.median(c[k] for c in px) for k in range(3)]
    far = sorted(px, key=lambda c: -_far(c, ground))[:12]
    part = [sum(c[k] for c in far) / len(far) for k in range(3)]
    return contrast(_rgb(part), _rgb(ground)), b["scheme"]


# Each kind of date field and select, where it is: (route or opener, field)
FIELD_PARTS = [
    ("() => InvoicesPage.showForm()", "#modal .form-group input[type=date]"),
    (
        f"#/reports/profit-loss?start_date={SEPT[0]}&end_date={SEPT[1]}",
        "#report-custom-start",
    ),
    (
        f"#/reports/profit-loss?start_date={SEPT[0]}&end_date={SEPT[1]}",
        "#report-period-select",
    ),
    ("#/iif", "#page-content .iif-date-range input[type=date]"),
    ("#/invoices", "#inv-status-filter"),
    (
        "#/reports/general-ledger?start_date=2025-01-01&end_date=2026-12-31",
        "#gl-account",
    ),
]


def test_a_dark_fields_own_parts_are_drawn_for_a_dark_field(browser, company, books):
    """The browser draws a field's own parts — a date's calendar icon, a
    select's arrow — for a light page unless the field says it is dark:
    black on the dark field, 1.15:1 (review), on a form's dates, a
    report's From and To, IIF export's dates and the selects. In the dark
    theme the fields say so (color-scheme: dark): each kind's icon or arrow
    stands 3:1 against its field as painted; in light the field is as it
    was (color-scheme normal) and its icon or arrow 3:1 too."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        seen = {}
        for where, sel in FIELD_PARTS:
            if where.startswith("#/"):
                _visit(page, handled, where)
            else:
                page.evaluate("() => closeModal()")
                page.evaluate(f"async () => {{ await ({where})(); }}")
                page.wait_for_function(MODAL_SHOWN)
            page.wait_for_selector(sel)
            settle(page, handled)
            page.evaluate(
                f"() => document.querySelector({sel!r}).scrollIntoView({{block: 'center'}})"
            )
            for theme in ("light", "dark"):
                _theme(page, theme)
                page.evaluate(SETTLED)
                seen[(sel, theme)] = _part_contrast(page, sel)
            _theme(page, "light")
            page.evaluate("() => closeModal()")
    finally:
        page.close()
    for (sel, theme), (ratio, scheme) in seen.items():
        assert scheme == ("dark" if theme == "dark" else "normal"), (sel, theme, scheme)
        assert ratio >= 3.0, (sel, theme, ratio, seen)


# Each plain table container on the page with rows: its padding below the
# table, its last row's cells' padding, top and bottom, and how far the
# words of that row's first cell sit from the middle of their row
LAST_ROWS = """() => [...document.querySelectorAll('#page-content .table-container')]
    .filter(c => c.offsetParent && !c.classList.contains('table-container--scroll')
                 && c.querySelector(':scope > table > tbody > tr'))
    .map(c => { const rows = c.querySelectorAll(':scope > table > tbody > tr'),
            last = rows[rows.length - 1], cell = last.querySelector('td, th'),
            cs = getComputedStyle(cell), range = document.createRange();
        range.selectNodeContents(cell);
        const words = range.getBoundingClientRect(), box = last.getBoundingClientRect();
        return { rows: rows.length, below: getComputedStyle(c).paddingBottom,
                 pad: [cs.paddingTop, cs.paddingBottom],
                 off: words.height ? (words.top + words.bottom) / 2 - (box.top + box.bottom) / 2 : 0,
                 text: cell.textContent.trim().slice(0, 30) }; })"""


def test_a_one_row_tables_words_sit_in_the_middle_of_their_row(browser, company, books):
    """The room for the last row's halo was 8px of padding on the last
    row's cells, which set a one-row table's words high in their row (Time
    Off's: final review). It is the container's, below its table: on Time
    Off, the Journal and Items, every plain table container has 8px below
    its table, its last row's cells as much padding above as below, and
    that row's words in the middle of the row (within a pixel); the last
    row's Void still draws its ring and halo whole (the test above)."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        seen = []
        for route in ("#/hr/pto", "#/journal", "#/items"):
            _visit(page, handled, route)
            for t in page.evaluate(LAST_ROWS):
                seen.append((route, t))
                assert t["below"] == "8px", (route, t)
                assert t["pad"][0] == t["pad"][1], (route, t)
                assert abs(t["off"]) <= 1, (route, t)
        assert any(t["rows"] == 1 for _, t in seen), seen
    finally:
        page.close()


# Every plain table container on the page: how far its table overflows it
# sideways, and whether it scrolls
WIDE = """() => [...document.querySelectorAll('#page-content .table-container')]
    .filter(c => c.offsetParent && !c.classList.contains('table-container--scroll'))
    .map((c, i) => { c.dataset.wideProbe = String(i);
        const was = c.scrollLeft;
        c.scrollLeft = c.scrollWidth;
        const moves = c.scrollLeft > 0;
        c.scrollLeft = was;
        return { i, over: c.scrollWidth - c.clientWidth, scroll: getComputedStyle(c).overflowX, moves }; })"""
# The last row's stops of container `i`, numbered
LAST_STOPS = """(i) => { const c = document.querySelector(`[data-wide-probe="${i}"]`),
        rows = c.querySelectorAll(':scope > table > tbody > tr'), last = rows[rows.length - 1];
    return [...last.querySelectorAll('a[href], button, input, select, textarea')]
        .filter(e => !e.disabled && e.tabIndex >= 0 && e.getClientRects().length && e.type !== 'hidden')
        .map((e, k) => { e.dataset.lastStop = `${i}-${k}`; return `${i}-${k}`; }); }"""
# The focused stop against what its container shows: how far its ring and
# halo (8px; a field's own border, 0) end inside on each side
STOP_INSIDE = """() => { const a = document.activeElement, c = a.closest('.table-container'),
        r = a.getBoundingClientRect(), cr = c.getBoundingClientRect(),
        out = a.matches('input, select, textarea') ? 0 : 8,
        left = cr.left + c.clientLeft, top = cr.top + c.clientTop;
    return { tag: a.tagName.toLowerCase(), text: (a.textContent || a.value || a.name || '').trim().slice(0, 20),
             field: out === 0, scrollLeft: c.scrollLeft,
             reach: [r.left - out - left, r.top - out - top,
                     left + c.clientWidth - (r.right + out), top + c.clientHeight - (r.bottom + out)] }; }"""
# A table too wide for its container at a width, where the test books
# make it so (the Journal's and the customers' at 390 too)
WIDE_TABLES = [
    (1440, "#/budgets"),
    (1024, "#/fixed-assets"),
    (1024, "#/jobs"),
    (900, "#/items"),
    (900, "#/hr/time-entries"),
    (900, "#/hr/pto"),
    (900, "#/fixed-assets"),
    (390, "#/journal"),
    (390, "#/customers"),
]


def test_a_table_wider_than_its_container_scrolls_and_tab_shows_its_stops_whole(
    browser, company, books
):
    """A table wider than its container was clipped, its last column cut
    off even at 900px, the desktop app's least (Items' History, Benefits'
    Retire, Fixed Assets' Dispose at 1024): out of reach for a mouse, and
    Tab left a stop partly in view, its ring cut (final review). The
    container scrolls it sideways now, and Tab brings a stop in whole. At
    1440 and 1280, on every page, no container that fits shows a scrollbar
    or overflows by a pixel of rounding; where a table is wider (Budgets at
    1440; Fixed Assets and Jobs at 1024; Items, Time Entries, Time Off and
    Fixed Assets at 900; the Journal and the customers at 390) its
    container scrolls sideways, and each of its last row's stops, reached
    by Tab, ends with its ring and halo (a field, its border) inside what
    the container shows, the ring painted on all four sides."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        paths = page.evaluate(
            "() => Object.keys(App.routes).filter(k => !k.includes('/:'))"
        )
        pages = [f"#{p}" for p in paths if p not in REDIRECTS]
        for width in (1440, 1280):
            page.set_viewport_size({"width": width, "height": 800})
            for route in pages:
                _visit(page, handled, route)
                for c in page.evaluate(WIDE):
                    assert c["scroll"] == "auto", (width, route, c)
                    # fits, so no scrollbar (none shows unless the table is
                    # wider), or plainly wider: never a pixel of rounding
                    assert c["over"] <= 0 or c["over"] >= 8, (width, route, c)
                    assert c["moves"] == (c["over"] > 0), (width, route, c)
        for width, route in WIDE_TABLES:
            page.set_viewport_size({"width": width, "height": 800})
            _visit(page, handled, route)
            wide = [c for c in page.evaluate(WIDE) if c["over"] > 0]
            assert wide, (width, route)
            c = wide[0]
            assert c["moves"], (width, route, c)  # it scrolls, for a mouse too
            stops = page.evaluate(LAST_STOPS, c["i"])
            assert stops, (width, route)
            for stop in stops:
                _keyed(page, f"document.querySelector('[data-last-stop=\"{stop}\"]')")
                page.evaluate(SETTLED)
                at = page.evaluate(STOP_INSIDE)
                assert min(at["reach"]) >= -0.5, (width, route, at)
                if not at["field"]:
                    sides = _painted(page)
                    cut = [s for s in SIDES if not sides[s] or sides[s]["px"] < 2]
                    assert not cut, (width, route, at, cut)
    finally:
        page.set_viewport_size({"width": 1500, "height": 980})
        page.close()


# _opensElsewhere on a click on the chart's first account link, with the
# keys held and the button pressed given, on the platform given; the click
# itself goes nowhere
OPENS_ELSEWHERE = """([platform, init]) => {
    Object.defineProperty(Navigator.prototype, 'platform', { get: () => platform, configurable: true });
    const a = document.querySelector('#page-content a[data-row-key^="account:"]');
    let got = null;
    const look = (e) => { got = ReportsPage._opensElsewhere(e); e.preventDefault(); };
    a.addEventListener('click', look);
    a.addEventListener('auxclick', look);
    a.dispatchEvent(new MouseEvent(init.button ? 'auxclick' : 'click',
        Object.assign({ bubbles: true, cancelable: true }, init)));
    a.removeEventListener('click', look);
    a.removeEventListener('auxclick', look);
    return got; }"""


def test_the_new_tab_modifier_is_the_platforms_own(browser, company, books):
    """A new tab is ⌘-click on a Mac and Ctrl-click elsewhere, where a
    Windows or Super key click follows the link in place, and on a Mac a
    Ctrl-click is the context menu's (final review: Meta counted as "opens
    elsewhere" everywhere). On a Mac, ⌘, Shift, Option or the middle button
    open the link elsewhere and Ctrl does not; on Windows and Linux, Ctrl,
    Shift, Alt or the middle button do and the Windows key does not; a
    plain click never does. In this browser (Linux), a Super-click on a
    chart account follows the link in place, is noted, and Back puts focus
    back on it."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/accounts")
        for platform, opens in (
            (
                "MacIntel",
                {"metaKey": True, "shiftKey": True, "altKey": True, "button": True},
            ),
            (
                "Win32",
                {"ctrlKey": True, "shiftKey": True, "altKey": True, "button": True},
            ),
            (
                "Linux x86_64",
                {"ctrlKey": True, "shiftKey": True, "altKey": True, "button": True},
            ),
        ):
            for held in ("metaKey", "ctrlKey", "shiftKey", "altKey", "button", None):
                init = (
                    {"button": 1}
                    if held == "button"
                    else ({held: True} if held else {})
                )
                got = page.evaluate(OPENS_ELSEWHERE, [platform, init])
                assert got is bool(opens.get(held)), (platform, held, got)
        page.evaluate("""() => Object.defineProperty(Navigator.prototype, 'platform',
                { get: () => 'Linux x86_64', configurable: true })""")
        if sys.platform == "darwin":
            # the browser's own answer to ⌘-click on a Mac is a new tab, and
            # to Ctrl-click the context menu: no key follows the link in
            # place here, so the Super-click below is the Linux and Windows
            # browsers' to show
            return
        # a Super-click on Linux: the link is followed here, and noted
        link = '#page-content a[data-row-key^="account:"]'
        key = page.get_attribute(link, "data-row-key")
        href = page.get_attribute(link, "href")
        pages = len(page.context.pages)
        page.click(link, modifiers=["Meta"])
        page.wait_for_function("(h) => location.hash === h", arg=href)
        settle(page, handled)
        assert len(page.context.pages) == pages
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(AT, arg="#/accounts")
        page.wait_for_function(f"(k) => ({FOCUS_KEY})() === k", arg=key)
    finally:
        page.close()


def test_a_file_picker_draws_the_keyboards_ring(browser, company, books):
    """A file's picker — QuickBooks Interop's report CSV, an invoice's
    attachment, the chart's import — drew the browser's own ring on Tab,
    where a button draws the keyboard's gold (final review). Reached by
    Tab, each draws the gold ring and its halo on all four sides, 3:1 as
    painted against the halo on both sides and against the gate's ground,
    in light and dark."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        _visit(page, handled, "#/iif")
        page.wait_for_selector("#qbcsv-file-input")
        _keyed(page, "document.getElementById('qbcsv-file-input')")
        _both_themes(page, "QuickBooks report CSV", GOLD)
        for opener, sel in (
            (f"() => InvoicesPage.view({books['sent']})", "#inv-attach-file"),
            ("() => App.showChartImport()", "#modal input[type=file]"),
        ):
            page.evaluate("() => closeModal()")
            page.evaluate(f"async () => {{ await ({opener})(); }}")
            page.wait_for_function(MODAL_SHOWN)
            page.wait_for_selector(sel)
            settle(page, handled)
            _keyed(page, f"document.querySelector({sel!r})")
            _both_themes(page, sel, GOLD)
        page.evaluate("() => closeModal()")
    finally:
        page.close()


# The focused control against its scrolling container: how far its ring
# and halo (8px) end inside what the container shows, right and bottom
SCROLL_ROOM = """() => { const a = document.activeElement, c = a.closest('.table-container'),
        r = a.getBoundingClientRect(), cr = c.getBoundingClientRect();
    return { text: (a.textContent || '').trim().slice(0, 20), scrolls: c.scrollWidth > c.clientWidth,
             right: cr.left + c.clientLeft + c.clientWidth - (r.right + 8),
             bottom: cr.top + c.clientTop + c.clientHeight - (r.bottom + 8),
             sticky: getComputedStyle(c.querySelector('tbody td') || c).position }; }"""
# The last button of a scrolling table's last row in the open dialog
LAST_IN_SCROLLER = """() => [...document.querySelectorAll(
    '#modal .table-container--scroll tbody tr:last-child button')].filter(b => b.offsetParent).pop()"""


def test_a_scrolling_tables_last_row_and_column_draw_their_whole_halo(
    browser, company, books
):
    """A line-item table's scrolling container (Job Cost Entry's lines, an
    estimate's, the pay run) had none of the room a plain one has: its
    last row's ✕ and the pay run's last Stub PDF sat 3 to 5px from its
    edges, their halo cut at the bottom and the right, and once it
    scrolled the ring sat on the scrollbar (final review). It has 8px
    below its table and at its right end now: at 1440, 1024 and 900 each,
    reached by Tab, ends with its ring and halo inside what the container
    shows, whether the container scrolls or not, and draws the ring 3:1 as
    painted on all four sides against the halo and the gate's ground (in
    both themes at 1440); the pay run's Employee column is still held."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        scrolled = set()
        for width in (1440, 1024, 900):
            page.set_viewport_size({"width": width, "height": 800})
            for what, open_it in (
                ("Job Cost Entry's last ✕", "() => JobCostsPage.showForm()"),
                ("an estimate's last ✕", "() => EstimatesPage.showForm()"),
                (
                    "the pay run's last Stub PDF",
                    f"() => PayrollPage.view({books['pay_run']})",
                ),
            ):
                page.evaluate("() => closeModal()")
                page.evaluate(f"async () => {{ await ({open_it})(); }}")
                page.wait_for_function(MODAL_SHOWN)
                settle(page, handled)
                _keyed(page, f"({LAST_IN_SCROLLER})()")
                page.evaluate(SETTLED)
                room = page.evaluate(SCROLL_ROOM)
                assert room["right"] >= -0.5 and room["bottom"] >= -0.5, (
                    width,
                    what,
                    room,
                )
                if room["scrolls"]:
                    scrolled.add(what)
                if "pay run" in what:
                    held = page.evaluate(
                        """() => getComputedStyle(document.querySelector(
                            '#modal .pay-run-table td:first-child')).position"""
                    )
                    assert held == "sticky", (width, held)
                if width == 1440:
                    _both_themes(page, (what, width), GOLD)
                else:
                    _ring_clears(page, (what, width), GOLD["light"])
            page.evaluate("() => closeModal()")
        # and among them a container that scrolls
        assert scrolled, scrolled
    finally:
        page.set_viewport_size({"width": 1500, "height": 980})
        page.close()


# A heading the grid's right edge cuts with 36px or more of it shown (so
# that Chromium's own focus leaves it where it is): a point on its shown
# part
CUT_HEADING = """() => { const g = document.getElementById('grid-scroll'), gr = g.getBoundingClientRect(),
        edge = gr.left + g.clientLeft + g.clientWidth;
    const a = [...g.querySelectorAll('thead a')].find(a => { const r = a.getBoundingClientRect();
        return edge - r.left >= 36 && r.right > edge + 2; });
    if (!a) return null;
    const r = a.getBoundingClientRect();
    return { text: a.textContent.trim(), x: (r.left + Math.min(r.right, edge - 3)) / 2, y: r.top + r.height / 2 }; }"""
HEADING_ROOM = """() => { const a = document.activeElement, g = document.getElementById('grid-scroll'),
        r = a.getBoundingClientRect(), gr = g.getBoundingClientRect();
    return { text: (a.textContent || '').trim(), input: App._input,
             right: gr.left + g.clientLeft + g.clientWidth - (r.right + 8) }; }"""


def test_a_key_after_a_click_is_the_keyboards(browser, company, books, divisions):
    """What moved focus last was the keyboard only after Tab or an arrow
    key, so after a click a Back chord that is neither — the Mac's ⌘[ —
    left the heading it put focus back on 79px past the grid's edge
    (final review). Any key but a modifier held alone is the keyboard's
    now, and a click is still the pointer's: with the platform a Mac's, a
    click on a heading the grid's edge cuts (36px or more of it shown, so
    that Chromium's own focus would leave it) opens its class's P&L, and
    ⌘[ comes back with focus on that heading, brought in whole with its
    ring and halo."""
    page, handled = _open_at(browser, company, BY_CLASS_URL)
    try:
        page.wait_for_selector("#report-content .pivot-grid")
        # a window width at which the grid's edge cuts a heading so
        cut = None
        for width in range(1000, 1400, 8):
            page.set_viewport_size({"width": width, "height": 768})
            page.wait_for_timeout(50)
            cut = page.evaluate(CUT_HEADING)
            if cut:
                break
        assert cut, "no heading cut at the grid's edge"
        settle(page, handled)
        page.evaluate("""() => Object.defineProperty(Navigator.prototype, 'platform',
                { get: () => 'MacIntel', configurable: true })""")
        page.mouse.click(cut["x"], cut["y"])
        page.wait_for_function(
            "() => location.hash.startsWith('#/reports/profit-loss-class')"
        )
        settle(page, handled)
        assert page.evaluate("() => App._input") == "pointer"
        page.keyboard.press("Meta+BracketLeft")
        page.wait_for_function(
            "() => location.hash.startsWith('#/reports/profit-loss-by-class')"
        )
        page.wait_for_function(
            "(t) => (document.activeElement.textContent || '').trim() === t",
            arg=cut["text"],
        )
        settle(page, handled)
        room = page.evaluate(HEADING_ROOM)
        assert room["input"] == "keyboard" and room["right"] >= -0.5, (cut, room)
    finally:
        page.close()


SCROLL_REGION = "#modal .table-container"


def test_a_wide_table_with_no_control_is_a_region_the_keyboard_scrolls(
    browser, company, books
):
    """A table wider than its box with no control inside it — Benefits'
    Rates, 106px wider — scrolled for a mouse but not for the keyboard,
    since a dialog's Tab walks controls only (WCAG 2.1.1; final review).
    Such a box takes Tab itself, a region named by its heading or its
    dialog's title: on the Rates dialog it is a region named "Rates — …"
    as the browser computes it, Tab reaches it and it draws the keyboard's
    ring as painted in both themes, and → scrolls it. A table that fits,
    or one that holds a control (Fixed Assets' at 1024, its Dispose
    buttons), takes no Tab of its own."""
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        _visit(page, handled, "#/hr/benefits")
        page.evaluate(
            f"async () => {{ await BenefitsPage.showRates({books['benefit_code']}); }}"
        )
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector(f"{SCROLL_REGION}[data-scroll-region]")
        settle(page, handled)
        title = page.evaluate(
            "() => document.getElementById('modal-title').textContent.trim()"
        )
        ax = _ax(page, SCROLL_REGION)
        assert ax["role"] == "region" and ax["name"] == title, (ax, title)
        assert title.startswith("Rates")
        _keyed(page, f"document.querySelector({SCROLL_REGION!r})")
        assert page.evaluate("() => document.activeElement.matches(':focus-visible')")
        _both_themes(page, "the Rates table", GOLD)
        before = page.evaluate(
            f"() => document.querySelector({SCROLL_REGION!r}).scrollLeft"
        )
        page.keyboard.press("ArrowRight")
        page.wait_for_function(
            f"(b) => document.querySelector({SCROLL_REGION!r}).scrollLeft > b",
            arg=before,
        )
        page.evaluate("() => closeModal()")
        # one that holds a control, and one that fits: nothing of their own
        for width, route in ((1024, "#/fixed-assets"), (1440, "#/customers")):
            page.set_viewport_size({"width": width, "height": 800})
            _visit(page, handled, route)
            page.wait_for_timeout(100)
            marks = page.evaluate(
                """() => [...document.querySelectorAll('#page-content .table-container')]
                    .filter(c => c.offsetParent).map(c => [c.scrollWidth > c.clientWidth,
                        c.hasAttribute('tabindex'), c.getAttribute('role')])"""
            )
            assert marks and all(m[1:] == [False, None] for m in marks), (route, marks)
            if route == "#/fixed-assets":
                assert any(m[0] for m in marks), marks  # it does overflow
    finally:
        page.set_viewport_size({"width": 1500, "height": 980})
        page.close()


# ── The gate's third round (macOS) ──────────────────────────────────────────

FOCUS_SPY = """() => { window.__focusCalls = [];
  if (window.__focusSpied) return; window.__focusSpied = true;
  const orig = HTMLElement.prototype.focus;
  HTMLElement.prototype.focus = function (opts) {
    window.__focusCalls.push({ key: (this.dataset && this.dataset.rowKey) || this.id || this.tagName,
                               visible: !!(opts && opts.focusVisible) });
    return orig.call(this, opts);
  }; }"""
CALLS = "() => window.__focusCalls.splice(0)"


def test_focus_moved_for_the_keyboard_says_so_and_a_clicks_does_not(
    browser, company, books
):
    """NEW-43, round 3: in a dialog opened with a click, Tab's ring didn't
    show on WebKit — script-moved focus counts as the keyboard's there only
    after keyboard focus. Every focus the app moves because of the keyboard
    now asks for the ring outright (focus({ focusVisible: true })); after a
    pointer it leaves the browser's own rule. Chromium draws the ring either
    way, so this records what the app asks for, which is what WebKit obeys."""
    page, handled = _open_at(browser, company, "#/reports")
    try:
        _no_splash(page)
        page.evaluate(FOCUS_SPY)
        # A/R Aging opened with a mouse click on its card
        page.locator("#page-content a.card-link", has_text="A/R Aging").first.click()
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector('#modal a[data-row-key^="customer:"]')
        settle(page, handled)
        opened = page.evaluate(CALLS)
        assert opened and not any(c["visible"] for c in opened), opened
        # Tab inside it: every move asks for the ring
        for _ in range(3):
            page.keyboard.press("Tab")
        tabbed = page.evaluate(CALLS)
        assert len(tabbed) >= 3 and all(c["visible"] for c in tabbed), tabbed
        # Enter on a customer's row, then the Back chord: the row comes back
        # with the ring asked for
        row = '#modal a[data-row-key^="customer:"]'
        page.focus(row)
        key = page.get_attribute(row, "data-row-key")
        page.keyboard.press("Enter")
        page.wait_for_function("() => /^#\\/customers\\/\\d+$/.test(location.hash)")
        settle(page, handled)
        page.evaluate(CALLS)
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function(
            "(k) => document.activeElement && document.activeElement.dataset.rowKey === k",
            arg=key,
        )
        back = [c for c in page.evaluate(CALLS) if c["key"] == key]
        assert back and back[-1]["visible"], back
        # the same hop by mouse, and the toolbar's Back clicked: no ring asked
        page.click(row)
        page.wait_for_function("() => /^#\\/customers\\/\\d+$/.test(location.hash)")
        settle(page, handled)
        page.evaluate(CALLS)
        page.click("#back-btn")
        page.wait_for_function(
            "(k) => document.activeElement && document.activeElement.dataset.rowKey === k",
            arg=key,
        )
        clicked = [c for c in page.evaluate(CALLS) if c["key"] == key]
        assert clicked and not clicked[-1]["visible"], clicked
    finally:
        page.close()


def test_the_search_shortcut_does_nothing_over_a_dialog(browser, company, books):
    """NEW-45: ⌘K over an open dialog moved focus to the search box under
    the dimmed overlay, and typing opened its hits behind the dialog. Over a
    dialog the shortcut now does nothing; on a page it finds as before."""
    page, handled = _open_at(browser, company, "#/invoices")
    try:
        _no_splash(page)
        _open_dialog(page, handled, "InvoicesPage.showForm()", "#invoice-form")
        page.fill("#inv-lines .line-desc", "kept")
        page.focus("#inv-lines .line-desc")
        for keys, mac in (("Control+KeyK", False), ("Meta+KeyK", True)):
            page.evaluate(MAC, mac)
            page.keyboard.press(keys)
            assert (
                page.evaluate("() => document.activeElement.id") != "global-search"
            ), keys
            assert page.evaluate(
                "() => document.getElementById('modal').contains(document.activeElement)"
            ), keys
            assert page.evaluate(MODAL_SHOWN)
            assert page.input_value("#inv-lines .line-desc") == "kept"
        page.evaluate(MAC, False)
        # "/" from a button in the dialog: nothing either
        page.focus("#modal-body button")
        page.keyboard.press("Slash")
        assert page.evaluate("() => document.activeElement.id") != "global-search"
        # closed, the shortcut finds again
        page.evaluate("() => closeModal()")
        page.keyboard.press("Control+KeyK")
        assert page.evaluate("() => document.activeElement.id") == "global-search"
    finally:
        page.close()


# ── #266: skip links inside the long forms and on the chart ───────────────

SKIP_IN_MODAL = "() => document.querySelector('#modal-body a.skip-link--inline')"
ACTIVE = """() => { const a = document.activeElement;
    return { tag: a.tagName.toLowerCase(), id: a.id, cls: a.className, inLines: !!a.closest('#inv-lines, #bill-lines, #je-lines'),
             ring: a.matches(':focus-visible') }; }"""


def test_the_long_forms_skip_to_their_lines_and_the_chart_to_its_filter(
    browser, company, books
):
    page, handled = _open(browser, company)
    try:
        for route, call, ready in (
            ("#/invoices", "InvoicesPage.showForm()", "#inv-lines tr"),
            ("#/bills", "BillsPage.showForm()", "#bill-lines tr"),
            ("#/journal", "JournalPage.showForm()", "#je-lines tr"),
        ):
            _visit(page, handled, route)
            _open_dialog(page, handled, call, ready)
            # the form is a landmark named by the dialog's title
            assert (
                page.evaluate(
                    "() => document.querySelector('#modal-body form').getAttribute('aria-labelledby')"
                )
                == "modal-title"
            )
            # it opens on its first field, not on the skip link
            was = page.evaluate(ACTIVE)
            assert "skip-link" not in was["cls"] and was["tag"] != "body", was
            # Shift+Tab from there reaches the skip link, shown in place
            page.keyboard.press("Shift+Tab")
            now = page.evaluate(ACTIVE)
            assert "skip-link--inline" in now["cls"], now
            assert page.evaluate(
                "() => { const a = document.activeElement; const r = a.getBoundingClientRect(); return r.width > 20 && r.height > 10; }"
            )
            # Enter lands on the first line's first box, with the ring
            page.keyboard.press("Enter")
            landed = page.evaluate(ACTIVE)
            assert landed["inLines"] and landed["tag"] in ("input", "select"), landed
            assert landed["ring"], landed
            page.evaluate("() => closeModal()")
            page.wait_for_function(f"() => !({MODAL_SHOWN})()")

        # the chart: a navigation landmark, and its skip link lands on the filter
        _visit(page, handled, "#/accounts")
        assert page.evaluate(
            "() => document.querySelector('#page-content nav[aria-labelledby=\"accounts-heading\"] table') !== null"
        )
        page.evaluate(
            "() => document.querySelector('#page-content a.skip-link--inline').focus()"
        )
        assert "skip-link--inline" in page.evaluate(ACTIVE)["cls"]
        page.keyboard.press("Enter")
        landed = page.evaluate(ACTIVE)
        assert landed["id"] == "accounts-filter" and landed["ring"], landed
    finally:
        page.close()
