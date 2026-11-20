"""Add selected in playwright's Chromium on the bakery's books (#1053):
two lines ticked from the keyboard, a category and a vendor set on them
from the bar above the list, and Add selected posts those two and leaves
the third waiting.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def _ok(resp):
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _lines(company, books):
    return {
        t["payee"]: t
        for t in _ok(
            company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
        )
    }


def test_tick_two_lines_set_them_from_the_bar_and_add_them(browser, company, books):
    vendor = _ok(company.post("/api/vendors", json={"name": "Lumber Yard 1053"}))
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "more.csv",
                    b"Date,Description,Amount\n"
                    b'09/26/2026,LUMBER YARD A,"-64.20"\n'
                    b'09/27/2026,LUMBER YARD B,"-12.80"\n',
                    "text/csv",
                )
            },
        )
    )
    waiting = _lines(company, books)
    a, b = waiting["LUMBER YARD A"]["id"], waiting["LUMBER YARD B"]["id"]
    fuel = waiting["SHELL OIL 5521"]["id"]
    expense = next(
        x["id"]
        for x in _ok(company.get("/api/accounts"))
        if x["account_type"] == "expense"
    )
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_function(
            "() => document.getElementById('bulk-cat').options.length > 1",
            timeout=5000,
        )
        # ticked from the keyboard: focus the box, Space
        for line in (a, b):
            page.focus(f"input[data-review-tick='{line}']")
            page.keyboard.press("Space")
        assert page.inner_text("#review-ticked") == "2 ticked"
        assert page.evaluate(
            "() => document.getElementById('review-tick-all').indeterminate"
        )
        page.select_option("#bulk-cat", str(expense))
        page.fill("#bulk-vendor", vendor["name"])
        page.click("button:text-is('Add selected')")
        page.wait_for_selector(
            "#toast-container .toast:has-text('Added 2 of 2 ticked')"
        )
        page.wait_for_function(
            f"() => !document.querySelector('tr[data-review-line=\"{a}\"]')"
            f" && !document.querySelector('tr[data-review-line=\"{b}\"]')",
            timeout=5000,
        )
        assert page.query_selector(f"tr[data-review-line='{fuel}']") is not None
        settle(page, handled)
    finally:
        page.close()
    after = _lines(company, books)
    assert after["LUMBER YARD A"]["match_status"] == "added"
    assert after["LUMBER YARD B"]["match_status"] == "added"
    assert after["LUMBER YARD A"]["vendor_name"] == "Lumber Yard 1053"
    assert after["SHELL OIL 5521"]["match_status"] == "unmatched"


def test_a_line_add_selected_leaves_out_is_named_and_stays_ticked(
    browser, company, books
):
    """W-190 / NEW-261: one ticked line with no category. The toast is a
    warning that names it (date, the bank's text, amount) with its reason,
    and after the list is drawn anew it is still ticked."""
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "w190.csv",
                    b"Date,Description,Amount\n"
                    b'09/28/2026,BETA HOMES CHECK,"-120.00"\n'
                    b'09/29/2026,FUEL STOP 9,"-40.00"\n',
                    "text/csv",
                )
            },
        )
    )
    waiting = _lines(company, books)
    beta, fuel = waiting["BETA HOMES CHECK"]["id"], waiting["FUEL STOP 9"]["id"]
    expense = next(
        x["id"]
        for x in _ok(company.get("/api/accounts"))
        if x["account_type"] == "expense"
    )
    _ok(
        company.patch(
            f"/api/banking/transactions/{fuel}", json={"category_account_id": expense}
        )
    )
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_selector(f"input[data-review-tick='{beta}']")
        for line in (beta, fuel):
            page.focus(f"input[data-review-tick='{line}']")
            page.keyboard.press("Space")
        page.click("button:text-is('Add selected')")
        toast = page.wait_for_selector(
            "#toast-container .toast:has-text('Added 1 of 2 ticked')"
        )
        assert "toast-warning" in toast.get_attribute("class")
        said = toast.inner_text()
        assert "Not added (1):" in said
        assert "BETA HOMES CHECK" in said and "120.00" in said
        assert "category" in said.lower()
        assert "FUEL STOP 9" not in said
        page.wait_for_function(
            f"() => !document.querySelector('tr[data-review-line=\"{fuel}\"]')",
            timeout=5000,
        )
        page.wait_for_function(
            f"() => document.querySelector(\"input[data-review-tick='{beta}']\")"
            "?.checked === true",
            timeout=5000,
        )
        assert page.inner_text("#review-ticked") == "1 ticked"
        settle(page, handled)
    finally:
        page.close()
    after = _lines(company, books)
    assert after["FUEL STOP 9"]["match_status"] == "added"
    assert after["BETA HOMES CHECK"]["match_status"] == "unmatched"


def test_a_review_memo_is_held_to_500_and_counted_near_it(browser, company, books):
    """W-191 in the window: the row's memo takes at most 500 characters and
    says its count once it is within 50 of that."""
    line = _lines(company, books)["SHELL OIL 5521"]["id"]
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        memo = f"#rv-memo-{line}"
        page.wait_for_selector(memo)
        page.fill(memo, "m" * 40)
        assert page.inner_text(f"#rv-memo-count-{line}") == ""
        page.fill(memo, "m" * 460)
        assert page.inner_text(f"#rv-memo-count-{line}") == "460 of 500 characters"
        page.fill(memo, "m" * 520)
        assert len(page.input_value(memo)) == 500
        assert page.inner_text(f"#rv-memo-count-{line}") == "500 of 500 characters"
        settle(page, handled)
    finally:
        page.close()
