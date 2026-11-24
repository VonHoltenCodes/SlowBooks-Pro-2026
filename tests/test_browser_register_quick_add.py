"""The register's quick add in a real browser (#386, and the #386 review):
the category picker is a plain select with an Add button beside it, so
arrowing never springs the panel; the panel makes an account and leaves out
the control accounts as parents; a payee is offered as a customer for money
in and a vendor for money out; and the question's toast is answerable by
keyboard. The API is stubbed; the page is the real one (tests/test_browser_ui.py's
fixtures)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

REGISTER = {
    "account_name": "Checking",
    "account_number": "1000",
    "bank_kind": "bank",
    "entries": [],
    "balance": 0,
    "opening_balance": 0,
    "class_name": None,
}
CHART = [
    {
        "id": 1,
        "name": "Checking",
        "account_number": "1000",
        "account_type": "asset",
        "bank_kind": "bank",
    },
    {
        "id": 2,
        "name": "Automobile",
        "account_number": "6100",
        "account_type": "expense",
    },
    {
        "id": 3,
        "name": "Accounts Receivable",
        "account_number": "1100",
        "account_type": "asset",
        "is_control": True,
    },
    {
        "id": 4,
        "name": "Prepaid Expenses",
        "account_number": "1400",
        "account_type": "asset",
    },
]


def _goto_register(browser):
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#qe-category")
    return page


def test_arrowing_the_category_picker_never_opens_the_panel(browser):
    page = _goto_register(browser)
    try:
        # the trap option is gone: the picker is categories only
        texts = page.locator("#qe-category option").all_inner_texts()
        assert "+ Add a new account…" not in texts
        assert not page.is_visible("#qe-category-new")

        # ArrowDown through the categories never springs the panel (the
        # review's trap: a "+ Add…" option one ArrowDown from the top).
        page.focus("#qe-category")
        page.press("#qe-category", "ArrowDown")
        assert not page.is_visible("#qe-category-new")
        page.press("#qe-category", "ArrowDown")
        assert not page.is_visible("#qe-category-new")
        # headless Chromium does not move a closed select's value on
        # ArrowDown; choosing a category (its change event) still never
        # opens the panel, and the category is reachable
        page.select_option("#qe-category", index=1)
        assert page.input_value("#qe-category") != ""
        assert not page.is_visible("#qe-category-new")

        # only the Add button opens it, with the cursor in the name
        page.click("#qe-category-add")
        page.wait_for_selector("#qe-category-new", state="visible")
        assert (
            page.evaluate("() => document.activeElement.id") == "qe-category-new-name"
        )
    finally:
        page.close()


def test_a_new_account_is_made_and_control_accounts_are_not_offered(browser):
    posted = []

    def _accounts(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(
                status=201, json={**posted[-1], "id": 77, "is_active": True}
            )
        return route.fulfill(json=CHART)

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", _accounts)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-category")

        page.click("#qe-category-add")
        page.wait_for_selector("#qe-category-new", state="visible")
        page.fill("#qe-category-new-name", "Truck Fuel")
        # expense parents: the one expense account, not the bank
        assert page.locator("#qe-category-new-parent option").all_inner_texts() == [
            "None",
            "6100 - Automobile",
        ]
        # an asset: Prepaid is offered, Accounts Receivable (a control
        # account) is not, nor the bank
        page.select_option("#qe-category-new-type", "asset")
        assert page.locator("#qe-category-new-parent option").all_inner_texts() == [
            "None",
            "1400 - Prepaid Expenses",
        ]

        page.select_option("#qe-category-new-type", "expense")
        page.select_option("#qe-category-new-parent", "2")
        page.click("text=Add account")
        page.wait_for_function(
            "() => document.getElementById('qe-category').value === '77'"
        )
        assert posted == [
            {
                "name": "Truck Fuel",
                "account_type": "expense",
                "parent_id": 2,
                "account_number": None,
            }
        ]
        # a sub-account of Automobile, named by its path as the chart nests it
        assert (
            page.locator("#qe-category option:checked").inner_text()
            == "Automobile: Truck Fuel"
        )
        assert not page.is_visible("#qe-category-new")
        assert page.evaluate("() => document.activeElement.id") == "qe-out"
    finally:
        page.close()


def test_a_known_payee_is_not_offered_whatever_the_side(browser):
    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route(
            "**/api/vendors*",
            lambda r: r.fulfill(json=[{"id": 1, "name": "Acme Supply"}]),
        )
        page.route(
            "**/api/customers*",
            lambda r: r.fulfill(json=[{"id": 1, "name": "Jane Homeowner"}]),
        )
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        # an existing vendor's name, any case: no question
        page.fill("#qe-payee", "acme supply")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0

        # an existing customer's name, even on a deposit: no question
        page.fill("#qe-in", "100")
        page.fill("#qe-payee", "jane homeowner")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0
    finally:
        page.close()


def test_a_deposit_offers_a_customer_and_the_toast_answers_by_keyboard(browser):
    posted = []

    def _customers(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 9})
        return route.fulfill(json=[])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
        page.route("**/api/customers*", _customers)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        # money in: offered as a customer
        page.fill("#qe-in", "250")
        page.fill("#qe-payee", "Jane Homeowner")
        page.press("#qe-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert toast.inner_text().split() == [
            "Add",
            '"Jane',
            'Homeowner"',
            "as",
            "a",
            "customer?",
            "Add",
            "Not",
            "now",
        ]
        # the question took focus (reachable without 12 Tabs), and the row
        # kept its text
        assert page.evaluate(
            "() => document.activeElement.closest('#toast-container') !== null"
            " && document.activeElement.textContent === 'Add'"
        )
        assert page.input_value("#qe-payee") == "Jane Homeowner"

        # Not now closes it and hands focus back to the row
        toast.locator("button", has_text="Not now").click()
        assert page.locator("#toast-container .toast").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "qe-payee"
        # answered: not asked again for this name, even money out
        page.fill("#qe-in", "")
        page.fill("#qe-out", "10")
        page.fill("#qe-payee", "Jane Homeowner")
        page.press("#qe-payee", "Tab")
        page.wait_for_timeout(100)
        assert page.locator("#toast-container .toast").count() == 0
    finally:
        page.close()


def test_an_ignored_toast_times_out_without_stealing_focus(browser):
    """#386 review: ignore the toast, type in Memo, and after the timeout the
    cursor must stay in Memo — the auto-dismiss used to hand focus to Payee."""
    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/vendors*", lambda r: r.fulfill(json=[]))
        page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")
        # a short timeout so the test need not wait the real 15 s (test-only)
        page.evaluate("() => { window.__payeeToastMs = 300; }")

        page.fill("#qe-out", "80")
        page.fill("#qe-payee", "Pine Street Lumber")
        page.press("#qe-payee", "Tab")
        page.locator("#toast-container .toast").wait_for()

        # ignore the toast: move to Memo and type, char by char
        page.focus("#qe-memo")
        page.type("#qe-memo", "Fuel")
        page.wait_for_timeout(500)  # past the 300 ms auto-dismiss
        # the toast is gone and it did NOT yank the caret to Payee
        assert page.locator("#toast-container .toast").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "qe-memo"
        # the rest of the typing still lands in Memo, not Payee
        page.type("#qe-memo", " receipt")
        assert page.input_value("#qe-memo") == "Fuel receipt"
        assert page.input_value("#qe-payee") == "Pine Street Lumber"
    finally:
        page.close()


def test_the_entry_dialog_toast_is_reachable_and_answerable_by_keyboard(browser):
    """#386 review: in + Entry (Amount before Payee) the offer fires on blur
    and the dialog traps Tab; the toast must still be Tab-reachable there."""
    posted = []

    def _vendors(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 5})
        return route.fulfill(json=[])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
        page.route("**/api/vendors*", _vendors)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        page.fill("[name='amount']", "-80")  # money out -> a vendor
        page.fill("#entry-payee", "Pine Street Lumber")
        page.press("#entry-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert "as a vendor?" in toast.inner_text()

        # from a form field, Tab must walk out to the toast's Add button and
        # wrap back; it was unreachable in 12 Tabs before the fix
        page.focus("#entry-payee")
        reached = False
        for _ in range(20):
            page.keyboard.press("Tab")
            if page.evaluate(
                "() => { const a = document.activeElement;"
                " return !!a && a.closest('#toast-container') !== null"
                " && a.textContent === 'Add'; }"
            ):
                reached = True
                break
        assert reached, "the toast's Add button was not reachable by Tab"

        # answer it from the keyboard
        page.keyboard.press("Enter")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')]"
            ".some(t => t.textContent.includes('added to Vendors'))"
        )
        assert posted == [{"name": "Pine Street Lumber"}]
    finally:
        page.close()


def test_money_out_offers_a_vendor_and_add_posts_it(browser):
    posted = []

    def _vendors(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            return route.fulfill(status=201, json={**posted[-1], "id": 5})
        return route.fulfill(json=[])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
        page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
        page.route("**/api/customers*", lambda r: r.fulfill(json=[]))
        page.route("**/api/vendors*", _vendors)
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-payee")

        page.fill("#qe-out", "80")
        page.fill("#qe-payee", "Pine Street Lumber")
        page.press("#qe-payee", "Tab")
        toast = page.locator("#toast-container .toast")
        toast.wait_for()
        assert "as a vendor?" in toast.inner_text()
        toast.locator("button", has_text="Add").click()
        page.wait_for_function(
            "() => [...document.querySelectorAll('#toast-container .toast')]"
            ".some(t => t.textContent.includes('added to Vendors'))"
        )
        assert posted == [{"name": "Pine Street Lumber"}]
    finally:
        page.close()


# ── a line opens over the register (Mike's report, 2026-10-26) ───────────

ENTRY = {
    "transaction_id": 42,
    "date": "2026-10-20",
    "description": "Deposit: Deposit Stripe",
    "reference": "Deposit",
    "payee": "",
    "source_type": "qb_ledger_import",
    "source_link": None,
    "amount": 90.52,
    "debit": 90.52,
    "credit": 0,
    "balance": 90.52,
    "editable": False,
    "printable": False,
    "voided": False,
}
JOURNAL_42 = {
    "id": 42,
    "date": "2026-10-20",
    "description": "Deposit: Deposit Stripe",
    "reference": "",
    "source_type": "qb_ledger_import",
    "lines": [],
    "total_debit": 90.52,
    "total_credit": 90.52,
}


def test_a_register_line_opens_over_the_register_and_closes_back_to_it(browser):
    page = _open(browser, 1280, 800, "#/")
    listed = []

    def journal(route):
        url = route.request.url
        if url.rstrip("/").endswith("/api/journal") or "/api/journal?" in url:
            listed.append(url)  # the whole Journal Entries list: must not load
            return route.fulfill(json=[])
        return route.fulfill(json=JOURNAL_42)

    page.route(
        "**/api/banking/check-register*",
        lambda r: r.fulfill(json={**REGISTER, "entries": [ENTRY], "balance": 90.52}),
    )
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/journal**", journal)
    page.route("**/api/attachments/**", lambda r: r.fulfill(json=[]))
    try:
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-category")
        page.click("text=Deposit: Deposit Stripe")
        page.wait_for_selector("text=Journal Entry #42")
        assert listed == [], listed
        assert page.url.endswith("#/journal/42")
        page.click("#modal-body >> button:has-text('Close')")
        page.wait_for_function("location.hash === '#/banking/1'")
        # the register is still the page under it, never redrawn as Journal Entries
        assert page.is_visible("#qe-category")
        assert listed == [], listed
    finally:
        page.close()


def test_voiding_a_posting_opened_from_the_register_comes_back_to_the_register(browser):
    page = _open(browser, 1280, 800, "#/")
    listed, voided = [], []

    def journal(route):
        url = route.request.url
        if route.request.method == "POST" and url.endswith("/api/journal/42/void"):
            voided.append(url)
            return route.fulfill(json={"id": 43})
        if url.rstrip("/").endswith("/api/journal") or "/api/journal?" in url:
            listed.append(url)
            return route.fulfill(json=[])
        return route.fulfill(json={**JOURNAL_42, "voidable": True})

    page.route(
        "**/api/banking/check-register*",
        lambda r: r.fulfill(json={**REGISTER, "entries": [ENTRY], "balance": 90.52}),
    )
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/journal**", journal)
    page.route("**/api/attachments/**", lambda r: r.fulfill(json=[]))
    page.on("dialog", lambda d: d.accept())
    try:
        page.goto(page.url.split("#")[0] + "#/banking/1")
        page.wait_for_selector("#qe-category")
        page.click("text=Deposit: Deposit Stripe")
        page.wait_for_selector("text=Journal Entry #42")
        page.click("#modal-body >> button:has-text('Void')")
        page.wait_for_function("location.hash === '#/banking/1'")
        page.wait_for_selector("#qe-category")
        assert voided, "the void was posted"
        assert listed == [], listed
    finally:
        page.close()


def _vendor_register(browser, posted, vendors):
    """The Checking register with the vendor list stubbed and the post
    captured, for the payee box's suggestions (#590)."""
    page = _open(browser, 1280, 800, "#/")
    page.route("**/api/banking/check-register*", lambda r: r.fulfill(json=REGISTER))
    page.route("**/api/accounts*", lambda r: r.fulfill(json=CHART))
    page.route("**/api/vendors*", lambda r: r.fulfill(json=vendors))
    page.route("**/api/customers*", lambda r: r.fulfill(json=[]))

    def _transactions(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=201, json={"id": len(posted)})

    page.route("**/api/banking/transactions", _transactions)
    page.goto(page.url.split("#")[0] + "#/banking/1")
    page.wait_for_selector("#qe-category")
    return page


