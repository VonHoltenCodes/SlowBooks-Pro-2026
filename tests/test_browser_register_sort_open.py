"""The register's Sort and Open at in a real browser (#903): two named boxes
in the filter bar, reached with Tab, kept per sign-in over a reload. Newest
first draws the entries the other way up with the entry row under the column
heads and each row's running balance unchanged; Open at chooses where an
opened register starts, and not chosen it starts at the top while lines wait
in To review (#728's newest entries otherwise). The To review count is a link
to the list, and Jump to top / Jump to newest move the page (and, from the
keyboard, the focus). The API is stubbed; the page is the real one
(tests/test_browser_ui.py's fixtures)."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from tests.test_browser_register_date import (  # noqa: E402
    ELEMENT_IN_VIEW,
    IN_VIEW,
    SCROLLED,
    _Ledger,
    _ledger_page,
    _redrawn,
    _wait_redrawn,
)
from tests.test_theme_contrast import browser_fixture  # noqa: E402,F401  (the fixture)

ROWS = "() => [...document.querySelectorAll('#page-content tr[data-txn]')].map(r => +r.dataset.txn)"
BALANCES = (
    "() => Object.fromEntries([...document.querySelectorAll('#page-content tr[data-txn]')]"
    ".map(r => [r.dataset.txn, r.cells[8].textContent.trim()]))"  # Balance (#729)
)
AT_FOOT = (
    "() => { const b = document.getElementById('content');"
    " return b.scrollHeight - b.clientHeight - b.scrollTop <= 2; }"
)
ACTIVE = "() => document.activeElement && document.activeElement.id"


def _ledger(review=0):
    ledger = _Ledger()
    # a running balance of its own on each row, as the service reads it
    for i, e in enumerate(ledger.entries):
        e["balance"] = 1000 + i
    ledger.review = [
        {
            "id": 70 + i,
            "date": "2026-10-01",
            "payee": f"Statement line {i}",
            "description": "",
            "amount": -5.0,
        }
        for i in range(review)
    ]
    return ledger


def _choose(page, sel, value):
    _redrawn(page)
    page.select_option(sel, value)
    _wait_redrawn(page)


def test_sort_and_open_at_are_named_reached_by_tab_and_remembered(browser):
    page = _ledger_page(browser, _ledger(), "#/banking/1")
    try:
        sort = page.get_by_role("combobox", name="Sort")
        open_at = page.get_by_role("combobox", name="Open at")
        assert sort.count() == 1 and open_at.count() == 1
        assert page.input_value("#reg-sort") == "oldest"
        assert page.input_value("#reg-open-at") == ""
        assert page.get_by_role("button", name="Jump to newest").count() == 1
        assert page.get_by_role("button", name="Jump to top").count() == 1
        assert page.get_attribute("#page-content thead th", "aria-sort") == "ascending"

        # Tab from Hide voided: Sort, Open at, Jump to newest
        page.focus("#reg-hide-voided")
        page.keyboard.press("Tab")
        assert page.evaluate(ACTIVE) == "reg-sort"
        page.keyboard.press("Tab")
        assert page.evaluate(ACTIVE) == "reg-open-at"
        page.keyboard.press("Tab")
        assert page.evaluate(ACTIVE) == "reg-jump-newest"

        before = page.evaluate(BALANCES)
        oldest = page.evaluate(ROWS)
        assert oldest[0] == 100 and oldest[-1] == 159

        page.focus("#reg-sort")
        _choose(page, "#reg-sort", "newest")
        # the other way up, each row's balance its own
        assert page.evaluate(ROWS) == list(reversed(oldest))
        assert page.evaluate(BALANCES) == before
        assert page.get_attribute("#page-content thead th", "aria-sort") == "descending"
        # the entry row heads the list, before the newest entry
        assert page.evaluate(
            "() => !!(document.getElementById('reg-quick-entry').compareDocumentPosition("
            "document.querySelector('#page-content tr[data-txn]')) & Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        # the keyboard back on the box
        assert page.evaluate(ACTIVE) == "reg-sort"
        assert (
            page.evaluate("() => Store.get(BankingPage._regPrefKey('sort'))")
            == "newest"
        )

        page.select_option("#reg-open-at", "top")
        assert (
            page.evaluate("() => Store.get(BankingPage._regPrefKey('open-at'))")
            == "top"
        )

        # kept over a reload
        page.reload()
        page.wait_for_selector("#page-content tr[data-txn]")
        assert page.input_value("#reg-sort") == "newest"
        assert page.input_value("#reg-open-at") == "top"
        assert page.evaluate(ROWS)[0] == 159
        assert page.evaluate(SCROLLED) == 0

        # and back: oldest first, Open at not chosen
        _choose(page, "#reg-sort", "oldest")
        page.select_option("#reg-open-at", "")
        assert page.evaluate(ROWS) == oldest
        assert page.evaluate("() => Store.get(BankingPage._regPrefKey('sort'))") is None
        assert (
            page.evaluate("() => Store.get(BankingPage._regPrefKey('open-at'))") is None
        )
    finally:
        page.close()


def test_newest_first_opens_on_the_entry_row_and_ends_on_the_balance_brought_forward(
    browser,
):
    page = _ledger_page(browser, _ledger(), "#/banking/1")
    try:
        _choose(page, "#reg-sort", "newest")
        page.goto(page.url.split("#")[0] + "#/banking/1?start_date=2026-09-05")
        page.wait_for_selector("#reg-opening")
        # opened at its newest: the entry row and the newest entry in view,
        # not the foot of the page
        page.wait_for_function(ELEMENT_IN_VIEW, arg="#reg-quick-entry")
        assert page.evaluate(IN_VIEW, 159)
        assert not page.evaluate(AT_FOOT)
        # the balance brought forward is the oldest line: the last row
        assert (
            page.evaluate(
                "() => { const rows = document.querySelectorAll('#page-content table tbody tr');"
                " return rows[rows.length - 1].id; }"
            )
            == "reg-opening"
        )
    finally:
        page.close()


def test_open_at_follows_to_review_unless_chosen(browser):
    # lines waiting, nothing chosen: the top of the page, To review in view
    page = _ledger_page(browser, _ledger(review=3), "#/banking/1", feed=True)
    try:
        page.wait_for_selector("#reg-review")
        assert page.evaluate(SCROLLED) == 0
        assert page.evaluate(ELEMENT_IN_VIEW, "#reg-review")

        # Newest entries chosen: the foot, lines waiting or not (#728)
        page.select_option("#reg-open-at", "newest")
        page.reload()
        page.wait_for_selector("#reg-review")
        page.wait_for_function(AT_FOOT)
        assert page.evaluate(ELEMENT_IN_VIEW, "#reg-quick-entry")
    finally:
        page.close()

    # nothing waiting, nothing chosen: the newest entries, as before
    page = _ledger_page(browser, _ledger(), "#/banking/1", feed=True)
    try:
        page.wait_for_function(AT_FOOT)
        assert page.locator("#reg-review").count() == 0
        assert page.locator("#reg-review-count").count() == 0
        # Top of page chosen: the top, nothing waiting
        page.select_option("#reg-open-at", "top")
        page.reload()
        page.wait_for_selector("#page-content tr[data-txn]")
        assert page.evaluate(SCROLLED) == 0
    finally:
        page.close()


def test_the_review_count_and_the_jumps_move_the_page(browser):
    page = _ledger_page(browser, _ledger(review=2), "#/banking/1", feed=True)
    try:
        page.wait_for_selector("#reg-review")
        link = page.get_by_role("link", name="2 to review: go to the To review list")
        assert link.count() == 1

        # Jump to newest, from the keyboard: the foot, the entry row's date
        page.focus("#reg-jump-newest")
        page.keyboard.press("Enter")
        page.wait_for_function(AT_FOOT)
        assert page.evaluate(ACTIVE) == "qe-date"

        # Jump to top, from the keyboard: the top, the heading
        page.focus("#reg-jump-top")
        page.keyboard.press("Enter")
        assert page.evaluate(SCROLLED) == 0
        assert page.evaluate("() => document.activeElement.tagName") == "H2"

        # the count, from the foot: To review in view and focused, the
        # address unchanged
        page.evaluate(
            "() => { const b = document.getElementById('content'); b.scrollTop = b.scrollHeight; }"
        )
        hash_before = page.evaluate("() => location.hash")
        link.focus()
        page.keyboard.press("Enter")
        assert page.evaluate(ACTIVE) == "reg-review"
        assert page.evaluate(ELEMENT_IN_VIEW, "#reg-review .card-header")
        assert page.evaluate("() => location.hash") == hash_before
    finally:
        page.close()
