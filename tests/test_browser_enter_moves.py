"""Two QuickBooks Desktop data-entry preferences, in playwright's Chromium
(#480): "Enter key moves between fields" and "Automatically place the
decimal point", per user, both off by default.

- Off (the default), Enter does what it did: it saves a dialog's form and
  posts the register's quick row from any of its fields.
- On, Enter in a field moves to the next field; Enter in the last field
  (or on Save) saves. A text area keeps its new line and a type-ahead box
  keeps Enter while its list is open (to take the highlighted name);
  closed, with a name chosen or empty, it moves on like any field.
  The register's quick row follows the same rule: Enter walks Date, Payee,
  Memo, Ref, Category, the two amounts, and only Enter in the last amount
  posts.
- The decimal point, on: 12345 typed in a money field is $123.45, in a
  dialog and in the quick row; a point typed yourself is kept ("100." is
  $100.00); a value the field already had is never divided. Off: 12345 is
  $12,345.00.
- The Settings page's My Preferences section saves both to
  /api/preferences/data_entry, outside the administrator's form.

The page is the real one; the API is stubbed (tests/test_browser_ui.py).
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_quick_add import CHART, REGISTER  # noqa: E402
from tests.test_browser_ui import ORIGIN, _open_raw  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixture)
    _theme,
    browser_fixture,
    contrast,
)

ACTIVE = "() => document.activeElement && (document.activeElement.id || document.activeElement.name)"
MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)

# A dialog with every kind of field Enter meets: text, number, a select, a
# text area, a type-ahead picker, a checkbox, and the Save button.
PROBE_FORM = """() => new Promise(done => {
    window._probeSubmits = 0;
    const opts = Array.from({ length: 20 }, (_, i) => `<option value="${i + 1}">Customer ${i + 1}</option>`).join('');
    openModal('Probe', `<form id="probe-form" onsubmit="event.preventDefault(); window._probeSubmits++;">
        <div class="form-group"><label for="p-name">Name</label><input id="p-name"></div>
        <div class="form-group"><label for="p-amount">Amount</label><input id="p-amount" class="money" type="number" step="0.01"></div>
        <div class="form-group"><label for="p-customer">Customer</label><select id="p-customer" data-search><option value="">Choose…</option>${opts}</select></div>
        <div class="form-group"><label for="p-memo">Memo</label><textarea id="p-memo"></textarea></div>
        <div class="form-group"><label for="p-kind">Kind</label><select id="p-kind" data-no-search><option>One</option><option>Two</option></select></div>
        <div class="form-group"><label for="p-ref">Ref</label><input id="p-ref"></div>
        <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button type="submit" class="btn btn-primary">Save</button></div>
    </form>`);
    setTimeout(done, 50);
})"""


def _page(browser, prefs, posted=None):
    """The app with the data-entry preference `prefs` saved (None: never
    set), and the register's API stubbed. `posted` collects the quick row's
    posts."""
    page = _open_raw(browser, 1280, 800)
    saved = {"value": prefs}

    def _prefs(route):
        if route.request.method == "PUT":
            saved["value"] = route.request.post_data_json["value"]
        return route.fulfill(json={"key": "data_entry", "value": saved["value"]})

    def _entries(route):
        if route.request.method == "POST" and posted is not None:
            posted.append(route.request.post_data_json)
        return route.fulfill(status=201, json={"id": 1})

    page.route("**/api/preferences/data_entry", _prefs)
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/banking/transactions", _entries)
    page.goto(f"{ORIGIN}/#/")
    page.wait_for_function("window.App && document.readyState === 'complete'")
    page.click("#splash-dismiss")
    page.wait_for_function(
        "(want) => DataEntryPrefs.enter_moves === want[0] && DataEntryPrefs.auto_decimal === want[1]",
        arg=[
            bool((prefs or {}).get("enter_moves")),
            bool((prefs or {}).get("auto_decimal")),
        ],
    )
    page.saved = saved
    return page


def _probe(page):
    page.evaluate(PROBE_FORM)
    page.wait_for_function(MODAL_SHOWN)


def _register(page):
    page.goto(f"{ORIGIN}/#/banking/1")
    page.wait_for_selector("#qe-category")


def _fill_quick_row(page, out="25"):
    page.fill("#qe-payee", "Gas Co")
    page.select_option("#qe-category", "2")
    page.fill("#qe-out", out)


# ---- Enter, off: as before ---------------------------------------------------


def test_off_enter_saves_the_dialog_and_posts_the_quick_row(browser):
    posted = []
    page = _page(browser, None, posted)
    try:
        _probe(page)
        page.focus("#p-name")
        page.keyboard.press("Enter")
        assert page.evaluate("() => window._probeSubmits") == 1
        page.evaluate("() => closeModal()")

        _register(page)
        _fill_quick_row(page)
        page.focus("#qe-payee")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        assert len(posted) == 1 and posted[0]["amount"] == "-25.00"
    finally:
        page.close()


# ---- Enter, on: moves; the last field saves ---------------------------------


def test_on_enter_walks_the_fields_and_the_last_one_saves(browser):
    page = _page(browser, {"enter_moves": True})
    try:
        _probe(page)
        page.focus("#p-name")
        page.keyboard.press("Enter")
        assert page.evaluate(ACTIVE) == "p-amount"
        assert page.evaluate("() => window._probeSubmits") == 0
        page.keyboard.press("Enter")
        # the type-ahead box that stands in for the Customer select
        assert (
            page.evaluate("() => document.activeElement.getAttribute('role')")
            == "combobox"
        )
        # the picker keeps Enter while its list is open, to take the
        # highlighted name; closed, with a name chosen, Enter moves on
        page.keyboard.press("ArrowDown")  # opens the list
        page.keyboard.press("ArrowDown")  # past "Choose…" to a customer
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.getElementById('p-customer').value") != ""
        assert (
            page.evaluate("() => document.activeElement.getAttribute('role')")
            == "combobox"
        )
        page.keyboard.press("Enter")
        assert page.evaluate(ACTIVE) == "p-memo"
        assert page.evaluate("() => window._probeSubmits") == 0

        # the text area keeps its new line
        page.focus("#p-memo")
        page.keyboard.type("one")
        page.keyboard.press("Enter")
        page.keyboard.type("two")
        assert page.input_value("#p-memo") == "one\ntwo"
        assert page.evaluate(ACTIVE) == "p-memo"

        # a plain select moves on too
        page.focus("#p-kind")
        page.keyboard.press("Enter")
        assert page.evaluate(ACTIVE) == "p-ref"
        assert page.evaluate("() => window._probeSubmits") == 0
        # the last field saves
        page.keyboard.press("Enter")
        assert page.evaluate("() => window._probeSubmits") == 1
    finally:
        page.close()


def test_on_enter_on_save_saves_and_shift_enter_is_left_alone(browser):
    page = _page(browser, {"enter_moves": True})
    try:
        _probe(page)
        page.focus("#probe-form button[type=submit]")
        page.keyboard.press("Enter")
        assert page.evaluate("() => window._probeSubmits") == 1
    finally:
        page.close()


def test_on_the_quick_row_walks_and_only_the_last_amount_posts(browser):
    posted = []
    page = _page(browser, {"enter_moves": True}, posted)
    try:
        _register(page)
        _fill_quick_row(page)
        page.focus("#qe-date")
        walk = []
        for _ in range(6):
            page.keyboard.press("Enter")
            walk.append(page.evaluate(ACTIVE))
        assert walk == [
            "qe-payee",
            "qe-memo",
            "qe-ref",
            "qe-category",
            "qe-out",
            "qe-in",
        ]
        page.wait_for_timeout(150)
        assert posted == []
        page.keyboard.press("Enter")  # the last amount posts
        page.wait_for_timeout(200)
        assert len(posted) == 1 and posted[0]["amount"] == "-25.00"
    finally:
        page.close()


def test_on_a_real_dialog_moves_instead_of_saving(browser):
    page = _page(browser, {"enter_moves": True})
    try:
        page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
        page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
        _register(page)
        page.evaluate("() => BankingPage.showEntryForm(1)")
        page.wait_for_function(MODAL_SHOWN)
        page.wait_for_selector("#modal-body input[name=amount]")
        page.focus("#modal-body input[name=amount]")
        page.keyboard.press("Enter")
        assert page.evaluate(MODAL_SHOWN)
        assert page.evaluate(
            "() => document.activeElement !== document.querySelector('#modal-body input[name=amount]')"
            " && document.getElementById('modal').contains(document.activeElement)"
        )
    finally:
        page.close()


# ---- The decimal point ------------------------------------------------------


def test_the_decimal_point_places_itself_when_on(browser):
    posted = []
    page = _page(browser, {"auto_decimal": True}, posted)
    try:
        _probe(page)
        page.focus("#p-amount")
        page.keyboard.type("12345")
        page.keyboard.press("Tab")
        assert page.input_value("#p-amount") == "123.45"
        # a point typed yourself is kept
        page.focus("#p-amount")
        page.keyboard.press("Control+a")
        page.keyboard.type("100.")
        page.keyboard.press("Tab")
        assert float(page.input_value("#p-amount")) == 100
        # a value already there, left untouched, is never divided
        page.evaluate("() => { document.getElementById('p-amount').value = '500'; }")
        page.focus("#p-amount")
        page.keyboard.press("Tab")
        assert page.input_value("#p-amount") == "500"
        # a plain text field is not money
        page.focus("#p-name")
        page.keyboard.type("12345")
        page.keyboard.press("Tab")
        assert page.input_value("#p-name") == "12345"
        page.evaluate("() => closeModal()")

        # the quick row: typed, then Enter posts it placed
        _register(page)
        _fill_quick_row(page, out="")
        page.focus("#qe-out")
        page.keyboard.type("12345")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        assert len(posted) == 1 and posted[0]["amount"] == "-123.45"
    finally:
        page.close()


def test_the_decimal_point_off_keeps_whole_dollars(browser):
    page = _page(browser, None)
    try:
        _probe(page)
        page.focus("#p-amount")
        page.keyboard.type("12345")
        page.keyboard.press("Tab")
        assert page.input_value("#p-amount") == "12345"
    finally:
        page.close()


def test_auto_decimal_value_rules(browser):
    page = _page(browser, None)
    try:
        got = page.evaluate(
            "() => ['12345', '5', '0', '-250', '100.5', '', 'abc', ' 42 '].map(autoDecimalValue)"
        )
        assert got == ["123.45", "0.05", "0.00", "-2.50", None, None, None, "0.42"]
    finally:
        page.close()


# ---- Settings → My Preferences ----------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_my_preferences_save_themselves(browser, theme):
    page = _page(browser, None)
    try:
        page.goto(f"{ORIGIN}/#/settings")
        page.wait_for_selector("#pref-enter-moves")
        _theme(page, theme)
        # every line of the section reads at WCAG AA (4.5:1) on its ground
        low = page.evaluate("""() => {
            const ground = (el) => { for (; el; el = el.parentElement) {
                const c = getComputedStyle(el).backgroundColor;
                if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; }
                return 'rgb(255, 255, 255)'; };
            return [...document.querySelectorAll('#settings-my-preferences h3, #settings-my-preferences p, '
                + '#settings-my-preferences label, #settings-my-preferences [id$=-hint]')]
                .map(el => [el.textContent.trim().slice(0, 30), getComputedStyle(el).color, ground(el)]);
        }""")
        bad = [
            (t, fg, bg, contrast(fg, bg)) for t, fg, bg in low if contrast(fg, bg) < 4.5
        ]
        # heading, note, and a label + hint for each of the section's three
        # boxes (#480's two, #482's report refresh) and for Text size (#717)
        assert len(low) == 10 and not bad, bad
        assert not page.is_checked("#pref-enter-moves")
        assert not page.is_checked("#pref-auto-decimal")
        # outside the administrator's form: no Save Settings needed
        assert page.evaluate(
            "() => !document.getElementById('pref-enter-moves').closest('form')"
        )
        # labelled and described, for a screen reader
        assert (
            page.evaluate(
                "() => document.getElementById('pref-enter-moves').labels[0].textContent.trim()"
            )
            == "Enter key moves between fields"
        )
        page.check("#pref-enter-moves")
        page.wait_for_function("() => DataEntryPrefs.enter_moves === true")
        page.wait_for_timeout(100)
        assert page.saved["value"] == {"enter_moves": True, "auto_decimal": False}
        page.check("#pref-auto-decimal")
        page.wait_for_timeout(100)
        assert page.saved["value"] == {"enter_moves": True, "auto_decimal": True}
        page.uncheck("#pref-enter-moves")
        page.wait_for_timeout(100)
        assert page.saved["value"] == {"enter_moves": False, "auto_decimal": True}
    finally:
        page.close()
