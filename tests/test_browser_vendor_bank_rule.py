"""The vendor form's bank-feed box and the Bank Rule form's vendor, in a
real browser (#493). The API is stubbed; the pages are the real ones."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_ui import _open  # noqa: E402
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

ACCOUNTS = [
    {
        "id": 2,
        "name": "Rent",
        "account_number": "6200",
        "account_type": "expense",
        "is_active": True,
    },
]
VENDOR = {
    "id": 7,
    "name": "NT Suites",
    "company": None,
    "email": None,
    "phone": None,
    "fax": None,
    "website": None,
    "address1": None,
    "address2": None,
    "city": None,
    "state": None,
    "zip": None,
    "country": "US",
    "terms": "Net 30",
    "tax_id": None,
    "account_number": None,
    "default_expense_account_id": None,
    "is_1099_vendor": False,
    "vendor_1099_type": None,
    "w9_on_file": False,
    "notes": None,
    "is_active": True,
    "balance": "0",
    "bank_feed_rule": False,
    "bank_rule_id": None,
}


def test_the_box_waits_for_the_default_account_and_is_sent(browser):
    sent = []

    def _vendor(route):
        if route.request.method == "PUT":
            sent.append(route.request.post_data_json)
            return route.fulfill(json={**VENDOR, **sent[-1]})
        return route.fulfill(json=VENDOR)

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/accounts*", lambda r: r.fulfill(json=ACCOUNTS))
        page.route("**/api/vendors/7", _vendor)
        page.evaluate("() => VendorsPage.showForm(7)")
        page.wait_for_selector("#vendor-bank-rule")
        assert page.is_disabled("#vendor-bank-rule")
        page.select_option("select[name=default_expense_account_id]", "2")
        assert not page.is_disabled("#vendor-bank-rule")
        page.check("#vendor-bank-rule")
        page.click("#modal-body button[type=submit]")
        page.wait_for_function("() => document.querySelector('.toast')")
        assert sent and sent[-1]["bank_feed_rule"] is True
        assert sent[-1]["default_expense_account_id"] == 2
        # clearing the account unticks the box
        page.evaluate("() => VendorsPage.showForm(7)")
        page.wait_for_selector("#vendor-bank-rule")
        page.select_option("select[name=default_expense_account_id]", "2")
        page.check("#vendor-bank-rule")
        page.select_option("select[name=default_expense_account_id]", "")
        assert not page.is_checked("#vendor-bank-rule")
        assert page.is_disabled("#vendor-bank-rule")
    finally:
        page.close()


def test_the_rule_form_shows_and_sends_the_vendor(browser):
    sent = []
    rule = {
        "id": 3,
        "name": "NT",
        "pattern": "NT SUITES",
        "account_id": 2,
        "vendor_id": 7,
        "rule_type": "contains",
        "priority": 0,
        "is_active": True,
        "created_at": "2026-10-24T00:00:00",
        "account_name": "6200 - Rent",
        "vendor_name": "NT Suites",
    }

    def _rule(route):
        if route.request.method == "PUT":
            sent.append(route.request.post_data_json)
        return route.fulfill(json=rule)

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/accounts*", lambda r: r.fulfill(json=ACCOUNTS))
        page.route(
            "**/api/vendors*",
            lambda r: r.fulfill(json=[VENDOR, {**VENDOR, "id": 8, "name": "Other"}]),
        )
        page.route("**/api/bank-rules/3", _rule)
        page.route("**/api/bank-rules", lambda r: r.fulfill(json=[rule]))
        page.evaluate("() => BankRulesPage.showForm(3)")
        page.wait_for_selector("#bank-rule-vendor")
        assert page.input_value("#bank-rule-vendor") == "7"
        page.select_option("#bank-rule-vendor", "8")
        page.click("#modal-body button[type=submit]")
        page.wait_for_function("() => document.querySelector('.toast')")
        assert sent[-1]["vendor_id"] == 8
        # the list names the vendor and the account, not an id
        page.goto(page.url.split("#")[0] + "#/bank-rules")
        page.wait_for_selector("text=6200 - Rent")
        assert "NT Suites" in page.locator("td").all_inner_texts()
    finally:
        page.close()


def test_the_rule_form_refuses_a_pattern_of_stars_and_says_a_star_in_words(browser):
    """W-193 and NEW-259 in the window: a pattern of only `*` is refused in
    the server's sentence before anything is sent; typed, a pattern with a
    star says how it reads, and the list says it under the pattern."""
    sent = []
    square = {
        "id": 4,
        "name": "Square",
        "pattern": "SQ \\*",
        "account_id": 2,
        "vendor_id": None,
        "class_id": None,
        "job_id": None,
        "customer_id": None,
        "memo": None,
        "rule_type": "contains",
        "priority": 0,
        "is_active": True,
        "created_at": "2026-11-01T00:00:00",
        "account_name": "6200 - Rent",
    }

    def _rules(route):
        if route.request.method == "POST":
            sent.append(route.request.post_data_json)
            return route.fulfill(status=201, json=square)
        return route.fulfill(json=[square])

    page = _open(browser, 1280, 800, "#/")
    try:
        page.route("**/api/accounts*", lambda r: r.fulfill(json=ACCOUNTS))
        page.route("**/api/vendors*", lambda r: r.fulfill(json=[VENDOR]))
        page.route("**/api/bank-rules", _rules)
        page.evaluate("() => BankRulesPage.showForm()")
        page.wait_for_selector("#bank-rule-pattern")
        page.fill("#modal-body input[name=name]", "Star")
        page.fill("#bank-rule-pattern", " * ")
        page.click("#modal-body button[type=submit]")
        page.wait_for_selector(".toast-error")
        sentence = page.evaluate("() => BankRulesPage.PATTERN_EMPTY")
        assert page.inner_text(".toast-error") == sentence
        assert (
            page.evaluate(
                "() => document.getElementById('bank-rule-pattern').validationMessage"
            )
            == sentence
        )
        assert sent == []
        # the tortoise holds the teaching; the reading is in the open
        assert (
            page.locator("#bank-rule-pattern-hint")
            .text_content()
            .count("stands for anything")
            == 1
        )
        page.fill("#bank-rule-pattern", "SQ \\*")
        assert page.inner_text("#bank-rule-pattern-reads") == "Reads: “SQ *”"
        page.click("#modal-body button[type=submit]")
        page.wait_for_function("() => document.querySelector('.toast-success')")
        assert sent[-1]["pattern"] == "SQ \\*"
        page.goto(page.url.split("#")[0] + "#/bank-rules")
        page.wait_for_selector(".rule-pattern-reads")
        assert page.inner_text(".rule-pattern-reads") == "Reads: “SQ *”"
    finally:
        page.close()
