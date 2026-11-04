"""Reports: Print beside Save PDF, in the page (#683).

A report with a PDF route shows a Print button, named Print, beside Save
PDF; a report with no PDF (the agings, CSV only, #393) shows none."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_grid import _open_at  # noqa: E402
from tests.test_theme_contrast import (  # noqa: E402,F401  (the fixtures)
    _visit,
    browser_fixture,
    company_fixture,
)

PL_URL = "#/reports/profit-loss?start_date=2026-09-01&end_date=2026-09-30"


def _buttons(page):
    return page.eval_on_selector_all(
        "#report-content button",
        "els => els.map(e => e.textContent.trim())",
    )


def test_print_is_beside_save_pdf_and_absent_without_one(browser, company, books):
    page, handled = _open_at(browser, company, PL_URL)
    try:
        page.wait_for_selector("#report-content table")
        assert page.get_by_role("button", name="Print", exact=True).count() == 1
        assert page.get_by_role("button", name="Save PDF").count() == 1
        labels = _buttons(page)
        assert labels.index("Print") + 1 == labels.index("Save PDF"), labels
        onclick = page.get_attribute(
            "#report-content button:text-is('Print')", "onclick"
        )
        assert "/api/reports/profit-loss/print?start_date=2026-09-01" in onclick

        _visit(page, handled, "#/reports/ar-aging?period=this_year_to_date")
        page.wait_for_selector("#report-content table")
        assert page.get_by_role("button", name="Save CSV").count() == 1
        assert page.get_by_role("button", name="Print", exact=True).count() == 0
        assert page.get_by_role("button", name="Save PDF").count() == 0
    finally:
        page.close()
