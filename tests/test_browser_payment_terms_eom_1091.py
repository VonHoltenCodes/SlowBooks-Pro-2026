"""#1091, Settings > Payment Terms: the end-of-month kind in playwright's
Chromium on the bakery's books of tests/test_theme_contrast.py. Picking it
shows its one field and the live example (the server's due date for "Net N
EOM" from today), hides the other kinds' fields, and the term it saves is
on the list in words and offered by the invoice form's terms.

Skipped, as one module, where playwright or its Chromium is not installed.
"""

from datetime import date, timedelta

import pytest

pytest.importorskip("playwright.sync_api")

from app.services.payment_terms import month_end  # noqa: E402
from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    browser_fixture,
    company_fixture,
    settle,
)


def _us(d):
    # the app's short date (formatDate): "Feb 13, 2027"
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def test_the_end_of_month_kind_on_the_terms_form(browser, company, books):
    page, handled = _open_at(browser, company, "#/settings", width=1264, height=900)
    try:
        page.click("#settings-tab-sales")
        settle(page, handled)
        page.click("text=+ Add Term")
        settle(page, handled)
        page.select_option("#pt-kind", "end_of_month")
        settle(page, handled)
        assert page.locator("#pt-eom-days").is_visible()
        assert page.locator("#pt-eom-discount-days").is_visible()
        assert not page.locator("#pt-net-days").is_visible()
        assert not page.locator("#pt-due-day").is_visible()
        assert not page.locator("#pt-cutoff").is_visible()
        page.fill("#pt-eom-days", "75")
        settle(page, handled)
        today = date.today()
        due = month_end(today) + timedelta(days=75)
        example = page.locator("#pt-eom-example").inner_text()
        assert example.startswith("For example, a document dated "), example
        assert example.endswith(f" is due {_us(due)}."), example
        # the teaching text is behind the field's tortoise, shut until asked
        button = page.locator(
            'button[aria-label="About Days after the end of the month"]'
        )
        note = page.locator(f"#{button.get_attribute('aria-controls')}")
        assert not note.is_visible()
        button.click()
        assert note.is_visible()
        page.keyboard.press("Escape")

        page.fill("#pt-name", "Month end + 75")
        page.click("#payment-term-editor button[type=submit]")
        settle(page, handled)
        row = page.locator("#payment-term-list tr", has_text="Month end + 75")
        assert "Due 75 days after the end of the month" in row.inner_text()
        # the forms that take terms offer it
        page.evaluate("async () => { await App.navigate('#/invoices'); }")
        settle(page, handled)
        page.evaluate("async () => { await InvoicesPage.showForm(); }")
        settle(page, handled)
        offered = page.evaluate(
            """() => [...document.querySelectorAll('#invoice-terms option')]
                .map(o => o.value).filter(v => v === 'Month end + 75').length"""
        )
        assert offered >= 1
    finally:
        page.close()
