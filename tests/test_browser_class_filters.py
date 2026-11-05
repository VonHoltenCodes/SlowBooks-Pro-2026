"""Class on the General Ledger and the register, the Chart of Accounts'
links and filter box, and line-level class with the warn-when-blank
setting (#236, #240, #243), in playwright's Chromium on the bakery's
books of tests/test_theme_contrast.py with the classes and the split bill
of tests/test_browser_classes.py:

- the General Ledger shows a Class column; its Account and Class pickers
  (named, keyboard) re-run the report, ride on its address (replaced, not
  pushed), its export links and its drill-down; a class-filtered ledger
  says so;
- the bank register's From / To / Class filters ride on its address and
  show the balance brought forward; a class-filtered register says the
  account's whole balance beside it;
- the Chart of Accounts: an account's name opens its register (the bank
  register for a bank account, the drill-down for this year for the rest),
  the filter box narrows the rows and keeps the type headings, and the
  global search finds an account and opens its register;
- the line Class cell on the bill, journal and invoice forms in business
  mode, and the setting that warns when a document is saved without a
  class: with it on the header picker starts blank and Save asks first
  (Cancel keeps the form open, OK saves to Uncategorized); with it off the
  picker starts on Uncategorized and nothing asks; Make Deposits, whose
  class picker is its own toolbar control, does the same;
- the search's hits take the keyboard (ArrowDown from the box, Enter opens
  one) and the chart's filter count is a status region.

Skipped, as one module, where playwright or its Chromium is not installed
(tests/test_gl_class_filter.py and tests/test_class_warn_setting.py check
the API and the sources).
"""

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_classes import classed as _classed  # noqa: E402,F401
from tests.test_browser_report_views import (
    _hash,
    _history,
    _open_at,
    _query,
)  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _visit,
    browser_fixture,
    company_fixture,
    settle,
)

SEPT = ("2026-09-01", "2026-09-30")
MODAL_SHOWN = (
    "() => !document.getElementById('modal-overlay').classList.contains('hidden')"
)
TITLE = "() => document.getElementById('modal-title').textContent"
# the register's own rows: the table headed Payee (the To-review panel has one too)
REG_ROWS = """() => { const t = [...document.querySelectorAll('#page-content table')]
    .find(t => t.tHead && /Payee/.test(t.tHead.textContent));
    return t ? [...t.tBodies[0].rows].map(r => r.textContent.replace(/\\s+/g, ' ').trim()) : []; }"""


@pytest.fixture(autouse=True)
def _no_edit_question(company):
    """These are about what an edit saves: the "Warn when editing a
    transaction" question before it, and the date question the books' fixed
    dates will draw as they age (#481, tests/test_browser_transaction_warnings.py),
    are turned off for them."""
    company.put(
        "/api/settings",
        json={"warn_edit_transaction": "false", "warn_date_past": "false"},
    )


def _ok(r):
    assert r.status_code in (200, 201), (str(r.request.url), r.status_code, r.text)
    return r.json()


@pytest.fixture(name="classed")
def classed_fixture(_classed):  # noqa: F811  (the fixture, by its name)
    """Site Prep, Old Crew (archived) and the bill split across them."""
    return _classed


# ── #236: the General Ledger ──────────────────────────────────────────────


