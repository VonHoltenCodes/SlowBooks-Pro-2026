"""The To review row's Class, Job and Name (#671), in playwright's Chromium
on the bakery's books of tests/test_theme_contrast.py (a fuel charge and a
deposit wait in To review; the bakery has a job and, here, a class of its
own). Picked on the row, the class, job and vendor or customer go out with
Add and post on the entry; every box has a name a screen reader says.

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


def test_the_review_row_posts_its_class_job_and_name(browser, company, books):
    cls = _ok(company.post("/api/classes", json={"name": "Catering Crew"}))
    vendors = _ok(company.get("/api/vendors?active_only=true"))
    vendor = vendors[0]
    # a deposit no entry matches, for the Customer box
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "more.csv",
                    b'Date,Description,Amount\n09/26/2026,FARMERS MARKET,"123.45"\n',
                    "text/csv",
                )
            },
        )
    )
    feed = _ok(
        company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
    )
    waiting = {t["payee"]: t for t in feed if t["match_status"] == "unmatched"}
    fuel = waiting["SHELL OIL 5521"]["id"]
    deposit = waiting["FARMERS MARKET"]["id"]
    expense = next(
        a["id"]
        for a in _ok(company.get("/api/accounts"))
        if a["account_type"] == "expense"
    )
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.wait_for_function(
            f"() => document.getElementById('cat-{fuel}').options.length > 1",
            timeout=5000,
        )
        names = page.evaluate(
            """(ids) => ids.map(id => { const el = document.getElementById(id);
                return el ? el.getAttribute('aria-label') : null; })""",
            [
                f"rv-class-{fuel}",
                f"rv-job-{fuel}",
                f"rv-vendor-{fuel}",
                f"rv-customer-{deposit}",
                f"rv-job-{deposit}",
            ],
        )
        # the deposit's Job box follows its Customer box
        bound = page.get_attribute(f"#rv-job-{deposit}", "data-customer-select")
        page.select_option(f"#cat-{fuel}", str(expense))
        page.select_option(f"#rv-class-{fuel}", str(cls["id"]))
        page.select_option(f"#rv-job-{fuel}", str(books["job"]))
        page.fill(f"#rv-vendor-{fuel}", vendor["name"])
        with page.expect_request(
            lambda r: r.url.endswith(f"/api/banking/transactions/{fuel}/add")
        ) as sent:
            page.click(f"tr:has(#cat-{fuel}) button.btn-primary:text-is('Add')")
        body = sent.value.post_data_json
        page.wait_for_selector("#toast-container .toast:has-text('Added to the books')")
        settle(page, handled)
    finally:
        page.close()

    assert names == [
        f"Class, line {fuel}",
        f"Job, line {fuel}",
        f"Vendor, line {fuel}",
        f"Customer, line {deposit}",
        f"Job, line {deposit}",
    ]
    assert bound == f"rv-customer-{deposit}"
    assert body == {
        "category_account_id": expense,
        "class_id": cls["id"],
        "job_id": books["job"],
        "vendor_id": vendor["id"],
    }
    line = next(
        t
        for t in _ok(
            company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
        )
        if t["id"] == fuel
    )
    entry = _ok(company.get(f"/api/journal/{line['transaction_id']}"))
    assert entry["class_id"] == cls["id"] and entry["job_id"] == books["job"]
