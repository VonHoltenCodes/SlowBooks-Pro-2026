"""The Fast Rack (#912) in a real browser, on the bakery's books served by
the app itself.

The top bar's row of seven buttons (Home to Reports) was fixed. It is now
the Fast Rack: up to eight buttons each sign-in chooses from every page the
sidebar offers and every new-document form, in its own order, kept with the
sign-in (the preferences store's `fast_rack`) and arranged in Settings ->
My Preferences -> Fast Rack. Checked here:

- with nothing saved, the rack is the shipped seven, each button 80 x 32,
  named by its words, the Alt-key ones with their shortcut;
- a saved list is drawn in its order; an id the catalog does not know, a
  second copy, and a nonprofit page on a business company are left out;
- every name in the catalog fits the button on at most two lines, and the
  catalog has every page of the sidebar; every new-document button opens
  its form;
- Settings arranges it: Move up / Move down / Remove / Add, at most eight,
  Reset to the shipped set, Save, through /api/preferences/fast_rack, and
  the bar is drawn again at once;
- a read-only sign-in is offered no new documents and none of the pages
  that only enter things, and its saved ones are skipped; an accountant's
  keeps New Journal Entry;
- at 900 wide with eight buttons, at 100% and 150%, the bar fits by
  folding the rack into More, the last first; More lists them by name and
  ends with Customize Fast Rack…, which opens Settings at the rack;
- the Alt keys work whether or not their button is on the rack.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_text_size import _open as _open_sized  # noqa: E402
from tests.test_readonly_browser import _signed_in, _user  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _ok,
    _open,
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

SHIPPED = [
    "home",
    "new_customer",
    "create_invoice",
    "receive_payment",
    "quick_entry",
    "bill_entry",
    "reports",
]
SHIPPED_NAMES = [
    "Home",
    "New Customer",
    "Create Invoice",
    "Receive Payment",
    "Quick Entry",
    "Bill Quick Entry",
    "Reports",
]
# The buttons that have an Alt letter, and theirs
ALT = {
    "home": "H",
    "create_invoice": "N",
    "receive_payment": "P",
    "quick_entry": "Q",
    "enter_bill": "B",
    "enter_expense": "E",
}

RACK = """() => [...document.querySelectorAll('#fast-rack .tb-btn')].map(b => ({
    id: b.dataset.rackId, text: b.textContent.trim(),
    keys: b.getAttribute('aria-keyshortcuts'), title: b.title,
    w: b.offsetWidth, h: b.offsetHeight, shown: b.offsetWidth > 0 }))"""


def _no_splash(page):
    # a fresh profile: the terms keep the splash up, over the bar
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def _put(client, items):
    _ok(client.put("/api/preferences/fast_rack", json={"value": {"items": items}}))


def _saved(client):
    return (_ok(client.get("/api/preferences/fast_rack"))["value"] or {}).get("items")


def _ids(page):
    return [b["id"] for b in page.evaluate(RACK)]


def _names_in_group(page, names):
    """Each name is a button's accessible name inside the group the screen
    reader hears as "Fast Rack"."""
    group = page.get_by_role("group", name="Fast Rack")
    for name in names:
        assert group.get_by_role("button", name=name, exact=True).count() == 1, name


def test_the_shipped_rack_is_the_seven_the_bar_had(browser, company):
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        settle(page, handled)
        page.wait_for_function(
            "() => document.getElementById('fast-rack').dataset.rendered"
        )
        rack = page.evaluate(RACK)
        assert [b["id"] for b in rack] == SHIPPED
        assert [b["text"] for b in rack] == SHIPPED_NAMES
        assert {(b["w"], b["h"]) for b in rack} == {(80, 32)}, rack
        _names_in_group(page, SHIPPED_NAMES)
        for b in rack:
            letter = ALT.get(b["id"])
            if letter:
                assert b["keys"] == f"Alt+{letter}", b
                assert b["title"] in (
                    f"{b['text']} (Alt+{letter})",
                    f"{b['text']} (⌥{letter})",
                ), b
            else:
                assert b["keys"] is None, b
        # a two-word name on two lines, a one-word name in the middle
        lines = page.evaluate(
            """() => [...document.querySelectorAll('#fast-rack .tb-btn')].map(b => {
                const spans = [...b.querySelectorAll('.fr-line')];
                const r = b.getBoundingClientRect();
                const t = spans[0].getBoundingClientRect(), u = spans[spans.length - 1].getBoundingClientRect();
                return [spans.length, Math.abs((t.top - r.top) - (r.bottom - u.bottom))]; })"""
        )
        assert [n for n, _ in lines] == [1, 2, 2, 2, 2, 2, 1], lines
        assert all(off <= 1.5 for _, off in lines), lines
        # the toolbar's Home goes home
        _visit(page, handled, "#/customers")
        page.click("#fast-rack [data-rack-id='home']")
        page.wait_for_function("() => location.hash === '#/'")
    finally:
        page.close()


def test_a_saved_rack_is_drawn_in_its_order(browser, company):
    _put(
        company,
        [
            "reports",
            "enter_bill",
            "nope",  # not in the catalog
            "enter_expense",
            "reports",  # a second copy
            "releases",  # a nonprofit's page, on a business company
            "home",
            "new_estimate",
            "journal",
            "new_journal_entry",
            "customers",
        ],
    )
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        settle(page, handled)
        want = [
            "reports",
            "enter_bill",
            "enter_expense",
            "home",
            "new_estimate",
            "journal",
            "new_journal_entry",
        ]
        page.wait_for_function(
            "(want) => [...document.querySelectorAll('#fast-rack .tb-btn')]"
            ".map(b => b.dataset.rackId).join() === want.join()",
            arg=want,
        )
        rack = {b["id"]: b for b in page.evaluate(RACK)}
        assert rack["enter_bill"]["keys"] == "Alt+B"
        assert rack["enter_expense"]["keys"] == "Alt+E"
        assert rack["new_estimate"]["keys"] is None
        _names_in_group(
            page, ["Reports", "Enter Bill", "Enter Expense", "New Journal Entry"]
        )
        # a new document's button opens its form
        page.click("#fast-rack [data-rack-id='enter_bill']")
        page.wait_for_selector("#modal-overlay:not(.hidden) #modal-body form")
    finally:
        page.close()


def test_every_name_fits_and_every_page_is_offered_and_every_form_opens(
    browser, company
):
    page, handled = _open(browser, company)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _no_splash(page)
        settle(page, handled)
        # the catalog has every page the sidebar offers
        missing = page.evaluate(
            """() => [...document.querySelectorAll('#sidebar .nav-link[data-page]')]
                .map(a => a.getAttribute('href'))
                .filter(h => !FastRack.CATALOG.some(c => c.kind === 'nav' && c.target === h))"""
        )
        assert missing == [], missing
        # each id once
        ids = page.evaluate("() => FastRack.CATALOG.map(c => c.id)")
        assert len(ids) == len(set(ids))
        # every name, in each company's words, fits its button: at most two
        # lines, nothing cut off at the side or the bottom
        for mode in ("business", "nonprofit"):
            bad = page.evaluate(
                """(mode) => {
                    Terms.mode = mode;
                    const out = [];
                    const all = FastRack.CATALOG.map(c => c.id);
                    for (let i = 0; i < all.length; i += 8) {
                        FastRack.items = all.slice(i, i + 8);
                        FastRack.allowed = () => true;
                        FastRack.render();
                        document.getElementById('topbar').classList.remove('tb-fold-1');
                        document.querySelectorAll('#fast-rack .tb-btn').forEach(b => {
                            b.classList.remove('fr-folded');
                            const spans = [...b.querySelectorAll('.fr-line')];
                            const cut = spans.some(s => s.scrollWidth > s.clientWidth + 0.5
                                || s.getClientRects().length !== 1);
                            if (spans.length > 2 || cut || b.scrollHeight > b.clientHeight
                                || b.scrollWidth > b.clientWidth) out.push(b.textContent.trim());
                        });
                    }
                    return out;
                }""",
                mode,
            )
            assert bad == [], (mode, bad)
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        settle(page, handled)
        # every new document's button opens its form, over the page shown
        actions = page.evaluate(
            "() => FastRack.CATALOG.filter(c => c.kind === 'action' && FastRack.allowed(c)).map(c => c.id)"
        )
        assert len(actions) >= 20, actions
        for action in actions:
            page.evaluate("(id) => FastRack.go(FastRack.byId(id))", action)
            page.wait_for_function(
                "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
                " && !!document.querySelector('#modal-body form, #modal-body input, #modal-body select')"
            )
            page.evaluate("() => closeModal()")
            settle(page, handled)
        assert not errors, errors
    finally:
        page.close()


def _editor(page):
    return page.evaluate("""() => ({
            rows: [...document.querySelectorAll('#fr-list .fr-row[data-rack-id]')].map(r => r.dataset.rackId),
            count: document.getElementById('fr-count').textContent.trim(),
            addDisabled: document.getElementById('fr-add').disabled,
            addBtnDisabled: document.getElementById('fr-add-btn').disabled,
            offered: [...document.querySelectorAll('#fr-add option')].map(o => o.value).filter(Boolean),
            groups: [...document.querySelectorAll('#fr-add optgroup')].map(g => g.label),
        })""")


def test_settings_arranges_it_and_save_draws_the_bar_at_once(browser, company):
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        settle(page, handled)
        _visit(page, handled, "#/settings")
        page.evaluate("() => SettingsPage.openGroup('preferences')")
        page.wait_for_selector("#fr-list .fr-row")
        got = _editor(page)
        assert got["rows"] == SHIPPED
        assert got["count"] == "7 of 8"
        assert got["groups"] == ["Pages", "New documents"]
        assert "home" not in got["offered"] and "enter_bill" in got["offered"]
        assert "releases" not in got["offered"]  # a business company
        # the buttons say which row they move
        page.get_by_role("button", name="Move down: Home", exact=True).click()
        assert _editor(page)["rows"][:2] == ["new_customer", "home"]
        # the moved button keeps the focus
        assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
            "Move down: Home"
        )
        page.get_by_role("button", name="Move up: Reports", exact=True).click()
        page.get_by_role("button", name="Remove: Quick Entry", exact=True).click()
        got = _editor(page)
        assert got["rows"] == [
            "new_customer",
            "home",
            "create_invoice",
            "receive_payment",
            "reports",
            "bill_entry",
        ]
        assert got["count"] == "6 of 8"
        # up to eight
        for add in ("enter_bill", "journal"):
            page.select_option("#fr-add", add)
            page.click("#fr-add-btn")
        got = _editor(page)
        assert got["rows"][-2:] == ["enter_bill", "journal"]
        assert got["count"].startswith("8 of 8")
        assert got["addDisabled"] and got["addBtnDisabled"]
        # and no ninth, however it is asked for
        page.evaluate("""() => { const s = document.getElementById('fr-add');
                s.disabled = false; s.value = 'payments'; FastRack.addChosen(); }""")
        assert len(_editor(page)["rows"]) == 8
        page.click("#fr-save")
        settle(page, handled)
        saved = _saved(company)
        assert saved == [
            "new_customer",
            "home",
            "create_invoice",
            "receive_payment",
            "reports",
            "bill_entry",
            "enter_bill",
            "journal",
        ]
        # the bar is drawn again at once, no reload
        assert _ids(page) == saved
        # Reset, then Save: the shipped set again
        page.click("#fr-reset")
        assert _editor(page)["rows"] == SHIPPED
        page.click("#fr-save")
        settle(page, handled)
        assert _saved(company) == SHIPPED
        assert _ids(page) == SHIPPED
        # and it is the server's: a fresh window reads it back
        _put(company, ["reports", "home"])
    finally:
        page.close()
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        settle(page, handled)
        assert _ids(page) == ["reports", "home"]
    finally:
        page.close()


WRITE_PAGES = {
    "quick_entry",
    "bill_entry",
    "batch_payments",
    "opening_balances",
    "migrate",
}
ADMIN_PAGES = {
    "employees",
    "payroll",
    "hr_onboarding",
    "hr_benefits",
    "hr_deductions",
    "hr_tax_forms",
    "migrate",
}


def test_a_read_only_sign_in_is_offered_no_writes_and_its_saved_ones_are_skipped(
    browser, company, db_session
):
    reader = _user(company, db_session, "rita", "readonly")
    _put(
        reader,
        [
            "create_invoice",
            "home",
            "quick_entry",
            "reports",
            "audit",
            "enter_bill",
            "journal",
            "new_journal_entry",
        ],
    )
    page, handled = _signed_in(browser, reader, "readonly")
    try:
        _no_splash(page)
        page.wait_for_function(
            "() => [...document.querySelectorAll('#fast-rack .tb-btn')]"
            ".map(b => b.dataset.rackId).join() === 'home,reports,journal'"
        )
        # no static button the role may not use is left in the bar either
        assert (
            page.evaluate(
                "() => [...document.querySelectorAll('#topbar .tb-btn[data-action]')]"
                ".filter(b => b.offsetWidth > 0).length"
            )
            == 0
        )
        _visit(page, handled, "#/settings")
        page.evaluate("() => SettingsPage.openGroup('preferences')")
        page.wait_for_selector("#fr-list .fr-row")
        got = _editor(page)
        assert got["rows"] == ["home", "reports", "journal"]
        assert got["groups"] == ["Pages"], got  # no new documents
        offered = set(got["offered"])
        assert not offered & (WRITE_PAGES | ADMIN_PAGES | {"audit"}), offered
        assert {"customers", "invoices", "bills"} <= offered
        # a read-only sign-in may still arrange its own rack, of what it may use
        assert page.evaluate("document.getElementById('fr-save').offsetWidth > 0")
        page.select_option("#fr-add", "customers")
        page.click("#fr-add-btn")
        page.click("#fr-save")
        settle(page, handled)
        assert _saved(reader) == ["home", "reports", "journal", "customers"]
        assert _ids(page) == ["home", "reports", "journal", "customers"]
    finally:
        page.close()


def test_an_accountants_sign_in_keeps_new_journal_entry(browser, company, db_session):
    accountant = _user(company, db_session, "alma", "accountant")
    _put(accountant, ["new_journal_entry", "create_invoice", "home", "quick_entry"])
    page, handled = _signed_in(browser, accountant, "accountant")
    try:
        _no_splash(page)
        page.wait_for_function(
            "() => [...document.querySelectorAll('#fast-rack .tb-btn')]"
            ".map(b => b.dataset.rackId).join() === 'new_journal_entry,home'"
        )
        _visit(page, handled, "#/settings")
        page.evaluate("() => SettingsPage.openGroup('preferences')")
        page.wait_for_selector("#fr-list .fr-row")
        actions = page.evaluate(
            "() => [...document.querySelectorAll('#fr-add optgroup[label=\"New documents\"] option')].map(o => o.value)"
        )
        # New Journal Entry is on the rack already: nothing else to offer
        assert actions == [], actions
    finally:
        page.close()


EIGHT = [
    "home",
    "new_customer",
    "create_invoice",
    "receive_payment",
    "enter_bill",
    "enter_expense",
    "journal",
    "reports",
]

FOLD = """() => {
    const bar = document.getElementById('topbar');
    const shown = el => !!el && el.offsetWidth > 0 && el.offsetHeight > 0;
    return {
        overflow: bar.scrollWidth - bar.clientWidth,
        sideways: document.documentElement.scrollWidth > innerWidth + 1,
        rack: [...document.querySelectorAll('#fast-rack .tb-btn')].map(b => [b.textContent.trim(), shown(b)]),
        more: shown(document.getElementById('topbar-more')),
        keep: ['logout-btn', 'back-btn', 'global-search', 'skip-link'].every(id => {
            const el = document.getElementById(id);
            return id === 'skip-link' ? !!el : shown(el); }),
        signOutRight: document.getElementById('logout-btn').getBoundingClientRect().right,
        width: innerWidth,
    };
}"""


@pytest.mark.parametrize("size", ["100", "150"])
def test_at_900_wide_eight_buttons_fold_into_more(browser, company, size):
    _put(company, EIGHT)
    page, handled = _open_sized(browser, company, "#/", size, width=900, height=700)
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#fast-rack .tb-btn').length === 8"
        )
        settle(page, handled)
        got = page.evaluate(FOLD)
        assert got["overflow"] <= 0 and not got["sideways"], got
        assert got["keep"] and got["signOutRight"] <= got["width"], got
        names = [n for n, _ in got["rack"]]
        shown = [n for n, s in got["rack"] if s]
        # the first few stay, in the rack's order; the last fold first
        assert shown == names[: len(shown)], got
        assert len(shown) < 8 and got["more"], got
        page.click("#topbar-more")
        listed = page.evaluate(
            "() => [...document.querySelectorAll('#topbar-more-menu > *')].map(el => el.textContent.trim())"
        )
        folded = names[len(shown) :]
        at = [listed.index(n) for n in folded]
        assert at == sorted(at), listed  # by name, in the rack's order
        assert listed[-1] == "Customize Fast Rack…", listed
        # a folded button chosen from More does what the button does
        if "Reports" in folded:
            page.click("#topbar-more-menu >> text=Reports")
            page.wait_for_function("() => location.hash === '#/reports'")
            settle(page, handled)
            page.click("#topbar-more")
        # Customize Fast Rack… opens Settings at the rack's block
        page.click("#topbar-more-menu >> text=Customize Fast Rack…")
        page.wait_for_function("() => location.hash === '#/settings'")
        page.wait_for_selector("#fr-list .fr-row")
        page.wait_for_function(
            "() => document.activeElement && document.activeElement.id === 'settings-h-fast-rack'"
        )
        box = page.evaluate(
            """() => { const r = document.getElementById('settings-h-fast-rack').getBoundingClientRect();
                const c = document.getElementById('content').getBoundingClientRect();
                return [r.top, r.bottom, c.top, c.bottom]; }"""
        )
        assert box[0] >= box[2] - 1 and box[1] <= box[3] + 1, box
    finally:
        page.close()


def test_the_alt_keys_work_off_the_rack_too(browser, company):
    _put(company, ["reports"])
    page, handled = _open(browser, company)
    try:
        _no_splash(page)
        settle(page, handled)
        assert _ids(page) == ["reports"]
        page.keyboard.press("Alt+KeyN")
        page.wait_for_selector("#modal-overlay:not(.hidden) #invoice-form")
        page.evaluate("() => closeModal()")
        settle(page, handled)
        page.keyboard.press("Alt+KeyQ")
        page.wait_for_function("() => location.hash === '#/quick-entry'")
    finally:
        page.close()