def test_the_ledger_shows_the_class_column_and_its_pickers_filter_it(
    browser, company, books, classed
):
    site = classed["site"]
    accounts = {a["account_number"]: a for a in _ok(company.get("/api/accounts"))}
    url = f"#/reports/general-ledger?start_date={SEPT[0]}&end_date={SEPT[1]}"
    page, handled = _open_at(browser, company, url)
    try:
        page.wait_for_selector("#report-content table")
        heads = page.evaluate(
            "() => [...document.querySelector('#report-content table thead').querySelectorAll('th')].map(t => t.textContent.trim())"
        )
        assert heads == [
            "Date",
            "Description",
            "Reference",
            "Source",
            "Class",
            "Debit",
            "Credit",
            "Balance",
        ]
        body = page.inner_text("#report-content")
        assert "Site Prep" in body and "Uncategorized" in body
        length = _history(page)

        # the class picker, by its name: the ledger is that class's alone
        # (its name exactly: the Report Center's cards under the dialog
        # are links named "P&L by Class" and "P&L Unclassified")
        page.get_by_label("Class", exact=True).select_option(str(site["id"]))
        settle(page, handled)
        page.wait_for_function(
            "() => document.getElementById('gl-class-note') !== null"
        )
        q = _query(_hash(page))
        assert (
            q["class_id"] == str(site["id"])
            and (q["start_date"], q["end_date"]) == SEPT
        )
        assert _history(page) == length, "a filter change replaces the address"
        classes = page.evaluate(
            "() => [...document.querySelectorAll('#report-content tbody tr')].map(r => r.children[4] && r.children[4].textContent.trim()).filter(Boolean)"
        )
        assert classes and set(classes) == {"Site Prep"}, classes
        assert "this class's lines only" in page.inner_text("#report-content")
        exports = page.evaluate(
            "() => [...document.querySelectorAll('#report-content button')].filter(b => /Save (PDF|CSV)/.test(b.textContent)).map(b => b.getAttribute('onclick'))"
        )
        assert len(exports) == 2 and all(f"class_id={site['id']}" in e for e in exports)

        # the account picker too; both on the address
        page.get_by_label("Account", exact=True).select_option(
            str(accounts["6000"]["id"])
        )
        settle(page, handled)
        page.wait_for_function(
            "() => document.querySelectorAll('#report-content h3').length === 1"
        )
        q = _query(_hash(page))
        assert q["account_id"] == str(accounts["6000"]["id"]) and q["class_id"] == str(
            site["id"]
        )
        assert (
            page.locator("#report-content h3")
            .inner_text()
            .endswith(accounts["6000"]["name"])
        )
        assert "$120.00" in page.inner_text("#report-content tbody")

        # a reload reproduces the filtered ledger, pickers set
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        page.wait_for_selector("#report-content table")
        settle(page, handled)
        assert page.evaluate("() => document.getElementById('gl-class').value") == str(
            site["id"]
        )
        assert page.evaluate(
            "() => document.getElementById('gl-account').value"
        ) == str(accounts["6000"]["id"])

        # the drill-down from a class-filtered ledger carries the class, and
        # Back to General Ledger returns to the filtered ledger
        page.locator("#report-content h3 a").first.click()
        page.wait_for_selector("#drilldown-body table")
        q = _query(_hash(page))
        assert q["class_id"] == str(site["id"]) and q["from"] == "general-ledger"
        assert "Site Prep" in page.evaluate(TITLE)
        page.get_by_role("button", name="Back to General Ledger").click()
        page.wait_for_selector("#report-content table")
        page.wait_for_function("() => document.getElementById('gl-class') !== null")
        assert page.evaluate("() => document.getElementById('gl-class').value") == str(
            site["id"]
        )
        assert _query(_hash(page))["class_id"] == str(site["id"])

        # a saved report keeps the filters
        page.on("dialog", lambda d: d.accept("Site Prep ledger"))
        page.get_by_role("button", name="Add to Saved Reports…").click()
        settle(page, handled)
        saved = _ok(company.get("/api/saved-reports"))
        mine = next(s for s in saved if s["name"] == "Site Prep ledger")
        assert mine["parameters"]["class_id"] == str(site["id"])
        assert mine["parameters"]["account_id"] == str(accounts["6000"]["id"])
    finally:
        page.close()


