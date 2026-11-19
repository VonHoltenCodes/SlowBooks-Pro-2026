"""Receive payment from a To review line, in playwright's Chromium on the
bakery's books (#1056): Match on a deposit, Receive payment…, the
customer picked, their open invoice filled with what the line covers, and
the payment posts, matched to the line, which leaves To review.

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


def test_receive_a_payment_from_a_deposit_line(browser, company, books):
    customer = _ok(company.post("/api/customers", json={"name": "Corner Deli 1056"}))
    invoice = _ok(
        company.post(
            "/api/invoices",
            json={
                "customer_id": customer["id"],
                "date": "2026-09-01",
                "lines": [{"description": "Rolls", "quantity": 1, "rate": 175}],
            },
        )
    )
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "deposit.csv",
                    b'Date,Description,Amount\n09/28/2026,DEPOSIT 1056,"175.00"\n',
                    "text/csv",
                )
            },
        )
    )
    line = next(
        t
        for t in _ok(
            company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
        )
        if t["payee"] == "DEPOSIT 1056"
    )
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        page.click(f"tr[data-review-line='{line['id']}'] button:text-is('Match')")
        page.wait_for_selector("#feed-pay-open")
        assert page.input_value("#match-range") == "30"
        page.click("#feed-pay-open")
        page.wait_for_selector("#feed-pay-party")
        assert page.evaluate("() => document.activeElement.id") == "feed-pay-party"
        page.select_option("#feed-pay-party", str(customer["id"]))
        box = f"#feed-pay-docs input[data-doc='{invoice['id']}']"
        page.wait_for_selector(box)
        assert page.input_value(box) == "175.00"
        assert "Applying all of this line's" in page.inner_text("#feed-pay-sum")
        # the amount can be lowered: the rest stays a credit
        page.fill(box, "100")
        assert "left over stays a credit" in page.inner_text("#feed-pay-sum")
        page.click("#feed-pay-submit")
        page.wait_for_selector(
            "#toast-container .toast:has-text('received and matched')"
        )
        page.wait_for_function(
            f"() => !document.querySelector('tr[data-review-line=\"{line['id']}\"]')",
            timeout=5000,
        )
        settle(page, handled)
    finally:
        page.close()
    after = next(
        t
        for t in _ok(
            company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
        )
        if t["id"] == line["id"]
    )
    assert after["match_status"] == "added"
    inv = _ok(company.get(f"/api/invoices/{invoice['id']}"))
    assert float(inv["balance_due"]) == 75.0


def test_pay_bills_from_a_payment_line(browser, company, books):
    accounts = _ok(company.get("/api/accounts"))
    expense = next(a["id"] for a in accounts if a["account_type"] == "expense")
    vendor = _ok(
        company.post(
            "/api/vendors",
            json={"name": "Egg Farm 1056", "default_expense_account_id": expense},
        )
    )
    bill = _ok(
        company.post(
            "/api/bills",
            json={
                "vendor_id": vendor["id"],
                "bill_number": "EF-1056",
                "date": "2026-09-01",
                "lines": [{"description": "Eggs", "quantity": 1, "rate": 90}],
            },
        )
    )
    _ok(
        company.post(
            f"/api/bank-import/import-csv/{books['bank']}",
            files={
                "file": (
                    "paid.csv",
                    b'Date,Description,Amount\n09/28/2026,ACH EGG FARM,"-90.00"\n',
                    "text/csv",
                )
            },
        )
    )
    line = next(
        t
        for t in _ok(
            company.get(f"/api/banking/transactions?bank_account_id={books['bank']}")
        )
        if t["payee"] == "ACH EGG FARM"
    )
    page, handled = _open_at(browser, company, f"#/banking/{books['checking']}")
    settle(page, handled)
    try:
        # the vendor typed on the row is the one the dialog opens with
        page.fill(f"#rv-vendor-{line['id']}", vendor["name"])
        page.click(f"tr[data-review-line='{line['id']}'] button:text-is('Match')")
        page.wait_for_selector("#feed-pay-open:text-is('Pay bills…')")
        page.click("#feed-pay-open")
        box = f"#feed-pay-docs input[data-doc='{bill['id']}']"
        page.wait_for_selector(box)
        assert page.input_value("#feed-pay-party") == str(vendor["id"])
        assert page.input_value(box) == "90.00"
        page.click("#feed-pay-submit")
        page.wait_for_selector("#toast-container .toast:has-text('paid and matched')")
        settle(page, handled)
    finally:
        page.close()
    assert float(_ok(company.get(f"/api/bills/{bill['id']}"))["balance_due"]) == 0.0