def _post_row(page, payee):
    page.fill("#qe-payee", payee)
    page.select_option("#qe-category", "2")
    page.fill("#qe-out", "25")
    page.click("#reg-quick-entry button:has-text('Post')")
    page.wait_for_selector("#toast-container .toast:has-text('Entry posted')")
    page.wait_for_function("() => document.getElementById('qe-out').value === ''")


def test_the_payee_box_suggests_vendors_and_links_the_one_named(browser):
    posted = []
    page = _vendor_register(
        browser,
        posted,
        [{"id": 7, "name": "Pine Street Lumber"}, {"id": 8, "name": "Acme Supply"}],
    )
    try:
        # the box is wired to a list of the vendors, in the row and the dialog
        assert page.get_attribute("#qe-payee", "list") == "qe-payee-vendors"
        assert page.locator("#qe-payee-vendors option").evaluate_all(
            "els => els.map(e => e.value)"
        ) == ["Pine Street Lumber", "Acme Supply"]
        page.click("text=+ Entry")
        page.wait_for_selector("#entry-payee")
        assert page.locator("#entry-payee-vendors option").count() == 2
        page.evaluate("() => closeModal()")

        # a name that matches a vendor exactly (any case) links its id
        _post_row(page, "pine street LUMBER")
        assert (
            posted[-1]["vendor_id"] == 7 and posted[-1]["payee"] == "pine street LUMBER"
        )
        # a name that is not a vendor stays text, and the offer is made
        page.evaluate("() => { window.__payeeToastMs = 300; }")
        _post_row(page, "Pine Street")
        assert posted[-1]["vendor_id"] is None and posted[-1]["payee"] == "Pine Street"
        assert (
            page.locator(
                "#toast-container .toast:has-text('Add \"Pine Street\"')"
            ).count()
            == 1
        )
    finally:
        page.close()