def test_the_register_filters_by_dates_and_class(browser, company, books, classed):
    site = classed["site"]
    accounts = {a["account_number"]: a for a in _ok(company.get("/api/accounts"))}
    bank = accounts["1000"]["id"]
    # a classed register entry in September, so the class filter has a line
    _ok(
        company.post(
            "/api/banking/transactions",
            json={
                "account_id": bank,
                "date": "2026-09-16",
                "amount": -45,
                "payee": "Site Prep fuel",
                "category_account_id": accounts["6000"]["id"],
                "class_id": site["id"],
            },
        )
    )
    page, handled = _open_at(browser, company, f"#/banking/{bank}")
    try:
        page.wait_for_selector("#page-content tbody tr")
        all_rows = len(page.evaluate(REG_ROWS))
        assert all_rows > 5
        assert page.locator("#reg-opening").count() == 0
        length = _history(page)

        page.get_by_label("From").fill(SEPT[0])
        page.dispatch_event("#reg-start", "change")
        settle(page, handled)
        page.wait_for_selector("#reg-opening")
        assert _query(_hash(page)) == {"start_date": SEPT[0]}
        assert _history(page) == length, "a filter change replaces the address"
        opening = page.inner_text("#reg-opening")
        assert "Balance brought forward" in opening
        assert (
            len(page.evaluate(REG_ROWS)) == all_rows
        ), "January's opening goes, the balance brought forward comes"

        page.get_by_label("Class", exact=True).select_option(str(site["id"]))
        settle(page, handled)
        page.wait_for_function(
            "() => document.getElementById('reg-filter-note') && /Site Prep/.test(document.getElementById('reg-filter-note').textContent)"
        )
        q = _query(_hash(page))
        assert q["class_id"] == str(site["id"]) and q["start_date"] == SEPT[0]
        rows = page.evaluate(REG_ROWS)
        # oldest first: the brought-forward balance opens the table (2.23.5
        # round 2, NEW-142), the entry follows
        assert (
            len(rows) == 2
            and "Balance brought forward (Site Prep)" in rows[0]
            and "Site Prep fuel" in rows[1]
        ), rows
        note = page.inner_text("#reg-filter-note")
        assert "the account's whole balance is" in note
        reg = _ok(
            company.get(
                "/api/banking/check-register",
                params={
                    "account_id": bank,
                    "start_date": SEPT[0],
                    "class_id": site["id"],
                },
            )
        )
        assert f"${reg['balance']:,.2f}" in note.replace("-$", "$")

        # a reload reproduces the filtered register; Clear empties it
        page.reload()
        page.wait_for_function("window.App && document.readyState === 'complete'")
        page.evaluate(
            "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
        )
        page.wait_for_selector("#reg-filter-note")
        assert page.evaluate("() => document.getElementById('reg-class').value") == str(
            site["id"]
        )
        assert (
            page.evaluate("() => document.getElementById('reg-start').value") == SEPT[0]
        )
        page.get_by_role("button", name="Clear", exact=True).click()
        settle(page, handled)
        page.wait_for_function(f"(n) => ({REG_ROWS})().length === n", arg=all_rows)
        assert _hash(page) == f"#/banking/{bank}"
    finally:
        page.close()


# ── #240: the Chart of Accounts opens the register; accounts in search ──


