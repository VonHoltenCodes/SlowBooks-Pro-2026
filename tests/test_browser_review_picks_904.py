"""The To review row's picks survive another line's Add (#904), in
playwright's Chromium on the bakery's books of tests/test_theme_contrast.py.

A class and a vendor picked on one line were lost when Add on another line
drew the list again: only the category was kept on the line. Now each pick
is saved as it is made, Add takes out only its own row, and the other row
keeps what was typed, on screen and after a reload.

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


def test_picks_on_one_line_survive_another_lines_add(browser, company, books):
    cls = _ok(company.post("/api/classes", json={"name": "Delivery Crew"}))
    vendor = _ok(company.get("/api/vendors?active_only=true"))[0]
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "more.csv",
                    b'Date,Description,Amount\n09/26/2026,LUMBER YARD 9,"-64.20"\n',
                    "text/csv",
                )
            },
        )
    )
    waiting = _lines(company, books)
    fuel = waiting["SHELL OIL 5521"]["id"]
    lumber = waiting["LUMBER YARD 9"]["id"]
    expense = next(
        a["id"]
        for a in _ok(company.get("/api/accounts"))
        if a["account_type"] == "expense"
    )

    def picked(r):
        return (
            r.request.method == "PATCH"
            and r.url.endswith(f"/api/banking/transactions/{lumber}")
            and r.ok
        )

    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_function(
            f"() => document.getElementById('cat-{fuel}').options.length > 1",
            timeout=5000,
        )
        # the lumber line: a class and a vendor, each saved as picked
        with page.expect_response(picked):
            page.select_option(f"#rv-class-{lumber}", str(cls["id"]))
        with page.expect_response(picked):
            page.fill(f"#rv-vendor-{lumber}", vendor["name"])
            page.press(f"#rv-vendor-{lumber}", "Tab")
        page.evaluate(
            f"() => document.getElementById('rv-class-{lumber}').dataset.mark = 'kept'"
        )
        # the fuel line is added on its own
        page.select_option(f"#cat-{fuel}", str(expense))
        page.click(f"tr:has(#cat-{fuel}) button.btn-primary:text-is('Add')")
        page.wait_for_selector("#toast-container .toast:has-text('Added to the books')")
        page.wait_for_function(
            f"() => !document.querySelector('tr[data-review-line=\"{fuel}\"]')"
        )
        # the register below is drawn again with the new entry
        page.wait_for_selector("#page-content tr[data-txn]:has-text('SHELL OIL 5521')")
        after = page.evaluate(
            f"""() => {{ const c = document.getElementById('rv-class-{lumber}');
                return [c.value, c.dataset.mark || '',
                        document.getElementById('rv-vendor-{lumber}').value]; }}"""
        )
        # a reload draws the row from what the line keeps
        page.reload()
        settle(page, handled)
        page.wait_for_selector(f"#rv-vendor-{lumber}")
        reloaded = page.evaluate(
            f"""() => [document.getElementById('rv-class-{lumber}').value,
                       document.getElementById('rv-vendor-{lumber}').value]"""
        )
        settle(page, handled)
    finally:
        page.close()

    # the same row, not one drawn again, with what was picked
    assert after == [str(cls["id"]), "kept", vendor["name"]]
    assert reloaded == [str(cls["id"]), vendor["name"]]
    line = _lines(company, books)["LUMBER YARD 9"]
    assert line["pick_class_id"] == cls["id"]
    assert line["pick_vendor_id"] == vendor["id"]