def test_chart_rows_open_the_register_and_the_filter_box_keeps_the_grouping(
    browser, company, books
):
    accounts = {a["account_number"]: a for a in _ok(company.get("/api/accounts"))}
    year = dt.date.today().year
    page, handled = _open_at(browser, company, "#/accounts")
    try:
        page.wait_for_selector("#page-content tbody tr")
        checking = page.locator("#page-content tbody a", has_text="Checking").first
        assert checking.get_attribute("href") == f"#/banking/{accounts['1000']['id']}"
        rent = page.locator(
            "#page-content tbody a", has_text=accounts["6000"]["name"]
        ).first
        q = _query(rent.get_attribute("href"))
        assert rent.get_attribute("href").startswith("#/reports/account-transactions?")
        assert q["account_id"] == str(accounts["6000"]["id"])
        assert q["period"] == "this_year"
        assert (q["start_date"], q["end_date"]) == (f"{year}-01-01", f"{year}-12-31")

        # the filter box, by keyboard: typing narrows the rows, the type
        # headings of the rows left stay, the others go
        box = page.get_by_label("Filter accounts")
        box.focus()
        page.keyboard.type("6000")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#page-content tbody tr')].filter(r => r.offsetParent !== null).length < 6"
        )
        shown = page.evaluate(
            "() => [...document.querySelectorAll('#page-content tbody tr')].filter(r => r.offsetParent !== null).map(r => r.textContent.replace(/\\s+/g, ' ').trim())"
        )
        assert any("6000" in r for r in shown) and any(
            r == "Expenses" for r in shown
        ), shown
        assert not any(r == "Assets" for r in shown), shown
        note = page.locator("#accounts-filter-note")
        assert note.get_attribute("role") == "status"
        assert note.inner_text() == "1 account matches"
        page.keyboard.press("ControlOrMeta+A")  # select all, the host's own key
        page.keyboard.type("checking")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#page-content tbody tr')].filter(r => r.offsetParent !== null).some(r => /Assets/.test(r.textContent))"
        )

        # the expense account's link opens the drill-down for this year
        box.fill("")
        rent = page.locator(
            "#page-content tbody a", has_text=accounts["6000"]["name"]
        ).first
        rent.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("#drilldown-body table")
        assert page.evaluate(TITLE) == f"Drill-down — {accounts['6000']['name']}"
        page.go_back()
        page.wait_for_selector("#page-content tbody tr")
        assert _hash(page) == "#/accounts"
        assert not page.evaluate(MODAL_SHOWN)

        # the bank account's link is the bank register
        page.locator("#page-content tbody a", has_text="Checking").first.click()
        page.wait_for_selector("#page-content .page-header h2")
        assert _hash(page) == f"#/banking/{accounts['1000']['id']}"

        # the search: an account by number, opening its register
        _visit(page, handled, "#/")
        page.fill("#global-search", "6000")
        page.wait_for_selector(
            "#search-results .search-section:has-text('Accounts')", timeout=5000
        )
        settle(page, handled)
        page.locator(
            "#search-results .search-item", has_text=accounts["6000"]["name"]
        ).first.click()
        page.wait_for_selector("#drilldown-body table")
        assert _query(_hash(page))["account_id"] == str(accounts["6000"]["id"])
        page.evaluate("() => closeModal()")

        # ... and by keyboard: ArrowDown from the box lands on the first hit,
        # Enter opens it (a search is the only way from anywhere to an
        # account register or a class page, so it cannot be mouse-only)
        _visit(page, handled, "#/")
        page.fill("#global-search", "6000")
        page.wait_for_selector(
            "#search-results .search-section:has-text('Accounts')", timeout=5000
        )
        settle(page, handled)
        page.focus("#global-search")
        page.keyboard.press("ArrowDown")
        focused = page.evaluate(
            "() => [document.activeElement.className, document.activeElement.textContent]"
        )
        assert focused[0] == "search-item" and accounts["6000"]["name"] in focused[1]
        # ArrowUp from the first hit is back to the box; ArrowDown returns
        page.keyboard.press("ArrowUp")
        assert page.evaluate("() => document.activeElement.id") == "global-search"
        page.keyboard.press("ArrowDown")
        assert page.evaluate("() => document.activeElement.textContent") == focused[1]
        page.keyboard.press("Enter")
        page.wait_for_selector("#drilldown-body table")
        assert _query(_hash(page))["account_id"] == str(accounts["6000"]["id"])
        assert page.evaluate(
            "() => document.getElementById('search-results').classList.contains('hidden')"
        )
    finally:
        page.close()


# ── #243: line class in business mode, and the warn-when-blank setting ──

OPEN = "() => !document.getElementById('modal-overlay').classList.contains('hidden')"


def _open_form(page, handled, call, ready):
    page.evaluate(f"async () => {{ await {call}; }}")
    page.wait_for_function(OPEN, timeout=5000)
    page.wait_for_selector(ready)
    settle(page, handled)


def _hide_splash(page):
    page.evaluate(
        "() => { const s = document.getElementById('splash'); if (s) s.classList.add('hidden'); }"
    )


def test_the_line_class_cell_is_on_the_bill_journal_and_invoice_forms(
    browser, company, books, classed
):
    site = classed["site"]
    page, handled = _open_at(browser, company, "#/bills")
    try:
        # the bill: a Class column with a select per line, named, offering the
        # active classes (the archived Old Crew is not a choice); no Function
        # column outside nonprofit mode
        _open_form(page, handled, "BillsPage.showForm()", "#bill-lines tr")
        heads = page.evaluate(
            "() => [...document.querySelectorAll('#modal-body .line-items-table thead th')].map(t => t.textContent.trim())"
        )
        assert "Class" in heads and "Function" not in heads, heads
        assert page.locator("#bill-lines tr .line-function-fund").count() >= 1
        assert page.locator("#bill-lines tr .line-function").count() == 0
        options = page.evaluate(
            "() => [...document.querySelector('#bill-lines tr .line-function-fund').options].map(o => o.textContent)"
        )
        assert options == ["Same as header", "Uncategorized", "Site Prep"], options
        assert page.get_by_label("Class, line 1").count() == 1
        page.evaluate("() => closeModal()")

        # the journal entry: the same cell, no function cell
        _visit(page, handled, "#/journal")
        _open_form(page, handled, "JournalPage.showForm()", "#je-lines tr")
        assert page.locator("#je-lines tr .je-function-fund").count() == 2
        assert page.locator("#je-lines tr select.je-function").count() == 0
        page.evaluate("() => closeModal()")

        # the invoice: the cell is new (lines carried only a hidden id); a
        # line saved with a class keeps it through an edit
        _visit(page, handled, "#/invoices")
        _open_form(page, handled, "InvoicesPage.showForm()", "#inv-lines tr")
        heads = page.evaluate(
            "() => [...document.querySelectorAll('#modal-body .line-items-table thead th')].map(t => t.textContent.trim())"
        )
        assert heads[:3] == ["Item", "Description", "Class"], heads
        assert page.locator("#inv-lines tr .line-class-fund").count() == 1
        page.evaluate(
            """(args) => {
                const form = document.querySelector('#modal-body form');
                form.customer_id.value = String(args.customer);
                const row = document.querySelector('#inv-lines tr');
                row.querySelector('.line-desc').value = 'Grading, by the line';
                row.querySelector('.line-rate').value = '75';
                row.querySelector('.line-class-fund').value = String(args.site);
                InvoicesPage.recalc();
            }""",
            {"customer": books["customer"], "site": site["id"]},
        )
        assert (
            page.evaluate(
                "() => document.querySelector('#modal-body form').class_id.value"
            )
            != ""
        )
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        settle(page, handled)
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        listed = _ok(company.get("/api/invoices?limit=100"))
        listed = listed if isinstance(listed, list) else listed["items"]
        full = [_ok(company.get(f"/api/invoices/{i['id']}")) for i in listed]
        inv = next(
            i
            for i in full
            if i["lines"] and i["lines"][0]["description"] == "Grading, by the line"
        )
        assert inv["lines"][0]["class_id"] == site["id"]

        _open_form(
            page, handled, f"InvoicesPage.showForm({inv['id']})", "#inv-lines tr"
        )
        assert page.evaluate(
            "() => document.querySelector('#inv-lines tr .line-class-fund').value"
        ) == str(site["id"])
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        settle(page, handled)
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        again = _ok(company.get(f"/api/invoices/{inv['id']}"))
        assert (
            again["lines"][0]["class_id"] == site["id"]
        ), "an edit does not strip the line's class"
    finally:
        page.close()


def test_the_warn_setting_asks_before_saving_without_a_class(
    browser, company, books, classed
):
    site = classed["site"]
    accounts = {a["account_number"]: a for a in _ok(company.get("/api/accounts"))}
    uncat = classed["uncat"]

    def fill_journal(page, memo):
        page.evaluate(
            """(args) => {
                const form = document.querySelector('#modal-body form');
                form.description.value = args.memo;
                const rows = document.querySelectorAll('#je-lines tr');
                rows[0].querySelector('.je-account').value = String(args.expense);
                rows[0].querySelector('.je-debit').value = '40';
                rows[1].querySelector('.je-account').value = String(args.bank);
                rows[1].querySelector('.je-credit').value = '40';
                JournalPage.recalc();
            }""",
            {
                "memo": memo,
                "expense": accounts["6000"]["id"],
                "bank": accounts["1000"]["id"],
            },
        )

    dialogs = []

    def on_dialog(d):
        dialogs.append(d.message)
        (d.dismiss if len(dialogs) == 1 else d.accept)()

    # off: the picker starts on Uncategorized and nothing asks
    page, handled = _open_at(browser, company, "#/journal")
    page.on("dialog", on_dialog)
    try:
        _open_form(page, handled, "JournalPage.showForm()", "#je-lines tr")
        assert page.evaluate(
            "() => document.querySelector('#modal-body form').class_id.value"
        ) == str(uncat["id"])
        fill_journal(page, "warn off")
        page.evaluate(
            "() => document.querySelector('#modal-body form').requestSubmit()"
        )
        settle(page, handled)
        page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
        assert dialogs == []
    finally:
        page.close()

    # on: the picker starts blank; Save asks; Cancel keeps the form, OK saves
    _ok(company.put("/api/settings", json={"class_warn_blank": "true"}))
    try:
        page, handled = _open_at(browser, company, "#/settings")
        page.on("dialog", on_dialog)
        try:
            page.click("#settings-tab-lists")  # its group (#720)
            page.wait_for_selector("#class-warn-blank")
            box = page.get_by_label("Warn when a transaction is saved without a class")
            assert box.is_checked()

            _visit(page, handled, "#/journal")
            _open_form(page, handled, "JournalPage.showForm()", "#je-lines tr")
            assert (
                page.evaluate(
                    "() => document.querySelector('#modal-body form').class_id.value"
                )
                == ""
            )
            assert "choose a class" in page.evaluate(
                "() => document.querySelector('#modal-body form').class_id.options[0].textContent"
            )
            fill_journal(page, "warn on, no class")
            page.evaluate(
                "() => document.querySelector('#modal-body form').requestSubmit()"
            )
            settle(page, handled)
            assert (
                len(dialogs) == 1 and "reported under Uncategorized" in dialogs[0]
            ), dialogs
            assert page.evaluate(OPEN), "Cancel keeps the form open"
            before = _ok(company.get(f"/api/classes/{uncat['id']}/transactions"))
            assert not any(
                e["description"] == "warn on, no class" for e in before["entries"]
            )

            page.evaluate(
                "() => document.querySelector('#modal-body form').requestSubmit()"
            )
            settle(page, handled)
            page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
            assert len(dialogs) == 2
            after = _ok(company.get(f"/api/classes/{uncat['id']}/transactions"))
            assert any(
                e["description"] == "warn on, no class" for e in after["entries"]
            ), "saved to Uncategorized"

            # a class on every line is as good as one on the header: no question
            _open_form(page, handled, "JournalPage.showForm()", "#je-lines tr")
            fill_journal(page, "warn on, lines classed")
            page.evaluate(
                "(id) => document.querySelectorAll('#je-lines tr .je-function-fund').forEach(s => { s.value = String(id); })",
                site["id"],
            )
            page.evaluate(
                "() => document.querySelector('#modal-body form').requestSubmit()"
            )
            settle(page, handled)
            page.wait_for_function(f"() => !({OPEN})()", timeout=5000)
            assert len(dialogs) == 2
            mine = _ok(company.get(f"/api/classes/{site['id']}/transactions"))
            assert (
                sum(
                    1
                    for e in mine["entries"]
                    if e["description"] == "warn on, lines classed"
                )
                == 2
            )

            # Settings: the box, by keyboard, and Save Settings turns it off
            _visit(page, handled, "#/settings")
            page.click("#settings-tab-lists")  # its group (#720)
            page.wait_for_selector("#class-warn-blank")
            box = page.get_by_label("Warn when a transaction is saved without a class")
            box.focus()
            page.keyboard.press("Space")
            assert not box.is_checked()
            page.evaluate(
                "() => document.getElementById('settings-form').requestSubmit()"
            )
            settle(page, handled)
            assert _ok(company.get("/api/settings"))["class_warn_blank"] == "false"
        finally:
            page.close()
    finally:
        _ok(company.put("/api/settings", json={"class_warn_blank": "false"}))


def test_the_warn_setting_starts_the_deposit_picker_blank_and_asks(
    browser, company, books, classed
):
    # Make Deposits keeps its class picker on its own toolbar, not in a form,
    # and used to preselect Uncategorized whatever the setting said, so the
    # deposit never asked (R14 review).
    uncat = classed["uncat"]
    customer = _ok(company.get("/api/customers"))[0]["id"]
    inv = _ok(
        company.post(
            "/api/invoices",
            json={
                "customer_id": customer,
                "date": "2026-09-25",
                "tax_rate": 0,
                "lines": [{"description": "Wedding cake", "quantity": 1, "rate": 95}],
            },
        )
    )
    _ok(
        company.post(
            "/api/payments",
            json={
                "customer_id": customer,
                "date": "2026-09-26",
                "amount": 95,
                "method": "Check",
                "check_number": "4410",
                "allocations": [{"invoice_id": inv["id"], "amount": 95}],
            },
        )
    )
    pending = _ok(company.get("/api/deposits/pending"))
    mine = next(p for p in pending if float(p["amount"]) == 95)
    box = f"input.dep-check[data-lineid='{mine['transaction_line_id']}']"
    dialogs = []

    def on_dialog(d):
        dialogs.append(d.message)
        (d.dismiss if len(dialogs) == 1 else d.accept)()

    # off: the picker starts on Uncategorized
    page, handled = _open_at(browser, company, "#/deposits")
    try:
        page.wait_for_selector("#deposit-class")
        assert page.evaluate(
            "() => document.getElementById('deposit-class').value"
        ) == str(uncat["id"])
    finally:
        page.close()

    _ok(company.put("/api/settings", json={"class_warn_blank": "true"}))
    try:
        page, handled = _open_at(browser, company, "#/deposits")
        page.on("dialog", on_dialog)
        try:
            page.wait_for_selector("#deposit-class")
            picker = page.locator("#deposit-class")
            assert picker.evaluate("el => el.value") == ""
            assert "choose a class" in picker.evaluate(
                "el => el.options[0].textContent"
            )
            # named: the label's `for` moves to the type-ahead box (2.19)
            named = page.get_by_label("Class:")
            assert named.count() == 1
            assert named.evaluate("el => el.id") in (
                "deposit-class",
                "deposit-class-box",
            )
            page.check(box)
            deposited_before = len(_ok(company.get("/api/deposits?limit=50")))
            # Cancel: nothing is deposited and the page stays
            page.get_by_role("button", name="Make Deposit", exact=True).click()
            settle(page, handled)
            assert len(dialogs) == 1 and "reported under Uncategorized" in dialogs[0]
            assert len(_ok(company.get("/api/deposits?limit=50"))) == deposited_before
            assert page.is_checked(box)
            # OK: deposited, under Uncategorized
            page.get_by_role("button", name="Make Deposit", exact=True).click()
            settle(page, handled)
            page.wait_for_function(
                f'() => !document.querySelector("{box}")', timeout=5000
            )
            assert len(dialogs) == 2
            entries = _ok(company.get(f"/api/classes/{uncat['id']}/transactions"))[
                "entries"
            ]
            assert any(
                e["source_type"] == "deposit"
                and e["date"] == dt.date.today().isoformat()
                for e in entries
            ), "the deposit is reported under Uncategorized"
        finally:
            page.close()
    finally:
        _ok(company.put("/api/settings", json={"class_warn_blank": "false"}))
